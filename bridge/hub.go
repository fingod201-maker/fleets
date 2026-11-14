package main

import "sync"

// Hub fans out Sliver events to every connected WebSocket subscriber without
// ever blocking the event loop on a slow subscriber (bounded drop).
type Hub struct {
	mu     sync.Mutex
	nextID int
	subs   map[int]chan []byte
}

func NewHub() *Hub {
	return &Hub{subs: map[int]chan []byte{}}
}

func (h *Hub) Subscribe() (int, <-chan []byte) {
	h.mu.Lock()
	defer h.mu.Unlock()
	ch := make(chan []byte, 128)
	id := h.nextID
	h.nextID++
	h.subs[id] = ch
	return id, ch
}

func (h *Hub) Unsubscribe(id int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if ch, ok := h.subs[id]; ok {
		delete(h.subs, id)
		close(ch)
	}
}

func (h *Hub) Publish(payload []byte) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, ch := range h.subs {
		select {
		case ch <- payload:
		default:
			// subscriber too slow; drop this frame
		}
	}
}
