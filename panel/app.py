"""FastAPI application factory, routes and WebSocket endpoints."""

from __future__ import annotations

import logging
import secrets
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import health
from .auth import Auth
from .config import Settings
from .db import Database
from .sliver import SliverBridge
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

    app = FastAPI(title="fleet panel", docs_url=None, redoc_url=None)
    app.state.settings = config
    app.state.db = database
    app.state.auth = auth
    app.state.bridge = SliverBridge(config)
    app.state.views = BridgeViews(config, database, app.state.bridge)

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

    return app


app = create_app()
