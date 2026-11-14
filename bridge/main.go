// Command bridge is the loopback-only gRPC<->HTTP/WS bridge between the Python
// panel and the Sliver teamserver. It is the only component that speaks the
// Sliver gRPC API, pinning the exact, grep-verified symbols in NOTES.md.
package main

import (
	"context"
	"errors"
	"net/http"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	cfg := loadConfig()
	hub := NewHub()
	mgr := NewManager(cfg, hub)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	go mgr.Run(ctx)

	server := NewServer(cfg, mgr, hub)
	httpServer := &http.Server{
		Addr:              cfg.Bind,
		Handler:           server.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		logf("listening on %s (operator config %s)", cfg.Bind, cfg.SliverCfg)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logf("http server: %v", err)
			stop()
		}
	}()

	<-ctx.Done()
	logf("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(shutdownCtx)
}
