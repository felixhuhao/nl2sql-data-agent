from __future__ import annotations

import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, StringConstraints

from backend.app.auth.dependencies import current_actor
from backend.app.auth.models import Actor
from backend.app.auth.passwords import verify_password
from backend.app.config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = "pbkdf2_sha256$260000$d5v-KddZSuPsO_PO9pQ0kg$2IfU_Hd9-6S9hWgGu0mT2HGkQWkUM2OeL9cnnCAoWVw"


class LoginRequest(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    password: Annotated[str, StringConstraints(min_length=1)]


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.auth_enabled:
        return _actor_public(Actor(user_id="local", username="local", role="admin"))

    username = _canonical_username(payload.username)
    limiter = _get_rate_limiter(request)
    limiter_key = _rate_limit_key(request, username)
    retry_after = limiter.retry_after(limiter_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    user = request.app.state.user_store.get_by_username(username)
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)
    if user is None or not password_ok:
        limiter.record_failure(limiter_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    limiter.record_success(limiter_key)
    session_id = request.app.state.login_session_store.create(
        user.user_id,
        ttl_seconds=settings.auth_session_ttl_seconds,
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=session_id,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return _actor_public(Actor.from_user(user))


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    settings = get_settings()
    cookie = request.cookies.get(settings.auth_cookie_name)
    if settings.auth_enabled and cookie:
        request.app.state.login_session_store.delete(cookie)
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.get("/me")
def me(actor: Annotated[Actor, Depends(current_actor)]) -> dict:
    return _actor_public(actor)


def _actor_public(actor: Actor) -> dict:
    return {"user_id": actor.user_id, "username": actor.username, "role": actor.role}


class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        lockout_seconds: float,
        clock=time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.clock = clock
        self._records: dict[str, dict] = {}
        self._lock = threading.RLock()

    def retry_after(self, key: str) -> int | None:
        now = self.clock()
        with self._lock:
            self._sweep(now)
            record = self._records.get(key)
            if record is None:
                return None
            locked_until = float(record.get("locked_until", 0))
            if locked_until <= now:
                return None
            return max(1, int(locked_until - now))

    def record_failure(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._sweep(now)
            record = self._records.setdefault(key, {"attempts": [], "locked_until": 0.0})
            attempts = [attempt for attempt in record["attempts"] if now - attempt <= self.window_seconds]
            attempts.append(now)
            record["attempts"] = attempts
            if len(attempts) >= self.max_attempts:
                record["locked_until"] = now + self.lockout_seconds
                record["attempts"] = []

    def record_success(self, key: str) -> None:
        with self._lock:
            self._records.pop(key, None)

    def _sweep(self, now: float) -> None:
        stale_keys = []
        for key, record in self._records.items():
            locked_until = float(record.get("locked_until", 0))
            attempts = [attempt for attempt in record.get("attempts", []) if now - attempt <= self.window_seconds]
            record["attempts"] = attempts
            if not attempts and locked_until <= now:
                stale_keys.append(key)
        for key in stale_keys:
            self._records.pop(key, None)


def _get_rate_limiter(request: Request) -> LoginRateLimiter:
    limiter = getattr(request.app.state, "login_rate_limiter", None)
    if limiter is None:
        settings = get_settings()
        limiter = LoginRateLimiter(
            max_attempts=settings.auth_login_max_attempts,
            window_seconds=settings.auth_login_window_seconds,
            lockout_seconds=settings.auth_login_lockout_seconds,
        )
        request.app.state.login_rate_limiter = limiter
    return limiter


def _rate_limit_key(request: Request, username: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{client_host}:{_canonical_username(username)}"


def _canonical_username(username: str) -> str:
    return username.strip().casefold()
