package main

import (
	"context"
	"net/http"
	"time"

	"github.com/bishopfox/sliver/client/core"
	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/bishopfox/sliver/protobuf/sliverpb"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1 << 16,
	WriteBufferSize: 1 << 16,
	CheckOrigin:     func(*http.Request) bool { return true },
}

// shellWS bridges a browser xterm WebSocket to a Sliver shell tunnel.
func (s *Server) shellWS(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	client, err := s.mgr.Client()
	if err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer func() { _ = conn.Close() }()

	rows := uint32(queryInt(r, "rows", 40))
	cols := uint32(queryInt(r, "cols", 120))

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	session, err := s.findSession(ctx, id)
	if err != nil {
		_ = conn.WriteMessage(websocket.TextMessage, []byte("error: "+err.Error()+"\r\n"))
		return
	}
	enablePTY := session.OS == "linux" || session.OS == "darwin"

	tunnelResp, err := client.CreateTunnel(ctx, &sliverpb.Tunnel{SessionID: id})
	if err != nil {
		_ = conn.WriteMessage(websocket.TextMessage, []byte("error: create tunnel: "+err.Error()+"\r\n"))
		return
	}
	tunnel := core.GetTunnels().Start(tunnelResp.TunnelID, tunnelResp.SessionID)

	select {
	case <-tunnel.Bound():
	case <-time.After(5 * time.Second):
		_ = conn.WriteMessage(websocket.TextMessage, []byte("error: tunnel bind timeout\r\n"))
		core.GetTunnels().CloseIf(tunnel)
		return
	}

	shell, err := client.Shell(ctx, &sliverpb.ShellReq{
		Request:   &commonpb.Request{SessionID: id},
		TunnelID:  tunnel.ID,
		EnablePTY: enablePTY,
		Rows:      rows,
		Cols:      cols,
	})
	if err != nil {
		_ = conn.WriteMessage(websocket.TextMessage, []byte("error: shell: "+err.Error()+"\r\n"))
		core.GetTunnels().CloseIf(tunnel)
		return
	}
	if shell.Response != nil && shell.Response.Err != "" {
		_ = conn.WriteMessage(websocket.TextMessage, []byte("error: "+shell.Response.Err+"\r\n"))
		core.GetTunnels().CloseIf(tunnel)
		return
	}

	done := make(chan struct{})
	go func() {
		defer close(done)
		buf := make([]byte, 32*1024)
		for {
			n, err := tunnel.Read(buf)
			if n > 0 {
				if werr := conn.WriteMessage(websocket.BinaryMessage, buf[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()

	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			break
		}
		if _, err := tunnel.Write(data); err != nil {
			break
		}
	}

	_, _ = tunnel.Write([]byte("exit\n"))
	closeCtx, closeCancel := context.WithTimeout(context.Background(), 5*time.Second)
	_, _ = client.CloseTunnel(closeCtx, &sliverpb.Tunnel{TunnelID: tunnel.ID, SessionID: tunnel.SessionID})
	closeCancel()
	core.GetTunnels().CloseIf(tunnel)
	<-done
}
