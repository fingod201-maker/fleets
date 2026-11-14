package main

import (
	"context"
	"testing"
	"time"
)

func TestNextBackoffCapsAtFiveSeconds(t *testing.T) {
	seq := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 5 * time.Second, 5 * time.Second}
	current := time.Second
	for _, want := range seq {
		if current != want {
			t.Fatalf("expected %s got %s", want, current)
		}
		current = nextBackoff(current)
	}
}

func TestClientErrorsBeforeConnect(t *testing.T) {
	mgr := NewManager(&Config{}, NewHub())
	if _, err := mgr.Client(); err == nil {
		t.Fatal("expected error when not connected")
	}
	connected, _, lastErr := mgr.Status()
	if connected {
		t.Fatal("expected connected=false")
	}
	if lastErr == "" {
		t.Fatal("expected a last error string")
	}
}

func TestSleepReturnsOnCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	start := time.Now()
	sleep(ctx, 10*time.Second)
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Fatalf("sleep did not return promptly: %s", elapsed)
	}
}

func TestHubFanoutAndUnsubscribe(t *testing.T) {
	hub := NewHub()
	id, ch := hub.Subscribe()
	hub.Publish([]byte("hello"))
	select {
	case got := <-ch:
		if string(got) != "hello" {
			t.Fatalf("got %q", got)
		}
	case <-time.After(time.Second):
		t.Fatal("no message delivered")
	}
	hub.Unsubscribe(id)
	if _, ok := <-ch; ok {
		t.Fatal("expected closed channel after unsubscribe")
	}
}
