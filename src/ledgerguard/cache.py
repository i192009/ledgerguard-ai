import json

from sqlalchemy import func, select

from ledgerguard.config import settings
from ledgerguard.db import public, session
from ledgerguard.models import (
    AgentRun,
    ApprovalRequest,
    AuditLog,
    ReconciliationJob,
    RiskAlert,
    RiskAssessment,
)
from ledgerguard.schemas import TransactionFilters
from ledgerguard.transactions import summary


async def dashboard_data(p):
    async with session() as db:
        totals = await summary(db, p, TransactionFilters())

        async def count(model, condition):
            return await db.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == p.tenant_id, condition)
            )

        totals["high_risk_alerts"] = await count(RiskAlert, RiskAlert.severity == "HIGH")
        totals["pending_approvals"] = await count(ApprovalRequest, ApprovalRequest.status == "PENDING")
        totals["active_runs"] = await count(
            AgentRun, AgentRun.status.in_(["QUEUED", "RUNNING", "WAITING_APPROVAL"])
        )
        jobs = (
            await db.scalars(select(ReconciliationJob).where(ReconciliationJob.tenant_id == p.tenant_id))
        ).all()
        latest = {}
        for job in sorted(jobs, key=lambda j: j.created_at):
            latest[job.account_id] = job
        matched = sum(j.summary.get("matched_ledger_records", 0) for j in latest.values())
        total = sum(j.summary.get("ledger_records", 0) for j in latest.values())
        totals["reconciliation_rate"] = round(100 * matched / total, 2) if total else 0
        totals["risk_distribution"] = {
            category: await count(RiskAssessment, RiskAssessment.category == category)
            for category in ("LOW", "MEDIUM", "HIGH")
        }
        totals["recent_activity"] = [
            public(a)
            for a in (
                await db.scalars(
                    select(AuditLog)
                    .where(AuditLog.tenant_id == p.tenant_id)
                    .order_by(AuditLog.created_at.desc())
                    .limit(8)
                )
            ).all()
        ]
        return totals


async def cached_dashboard(p):
    try:
        from redis.asyncio import Redis

        async with Redis.from_url(
            settings().redis_url, socket_connect_timeout=0.3, socket_timeout=0.3
        ) as redis:
            key = f"dashboard:{p.tenant_id}"
            cached = await redis.get(key)
            if cached:
                return json.loads(cached)
            data = await dashboard_data(p)
            await redis.setex(key, 10, json.dumps(data))
            return data
    except (ConnectionError, OSError, TimeoutError):
        return await dashboard_data(p)
