package main

import (
	"context"
	"net/http"
	"time"

	"github.com/bishopfox/sliver/protobuf/clientpb"
	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/bishopfox/sliver/protobuf/sliverpb"
)

func sessionJSON(s *clientpb.Session) map[string]any {
	return map[string]any{
		"id":                 s.ID,
		"name":               s.Name,
		"host":               s.Hostname,
		"os":                 s.OS,
		"arch":               s.Arch,
		"user":               s.Username,
		"uid":                s.UID,
		"gid":                s.GID,
		"pid":                s.PID,
		"transport":          s.Transport,
		"remote_address":     s.RemoteAddress,
		"last_checkin":       s.LastCheckin,
		"first_contact":      s.FirstContact,
		"integrity":          s.Integrity,
		"version":            s.Version,
		"active_c2":          s.ActiveC2,
		"reconnect_interval": s.ReconnectInterval / int64(time.Second),
		"locale":             s.Locale,
		"filename":           s.Filename,
		"evasion":            s.Evasion,
		"is_dead":            s.IsDead,
	}
}

func (s *Server) findSession(ctx context.Context, id string) (*clientpb.Session, error) {
	client, err := s.mgr.Client()
	if err != nil {
		return nil, err
	}
	sessions, err := client.GetSessions(ctx, &commonpb.Empty{})
	if err != nil {
		return nil, err
	}
	for _, session := range sessions.Sessions {
		if session.ID == id {
			return session, nil
		}
	}
	return nil, errNotFound("session " + id)
}

type notFound string

func (n notFound) Error() string { return string(n) }
func errNotFound(what string) error {
	return notFound(what)
}

func (s *Server) sessions(w http.ResponseWriter, r *http.Request) {
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	resp, err := client.GetSessions(ctx, &commonpb.Empty{})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	out := make([]map[string]any, 0, len(resp.Sessions))
	for _, session := range resp.Sessions {
		out = append(out, sessionJSON(session))
	}
	writeJSON(w, http.StatusOK, map[string]any{"sessions": out})
}

func (s *Server) beacons(w http.ResponseWriter, r *http.Request) {
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	resp, err := client.GetBeacons(ctx, &commonpb.Empty{})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	out := make([]map[string]any, 0, len(resp.Beacons))
	for _, beacon := range resp.Beacons {
		out = append(out, map[string]any{
			"id":           beacon.ID,
			"name":         beacon.Name,
			"host":         beacon.Hostname,
			"os":           beacon.OS,
			"arch":         beacon.Arch,
			"user":         beacon.Username,
			"pid":          beacon.PID,
			"transport":    beacon.Transport,
			"last_checkin": beacon.LastCheckin,
			"interval":     beacon.Interval,
			"jitter":       beacon.Jitter,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"beacons": out})
}

func (s *Server) jobs(w http.ResponseWriter, r *http.Request) {
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	resp, err := client.GetJobs(ctx, &commonpb.Empty{})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	out := make([]map[string]any, 0, len(resp.Active))
	for _, job := range resp.Active {
		out = append(out, map[string]any{
			"id":       job.ID,
			"name":     job.Name,
			"protocol": job.Protocol,
			"port":     job.Port,
			"domains":  job.Domains,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"jobs": out})
}

func (s *Server) info(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	session, err := s.findSession(ctx, id)
	if err != nil {
		writeErr(w, http.StatusNotFound, err.Error())
		return
	}
	payload := sessionJSON(session)
	payload["age_seconds"] = time.Now().Unix() - session.LastCheckin

	if ifc, err := client.Ifconfig(ctx, &sliverpb.IfconfigReq{Request: &commonpb.Request{SessionID: id}}); err == nil {
		ifaces := make([]map[string]any, 0, len(ifc.NetInterfaces))
		for _, in := range ifc.NetInterfaces {
			ifaces = append(ifaces, map[string]any{
				"index": in.Index,
				"name":  in.Name,
				"mac":   in.MAC,
				"ips":   in.IPAddresses,
			})
		}
		payload["interfaces"] = ifaces
	}
	if privs, err := client.GetPrivs(ctx, &sliverpb.GetPrivsReq{Request: &commonpb.Request{SessionID: id}}); err == nil {
		payload["privs"] = map[string]any{
			"process_integrity": privs.ProcessIntegrity,
			"process_name":      privs.ProcessName,
		}
	}
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) rename(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var body struct {
		Name string `json:"name"`
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
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	if _, err := client.Rename(ctx, &clientpb.RenameReq{SessionID: id, Name: body.Name}); err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "name": body.Name})
}
