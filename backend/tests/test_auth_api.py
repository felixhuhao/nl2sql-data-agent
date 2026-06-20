import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.api import auth as auth_module
from backend.app.auth.login_sessions import SQLiteLoginSessionStore
from backend.app.auth.models import User
from backend.app.auth.passwords import hash_password
from backend.app.config import get_settings


def test_me_returns_local_actor_when_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()

    client = TestClient(main.app)
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {"user_id": "local", "username": "local", "role": "admin"}


def test_login_me_and_logout(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")

    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    assert login.status_code == 200
    assert login.json() == {"user_id": login.json()["user_id"], "username": "admin", "role": "admin"}
    assert "nl2sql_session" in login.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    expired = client.get("/api/auth/me")
    assert expired.status_code == 401


def test_login_accepts_case_insensitive_bootstrap_username(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, username="Admin", password="pw")

    client = TestClient(main.app)
    response = client.post("/api/auth/login", json={"username": "ADMIN", "password": "pw"})

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_login_rejects_bad_password(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")

    client = TestClient(main.app)
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid credentials"


def test_unknown_user_login_uses_dummy_password_verify(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")
    calls = []
    original_verify = auth_module.verify_password

    def spy_verify(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return original_verify(password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", spy_verify)

    client = TestClient(main.app)
    response = client.post("/api/auth/login", json={"username": "ghost", "password": "pw"})

    assert response.status_code == 401
    assert calls == [auth_module._DUMMY_PASSWORD_HASH]


def test_login_rate_limit_locks_repeated_failures(monkeypatch, tmp_path):
    _enable_auth(
        monkeypatch,
        tmp_path,
        password="pw",
        max_attempts=2,
        window_seconds=60,
        lockout_seconds=60,
    )

    client = TestClient(main.app)
    assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401

    response = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})

    assert response.status_code == 429
    assert response.headers["retry-after"]


def test_login_rate_limiter_sweeps_stale_records():
    now = [1000.0]
    limiter = auth_module.LoginRateLimiter(
        max_attempts=2,
        window_seconds=10,
        lockout_seconds=20,
        clock=lambda: now[0],
    )
    limiter.record_failure("client:ghost")
    assert "client:ghost" in limiter._records

    now[0] = 1011.0

    assert limiter.retry_after("client:other") is None
    assert "client:ghost" not in limiter._records


def test_protected_route_rejects_anonymous_when_auth_enabled(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")

    client = TestClient(main.app)
    response = client.get("/api/datasources")

    assert response.status_code == 401


def test_chat_query_rejects_anonymous_when_auth_enabled(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")

    client = TestClient(main.app)
    response = client.post("/api/chat/query", json={"question": "hello"})

    assert response.status_code == 401


def test_protected_route_allows_authenticated_user(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")

    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    assert login.status_code == 200

    response = client.get("/api/datasources")

    assert response.status_code == 200
    assert response.json()["default"]


def test_protected_route_rejects_non_admin_user(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")
    main.app.state.user_store.create(
        User(
            user_id="viewer-1",
            username="viewer",
            password_hash=hash_password("pw"),
            role="viewer",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )

    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"username": "viewer", "password": "pw"})
    assert login.status_code == 200

    response = client.get("/api/datasources")

    assert response.status_code == 403


def test_expired_session_rejects_me(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path, password="pw")
    now = [1000.0]
    store = SQLiteLoginSessionStore(tmp_path / "auth.sqlite", clock=lambda: now[0])
    store.ensure_schema()
    main.app.state.login_session_store = store
    user = main.app.state.user_store.get_by_username("admin")
    session_id = store.create(user.user_id, ttl_seconds=10)
    now[0] = 1011.0

    client = TestClient(main.app)
    client.cookies.set("nl2sql_session", session_id)
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "session expired"


def test_auth_enabled_without_bootstrap_password_fails_fast(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_PASSWORD", "")
    monkeypatch.setenv("AUTH_SQLITE_PATH", str(tmp_path / "auth.sqlite"))
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="AUTH_BOOTSTRAP_PASSWORD"):
        main._configure_auth(main.app)


def _enable_auth(
    monkeypatch,
    tmp_path,
    *,
    password: str,
    username: str = "admin",
    max_attempts: int = 5,
    window_seconds: int = 60,
    lockout_seconds: int = 300,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_USERNAME", username)
    monkeypatch.setenv("AUTH_BOOTSTRAP_PASSWORD", password)
    monkeypatch.setenv("AUTH_SQLITE_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_LOGIN_MAX_ATTEMPTS", str(max_attempts))
    monkeypatch.setenv("AUTH_LOGIN_WINDOW_SECONDS", str(window_seconds))
    monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_SECONDS", str(lockout_seconds))
    get_settings.cache_clear()
    main._configure_auth(main.app)
    assert isinstance(main.app.state.login_rate_limiter, auth_module.LoginRateLimiter)
