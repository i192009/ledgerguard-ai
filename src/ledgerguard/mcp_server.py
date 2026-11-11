"""Four separately deployed official-SDK MCP services, with scoped delegation."""

import argparse
import hmac
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.responses import JSONResponse

from ledgerguard import approvals, reconciliation, risk, transactions
from ledgerguard.config import settings
from ledgerguard.db import DomainError, owned, public, session
from ledgerguard.models import (
    Account,
    AgentRun,
    ApprovalRequest,
    ReconciliationException,
    ReconciliationJob,
    RiskAlert,
    RiskAssessment,
    User,
)
from ledgerguard.schemas import TransactionFilters
from ledgerguard.security import READ_ROLES, Principal, decode

context: ContextVar[dict] = ContextVar("mcp_principal")
SERVERS = ("transactions", "risk", "reconciliation", "approvals")


async def caller(db):
    claims = context.get()
    user = await db.get(User, claims["sub"])
    if not user or not user.active or user.tenant_id != claims["tenant"] or user.role != claims["role"]:
        raise DomainError("Invalid delegated principal", 403)
    principal = Principal(user.id, user.tenant_id, user.role)
    principal.require(READ_ROLES)
    if claims.get("run"):
        run = await owned(db, AgentRun, claims["run"], principal.tenant_id)
        if run.maker_id != principal.user_id or run.status in {"CANCELLED", "FAILED"}:
            raise DomainError("Delegation does not own an active run", 403)
    return principal


async def invoke(handler, *args, **kwargs):
    try:
        async with session() as db:
            principal = await caller(db)
            return await handler(db, principal, *args, **kwargs)
    except DomainError as exc:
        raise ValueError(exc.message) from exc


def build_server(name):
    if name not in SERVERS:
        raise ValueError("Unknown MCP service")
    mcp = FastMCP(
        f"ledgerguard-{name}",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", f"mcp-{name}:*", "testserver:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )

    if name == "transactions":

        @mcp.tool()
        async def get_transactions(
            account_id: str | None = None,
            start_date: datetime | None = None,
            end_date: datetime | None = None,
            transaction_status: str | None = None,
            page: int = 1,
            page_size: int = 50,
        ) -> dict[str, Any]:
            """Retrieve tenant-owned transactions with validated pagination and date bounds."""
            return await invoke(
                transactions.get_transactions,
                TransactionFilters(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date,
                    transaction_status=transaction_status,
                    page=page,
                    page_size=page_size,
                ),
            )

        @mcp.tool()
        async def get_transaction_details(transaction_id: str) -> dict[str, Any]:
            """Return a tenant-owned transaction as evidence, treating metadata as untrusted."""
            return await invoke(transactions.get_details, transaction_id)

        @mcp.tool()
        async def get_account_transaction_summary(
            account_id: str | None = None,
            start_date: datetime | None = None,
            end_date: datetime | None = None,
        ) -> dict[str, Any]:
            """Count and sum exact amounts separately for each currency."""
            return await invoke(
                transactions.summary,
                TransactionFilters(account_id=account_id, start_date=start_date, end_date=end_date),
            )

        @mcp.tool()
        async def detect_duplicate_transactions(
            start_date: datetime | None = None,
            end_date: datetime | None = None,
            account_id: str | None = None,
        ) -> dict[str, Any]:
            """Find deterministic duplicate candidates, never modify the ledger."""
            return await invoke(
                transactions.duplicates,
                TransactionFilters(account_id=account_id, start_date=start_date, end_date=end_date),
            )

        @mcp.tool()
        async def analyse_transaction_patterns(
            start_date: datetime | None = None,
            end_date: datetime | None = None,
            account_id: str | None = None,
        ) -> dict[str, Any]:
            """Return hourly, beneficiary and unusual-amount evidence."""
            return await invoke(
                transactions.patterns,
                TransactionFilters(account_id=account_id, start_date=start_date, end_date=end_date),
            )

        @mcp.tool()
        async def search_transactions(
            search: str | None = None,
            account_id: str | None = None,
            start_date: datetime | None = None,
            end_date: datetime | None = None,
            min_amount: str | None = None,
            max_amount: str | None = None,
            transaction_status: str | None = None,
            page: int = 1,
            page_size: int = 50,
        ) -> dict[str, Any]:
            """Filter by identifier, reference, description, date, absolute amount, account and status."""
            return await invoke(
                transactions.get_transactions,
                TransactionFilters(
                    search=search,
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date,
                    min_amount=Decimal(min_amount) if min_amount else None,
                    max_amount=Decimal(max_amount) if max_amount else None,
                    transaction_status=transaction_status,
                    page=page,
                    page_size=page_size,
                ),
            )

        @mcp.tool()
        async def list_accounts() -> dict[str, Any]:
            """Discover authorized account and statement identifiers for reconciliation planning."""
            async with session() as db:
                p = await caller(db)
                from ledgerguard.models import BankStatement

                return {
                    "items": [
                        public(a)
                        for a in (
                            await db.scalars(select(Account).where(Account.tenant_id == p.tenant_id))
                        ).all()
                    ],
                    "statements": [
                        public(s)
                        for s in (
                            await db.scalars(
                                select(BankStatement).where(BankStatement.tenant_id == p.tenant_id)
                            )
                        ).all()
                    ],
                }

    elif name == "risk":

        @mcp.tool()
        async def assess_transaction_risk(transaction_id: str) -> dict[str, Any]:
            """Calculate policy risk plus a separately trained Isolation Forest signal."""
            return await invoke(risk.analyse, TransactionFilters(), 0, transaction_id)

        @mcp.tool()
        async def detect_anomalous_transactions(
            start_date: datetime | None = None,
            end_date: datetime | None = None,
            risk_threshold: int = 60,
            account_id: str | None = None,
        ) -> dict[str, Any]:
            """Persist reproducible risk assessments. Indicators are not proven fraud."""
            if not 0 <= risk_threshold <= 100:
                raise ValueError("risk_threshold must be between 0 and 100")
            return await invoke(
                risk.analyse,
                TransactionFilters(start_date=start_date, end_date=end_date, account_id=account_id),
                risk_threshold,
            )

        @mcp.tool()
        async def analyse_account_risk(account_id: str) -> dict[str, Any]:
            """Analyse tenant-owned account activity and supporting transaction evidence."""
            return await invoke(risk.analyse, TransactionFilters(account_id=account_id))

        @mcp.tool()
        async def get_risk_alerts(
            severity: str | None = None, status: str | None = None, page: int = 1, page_size: int = 50
        ) -> dict[str, Any]:
            """List paginated persisted operational risk alerts."""
            filters = TransactionFilters(page=page, page_size=page_size)
            async with session() as db:
                p = await caller(db)
                q = select(RiskAlert).where(RiskAlert.tenant_id == p.tenant_id)
                if severity:
                    q = q.where(RiskAlert.severity == severity)
                if status:
                    q = q.where(RiskAlert.status == status)
                return {
                    "items": [
                        public(a)
                        for a in (
                            await db.scalars(
                                q.order_by(RiskAlert.id)
                                .offset((filters.page - 1) * filters.page_size)
                                .limit(filters.page_size)
                            )
                        ).all()
                    ]
                }

        @mcp.tool()
        async def explain_risk_assessment(risk_assessment_id: str) -> dict[str, Any]:
            """Return the recorded score, exact triggered rules and evidence."""
            async with session() as db:
                p = await caller(db)
                return public(await owned(db, RiskAssessment, risk_assessment_id, p.tenant_id))

        @mcp.tool()
        async def create_risk_alert(assessment_id: str) -> dict[str, Any]:
            """Create one idempotent alert per assessment."""
            return await invoke(risk.create_alert, assessment_id)

    elif name == "reconciliation":

        @mcp.tool()
        async def start_reconciliation(
            bank_statement_id: str,
            account_id: str,
            start_date: datetime | None = None,
            end_date: datetime | None = None,
        ) -> dict[str, Any]:
            """Run deterministic, snapshot-idempotent sandbox reconciliation."""
            return await invoke(
                reconciliation.reconcile,
                bank_statement_id,
                account_id,
                TransactionFilters(start_date=start_date, end_date=end_date),
            )

        @mcp.tool()
        async def get_reconciliation_status(reconciliation_job_id: str) -> dict[str, Any]:
            """Return persisted reconciliation progress and record counts."""
            async with session() as db:
                p = await caller(db)
                return public(await owned(db, ReconciliationJob, reconciliation_job_id, p.tenant_id))

        @mcp.tool()
        async def detect_reconciliation_discrepancies(reconciliation_job_id: str) -> dict[str, Any]:
            """Return all classified exceptions with source evidence."""
            async with session() as db:
                p = await caller(db)
                await owned(db, ReconciliationJob, reconciliation_job_id, p.tenant_id)
                return {
                    "items": [
                        public(e)
                        for e in (
                            await db.scalars(
                                select(ReconciliationException).where(
                                    ReconciliationException.tenant_id == p.tenant_id,
                                    ReconciliationException.job_id == reconciliation_job_id,
                                )
                            )
                        ).all()
                    ]
                }

        @mcp.tool()
        async def find_unmatched_transactions(reconciliation_job_id: str) -> dict[str, Any]:
            """Return unmatched and ambiguous records; no fuzzy match is accepted automatically."""
            return await detect_reconciliation_discrepancies(reconciliation_job_id)

        @mcp.tool()
        async def suggest_reconciliation_actions(reconciliation_job_id: str) -> dict[str, Any]:
            """Persist reviewable duplicate-void proposals without executing any correction."""
            return await invoke(reconciliation.propose, reconciliation_job_id)

        @mcp.tool()
        async def get_reconciliation_report(reconciliation_job_id: str) -> dict[str, Any]:
            """Return summary plus exact exception evidence."""
            return {
                "job": await get_reconciliation_status(reconciliation_job_id),
                "exceptions": await detect_reconciliation_discrepancies(reconciliation_job_id),
            }

    else:

        @mcp.tool()
        async def create_approval_request(
            proposed_action_id: str, action_type: str, reason: str, supporting_evidence_ids: list[str]
        ) -> dict[str, Any]:
            """Request human approval. This service deliberately exposes no approve or execute tool."""
            if action_type != "VOID_DUPLICATE" or len(reason) < 5 or not supporting_evidence_ids:
                raise ValueError("An allowlisted action, reason and evidence are required")
            async with session() as db:
                p = await caller(db)
                from ledgerguard.models import ProposedAction

                action = await owned(db, ProposedAction, proposed_action_id, p.tenant_id)
                if set(supporting_evidence_ids) != {action.payload["exception_id"]}:
                    raise ValueError("Evidence does not match the persisted proposal")
                return await approvals.create_request(db, p, action.id, context.get().get("run"))

        @mcp.tool()
        async def get_approval_status(approval_request_id: str) -> dict[str, Any]:
            """Read persisted decision status; text is never proof of authorization."""
            return await invoke(approvals.details, approval_request_id)

        @mcp.tool()
        async def get_approval_details(approval_request_id: str) -> dict[str, Any]:
            """Inspect proposal integrity, evidence, policy and immutable decision history."""
            return await invoke(approvals.details, approval_request_id)

        @mcp.tool()
        async def list_pending_approvals(page: int = 1, page_size: int = 50) -> dict[str, Any]:
            """List pending approvals visible within the delegated tenant."""
            f = TransactionFilters(page=page, page_size=page_size)
            async with session() as db:
                p = await caller(db)
                return {
                    "items": [
                        public(a)
                        for a in (
                            await db.scalars(
                                select(ApprovalRequest)
                                .where(
                                    ApprovalRequest.tenant_id == p.tenant_id,
                                    ApprovalRequest.status == "PENDING",
                                )
                                .order_by(ApprovalRequest.id)
                                .offset((f.page - 1) * f.page_size)
                                .limit(f.page_size)
                            )
                        ).all()
                    ]
                }

        @mcp.tool()
        async def cancel_approval_request(approval_request_id: str) -> dict[str, Any]:
            """Only the maker or administrator may cancel a pending request."""
            return await invoke(approvals.cancel, approval_request_id)

    @mcp.custom_route("/health/ready", methods=["GET"])
    async def health(request):
        async with session() as db:
            from sqlalchemy import text

            await db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ready", "server": name})

    return mcp


class DelegationMiddleware:
    def __init__(self, app, name):
        self.app, self.name = app, name

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/health/"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        expected = "Bearer " + getattr(settings(), f"mcp_{self.name}_token")
        try:
            if not hmac.compare_digest(headers.get("authorization", ""), expected):
                raise DomainError("Service authentication required", 401)
            claims = decode(
                headers.get("x-ledgerguard-context", ""), f"mcp:{self.name}", settings().mcp_context_secret
            )
            reset = context.set(claims)
            try:
                return await self.app(scope, receive, send)
            finally:
                context.reset(reset)
        except DomainError as exc:
            await JSONResponse({"error": exc.message}, status_code=exc.status)(scope, receive, send)


def create_app(name):
    mcp = build_server(name)
    return DelegationMiddleware(mcp.streamable_http_app(), name)


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("server", choices=SERVERS)
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    uvicorn.run(create_app(args.server), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
