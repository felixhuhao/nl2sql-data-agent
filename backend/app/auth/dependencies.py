from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from backend.app.auth.models import Actor
from backend.app.config import get_settings


def current_actor(request: Request) -> Actor:
    settings = get_settings()
    if not settings.auth_enabled:
        return Actor(user_id="local", username="local", role="admin")

    cookie = request.cookies.get(settings.auth_cookie_name)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    session_store = getattr(request.app.state, "login_session_store", None)
    user_store = getattr(request.app.state, "user_store", None)
    if session_store is None or user_store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="auth store unavailable")

    record = session_store.get(cookie)
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")

    user = user_store.get_by_id(record["user_id"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return Actor.from_user(user)


def require_admin(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
    if actor.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return actor
