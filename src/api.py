"""FastAPI application for HubSpot→Sheet sync dashboard.

Routes:
  GET  /              — Jinja2 dashboard
  POST /api/sync      — trigger sync (background task)
  GET  /api/status    — last run status + running flag
  GET  /api/logs      — recent run history
  GET  /health        — health check

Auth: controlled by AUTH_ENABLED env var (default false).
  When true, Google OIDC auth wall is applied to all routes except /health.
  When false, all routes are open.

Port: PORT env var (default 8008).
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src import scheduler
from src.sync import run_sync
from src.scheduler import append_run_history, get_last_result, get_next_run_times, load_run_history

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="HubSpot Sheet Sync",
    description="Syncs HubSpot Sales Pipeline to Google Sheet",
    version="1.0.0",
)

# Static files and templates
_ROOT = Path(__file__).parent.parent
app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

# ── Google OIDC auth (optional) ───────────────────────────────────────────────

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").strip().lower() == "true"
ALLOWED_EMAILS: set[str] = set()

if AUTH_ENABLED:
    try:
        from authlib.integrations.starlette_client import OAuth
        from starlette.config import Config as StarletteConfig

        _raw_emails = os.getenv("ALLOWED_EMAILS", "").strip()
        ALLOWED_EMAILS = {e.strip().lower() for e in _raw_emails.split(",") if e.strip()}

        app.add_middleware(
            SessionMiddleware,
            secret_key=os.getenv("SESSION_SECRET", os.urandom(32).hex()),
        )

        _starlette_config = StarletteConfig(environ=os.environ)
        _oauth = OAuth(_starlette_config)
        _oauth.register(
            name="google",
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            client_kwargs={"scope": "openid email profile"},
        )
        logger.info("Google OIDC auth enabled. Allowed emails: %s", ALLOWED_EMAILS or "all")

    except ImportError:
        logger.warning(
            "AUTH_ENABLED=true but authlib is not installed. "
            "Install 'authlib' and 'itsdangerous' to enable auth. Auth skipped."
        )
        AUTH_ENABLED = False

# ── Auth helper ───────────────────────────────────────────────────────────────

def _get_current_user(request: Request) -> str | None:
    """Return email of authenticated user, or None."""
    if not AUTH_ENABLED:
        return None
    return request.session.get("user_email")


def _require_auth(request: Request) -> str:
    """Raise 401/redirect if user is not authenticated."""
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if ALLOWED_EMAILS and user.lower() not in ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


# ── Sync state ────────────────────────────────────────────────────────────────

_sync_lock = asyncio.Lock()
_sync_running = False


async def _run_sync_background() -> None:
    """Run sync in a thread pool (blocking I/O) and record result."""
    global _sync_running
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, lambda: run_sync(dry_run=False))
        append_run_history(result)
        from src import slack
        slack.notify_success(result)
    except Exception as exc:
        logger.error("Background sync failed: %s", exc, exc_info=True)
        from src import slack
        slack.notify_error(str(exc))
    finally:
        _sync_running = False


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup() -> None:
    scheduler.start_scheduler()
    logger.info("HubSpot Sheet Sync service started on port %s", os.getenv("PORT", "8008"))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    scheduler.stop_scheduler()
    logger.info("HubSpot Sheet Sync service stopped")


# ── Auth routes (only registered when AUTH_ENABLED=true) ──────────────────────

if AUTH_ENABLED:
    @app.get("/auth/login")
    async def auth_login(request: Request):
        redirect_uri = request.url_for("auth_callback")
        return await _oauth.google.authorize_redirect(request, redirect_uri)

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):
        try:
            token = await _oauth.google.authorize_access_token(request)
            user_info = token.get("userinfo") or await _oauth.google.userinfo(token=token)
            email = user_info.get("email", "").lower()

            if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
                return HTMLResponse("<h1>403 — Access Denied</h1><p>Your account is not allowed.</p>", status_code=403)

            request.session["user_email"] = email
            return RedirectResponse(url="/")
        except Exception as exc:
            logger.warning("OAuth callback error: %s", exc)
            return HTMLResponse(f"<h1>Auth error</h1><p>{exc}</p>", status_code=400)

    @app.get("/auth/logout")
    async def auth_logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/")


# ── Main routes ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if AUTH_ENABLED:
        try:
            _require_auth(request)
        except HTTPException:
            return RedirectResponse(url="/auth/login")

    last_result = get_last_result()
    next_runs = get_next_run_times()
    history = load_run_history(limit=10)

    # Determine status colour based on last sync time
    status_color = "red"
    if last_result:
        try:
            finished_str = last_result.get("finished_at", "")
            finished_dt = datetime.fromisoformat(finished_str)
            if finished_dt.tzinfo is None:
                finished_dt = finished_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - finished_dt).total_seconds() / 3600
            if age_hours < 13:
                status_color = "green"
            elif age_hours < 25:
                status_color = "yellow"
            else:
                status_color = "red"
        except Exception:
            status_color = "red"

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "last_result": last_result,
            "next_runs": next_runs,
            "history": history,
            "sync_running": _sync_running,
            "status_color": status_color,
            "user": _get_current_user(request),
            "auth_enabled": AUTH_ENABLED,
        },
    )


@app.post("/api/sync")
async def trigger_sync(request: Request, background_tasks=None):
    """Trigger a sync run. Returns 409 if one is already running."""
    global _sync_running

    if AUTH_ENABLED:
        _require_auth(request)

    async with _sync_lock:
        if _sync_running:
            raise HTTPException(status_code=409, detail="Sync already in progress")
        _sync_running = True

    # Schedule as background task
    asyncio.get_event_loop().create_task(_run_sync_background())

    return JSONResponse(
        status_code=202,
        content={"message": "Sync started", "running": True},
    )


@app.get("/api/status")
async def get_status(request: Request):
    """Return last run result + running flag + next scheduled run."""
    if AUTH_ENABLED:
        _require_auth(request)

    last_result = get_last_result()
    next_runs = get_next_run_times()

    return {
        "running": _sync_running,
        "next_run_at": next_runs[0] if next_runs else None,
        "last_result": last_result,
    }


@app.get("/api/logs")
async def get_logs(request: Request, limit: int = 20):
    """Return last N run history entries."""
    if AUTH_ENABLED:
        _require_auth(request)

    if limit < 1 or limit > 200:
        limit = 20

    return {"runs": load_run_history(limit=limit)}
