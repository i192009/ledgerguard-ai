from contextlib import asynccontextmanager

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ledgerguard.config import settings

engine = create_async_engine(settings().database_url, pool_pre_ping=True)
if settings().database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_options(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


Session = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session():
    async with Session() as db:
        async with db.begin():
            yield db


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


async def owned(db, model, entity_id, tenant_id, lock=False):
    query = select(model).where(model.id == entity_id, model.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update()
    value = await db.scalar(query)
    if value is None:
        raise DomainError("Record not found", 404)
    return value


def public(entity):
    from datetime import datetime
    from decimal import Decimal

    result = {}
    for column in entity.__table__.columns:
        if column.name in {"password_hash", "token_hash"}:
            continue
        value = getattr(entity, column.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        result[column.name] = value
    return result
