import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, timedelta

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select

from ledgerguard.config import settings
from ledgerguard.db import DomainError, owned
from ledgerguard.models import AuditLog, RefreshSession, User, now

passwords = PasswordHash.recommended()
READ_ROLES = {"admin", "analyst", "risk_analyst", "approver", "auditor"}
WRITE_ROLES = {"admin", "analyst", "risk_analyst"}


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str

    def require(self, roles):
        if self.role not in roles:
            raise DomainError("Insufficient permissions", 403)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def jwt_encode(claims, audience="ledgerguard-api", minutes=None, key=None):
    stamp = now()
    return jwt.encode(
        {
            **claims,
            "iat": stamp,
            "exp": stamp + timedelta(minutes=minutes or settings().jwt_access_token_minutes),
            "aud": audience,
            "iss": "ledgerguard",
            "jti": secrets.token_hex(16),
        },
        key or settings().jwt_secret_key,
        algorithm="HS256",
    )


def decode(token, audience="ledgerguard-api", key=None):
    try:
        return jwt.decode(
            token,
            key or settings().jwt_secret_key,
            algorithms=["HS256"],
            audience=audience,
            issuer="ledgerguard",
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise DomainError("Invalid or expired credentials", 401) from exc


async def authenticate(db, token):
    claims = decode(token)
    user = await db.get(User, claims["sub"])
    login = await db.get(RefreshSession, claims.get("sid", ""))
    if not user or not user.active or not login or login.revoked or login.user_id != user.id:
        raise DomainError("Session revoked", 401)
    if aware(login.expires_at) <= now():
        raise DomainError("Session expired", 401)
    return Principal(user.id, user.tenant_id, user.role)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def issue_tokens(db, user):
    raw = secrets.token_urlsafe(48)
    login = RefreshSession(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=digest(raw),
        expires_at=now() + timedelta(days=settings().jwt_refresh_token_days),
    )
    db.add(login)
    await db.flush()
    access = jwt_encode({"sub": user.id, "sid": login.id})
    return {"access_token": access, "refresh_token": raw, "token_type": "bearer"}


async def rotate(db, raw):
    login = await db.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == digest(raw)).with_for_update()
    )
    if not login or login.revoked or aware(login.expires_at) <= now():
        raise DomainError("Invalid refresh session", 401)
    # Compare-and-swap also protects portable SQLite against concurrent replay.
    from sqlalchemy import update

    result = await db.execute(
        update(RefreshSession)
        .where(RefreshSession.id == login.id, RefreshSession.revoked.is_(False))
        .values(revoked=True)
    )
    if result.rowcount != 1:
        raise DomainError("Refresh token already used", 401)
    user = await owned(db, User, login.user_id, login.tenant_id)
    if not user.active:
        raise DomainError("Account disabled", 401)
    return await issue_tokens(db, user)


def audit(db, principal, action, entity_id, details=None, outcome="SUCCESS", correlation_id=None):
    data = dict(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action=action,
        entity_id=entity_id,
        details=details or {},
        outcome=outcome,
    )
    if correlation_id:
        data["correlation_id"] = correlation_id
    db.add(AuditLog(**data))


def delegation(principal, server, run_id):
    return jwt_encode(
        {"sub": principal.user_id, "tenant": principal.tenant_id, "role": principal.role, "run": run_id},
        f"mcp:{server}",
        5,
        settings().mcp_context_secret,
    )
