package main

import (
	"flag"
	"os"
	"strconv"
)

// Config is the bridge's runtime configuration. All values are loopback-only;
// the bind address is never exposed to the internet directly (Caddy/tunnel sit
// in front).
type Config struct {
	Bind         string
	SliverCfg    string
	LHost        string
	LPort        int
	Token        string
	DocumentName string
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func loadConfig() *Config {
	cfg := &Config{
		Bind:         env("BRIDGE_BIND", "127.0.0.1:8001"),
		SliverCfg:    env("SLIVER_CFG", "/opt/panel/panel.cfg"),
		LHost:        os.Getenv("SLIVER_LHOST"),
		DocumentName: env("DOCUMENT_NAME", "Q3 Report"),
	}
	if p := os.Getenv("SLIVER_LPORT"); p != "" {
		if n, err := strconv.Atoi(p); err == nil {
			cfg.LPort = n
		}
	}
	cfg.Token = os.Getenv("BRIDGE_TOKEN")

	flag.StringVar(&cfg.Bind, "bind", cfg.Bind, "loopback bind address")
	flag.StringVar(&cfg.SliverCfg, "config", cfg.SliverCfg, "sliver operator config path")
	flag.StringVar(&cfg.LHost, "lhost", cfg.LHost, "override teamserver host")
	flag.IntVar(&cfg.LPort, "lport", cfg.LPort, "override teamserver port")
	flag.Parse()
	return cfg
}
