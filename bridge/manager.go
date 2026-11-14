package main

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/bishopfox/sliver/client/assets"
	"github.com/bishopfox/sliver/client/core"
	"github.com/bishopfox/sliver/client/transport"
	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/bishopfox/sliver/protobuf/rpcpb"
	"google.golang.org/grpc"
)

// Manager owns the single long-lived, reconnect-aware gRPC connection to the
// Sliver teamserver. Handlers never dial; they ask the Manager for the current
// client.
type Manager struct {
	cfg *Config
	hub *Hub

	mu        sync.RWMutex
	client    rpcpb.SliverRPCClient
	conn      *grpc.ClientConn
	connected bool
	lastErr   string
	version   string
}

func NewManager(cfg *Config, hub *Hub) *Manager {
	return &Manager{cfg: cfg, hub: hub, lastErr: "not connected"}
}

// Run reconnects forever with exponential backoff until ctx is done.
func (m *Manager) Run(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		if !m.connect(ctx) {
			sleep(ctx, backoff)
			backoff = nextBackoff(backoff)
			continue
		}
		backoff = time.Second
		tunnelCtx, cancel := context.WithCancel(ctx)
		go func() {
			if err := core.TunnelLoop(tunnelCtx, m.currentClient()); err != nil {
				logf("tunnel loop exited: %v", err)
			}
			cancel()
		}()
		go m.eventsLoop(tunnelCtx)
		m.monitor(tunnelCtx)
		cancel()
		m.teardown()
		sleep(ctx, backoff)
	}
}

func (m *Manager) connect(ctx context.Context) bool {
	conf, err := assets.ReadConfig(m.cfg.SliverCfg)
	if err != nil {
		m.setDown(fmt.Sprintf("read config %s: %v", m.cfg.SliverCfg, err))
		return false
	}
	if m.cfg.LHost != "" {
		conf.LHost = m.cfg.LHost
	}
	if m.cfg.LPort != 0 {
		conf.LPort = m.cfg.LPort
	}
	client, conn, err := transport.MTLSConnect(conf)
	if err != nil {
		m.setDown(fmt.Sprintf("connect: %v", err))
		return false
	}
	version := "unknown"
	if v, err := client.GetVersion(ctx, &commonpb.Empty{}); err == nil {
		version = fmt.Sprintf("%d.%d.%d", v.Major, v.Minor, v.Patch)
	}
	m.setConnected(client, conn, version)
	logf("connected to teamserver %s:%d (sliver %s)", conf.LHost, conf.LPort, version)
	return true
}

// monitor blocks until a ping fails or ctx is done.
func (m *Manager) monitor(ctx context.Context) {
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			client := m.currentClient()
			if client == nil {
				return
			}
			pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
			_, err := client.GetVersion(pingCtx, &commonpb.Empty{})
			cancel()
			if err != nil {
				m.setDown(fmt.Sprintf("ping: %v", err))
				return
			}
		}
	}
}

func (m *Manager) Client() (rpcpb.SliverRPCClient, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	if m.client == nil || !m.connected {
		return nil, fmt.Errorf("teamserver not connected: %s", m.lastErr)
	}
	return m.client, nil
}

func (m *Manager) currentClient() rpcpb.SliverRPCClient {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.client
}

func (m *Manager) Status() (bool, string, string) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.connected, m.version, m.lastErr
}

func (m *Manager) setConnected(client rpcpb.SliverRPCClient, conn *grpc.ClientConn, version string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.client, m.conn, m.connected, m.version, m.lastErr = client, conn, true, version, ""
}

func (m *Manager) setDown(err string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.connected = false
	m.lastErr = err
}

func (m *Manager) teardown() {
	m.mu.Lock()
	conn := m.conn
	m.mu.Unlock()
	if conn != nil {
		_ = transport.CloseGRPCConnection(conn)
	}
}

const maxBackoff = 5 * time.Second

func nextBackoff(d time.Duration) time.Duration {
	if d >= maxBackoff {
		return maxBackoff
	}
	if doubled := d * 2; doubled < maxBackoff {
		return doubled
	}
	return maxBackoff
}

func sleep(ctx context.Context, d time.Duration) {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}
