import asyncio
import csv
import io
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4

import structlog
from fastapi import Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from ledgerguard import approvals, transactions
from ledgerguard.config import settings
from ledgerguard.db import DomainError, owned, public, session
from ledgerguard.imports import MAX_BYTES, parse_csv
from ledgerguard.models import (
    Account,
    AgentEvent,
    AgentRun,
    ApprovalPolicy,
    ApprovalRequest,
    AuditLog,
    BackgroundJob,
    BankStatement,
    ProposedAction,
    ReconciliationException,
    ReconciliationJob,
    RefreshSession,
    Report,
    RiskAlert,
    RiskAssessment,
    RiskRule,
    Role,
    Tenant,
    ToolExecution,
    User,
)
from ledgerguard.observability import HTTP_TIME, configure_logging
from ledgerguard.schemas import (
    ApprovalPolicyInput,
    Credentials,
    DateRange,
    DecisionInput,
    ReconcileInput,
    Registration,
    RiskPolicyInput,
    RunRequest,
    TokenRefresh,
    TransactionFilters,
)
from ledgerguard.security import (
    WRITE_ROLES,
    Principal,
    audit,
    authenticate,
    decode,
    digest,
    issue_tokens,
    passwords,
    rotate,
)

tasks: set[asyncio.Task] = set()
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    yield
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="LedgerGuard AI · Sandbox Financial Operations", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origins.split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
)
bearer = HTTPBearer(auto_error=False)
prefix = "/api/v1"
_rate_buckets: dict[str, tuple[int, float]] = {}


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {"error": {"message": exc.message, "request_id": getattr(request.state, "request_id", None)}},
        status_code=exc.status,
    )


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {"error": {"message": "Record conflict; reload and retry", "request_id": request.state.request_id}},
        status_code=409,
    )


@app.middleware("http")
async def request_context(request, call_next):
    started = time.monotonic()
    request.state.request_id = str(uuid4())
    if request.url.path.startswith(prefix):
        window = int(time.time() // 60)
        is_auth = request.method == "POST" and request.url.path in {
            prefix + "/auth/login",
            prefix + "/auth/register",
            prefix + "/auth/refresh",
        }
        bucket = "auth" if is_auth else "api"
        key = f"ratelimit:{bucket}:{digest(request.client.host if request.client else 'unknown')}:{window}"
        limit = 30 if is_auth else 600
        try:
            from redis.asyncio import Redis

            async with Redis.from_url(
                settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
            ) as redis:
                count = await redis.incr(key)
                if count == 1:
                    await redis.expire(key, 61)
        except Exception:
            if settings().app_env == "production":
                from fastapi.responses import JSONResponse

                return JSONResponse({"error": {"message": "Rate limit service unavailable"}}, status_code=503)
            count, _ = _rate_buckets.get(key, (0, time.time()))
            count += 1
            _rate_buckets[key] = (count, time.time())
            for stale in [k for k, (_, stamp) in _rate_buckets.items() if stamp < time.time() - 120]:
                _rate_buckets.pop(stale, None)
        if count > limit:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"error": {"message": "Rate limit exceeded"}}, status_code=429, headers={"Retry-After": "60"}
            )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    HTTP_TIME.labels(request.method, response.status_code).observe(time.monotonic() - started)
    logger.info(
        "http_request",
        request_id=request.state.request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return response


async def principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise DomainError("Authentication required", 401)
    async with session() as db:
        return await authenticate(db, credentials.credentials)


def schedule(coroutine):
    task = asyncio.create_task(coroutine)
    tasks.add(task)

    def done(result):
        tasks.discard(result)
        if not result.cancelled() and result.exception():
            logger.error("background_failure", error=str(result.exception())[:500])

    task.add_done_callback(done)


def dispatch_run(run_id, resume=False):
    if settings().task_mode == "celery":
        from ledgerguard.workers import run_task

        run_task.delay(run_id, resume)
    else:
        from ledgerguard.orchestrator import process_run

        schedule(process_run(run_id, resume))


async def queue_job(p, kind, payload):
    p.require(WRITE_ROLES)
    async with session() as db:
        job = BackgroundJob(tenant_id=p.tenant_id, kind=kind, payload={**payload, "actor_id": p.user_id})
        db.add(job)
        await db.flush()
        result = public(job)
    if settings().task_mode == "celery":
        from ledgerguard.workers import job_task

        job_task.delay(job.id)
    else:
        from ledgerguard.workers import process_job

        schedule(process_job(job.id))
    return result


async def listing(db, p, model, page=1, page_size=50, conditions=()):
    q = select(model).where(model.tenant_id == p.tenant_id, *conditions)
    count = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await db.scalars(
            q.order_by(model.created_at.desc(), model.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {"items": [public(r) for r in rows], "total": count, "page": page, "page_size": page_size}


@app.get("/health/live")
async def live():
    return {"status": "live", "sandbox": True}


@app.get("/health/ready")
async def ready():
    async with session() as db:
        await db.execute(text("SELECT 1"))
    if settings().task_mode == "celery":
        from redis.asyncio import Redis

        async with Redis.from_url(settings().redis_url) as redis:
            await redis.execute_command("PING")
    return {"status": "ready"}


@app.get("/metrics")
async def metrics(credentials=Depends(bearer)):
    import hmac

    from ledgerguard.observability import PERSISTED_TOOLS

    if not credentials:
        raise DomainError("Monitoring authentication required", 401)
    scope = None
    if not hmac.compare_digest(credentials.credentials, settings().metrics_token):
        async with session() as db:
            p = await authenticate(db, credentials.credentials)
            p.require({"admin", "auditor"})
            scope = p.tenant_id
    async with session() as db:
        query = select(ToolExecution.server, ToolExecution.tool, ToolExecution.status, func.count()).group_by(
            ToolExecution.server, ToolExecution.tool, ToolExecution.status
        )
        if scope:
            query = query.where(ToolExecution.tenant_id == scope)
        PERSISTED_TOOLS.clear()
        for server, tool, status, count in (await db.execute(query)).all():
            PERSISTED_TOOLS.labels(server, tool, status).set(count)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(prefix + "/auth/register", status_code=201)
async def register(body: Registration):
    async with session() as db:
        tenant = Tenant(name=body.tenant_name)
        db.add(tenant)
        await db.flush()
        # Public registration always creates a separate tenant with an analyst role.
        user = User(
            tenant_id=tenant.id,
            name=body.name,
            email=body.email.lower(),
            password_hash=passwords.hash(body.password),
            role="analyst",
        )
        db.add(user)
        await db.flush()
        from ledgerguard.models import Membership

        role = await db.scalar(select(Role).where(Role.name == "analyst"))
        if not role:
            role = Role(name="analyst")
            db.add(role)
            await db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role_id=role.id))
        return await issue_tokens(db, user)


@app.post(prefix + "/auth/login")
async def login(body: Credentials):
    async with session() as db:
        user = await db.scalar(select(User).where(User.email == body.email.lower()))
        if not user or not user.active or not passwords.verify(body.password, user.password_hash):
            raise DomainError("Invalid email or password", 401)
        result = await issue_tokens(db, user)
        audit(db, Principal(user.id, user.tenant_id, user.role), "LOGIN", user.id)
        return result


@app.post(prefix + "/auth/refresh")
async def refresh(body: TokenRefresh):
    async with session() as db:
        return await rotate(db, body.refresh_token)


@app.post(prefix + "/auth/logout")
async def logout(credentials=Depends(bearer), p=Depends(principal)):
    claims = decode(credentials.credentials)
    async with session() as db:
        login = await owned(db, RefreshSession, claims["sid"], p.tenant_id, lock=True)
        login.revoked = True
        audit(db, p, "LOGOUT", p.user_id)
    return {"status": "revoked"}


@app.get(prefix + "/auth/me")
async def me(p=Depends(principal)):
    async with session() as db:
        user = await owned(db, User, p.user_id, p.tenant_id)
        tenant = await db.get(Tenant, p.tenant_id)
        return {**public(user), "tenant_name": tenant.name}


@app.get(prefix + "/accounts")
async def accounts(p=Depends(principal)):
    async with session() as db:
        return await listing(db, p, Account)


@app.get(prefix + "/accounts/{account_id}")
async def account(account_id: str, p=Depends(principal)):
    async with session() as db:
        return public(await owned(db, Account, account_id, p.tenant_id))


@app.get(prefix + "/accounts/{account_id}/summary")
async def account_summary(account_id: str, p=Depends(principal)):
    async with session() as db:
        return await transactions.account_summary(db, p, account_id)


@app.get(prefix + "/transactions")
async def transaction_list(filters: TransactionFilters = Depends(), p=Depends(principal)):
    async with session() as db:
        return await transactions.get_transactions(db, p, filters)


@app.get(prefix + "/transactions/analytics/summary")
async def transaction_summary(filters: TransactionFilters = Depends(), p=Depends(principal)):
    async with session() as db:
        return await transactions.summary(db, p, filters)


@app.get(prefix + "/transactions/export")
async def transaction_export(filters: TransactionFilters = Depends(), p=Depends(principal)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["source_id", "account_id", "amount", "currency", "reference", "booked_at", "description"]
    )
    async with session() as db:
        rows = (
            await db.scalars(
                transactions.filtered(p.tenant_id, filters).order_by(transactions.Transaction.id).limit(20000)
            )
        ).all()
        for row in rows:
            # Escape spreadsheet formulas in user-supplied textual cells.
            def safe(s):
                return "'" + s if s.startswith(("=", "+", "-", "@")) else s

            writer.writerow(
                [
                    row.source_id,
                    row.account_id,
                    str(row.amount),
                    row.currency,
                    safe(row.reference),
                    row.booked_at.isoformat(),
                    safe(row.description),
                ]
            )
        audit(db, p, "TRANSACTIONS_EXPORTED", "transactions", {"rows": len(rows)})
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@app.post(prefix + "/transactions/import", status_code=202)
async def transaction_import(file: UploadFile = File(), p=Depends(principal)):
    content = await file.read(MAX_BYTES + 1)
    rows = parse_csv(content)
    return await queue_job(p, "import", {"rows": [r.model_dump(mode="json") for r in rows]})


@app.get(prefix + "/transactions/{transaction_id}")
async def transaction_detail(transaction_id: str, p=Depends(principal)):
    async with session() as db:
        return await transactions.get_details(db, p, transaction_id)


@app.post(prefix + "/risk/analyse", status_code=202)
async def risk_analysis(body: DateRange, p=Depends(principal)):
    return await queue_job(p, "risk", {"filters": body.model_dump(mode="json")})


@app.get(prefix + "/risk/assessments")
async def assessments(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category: str | None = None,
    p=Depends(principal),
):
    async with session() as db:
        return await listing(
            db, p, RiskAssessment, page, page_size, [RiskAssessment.category == category] if category else []
        )


@app.get(prefix + "/risk/assessments/{assessment_id}")
async def assessment(assessment_id: str, p=Depends(principal)):
    async with session() as db:
        return public(await owned(db, RiskAssessment, assessment_id, p.tenant_id))


@app.get(prefix + "/risk/analytics/summary")
async def risk_analytics(account_id: str | None = None, p=Depends(principal)):
    from collections import Counter, defaultdict

    transaction = transactions.Transaction
    async with session() as db:
        query = (
            select(RiskAssessment, transaction.booked_at)
            .join(transaction, transaction.id == RiskAssessment.transaction_id)
            .where(RiskAssessment.tenant_id == p.tenant_id, transaction.tenant_id == p.tenant_id)
        )
        if account_id:
            query = query.where(transaction.account_id == account_id)
        rows = (await db.execute(query.order_by(RiskAssessment.created_at.desc(), RiskAssessment.id))).all()
        latest: dict[str, tuple[RiskAssessment, datetime]] = {}
        for assessment, booked in rows:
            latest.setdefault(assessment.transaction_id, (assessment, booked))
        counts: Counter[str] = Counter()
        monthly: dict[str, Counter[str]] = defaultdict(Counter)
        for assessment, booked in latest.values():
            counts[assessment.category] += 1
            monthly[booked.strftime("%Y-%m")][assessment.category] += 1
        return {
            "counts": dict(counts),
            "assessed_transactions": len(latest),
            "monthly": [
                {"month": month, **dict(categories)} for month, categories in sorted(monthly.items())
            ],
            "method": "Latest recorded assessment per transaction; grouped by source booking month",
        }


@app.get(prefix + "/risk/alerts")
async def alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    severity: str | None = None,
    status: str | None = None,
    p=Depends(principal),
):
    conditions = []
    if severity:
        conditions.append(RiskAlert.severity == severity)
    if status:
        conditions.append(RiskAlert.status == status)
    async with session() as db:
        return await listing(db, p, RiskAlert, page, page_size, conditions)


@app.get(prefix + "/risk/alerts/{alert_id}")
async def alert(alert_id: str, p=Depends(principal)):
    async with session() as db:
        return public(await owned(db, RiskAlert, alert_id, p.tenant_id))


@app.get(prefix + "/bank-statements")
async def statements(p=Depends(principal)):
    async with session() as db:
        return await listing(db, p, BankStatement)


@app.post(prefix + "/bank-statements/{statement_id}/import", status_code=202)
async def bank_import(statement_id: str, file: UploadFile = File(), p=Depends(principal)):
    rows = parse_csv(await file.read(MAX_BYTES + 1))
    return await queue_job(
        p,
        "import",
        {"rows": [r.model_dump(mode="json") for r in rows], "kind": "bank", "statement_id": statement_id},
    )


@app.post(prefix + "/reconciliation/jobs", status_code=202)
async def start_reconcile(body: ReconcileInput, p=Depends(principal)):
    return await queue_job(
        p,
        "reconciliation",
        {
            "statement_id": body.bank_statement_id,
            "account_id": body.account_id,
            "filters": body.model_dump(mode="json", exclude={"bank_statement_id", "account_id"}),
        },
    )


@app.get(prefix + "/reconciliation/jobs")
async def reconciliation_jobs(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), p=Depends(principal)
):
    async with session() as db:
        return await listing(db, p, ReconciliationJob, page, page_size)


@app.get(prefix + "/reconciliation/jobs/{job_id}")
async def reconciliation_job(job_id: str, p=Depends(principal)):
    async with session() as db:
        return public(await owned(db, ReconciliationJob, job_id, p.tenant_id))


@app.get(prefix + "/reconciliation/jobs/{job_id}/exceptions")
async def reconciliation_exceptions(job_id: str, page: int = Query(1, ge=1), p=Depends(principal)):
    async with session() as db:
        await owned(db, ReconciliationJob, job_id, p.tenant_id)
        return await listing(
            db, p, ReconciliationException, page, 100, [ReconciliationException.job_id == job_id]
        )


@app.get(prefix + "/reconciliation/jobs/{job_id}/proposals")
async def reconciliation_proposals(job_id: str, p=Depends(principal)):
    async with session() as db:
        await owned(db, ReconciliationJob, job_id, p.tenant_id)
        return await listing(db, p, ProposedAction, conditions=[ProposedAction.job_id == job_id])


@app.get(prefix + "/approvals")
async def approval_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = None,
    p=Depends(principal),
):
    async with session() as db:
        return await listing(
            db, p, ApprovalRequest, page, page_size, [ApprovalRequest.status == status] if status else []
        )


@app.get(prefix + "/approvals/{approval_id}")
async def approval_detail(approval_id: str, p=Depends(principal)):
    async with session() as db:
        return await approvals.details(db, p, approval_id)


async def decision_route(approval_id, body, p, decision):
    async with session() as db:
        result = await approvals.decide(db, p, approval_id, decision, body.reason, body.version)
        run_id = result["run_id"]
        resume = False
        if run_id and result["status"] in {"APPROVED", "REJECTED"}:
            pending = await db.scalar(
                select(func.count())
                .select_from(ApprovalRequest)
                .where(
                    ApprovalRequest.run_id == run_id,
                    ApprovalRequest.tenant_id == p.tenant_id,
                    ApprovalRequest.status == "PENDING",
                )
            )
            run = await owned(db, AgentRun, run_id, p.tenant_id)
            resume = not pending and run.status == "WAITING_APPROVAL"
    if resume:
        dispatch_run(run_id, True)
    return result


@app.post(prefix + "/approvals/{approval_id}/approve")
async def approve(approval_id: str, body: DecisionInput, p=Depends(principal)):
    return await decision_route(approval_id, body, p, "APPROVE")


@app.post(prefix + "/approvals/{approval_id}/reject")
async def reject(approval_id: str, body: DecisionInput, p=Depends(principal)):
    return await decision_route(approval_id, body, p, "REJECT")


@app.post(prefix + "/approvals/{approval_id}/request-investigation")
async def investigate(approval_id: str, body: DecisionInput, p=Depends(principal)):
    return await decision_route(approval_id, body, p, "INVESTIGATE")


@app.post(prefix + "/agents/run", status_code=202)
async def start_run(body: RunRequest, p=Depends(principal)):
    p.require(WRITE_ROLES)
    async with session() as db:
        run = AgentRun(tenant_id=p.tenant_id, maker_id=p.user_id, request=body.request)
        db.add(run)
        await db.flush()
        result = public(run)
        audit(db, p, "AGENT_RUN_REQUESTED", run.id)
    dispatch_run(run.id)
    return result


@app.get(prefix + "/agents/runs")
async def run_list(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), p=Depends(principal)
):
    async with session() as db:
        return await listing(db, p, AgentRun, page, page_size)


@app.get(prefix + "/agents/runs/{run_id}")
async def run_detail(run_id: str, p=Depends(principal)):
    async with session() as db:
        return public(await owned(db, AgentRun, run_id, p.tenant_id))


@app.post(prefix + "/agents/runs/{run_id}/cancel")
async def cancel_run(run_id: str, p=Depends(principal)):
    async with session() as db:
        run = await owned(db, AgentRun, run_id, p.tenant_id, lock=True)
        if run.maker_id != p.user_id and p.role != "admin":
            raise DomainError("Only the maker or admin may cancel", 403)
        if run.status in {"COMPLETED", "CANCELLED"}:
            raise DomainError("Run is already terminal", 409)
        run.status = "CANCELLED"
        for approval in (
            await db.scalars(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.run_id == run.id,
                    ApprovalRequest.status == "PENDING",
                    ApprovalRequest.tenant_id == p.tenant_id,
                )
                .with_for_update()
            )
        ).all():
            approval.status = "CANCELLED"
        audit(db, p, "RUN_CANCELLED", run.id)
    return {"status": "CANCELLED"}


@app.post(prefix + "/agents/runs/{run_id}/retry")
async def retry_run(run_id: str, p=Depends(principal)):
    async with session() as db:
        run = await owned(db, AgentRun, run_id, p.tenant_id)
        if run.maker_id != p.user_id or run.status != "FAILED":
            raise DomainError("Only the maker may retry a failed run", 403)
        run.status = "QUEUED"
        audit(db, p, "RUN_RETRY_REQUESTED", run.id)
    dispatch_run(run_id)
    return {"status": "QUEUED"}


@app.get(prefix + "/agents/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: str | None = None, p=Depends(principal)):
    async with session() as db:
        await owned(db, AgentRun, run_id, p.tenant_id)
        history = (
            await db.scalars(
                select(AgentEvent)
                .where(AgentEvent.run_id == run_id, AgentEvent.tenant_id == p.tenant_id)
                .order_by(AgentEvent.created_at, AgentEvent.id)
            )
        ).all()
    cursor = after or request.headers.get("last-event-id")
    seen = set()
    if cursor:
        for item in history:
            seen.add(item.id)
            if item.id == cursor:
                break
        if cursor not in seen:
            seen.clear()

    async def stream():
        for _ in range(900):
            if await request.is_disconnected():
                break
            async with session() as db:
                run = await owned(db, AgentRun, run_id, p.tenant_id)
                records = (
                    await db.scalars(
                        select(AgentEvent)
                        .where(AgentEvent.run_id == run_id, AgentEvent.tenant_id == p.tenant_id)
                        .order_by(AgentEvent.created_at, AgentEvent.id)
                    )
                ).all()
            for item in records:
                if item.id not in seen:
                    seen.add(item.id)
                    yield f"id: {item.id}\nevent: {item.event}\ndata: {json.dumps(public(item))}\n\n"
            yield ": keepalive\n\n"
            if run.status in {"COMPLETED", "FAILED", "CANCELLED", "WAITING_APPROVAL"}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get(prefix + "/reports")
async def reports(page: int = Query(1, ge=1), p=Depends(principal)):
    async with session() as db:
        return await listing(db, p, Report, page)


@app.post(prefix + "/reports/generate", status_code=202)
async def report_generate(body: dict, p=Depends(principal)):
    if set(body) != {"run_id"} or not isinstance(body["run_id"], str):
        raise DomainError("run_id is required")
    return await queue_job(p, "report", body)


@app.get(prefix + "/reports/{report_id}")
async def report_detail(report_id: str, p=Depends(principal)):
    async with session() as db:
        return public(await owned(db, Report, report_id, p.tenant_id))


@app.get(prefix + "/reports/{report_id}/pdf")
async def download_pdf(report_id: str, p=Depends(principal)):
    from ledgerguard.reports import report_pdf

    async with session() as db:
        report = await owned(db, Report, report_id, p.tenant_id)
        audit(db, p, "REPORT_DOWNLOADED", report.id)
    pdf = await asyncio.to_thread(report_pdf, report)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ledgerguard-{report_id}.pdf"'},
    )


@app.get(prefix + "/audit-logs")
async def audit_logs(page: int = Query(1, ge=1), p=Depends(principal)):
    async with session() as db:
        return await listing(db, p, AuditLog, page, 100)


@app.get(prefix + "/mcp/tool-executions")
async def tool_executions(run_id: str | None = None, page: int = Query(1, ge=1), p=Depends(principal)):
    async with session() as db:
        return await listing(
            db, p, ToolExecution, page, 100, [ToolExecution.run_id == run_id] if run_id else []
        )


@app.get(prefix + "/jobs/{job_id}")
async def job_detail(job_id: str, p=Depends(principal)):
    async with session() as db:
        result = public(await owned(db, BackgroundJob, job_id, p.tenant_id))
        result.pop("payload", None)
        return result


@app.get(prefix + "/dashboard")
async def dashboard(p=Depends(principal)):
    from ledgerguard.cache import cached_dashboard

    return await cached_dashboard(p)


@app.get(prefix + "/settings")
async def get_settings(p=Depends(principal)):
    async with session() as db:
        return {
            "sandbox": True,
            "llm_provider": settings().llm_provider,
            "llm_model": settings().llm_model,
            "llm_configured": bool(settings().openai_api_key),
            "task_mode": settings().task_mode,
            "risk_rules": (await listing(db, p, RiskRule))["items"],
            "approval_policies": (await listing(db, p, ApprovalPolicy))["items"],
            "observability": {"langsmith": settings().langsmith_tracing},
        }


@app.get(prefix + "/mcp/health")
async def mcp_health(p=Depends(principal)):
    from ledgerguard.mcp_client import MCPClientManager

    client = MCPClientManager(p)

    async def inspect(name):
        try:
            tools = await asyncio.wait_for(client.discover(name), 5)
            return {"server": name, "status": "ready", "tools": [t.name for t in tools]}
        except Exception:
            return {"server": name, "status": "unavailable", "tools": []}

    return {
        "items": await asyncio.gather(
            *[inspect(n) for n in ("transactions", "risk", "reconciliation", "approvals")]
        )
    }


@app.put(prefix + "/settings/risk")
async def update_risk_policy(body: RiskPolicyInput, p=Depends(principal)):
    p.require({"admin"})
    from ledgerguard.reconciliation import advisory_lock

    async with session() as db:
        await advisory_lock(db, f"policy:{p.tenant_id}")
        current = await db.scalar(
            select(RiskRule)
            .where(RiskRule.tenant_id == p.tenant_id)
            .order_by(RiskRule.version.desc())
            .limit(1)
        )
        if not current:
            raise DomainError("No initial risk policy configured", 409)
        version = current.version + 1
        policy = RiskRule(
            tenant_id=p.tenant_id,
            name=f"sandbox-v{version}",
            version=version,
            config={**current.config, "high_value": str(body.high_value), "velocity": body.velocity},
        )
        db.add(policy)
        await db.flush()
        audit(db, p, "RISK_POLICY_CHANGED", policy.id, {"version": version})
        return public(policy)


@app.put(prefix + "/settings/approvals")
async def update_approval_policy(body: ApprovalPolicyInput, p=Depends(principal)):
    p.require({"admin"})
    from ledgerguard.reconciliation import advisory_lock

    async with session() as db:
        await advisory_lock(db, f"policy:{p.tenant_id}")
        current = await db.scalar(
            select(ApprovalPolicy)
            .where(ApprovalPolicy.tenant_id == p.tenant_id)
            .order_by(ApprovalPolicy.version.desc())
            .limit(1)
        )
        version = current.version + 1 if current else 1
        policy = ApprovalPolicy(tenant_id=p.tenant_id, version=version, config=body.model_dump(mode="json"))
        db.add(policy)
        await db.flush()
        audit(db, p, "APPROVAL_POLICY_CHANGED", policy.id, {"version": version})
        return public(policy)


@app.post(prefix + "/risk/train", status_code=202)
async def train_risk_model(p=Depends(principal)):
    p.require({"admin"})
    return await queue_job(p, "training", {})
