package main

import "testing"

func TestC2URLAddsScheme(t *testing.T) {
	cases := map[string]string{
		"mtls://1.2.3.4:8888": "mtls://1.2.3.4:8888",
		"http://x:80":         "http://x:80",
	}
	for in, want := range cases {
		if got := c2URL("mtls", in); got != want {
			t.Fatalf("c2URL(%q) = %q, want %q", in, got, want)
		}
	}
	if got := c2URL("mtls", "1.2.3.4:8888"); got != "mtls://1.2.3.4:8888" {
		t.Fatalf("mtls prefix = %q", got)
	}
	if got := c2URL("https", "x.example"); got != "https://x.example" {
		t.Fatalf("https prefix = %q", got)
	}
	if got := c2URL("mtls", ""); got != "" {
		t.Fatalf("empty returned %q", got)
	}
}

func TestOutputFormatAndEncoder(t *testing.T) {
	if outputFormat("shellcode", true) == outputFormat("executable", false) {
		t.Fatal("shellcode and executable must differ")
	}
	if outputFormat("shared", false).String() != "SHARED_LIB" {
		t.Fatal("shared format mapping")
	}
	if outputFormat("service", false).String() != "SERVICE" {
		t.Fatal("service format mapping")
	}
	if encoder("XOR") == encoder("NONE") {
		t.Fatal("XOR encoder must differ from NONE")
	}
}

func TestBuildImplantConfigDefaults(t *testing.T) {
	conf := buildImplantConfig(generateRequest{
		OS:        "linux",
		Arch:      "amd64",
		Transport: "mtls",
		C2URL:     "127.0.0.1:8888",
	})
	if conf.HTTPC2ConfigName == "" {
		t.Fatal("HTTPC2ConfigName must be set (server requires it)")
	}
	if conf.ReconnectInterval <= 0 {
		t.Fatal("reconnect interval must default to a positive nanosecond value")
	}
	if len(conf.C2) != 1 || conf.C2[0].URL != "mtls://127.0.0.1:8888" {
		t.Fatalf("unexpected C2: %+v", conf.C2)
	}
	if !conf.IncludeMTLS {
		t.Fatal("expected IncludeMTLS")
	}
}
