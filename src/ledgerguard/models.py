from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Tenant(Entity):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(200))


class Scoped(Entity):
    __abstract__ = True
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)


class Role(Entity):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(40), unique=True)


class User(Scoped):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(40), default="analyst")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Scoped):
    __tablename__ = "tenant_memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))


class RefreshSession(Scoped):
    __tablename__ = "refresh_token_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Account(Scoped):
    __tablename__ = "accounts"
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("1000000"))


class Counterparty(Scoped):
    __tablename__ = "counterparties"
    name: Mapped[str] = mapped_column(String(100))


class Transaction(Scoped):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id"),
        Index("ix_transactions_scope_date", "tenant_id", "booked_at", "id"),
        Index("ix_transactions_scope_reference", "tenant_id", "account_id", "reference"),
    )
    source_id: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3))
    reference: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(String(1000), default="")
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="POSTED")
    labels: Mapped[dict] = mapped_column(JSON, default=dict)


class LedgerEntry(Scoped):
    __tablename__ = "ledger_entries"
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class BankStatement(Scoped):
    __tablename__ = "bank_statements"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(200))


class BankEntry(Scoped):
    __tablename__ = "bank_statement_entries"
    __table_args__ = (UniqueConstraint("statement_id", "source_id"),)
    statement_id: Mapped[str] = mapped_column(ForeignKey("bank_statements.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3))
    reference: Mapped[str] = mapped_column(String(150))
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(String(1000), default="")
    group_reference: Mapped[str | None] = mapped_column(String(150), nullable=True)


class RiskRule(Scoped):
    __tablename__ = "risk_rules"
    name: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ModelVersion(Scoped):
    __tablename__ = "anomaly_model_versions"
    version: Mapped[str] = mapped_column(String(100), unique=True)
    artifact_path: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON)


class RiskAssessment(Scoped):
    __tablename__ = "risk_assessments"
    __table_args__ = (UniqueConstraint("tenant_id", "transaction_id", "model_version"),)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    rules: Mapped[list] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(100))


class RiskAlert(Scoped):
    __tablename__ = "risk_alerts"
    assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id"), unique=True)
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="OPEN")


class ReconciliationJob(Scoped):
    __tablename__ = "reconciliation_jobs"
    key: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    statement_id: Mapped[str] = mapped_column(ForeignKey("bank_statements.id"))
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class ReconciliationMatch(Scoped):
    __tablename__ = "reconciliation_matches"
    job_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_jobs.id"), index=True)
    ledger_ids: Mapped[list] = mapped_column(JSON)
    bank_ids: Mapped[list] = mapped_column(JSON)
    rule: Mapped[str] = mapped_column(String(80))
    explanation: Mapped[str] = mapped_column(String(500))


class ReconciliationException(Scoped):
    __tablename__ = "reconciliation_exceptions"
    job_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    evidence: Mapped[dict] = mapped_column(JSON)


class AgentRun(Scoped):
    __tablename__ = "agent_runs"
    maker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request: Mapped[str] = mapped_column(String(4000))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class AgentEvent(Scoped):
    __tablename__ = "agent_messages"
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    event: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ToolExecution(Scoped):
    __tablename__ = "tool_executions"
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    server: Mapped[str] = mapped_column(String(40))
    tool: Mapped[str] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ApprovalPolicy(Scoped):
    __tablename__ = "approval_policy_versions"
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSON)


class ProposedAction(Scoped):
    __tablename__ = "proposed_actions"
    __table_args__ = (UniqueConstraint("tenant_id", "job_id", "target_id"),)
    job_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_jobs.id"))
    maker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("ledger_entries.id"))
    action_type: Mapped[str] = mapped_column(String(50), default="VOID_DUPLICATE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))


class ApprovalRequest(Scoped):
    __tablename__ = "approval_requests"
    action_id: Mapped[str] = mapped_column(ForeignKey("proposed_actions.id"), unique=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)
    digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class ApprovalDecision(Scoped):
    __tablename__ = "approval_decisions"
    __table_args__ = (UniqueConstraint("approval_id", "actor_id"),)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(1000))
    proposal_digest: Mapped[str] = mapped_column(String(64))


class ActionExecution(Scoped):
    __tablename__ = "action_executions"
    action_id: Mapped[str] = mapped_column(ForeignKey("proposed_actions.id"), unique=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id"), unique=True)
    result: Mapped[dict] = mapped_column(JSON)


class Report(Scoped):
    __tablename__ = "research_or_analysis_reports"
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, unique=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[dict] = mapped_column(JSON)


class AuditLog(Scoped):
    __tablename__ = "audit_logs"
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(100))
    correlation_id: Mapped[str] = mapped_column(String(100), default=uid)
    outcome: Mapped[str] = mapped_column(String(30), default="SUCCESS")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class BackgroundJob(Scoped):
    __tablename__ = "background_jobs"
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
