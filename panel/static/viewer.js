(function () {
  "use strict";
  var cfg = window.__VIEWER || {};
  var sid = cfg.sid;
  var mode = cfg.mode === "hvnc" ? "hvnc" : "desktop";
  var canvas = document.getElementById("v-canvas");
  var status = document.getElementById("v-status");
  var cursor = document.getElementById("v-cursor");
  var socket = null;
  var frameCount = 0;
  var wsFrames = 0;
  var gotFrame = false;
  var pollTimer = null;
  var kbOn = true;
  var oskOn = false;
  var oskEl = null;
  var monitors = [{ left: 0, top: 0, width: 0, height: 0, label: "primary" }];
  var monSel = null;
  var monIdx = 0;

  function monitorRect() { return monitors[monIdx] || monitors[0]; }
  function rebase(x, y) {
    var r = monitorRect();
    var W = r.width || 1, H = r.height || 1;
    return { x: r.left + x * W, y: r.top + y * H };
  }

  function wsUrl(path) {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + path;
  }
  function setStatus(text) { if (status) status.textContent = text; }

  function drawBitmap(src) {
    canvas.width = src.width;
    canvas.height = src.height;
    canvas.getContext("2d").drawImage(src, 0, 0);
    frameCount += 1;
    if (frameCount <= 3 || frameCount % 30 === 0) {
      setStatus("streaming " + src.width + "x" + src.height + " · frame " + frameCount);
    }
  }

  function startSnapshotPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      if (wsFrames > 0) { clearInterval(pollTimer); pollTimer = null; return; }
      var img = new Image();
      img.onload = function () {
        drawBitmap(img);
        gotFrame = true;
        setStatus("streaming (snapshot " + img.width + "x" + img.height + ")");
      };
      img.src = "/api/" + sid + "/frame.jpg?t=" + Date.now();
    }, 400);
  }

  function send(obj) {
    if (socket && socket.readyState === 1) socket.send(JSON.stringify(obj));
  }
  function key(code, down) { send({ t: "k", code: code, down: down }); }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  async function combo(codes) {
    for (var i = 0; i < codes.length; i++) { key(codes[i], 1); await sleep(25); }
    for (var j = codes.length - 1; j >= 0; j--) { key(codes[j], 0); await sleep(25); }
  }

  function connect() {
    socket = new WebSocket(wsUrl("/rd/" + sid));
    socket.binaryType = "blob";
    socket.onopen = function () { setStatus("broker connected"); canvas.focus(); };
    socket.onclose = function () { setStatus("connection closed"); };
    socket.onmessage = async function (event) {
      if (typeof event.data === "string") {
        try {
          var msg = JSON.parse(event.data);
          if (msg.t === "hello") {
            if (msg.w && msg.h) { canvas.width = msg.w; canvas.height = msg.h; }
            if (msg.view_only) setStatus("view only (no display)");
            else setStatus("streaming (" + (msg.mode || mode) + ")");
            if (msg.needs_accessibility) setStatus("Accessibility not granted — input disabled until allowed in System Settings");
            if (Array.isArray(msg.monitors) && msg.monitors.length) {
              monitors = msg.monitors.map(function (m, i) {
                return { left: m.left || 0, top: m.top || 0, width: m.width || 1, height: m.height || 1,
                         label: m.label || ("monitor " + (i + 1)) };
              });
              if (monSel) refreshMonitors();
            }
          }
        } catch (err) { /* ignore */ }
        return;
      }
      gotFrame = true;
      wsFrames += 1;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      try {
        drawBitmap(await createImageBitmap(event.data));
      } catch (err) {
        var url = URL.createObjectURL(event.data);
        var im = new Image();
        im.onload = function () { drawBitmap(im); URL.revokeObjectURL(url); };
        im.onerror = function () { setStatus("frame decode error"); URL.revokeObjectURL(url); };
        im.src = url;
      }
    };
  }

  async function launch() {
    try {
      var r = await fetch("/api/" + sid + "/" + mode, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (!r.ok) {
        var data = {};
        try { data = await r.json(); } catch (e) {}
        setStatus("launch failed: " + (data.detail || r.status));
      }
    } catch (err) { setStatus("launch request failed"); }
    connect();
    setTimeout(function () { if (!gotFrame) startSnapshotPolling(); }, 2500);
  }

  function norm(ev) {
    var rect = canvas.getBoundingClientRect();
    var nx = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
    var ny = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height));
    var r = monitorRect();
    return {
      x: r.left + nx * r.width,
      y: r.top + ny * r.height,
    };
  }
  var movePending = null;
  var moveTimer = null;
  canvas.addEventListener("mousemove", function (ev) {
    if (cursor) { cursor.style.display = "block"; cursor.style.left = ev.clientX + "px"; cursor.style.top = ev.clientY + "px"; }
    movePending = norm(ev);
    if (!moveTimer) {
      moveTimer = setTimeout(function () {
        moveTimer = null;
        if (movePending) { send({ t: "m", x: movePending.x, y: movePending.y }); movePending = null; }
      }, 16);
    }
  });
  canvas.addEventListener("mouseleave", function () { if (cursor) cursor.style.display = "none"; });
  canvas.addEventListener("mousedown", function (ev) {
    ev.preventDefault(); canvas.focus();
    send({ t: "b", btn: ev.button, down: 1 });
  });
  canvas.addEventListener("mouseup", function (ev) { send({ t: "b", btn: ev.button, down: 0 }); });
  canvas.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });
  var lastWheel = 0;
  canvas.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var now = Date.now();
    if (now - lastWheel < 30) return;
    lastWheel = now;
    send({ t: "b", btn: ev.deltaY < 0 ? 4 : 5, down: 1 });
  }, { passive: false });
  function typingTarget(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea";
  }
  function printable(ev) {
    return ev.key && ev.key.length === 1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey;
  }
  document.addEventListener("keydown", function (ev) {
    if (!kbOn || typingTarget(ev.target)) return;
    if (ev.key === "F5" || ev.key === "F12") return;
    ev.preventDefault();
    if (printable(ev)) { send({ t: "txt", text: ev.key }); return; }
    key(ev.keyCode, 1);
  }, true);
  document.addEventListener("keyup", function (ev) {
    if (!kbOn || typingTarget(ev.target)) return;
    if (ev.key === "F5" || ev.key === "F12") return;
    ev.preventDefault();
    if (printable(ev)) return;
    key(ev.keyCode, 0);
  }, true);
  window.addEventListener("beforeunload", function () {
    if (socket) { try { socket.close(); } catch (e) {} }
    if (pollTimer) clearInterval(pollTimer);
  });

  function onClick(id, fn) { var el = document.getElementById(id); if (el) el.addEventListener("click", fn); }
  onClick("v-ctrlaltdel", function () { combo([17, 18, 46]); });
  onClick("v-alttab", function () { combo([18, 9]); });
  onClick("v-win", function () { key(91, 1); setTimeout(function () { key(91, 0); }, 60); });
  onClick("v-esc", function () { key(27, 1); setTimeout(function () { key(27, 0); }, 60); });
  onClick("v-type", function () {
    var text = window.prompt("Text to type on the remote device:");
    if (text) send({ t: "txt", text: text });
  });
  onClick("v-paste", async function () {
    try {
      var text = await navigator.clipboard.readText();
      if (text) send({ t: "txt", text: text });
    } catch (err) { setStatus("clipboard blocked - use Type…"); }
  });
  onClick("v-kb", function () {
    kbOn = !kbOn;
    this.classList.toggle("on", kbOn);
    this.textContent = "Keyboard: " + (kbOn ? "on" : "off");
  });
  onClick("v-full", function () {
    if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
    else if (canvas.requestFullscreen) canvas.requestFullscreen().catch(function () {});
  });

  function refreshMonitors() {
    if (!monSel) return;
    monSel.innerHTML = "";
    monitors.forEach(function (m, i) {
      var o = document.createElement("option");
      o.value = i;
      o.textContent = m.label + " (" + m.width + "x" + m.height + ")";
      if (i === monIdx) o.selected = true;
      monSel.appendChild(o);
    });
  }
  function initUI() {
    var bar = document.querySelector(".vbar");
    if (!bar) return;
    monSel = document.createElement("select");
    monSel.id = "v-mon";
    monSel.title = "Monitor";
    refreshMonitors();
    monSel.addEventListener("change", function () {
      monIdx = parseInt(monSel.value, 10) || 0;
    });
    bar.appendChild(document.createTextNode(" "));
    bar.appendChild(monSel);
    var osk = document.createElement("button");
    osk.id = "v-osk";
    osk.textContent = "OSK";
    osk.title = "On-screen keyboard";
    bar.appendChild(document.createTextNode(" "));
    bar.appendChild(osk);
    var panel = document.createElement("div");
    panel.id = "v-osk-panel";
    panel.style.display = "none";
    panel.style.position = "fixed";
    panel.style.bottom = "10px";
    panel.style.left = "10px";
    panel.style.background = "#171d27";
    panel.style.border = "1px solid #2a3342";
    panel.style.borderRadius = "7px";
    panel.style.padding = "6px";
    panel.style.gap = "4px";
    panel.style.display = "grid";
    panel.style.gridAutoFlow = "column";
    panel.style.zIndex = "1000";
    var rows = [
      ["`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "bksp"],
      ["tab", "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\"],
      ["caps", "a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'", "enter"],
      ["shift", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "shift"],
      ["ctrl", "alt", "space", "alt", "ctrl"]
    ];
    function keycap(label) {
      var b = document.createElement("button");
      b.textContent = label;
      b.style.padding = "4px 8px";
      b.style.background = "#1d2532";
      b.style.color = "#dfe5f0";
      b.style.border = "1px solid #2a3342";
      b.style.borderRadius = "5px";
      b.style.cursor = "pointer";
      b.addEventListener("click", function () { window.oskKey(label); });
      return b;
    }
    rows.forEach(function (row) {
      row.forEach(function (label) { panel.appendChild(keycap(label)); });
      var br = document.createElement("div");
      br.style.gridColumn = "1 / -1";
      br.style.height = "4px";
      panel.appendChild(br);
    });
    document.body.appendChild(panel);
    oskEl = panel;
    osk.addEventListener("click", function () {
      oskOn = !oskOn;
      oskEl.style.display = oskOn ? "grid" : "none";
      osk.classList.toggle("on", oskOn);
    });
    var codes = { enter: 13, bksp: 8, tab: 9, space: 32, ctrl: 17, alt: 18, shift: 16, caps: 20 };
    window.oskKey = function (label) {
      var c = codes[label];
      if (c !== undefined) { send({ t: "k", code: c, down: 1 }); send({ t: "k", code: c, down: 0 }); }
      else { send({ t: "txt", text: label === "space" ? " " : label }); }
    };
  }
  initUI();

  launch();
})();
