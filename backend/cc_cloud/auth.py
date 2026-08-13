"""Auth primitives and FastAPI dependencies.

- Passwords: ``hashlib.scrypt`` (stdlib) — ``scrypt$<salt-hex>$<hash-hex>``.
- UI sessions: short-lived HMAC-signed tokens (mini-JWT, stdlib only).
- CLI: long-lived ``cc_…`` API tokens stored as sha256 hashes.

Every read/ingest endpoint resolves the current user, then scopes through team
membership (see ``require_project``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import ApiToken, Project, TeamMember, User

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


def new_api_token() -> str:
    return "cc_" + uuid.uuid4().hex + uuid.uuid4().hex


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Signed session tokens (mini-JWT)
# ---------------------------------------------------------------------------


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(payload: dict, secret: str, ttl_seconds: int) -> str:
    body = _b64e(json.dumps({**payload, "exp": int(time.time()) + ttl_seconds}).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str, secret: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=401, detail="missing credentials")
    token = cred.credentials
    settings = get_settings()

    user: User | None = None
    if token.startswith("cc_"):
        row = (
            db.query(ApiToken)
            .filter(ApiToken.token_hash == hash_api_token(token), ApiToken.revoked_at.is_(None))
            .first()
        )
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
            db.commit()
            user = row.user
    else:
        payload = verify_token(token, settings.jwt_secret)
        if payload is not None:
            try:
                user = db.get(User, uuid.UUID(payload["sub"]))
            except (ValueError, KeyError):
                user = None

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return user


def team_role(db: Session, team_id, user_id: uuid.UUID) -> str | None:
    member = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        .first()
    )
    return member.role if member else None


def require_project(db: Session, slug: str, user: User, min_role: str = "member") -> Project:
    """Resolve a project by slug and enforce team membership."""
    project = db.query(Project).filter(Project.slug == slug).first()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    role = team_role(db, project.team_id, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="not a member of this project's team")
    if ROLE_RANK.get(role, 0) < ROLE_RANK.get(min_role, 1):
        raise HTTPException(status_code=403, detail=f"{min_role} role required")
    return project
