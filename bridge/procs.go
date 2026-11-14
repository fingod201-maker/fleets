package main

import (
	"context"
	"net/http"
	"time"

	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/bishopfox/sliver/protobuf/sliverpb"
)

func (s *Server) ps(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var body struct {
		Full bool `json:"full"`
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
	resp, err := client.Ps(ctx, &sliverpb.PsReq{
		FullInfo: body.Full,
		Request:  &commonpb.Request{SessionID: id},
	})
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	if resp.Response != nil && resp.Response.Err != "" {
		writeErr(w, http.StatusBadGateway, resp.Response.Err)
		return
	}
	procs := make([]map[string]any, 0, len(resp.Processes))
	for _, p := range resp.Processes {
		procs = append(procs, map[string]any{
			"pid":          p.Pid,
			"ppid":         p.Ppid,
			"executable":   p.Executable,
			"owner":        p.Owner,
			"architecture": p.Architecture,
			"session_id":   p.SessionID,
			"cmdline":      p.CmdLine,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"processes": procs})
}
