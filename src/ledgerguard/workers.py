import asyncio

from celery import Celery
from sqlalchemy import select

from ledgerguard.config import settings
from ledgerguard.db import engine, session
from ledgerguard.models import AgentRun, BackgroundJob, User
from ledgerguard.security import Principal

celery = Celery("ledgerguard", broker=settings().redis_url, backend=settings().redis_url)
celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=540,
    task_time_limit=600,
    broker_connection_retry_on_startup=True,
    worker_concurrency=2,
    task_track_started=True,
    result_expires=86400,
    beat_schedule={"recover-queued-runs": {"task": "ledgerguard.recover", "schedule": 60.0}},
)


async def isolated(coroutine):
    try:
        return await coroutine
    finally:
        # Celery creates a loop for each task; discard loop-bound asyncpg pooled connections.
        await engine.dispose()


@celery.task(
    name="ledgerguard.run",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def run_task(run_id, resume=False):
    from ledgerguard.orchestrator import process_run

    return asyncio.run(isolated(process_run(run_id, resume)))


async def process_job(job_id):
    from ledgerguard.reconciliation import advisory_lock

    async with session() as lock_db:
        await advisory_lock(lock_db, f"job:{job_id}")
        return await _process_job(job_id)


async def _process_job(job_id):
    async with session() as db:
        job = await db.get(BackgroundJob, job_id, with_for_update=True)
        if not job or job.status == "COMPLETED":
            return
        job.status = "RUNNING"
        payload, kind = job.payload, job.kind
        user = await db.get(User, payload["actor_id"])
        if not user or not user.active or user.tenant_id != job.tenant_id:
            raise ValueError("Background job owner is inactive or outside tenant")
        p = Principal(user.id, user.tenant_id, user.role)
    try:
        async with session() as db:
            if kind == "risk":
                from ledgerguard.risk import analyse
                from ledgerguard.schemas import TransactionFilters

                result = await analyse(db, p, TransactionFilters(**payload.get("filters", {})))
            elif kind == "reconciliation":
                from ledgerguard.reconciliation import reconcile
                from ledgerguard.schemas import TransactionFilters

                result = await reconcile(
                    db,
                    p,
                    payload["statement_id"],
                    payload["account_id"],
                    TransactionFilters(**payload.get("filters", {})),
                )
            elif kind == "import":
                from ledgerguard.imports import import_rows

                result = await import_rows(
                    db, p, payload["rows"], payload.get("kind", "transactions"), payload.get("statement_id")
                )
            elif kind == "training":
                p.require({"admin"})
                from ledgerguard.risk import train_model

                result = await train_model(db, p.tenant_id)
            elif kind == "report":
                from ledgerguard.db import owned
                from ledgerguard.reports import generate_report

                run = await owned(db, AgentRun, payload["run_id"], p.tenant_id)
                result = await generate_report(db, p, run.id, run.state)
            else:
                raise ValueError("Unsupported background job kind")
            job = await db.get(BackgroundJob, job_id)
            job.status, job.result = "COMPLETED", result
    except Exception as exc:
        async with session() as db:
            job = await db.get(BackgroundJob, job_id)
            job.status, job.error = "FAILED", str(exc)[:1000]
        raise


@celery.task(
    name="ledgerguard.job", autoretry_for=(ConnectionError, TimeoutError), retry_backoff=True, max_retries=2
)
def job_task(job_id):
    return asyncio.run(isolated(process_job(job_id)))


@celery.task(name="ledgerguard.recover")
def recover():
    async def queued():
        from sqlalchemy import func

        from ledgerguard.models import ApprovalRequest, now
        from ledgerguard.security import audit

        async with session() as db:
            expired = (
                await db.scalars(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.status == "PENDING", ApprovalRequest.expires_at <= now())
                    .with_for_update()
                )
            ).all()
            for approval in expired:
                approval.status = "EXPIRED"
                audit(
                    db, Principal("scheduler", approval.tenant_id, "admin"), "APPROVAL_EXPIRED", approval.id
                )
            runs = (await db.scalars(select(AgentRun).where(AgentRun.status == "QUEUED"))).all()
            jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.status == "QUEUED"))).all()
            waiters = (await db.scalars(select(AgentRun).where(AgentRun.status == "WAITING_APPROVAL"))).all()
            resumable = []
            for run in waiters:
                pending = await db.scalar(
                    select(func.count())
                    .select_from(ApprovalRequest)
                    .where(
                        ApprovalRequest.run_id == run.id,
                        ApprovalRequest.tenant_id == run.tenant_id,
                        ApprovalRequest.status == "PENDING",
                    )
                )
                if not pending:
                    resumable.append(run.id)
        for run in runs:
            run_task.delay(run.id)
        for job in jobs:
            job_task.delay(job.id)
        for run_id in resumable:
            run_task.delay(run_id, True)

    asyncio.run(isolated(queued()))
