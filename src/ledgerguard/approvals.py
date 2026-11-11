from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update

from ledgerguard.config import settings
from ledgerguard.db import DomainError, owned, public
from ledgerguard.models import (
    ActionExecution,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    BankEntry,
    LedgerEntry,
    ProposedAction,
    Transaction,
    User,
    now,
    uid,
)
from ledgerguard.reconciliation import action_digest, advisory_lock
from ledgerguard.security import WRITE_ROLES, audit, aware


async def create_request(db, principal, action_id, run_id=None):
    principal.require(WRITE_ROLES)
    action = await owned(db, ProposedAction, action_id, principal.tenant_id)
    await advisory_lock(db, f"approval:{action.id}")
    existing = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.action_id == action.id, ApprovalRequest.tenant_id == principal.tenant_id
        )
    )
    if existing:
        return public(existing)
    policy = await db.scalar(
        select(ApprovalPolicy)
        .where(ApprovalPolicy.tenant_id == principal.tenant_id)
        .order_by(ApprovalPolicy.version.desc())
        .limit(1)
    )
    threshold = (
        Decimal(str(policy.config["two_person_threshold"]))
        if policy
        else Decimal(settings().two_person_threshold)
    )
    required = 2 if abs(Decimal(action.payload["amount"])) >= threshold else 1
    approval = ApprovalRequest(
        tenant_id=principal.tenant_id,
        action_id=action.id,
        run_id=run_id,
        required_approvals=required,
        digest=action.digest,
        expires_at=now()
        + timedelta(
            hours=int(policy.config.get("expires_hours", settings().approval_hours))
            if policy
            else settings().approval_hours
        ),
    )
    db.add(approval)
    await db.flush()
    audit(db, principal, "APPROVAL_REQUESTED", approval.id, {"action_id": action.id, "required": required})
    return public(approval)


async def details(db, principal, approval_id):
    approval = await owned(db, ApprovalRequest, approval_id, principal.tenant_id)
    action = await owned(db, ProposedAction, approval.action_id, principal.tenant_id)
    decisions = (
        await db.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.approval_id == approval.id, ApprovalDecision.tenant_id == principal.tenant_id
            )
        )
    ).all()
    from ledgerguard.models import AuditLog

    history = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.tenant_id == principal.tenant_id, AuditLog.entity_id == approval.id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    ).all()
    return {
        **public(approval),
        "action": public(action),
        "decisions": [public(d) for d in decisions],
        "history": [public(item) for item in history],
        "policy": {
            "maker_checker": True,
            "required_approvals": approval.required_approvals,
            "action_allowlist": ["VOID_DUPLICATE"],
            "sandbox_only": True,
        },
    }


async def decide(db, principal, approval_id, decision, reason, version):
    principal.require({"approver", "admin"})
    approval = await owned(db, ApprovalRequest, approval_id, principal.tenant_id, lock=True)
    action = await owned(db, ProposedAction, approval.action_id, principal.tenant_id, lock=True)
    if principal.user_id == action.maker_id:
        raise DomainError("Maker cannot decide their own proposal", 403)
    if approval.status != "PENDING":
        raise DomainError(f"Approval is {approval.status}", 409)
    if aware(approval.expires_at) <= now():
        raise DomainError("Approval expired", 409)
    if version != approval.version:
        raise DomainError("Stale approval revision", 409)
    if action.digest != approval.digest or action_digest(action.payload) != approval.digest:
        raise DomainError("Proposal changed; a new review is required", 409)
    if await db.scalar(
        select(ApprovalDecision).where(
            ApprovalDecision.approval_id == approval.id, ApprovalDecision.actor_id == principal.user_id
        )
    ):
        raise DomainError("This user already decided", 409)
    result = await db.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval.id,
            ApprovalRequest.version == version,
            ApprovalRequest.status == "PENDING",
        )
        .values(version=version + 1)
    )
    if result.rowcount != 1:
        raise DomainError("Concurrent decision; reload approval", 409)
    if decision == "INVESTIGATE":
        # Return to review without treating investigation as an approval vote.
        audit(db, principal, "INVESTIGATION_REQUESTED", approval.id, {"reason": reason})
        return await details(db, principal, approval.id)
    db.add(
        ApprovalDecision(
            tenant_id=principal.tenant_id,
            approval_id=approval.id,
            actor_id=principal.user_id,
            decision=decision,
            reason=reason,
            proposal_digest=approval.digest,
        )
    )
    await db.flush()
    votes = (
        await db.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.approval_id == approval.id,
                ApprovalDecision.tenant_id == principal.tenant_id,
                ApprovalDecision.decision == "APPROVE",
            )
        )
    ).all()
    if decision == "REJECT":
        approval.status = "REJECTED"
    elif len(votes) >= approval.required_approvals:
        approval.status = "APPROVED"
    audit(
        db,
        principal,
        f"APPROVAL_{decision}",
        approval.id,
        {"reason": reason, "status": approval.status, "digest": approval.digest},
    )
    await db.flush()
    return await details(db, principal, approval.id)


async def execute(db, principal, approval_id):
    """Worker-only domain entry; always re-check persisted human authorization."""
    approval = await owned(db, ApprovalRequest, approval_id, principal.tenant_id, lock=True)
    existing = await db.scalar(
        select(ActionExecution).where(
            ActionExecution.approval_id == approval.id, ActionExecution.tenant_id == principal.tenant_id
        )
    )
    if existing:
        return public(existing)
    if approval.status != "APPROVED" or aware(approval.expires_at) <= now():
        raise DomainError("Persisted, unexpired human approval required", 403)
    action = await owned(db, ProposedAction, approval.action_id, principal.tenant_id, lock=True)
    if (
        action.action_type != "VOID_DUPLICATE"
        or action.digest != approval.digest
        or action_digest(action.payload) != approval.digest
    ):
        raise DomainError("Proposal integrity or action allowlist check failed", 409)
    decisions = (
        await db.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.tenant_id == principal.tenant_id,
                ApprovalDecision.approval_id == approval.id,
                ApprovalDecision.decision == "APPROVE",
            )
        )
    ).all()
    valid = set()
    for vote in decisions:
        user = await owned(db, User, vote.actor_id, principal.tenant_id)
        if (
            user.active
            and user.role in {"admin", "approver"}
            and user.id != action.maker_id
            and vote.proposal_digest == action.digest
        ):
            valid.add(user.id)
    policy = await db.scalar(
        select(ApprovalPolicy)
        .where(ApprovalPolicy.tenant_id == principal.tenant_id)
        .order_by(ApprovalPolicy.version.desc())
        .limit(1)
    )
    current_threshold = (
        Decimal(str(policy.config["two_person_threshold"]))
        if policy
        else Decimal(settings().two_person_threshold)
    )
    current_required = 2 if abs(Decimal(action.payload["amount"])) >= current_threshold else 1
    if len(valid) < max(approval.required_approvals, current_required):
        raise DomainError("Human authorization no longer satisfies policy", 403)
    ledger = await owned(db, LedgerEntry, action.target_id, principal.tenant_id, lock=True)
    target = await owned(db, Transaction, ledger.transaction_id, principal.tenant_id)
    original = await owned(db, Transaction, action.payload["original_id"], principal.tenant_id)
    bank = await owned(db, BankEntry, action.payload["bank_id"], principal.tenant_id)
    from ledgerguard.models import BankStatement, ReconciliationJob

    job = await owned(db, ReconciliationJob, action.job_id, principal.tenant_id)
    statement = await owned(db, BankStatement, bank.statement_id, principal.tenant_id)
    settlements = (
        await db.scalars(
            select(BankEntry).where(
                BankEntry.tenant_id == principal.tenant_id,
                BankEntry.statement_id == bank.statement_id,
                BankEntry.reference == bank.reference,
                BankEntry.amount == bank.amount,
                BankEntry.currency == bank.currency,
            )
        )
    ).all()
    if (
        len(settlements) != 1
        or bank.statement_id != job.statement_id
        or target.account_id != statement.account_id
        or target.account_id != job.account_id
    ):
        raise DomainError("Settlement evidence changed; investigate again", 409)
    original_ledger = await db.scalar(
        select(LedgerEntry)
        .where(LedgerEntry.transaction_id == original.id, LedgerEntry.tenant_id == principal.tenant_id)
        .with_for_update()
    )

    def fields(t):
        return (t.account_id, t.amount, t.currency, t.reference, t.booked_at.date(), t.counterparty_id)

    if (
        not original_ledger
        or not original_ledger.active
        or target.id == original.id
        or fields(target) != fields(original)
        or bank.amount != target.amount
        or bank.currency != target.currency
        or bank.reference != target.reference
        or str(target.amount) != action.payload["amount"]
        or target.currency != action.payload["currency"]
    ):
        raise DomainError("Financial evidence changed; investigate again", 409)
    result = await db.execute(
        update(LedgerEntry)
        .where(
            LedgerEntry.id == ledger.id,
            LedgerEntry.tenant_id == principal.tenant_id,
            LedgerEntry.active.is_(True),
            LedgerEntry.version == action.payload["ledger_version"],
        )
        .values(active=False, version=LedgerEntry.version + 1)
    )
    if result.rowcount != 1:
        raise DomainError("Ledger revision conflict", 409)
    execution = ActionExecution(
        id=uid(),
        tenant_id=principal.tenant_id,
        action_id=action.id,
        approval_id=approval.id,
        result={
            "sandbox": True,
            "voided_ledger_id": ledger.id,
            "transaction_id": target.id,
            "original_retained": original.id,
            "amount": str(target.amount),
        },
    )
    db.add(execution)
    approval.status = "EXECUTED"
    audit(
        db,
        principal,
        "SANDBOX_ACTION_EXECUTED",
        action.id,
        {"approval_id": approval.id, "execution_id": execution.id, **execution.result},
    )
    await db.flush()
    return public(execution)


async def cancel(db, principal, approval_id):
    approval = await owned(db, ApprovalRequest, approval_id, principal.tenant_id, lock=True)
    action = await owned(db, ProposedAction, approval.action_id, principal.tenant_id)
    if principal.user_id != action.maker_id and principal.role != "admin":
        raise DomainError("Only the maker or admin may cancel", 403)
    if approval.status != "PENDING":
        raise DomainError("Only pending requests can be cancelled", 409)
    approval.status = "CANCELLED"
    audit(db, principal, "APPROVAL_CANCELLED", approval.id)
    return public(approval)
