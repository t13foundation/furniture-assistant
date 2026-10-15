from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from fastapi import Request
from fastapi.responses import JSONResponse

Role = Literal["viewer", "editor", "admin"]
COOKIE_NAME = "fa_session"
SESSION_SECONDS = 60 * 60 * 24 * 7
ROOT = Path(__file__).resolve().parents[2]


def users_path() -> Path:
    return Path(os.environ.get("FA_USERS_FILE", ROOT / "config" / "users.yaml"))


_USERS_CACHE: tuple[Path, float, dict[str, dict[str, str]]] | None = None


def load_users() -> dict[str, dict[str, str]]:
    """Users keyed by name; cached until the file's mtime changes (it is read on every request)."""
    global _USERS_CACHE
    path = users_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        _USERS_CACHE = None
        return {}
    if _USERS_CACHE and _USERS_CACHE[:2] == (path, stamp):
        return _USERS_CACHE[2]
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    users = raw.get("users", raw) if isinstance(raw, dict) else {}
    loaded = {str(name): value for name, value in users.items() if isinstance(value, dict)}
    _USERS_CACHE = (path, stamp, loaded)
    return loaded


def auth_enabled() -> bool:
    return bool(load_users())


def _secret() -> bytes:
    value = os.environ.get("FA_SESSION_SECRET")
    if value:
        return value.encode()
    path = ROOT / "config" / ".session_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        secret = secrets.token_bytes(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes().strip()
        with os.fdopen(fd, "wb") as stream:
            stream.write(secret)
        return secret


def _signature(payload: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def create_session(username: str, expiry: int | None = None) -> str:
    payload = f"{username}|{expiry or int(time.time()) + SESSION_SECONDS}"
    return f"{payload}|{_signature(payload)}"


def session_username(token: str | None) -> str | None:
    if not token:
        return None
    try:
        username, expiry, signature = token.rsplit("|", 2)
        payload = f"{username}|{expiry}"
        if int(expiry) <= int(time.time()) or not hmac.compare_digest(signature, _signature(payload)):
            return None
        return username if username in load_users() else None
    except (ValueError, TypeError):
        return None


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def verify_password(username: str, password: str) -> bool:
    record = load_users().get(username)
    if not record:
        # Same work as for a real user, so response time does not reveal which names exist.
        dummy = _scrypt(password, b"furniture-assistant-dummy-salt")
        return hmac.compare_digest(dummy, dummy[::-1]) and False
    try:
        salt = base64.urlsafe_b64decode(record["salt"])
        expected = base64.urlsafe_b64decode(record["password_hash"])
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(_scrypt(password, salt), expected)


# Failed logins per (ip, username): after MAX_FAILURES within WINDOW the key is locked for LOCK_SECONDS.
_MAX_FAILURES = 5
_WINDOW_SECONDS = 15 * 60
_LOCK_SECONDS = 5 * 60
_LOGIN_FAILURES: dict[tuple[str, str], list[float]] = {}
_LOGIN_LOCK = threading.Lock()


def login_retry_after(ip: str, username: str) -> int:
    """Seconds until the next login attempt is accepted, 0 when it is accepted now."""
    now = time.time()
    with _LOGIN_LOCK:
        failures = [t for t in _LOGIN_FAILURES.get((ip, username), []) if now - t < _WINDOW_SECONDS]
        if len(failures) >= _MAX_FAILURES:
            return max(1, int(failures[-1] + _LOCK_SECONDS - now))
    return 0


def record_login_result(ip: str, username: str, ok: bool) -> None:
    with _LOGIN_LOCK:
        if ok:
            _LOGIN_FAILURES.pop((ip, username), None)
            return
        now = time.time()
        failures = [t for t in _LOGIN_FAILURES.get((ip, username), []) if now - t < _WINDOW_SECONDS]
        failures.append(now)
        _LOGIN_FAILURES[(ip, username)] = failures


PUBLIC_API_PATHS = frozenset({"/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/health"})


async def auth_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    enabled = auth_enabled()
    username = session_username(request.cookies.get(COOKIE_NAME)) if enabled else None
    request.state.username = username
    if enabled and request.url.path not in PUBLIC_API_PATHS and username is None:
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    if enabled and username and request.url.path not in PUBLIC_API_PATHS:
        role = user_role(username)
        if role not in ROLES:
            return JSONResponse(status_code=403, content={"detail": "User role is invalid"})
        request.state.role = role
        if role == "viewer" and request.method not in {"GET", "HEAD"}:
            return JSONResponse(status_code=403, content={"detail": "Viewer role is read-only"})
        if role == "editor" and request.method == "PUT" and request.url.path == "/api/pricing":
            return JSONResponse(status_code=403, content={"detail": "Editor role cannot change workspace pricing"})
    else:
        request.state.role = "admin" if not enabled else None
    return await call_next(request)


def user_role(username: str | None) -> Role | None:
    if username is None:
        return None
    return cast(Role, load_users().get(username, {}).get("role", "admin"))


ROLES = frozenset({"viewer", "editor", "admin"})


def _write_users(target: Path, data: dict[str, Any]) -> None:
    """Replace users.yaml atomically so a crash never leaves it half-written."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def add_user(username: str, password: str, path: Path | None = None, role: str = "admin") -> None:
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    target = path or users_path()
    data: Any = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else {}
    users = data.setdefault("users", {})
    if username in users:
        raise ValueError(f"User already exists: {username}")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    users[username] = {
        "salt": base64.urlsafe_b64encode(salt).decode(),
        "password_hash": base64.urlsafe_b64encode(digest).decode(),
        "role": role,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_users(target, data)


def set_user_role(username: str, role: str, path: Path | None = None) -> None:
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    target = path or users_path()
    data: Any = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else {}
    users = data.setdefault("users", {})
    if username not in users:
        raise ValueError(f"Unknown user: {username}")
    users[username]["role"] = role
    _write_users(target, data)


def remove_user(username: str, path: Path | None = None) -> None:
    target = path or users_path()
    data: Any = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else {}
    users = data.setdefault("users", {})
    if username not in users:
        raise ValueError(f"Unknown user: {username}")
    del users[username]
    _write_users(target, data)
