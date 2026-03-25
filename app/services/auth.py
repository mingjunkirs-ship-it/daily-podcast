from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminUser

SESSION_COOKIE_NAME = "podcast_session"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padding = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def _derive_password_key(secret: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return _b64encode(digest)


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    derived = _derive_password_key(plain, salt)
    return f"pbkdf2_sha256${salt}${derived}"


def verify_password(plain: str, encoded: str) -> bool:
    try:
        algo, salt, digest = encoded.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    expected = _derive_password_key(plain, salt)
    return hmac.compare_digest(expected, digest)


def _session_secret() -> str:
    return os.getenv("AUTH_SECRET", "change-this-auth-secret")


def _session_ttl_hours() -> int:
    try:
        value = int(os.getenv("AUTH_SESSION_TTL_HOURS", "48"))
        return max(1, min(value, 24 * 30))
    except Exception:
        return 48


def session_ttl_seconds() -> int:
    return _session_ttl_hours() * 3600


def auth_cookie_secure() -> bool:
    raw = os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def create_session_token(username: str) -> str:
    exp = int((_utcnow() + timedelta(hours=_session_ttl_hours())).timestamp())
    payload = {"u": username, "exp": exp}
    payload_raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = _b64encode(payload_raw)

    sig = hmac.new(_session_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def parse_session_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    payload_b64, provided_sig = token.split(".", 1)
    expected_sig = hmac.new(
        _session_secret().encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None

    try:
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    exp = int(payload.get("exp", 0))
    if exp <= int(_utcnow().timestamp()):
        return None
    username = str(payload.get("u", "")).strip()
    if not username:
        return None
    return payload


def ensure_default_admin(db: Session) -> None:
    admin_username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    admin_password = os.getenv("ADMIN_PASSWORD", "adminadmin")

    existing = db.scalar(select(AdminUser).where(AdminUser.username == admin_username))
    if existing:
        return

    row = AdminUser(username=admin_username, password_hash=hash_password(admin_password))
    db.add(row)
    db.commit()


def authenticate_admin(db: Session, username: str, password: str) -> AdminUser | None:
    row = db.scalar(select(AdminUser).where(AdminUser.username == username.strip()))
    if not row:
        return None
    if not verify_password(password, row.password_hash):
        return None
    return row


def get_admin_by_username(db: Session, username: str) -> AdminUser | None:
    return db.scalar(select(AdminUser).where(AdminUser.username == username.strip()))


def update_admin_password(db: Session, username: str, new_password: str) -> bool:
    row = get_admin_by_username(db, username)
    if not row:
        return False
    row.password_hash = hash_password(new_password)
    db.commit()
    return True
