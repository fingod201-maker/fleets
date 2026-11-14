package main

import (
	"encoding/json"
	"net/http"
	"strconv"
)

// Server exposes the loopback HTTP/WS surface the panel consumes.
type Server struct {
	cfg *Config
	mgr *Manager
	hub *Hub
}

func NewServer(cfg *Config, mgr *Manager, hub *Hub) *Server {
	return &Server{cfg: cfg, mgr: mgr, hub: hub}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", s.healthz)
	mux.HandleFunc("GET /sliver/version", s.version)

	mux.HandleFunc("GET /sliver/sessions", s.sessions)
	mux.HandleFunc("GET /sliver/beacons", s.beacons)
	mux.HandleFunc("GET /sliver/jobs", s.jobs)

	mux.HandleFunc("GET /sliver/session/{id}/info", s.info)
	mux.HandleFunc("POST /sliver/session/{id}/exec", s.exec)
	mux.HandleFunc("POST /sliver/session/{id}/ls", s.ls)
	mux.HandleFunc("POST /sliver/session/{id}/upload", s.upload)
	mux.HandleFunc("GET /sliver/session/{id}/download", s.download)
	mux.HandleFunc("POST /sliver/session/{id}/ps", s.ps)
	mux.HandleFunc("POST /sliver/session/{id}/kill", s.kill)
	mux.HandleFunc("POST /sliver/session/{id}/ifconfig", s.ifconfig)
	mux.HandleFunc("GET /sliver/session/{id}/getsystem", s.getsystem)
	mux.HandleFunc("POST /sliver/session/{id}/rename", s.rename)

	mux.HandleFunc("POST /sliver/generate", s.generate)

	mux.HandleFunc("GET /sliver/events", s.eventsWS)
	mux.HandleFunc("GET /sliver/session/{id}/shell", s.shellWS)

	return s.withToken(mux)
}

func (s *Server) withToken(next http.Handler) http.Handler {
	if s.cfg.Token == "" {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token := r.Header.Get("X-Bridge-Token")
		if token == "" {
			token = r.URL.Query().Get("token")
		}
		if token != s.cfg.Token {
			writeErr(w, http.StatusUnauthorized, "bad bridge token")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		logf("encode response: %v", err)
	}
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func decode(r *http.Request, dst any) error {
	defer func() { _ = r.Body.Close() }()
	dec := json.NewDecoder(r.Body)
	return dec.Decode(dst)
}

func queryInt(r *http.Request, key string, fallback int) int {
	if v := r.URL.Query().Get(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func (s *Server) healthz(w http.ResponseWriter, _ *http.Request) {
	connected, version, lastErr := s.mgr.Status()
	resp := map[string]any{"ok": true, "connected": connected, "version": version}
	if lastErr != "" {
		resp["last_error"] = lastErr
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) version(w http.ResponseWriter, _ *http.Request) {
	connected, version, lastErr := s.mgr.Status()
	if !connected {
		writeErr(w, http.StatusServiceUnavailable, lastErr)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"version": version})
}
