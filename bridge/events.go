package main

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/bishopfox/sliver/protobuf/commonpb"
	"github.com/gorilla/websocket"
)

// eventsLoop subscribes to the Sliver client event stream and republishes each
// event to the hub. Restarts (with the connection) on error.
func (m *Manager) eventsLoop(ctx context.Context) {
	client := m.currentClient()
	if client == nil {
		return
	}
	stream, err := client.Events(ctx, &commonpb.Empty{})
	if err != nil {
		logf("events subscribe: %v", err)
		return
	}
	logf("subscribed to sliver event stream")
	for {
		event, err := stream.Recv()
		if err != nil {
			if ctx.Err() == nil {
				logf("event stream closed: %v", err)
			}
			return
		}
		payload := map[string]any{
			"type": event.EventType,
			"err":  event.Err,
		}
		if event.Session != nil {
			payload["session"] = sessionJSON(event.Session)
		}
		if len(event.Data) > 0 {
			payload["data"] = string(event.Data)
		}
		encoded, err := json.Marshal(payload)
		if err != nil {
			continue
		}
		m.hub.Publish(encoded)
	}
}

func (s *Server) eventsWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer func() { _ = conn.Close() }()

	id, ch := s.hub.Subscribe()
	defer s.hub.Unsubscribe(id)

	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}()

	for {
		select {
		case payload, ok := <-ch:
			if !ok {
				return
			}
			if err := conn.WriteMessage(websocket.TextMessage, payload); err != nil {
				return
			}
		case <-readDone:
			return
		}
	}
}
