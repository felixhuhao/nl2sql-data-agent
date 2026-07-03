import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp
from starlette.responses import JSONResponse

from backend.app.api.auth import router as auth_router
from backend.app.api.chat import router as chat_router
from backend.app.api.datasources import router as datasources_router
from backend.app.api.metadata import router as metadata_router
from backend.app.api.auth import LoginRateLimiter
from backend.app.auth.dependencies import require_admin
from backend.app.auth.login_sessions import SQLiteLoginSessionStore
from backend.app.auth.models import User
from backend.app.auth.passwords import hash_password
from backend.app.auth.users_store import SQLiteUserStore
from backend.app.config import (
    cors_allow_origins,
    deepseek_config_available,
    effective_llm_provider_name,
    get_settings,
    semantic_guard_mode,
)
from backend.app.connectors.registry import get_datasource_manager

logger = logging.getLogger(__name__)

try:
    from mcp_servers.combined.server import (
        create_http_app as create_mcp_http_app,
        get_server as get_mcp_server,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"mcp", "mcp_servers"}:
        raise
    create_mcp_http_app = None
    get_mcp_server = None


class ServiceTokenMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        token = get_settings().nl2sql_mcp_service_token.strip()
        if scope.get("type") != "http" or not token:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"x-service-token", b"")
        if not hmac.compare_digest(supplied, token.encode("utf-8")):
            response = JSONResponse({"detail": "invalid service token"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_auth(app)
    if get_mcp_server is None:
        yield
        return
    async with get_mcp_server().session_manager.run():
        yield


def _configure_auth(app: FastAPI) -> None:
    settings = get_settings()
    if not settings.auth_enabled:
        return
    if not settings.auth_cookie_secure:
        logger.warning("AUTH_ENABLED=true with AUTH_COOKIE_SECURE=false; use this only behind local HTTP.")

    user_store = SQLiteUserStore(settings.resolved_auth_sqlite_path())
    login_session_store = SQLiteLoginSessionStore(settings.resolved_auth_sqlite_path())
    user_store.ensure_schema()
    login_session_store.ensure_schema()
    login_session_store.delete_expired()

    if user_store.count() == 0 and settings.auth_bootstrap_password:
        user_store.create(
            User(
                user_id=uuid.uuid4().hex,
                username=settings.auth_bootstrap_username.strip().casefold(),
                password_hash=hash_password(settings.auth_bootstrap_password),
                role="admin",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    elif user_store.count() == 0:
        logger.error("AUTH_ENABLED=true but no users exist. Set AUTH_BOOTSTRAP_PASSWORD for first startup.")
        raise RuntimeError("AUTH_BOOTSTRAP_PASSWORD is required when auth is enabled and no users exist.")

    app.state.user_store = user_store
    app.state.login_session_store = login_session_store
    app.state.login_rate_limiter = LoginRateLimiter(
        max_attempts=settings.auth_login_max_attempts,
        window_seconds=settings.auth_login_window_seconds,
        lockout_seconds=settings.auth_login_lockout_seconds,
    )


app = FastAPI(title="NL2SQL Data Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.datasource_manager = get_datasource_manager()
auth_dependency = [Depends(require_admin)]
app.include_router(auth_router)
app.include_router(datasources_router, dependencies=auth_dependency)
app.include_router(chat_router, dependencies=auth_dependency)
app.include_router(metadata_router, dependencies=auth_dependency)

if create_mcp_http_app is not None:
    app.mount("/mcp", cast(ASGIApp, ServiceTokenMiddleware(create_mcp_http_app())))


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    settings = get_settings()
    guard_mode = semantic_guard_mode(settings)
    verifier_available = deepseek_config_available(settings)
    verifier_status = "disabled" if guard_mode == "off" else ("enabled" if verifier_available else "unavailable")
    status = "degraded" if guard_mode == "enforce" and not verifier_available else "ok"
    return {
        "status": status,
        "llm_provider": effective_llm_provider_name(),
        "semantic_guard": guard_mode,
        "semantic_verifier": verifier_status,
    }
