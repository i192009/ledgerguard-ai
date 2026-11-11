from datetime import timedelta

import pytest
from sqlalchemy import func, select

from ledgerguard.approvals import decide, execute
from ledgerguard.db import DomainError, owned, session
from ledgerguard.models import ActionExecution, ApprovalRequest, LedgerEntry, ProposedAction, User, now
from ledgerguard.security import Principal


async def vote(case, who=None, decision="APPROVE", version=1):
    async with session() as db:
        return await decide(
            db, who or case["checker"], case["approval"], decision, "Reviewed source evidence", version
        )


async def test_cannot_execute_without_human_decision(governed_case):
    c = governed_case
    with pytest.raises(DomainError, match="human approval"):
        async with session() as db:
            await execute(db, c["maker"], c["approval"])


async def test_role_and_maker_checker(governed_case):
    c = governed_case
    with pytest.raises(DomainError, match="permissions"):
        await vote(c, c["maker"])
    async with session() as db:
        user = await db.get(User, c["maker"].user_id)
        user.role = "admin"
    with pytest.raises(DomainError, match="Maker"):
        await vote(c, Principal(c["maker"].user_id, c["maker"].tenant_id, "admin"))


async def test_tenant_isolation(governed_case, analyst):
    with pytest.raises(DomainError, match="not found"):
        async with session() as db:
            await owned(db, ApprovalRequest, governed_case["approval"], analyst.tenant_id)


async def test_stale_revision_rejected(governed_case):
    with pytest.raises(DomainError, match="Stale"):
        await vote(governed_case, version=5)


async def test_expired_approval_rejected(governed_case):
    async with session() as db:
        approval = await db.get(ApprovalRequest, governed_case["approval"])
        approval.expires_at = now() - timedelta(seconds=1)
    with pytest.raises(DomainError, match="expired"):
        await vote(governed_case)


async def test_changed_proposal_rejected(governed_case):
    async with session() as db:
        action = await db.get(ProposedAction, governed_case["action"])
        action.payload = {**action.payload, "amount": "-999.00"}
    with pytest.raises(DomainError, match="Proposal changed"):
        await vote(governed_case)


async def test_rejected_decision_prevents_execution(governed_case):
    c = governed_case
    await vote(c, decision="REJECT")
    with pytest.raises(DomainError, match="human approval"):
        async with session() as db:
            await execute(db, c["maker"], c["approval"])


async def test_two_independent_humans_required(governed_case):
    c = governed_case
    async with session() as db:
        approval = await db.get(ApprovalRequest, c["approval"])
        approval.required_approvals = 2
    first = await vote(c)
    assert first["status"] == "PENDING"
    with pytest.raises(DomainError, match="already decided"):
        await vote(c, version=2)
    second = await vote(c, c["second"], version=2)
    assert second["status"] == "APPROVED"


async def test_revalidate_approver_role_before_execution(governed_case):
    c = governed_case
    await vote(c)
    async with session() as db:
        user = await db.get(User, c["checker"].user_id)
        user.role = "auditor"
    with pytest.raises(DomainError, match="authorization"):
        async with session() as db:
            await execute(db, c["maker"], c["approval"])


async def test_approved_execution_exactly_once(governed_case):
    c = governed_case
    await vote(c)
    async with session() as db:
        first = await execute(db, c["maker"], c["approval"])
    async with session() as db:
        second = await execute(db, c["maker"], c["approval"])
        ledger = await db.get(LedgerEntry, c["ledger"])
        count = await db.scalar(
            select(func.count()).select_from(ActionExecution).where(ActionExecution.action_id == c["action"])
        )
    assert first["id"] == second["id"] and count == 1
    assert not ledger.active and ledger.version == 2


async def test_investigation_remains_pending_without_approval_vote(governed_case):
    result = await vote(governed_case, decision="INVESTIGATE")
    assert result["status"] == "PENDING" and not result["decisions"]


async def test_stricter_current_policy_blocks_old_quorum(governed_case):
    from ledgerguard.models import ApprovalPolicy

    c = governed_case
    await vote(c)
    async with session() as db:
        db.add(
            ApprovalPolicy(
                tenant_id=c["maker"].tenant_id,
                version=2,
                config={"two_person_threshold": "100.00", "expires_hours": 24},
            )
        )
    with pytest.raises(DomainError, match="authorization"):
        async with session() as db:
            await execute(db, c["maker"], c["approval"])


async def test_new_settlement_invalidates_duplicate_proof(governed_case):
    from ledgerguard.models import BankEntry, uid

    c = governed_case
    await vote(c)
    async with session() as db:
        action = await db.get(ProposedAction, c["action"])
        bank = await db.get(BankEntry, action.payload["bank_id"])
        db.add(
            BankEntry(
                tenant_id=bank.tenant_id,
                statement_id=bank.statement_id,
                source_id=uid(),
                amount=bank.amount,
                currency=bank.currency,
                reference=bank.reference,
                booked_at=bank.booked_at,
            )
        )
    with pytest.raises(DomainError, match="Settlement evidence changed"):
        async with session() as db:
            await execute(db, c["maker"], c["approval"])


async def test_ledger_revision_revalidated_after_approval(governed_case):
    c = governed_case
    await vote(c)
    async with session() as db:
        ledger = await db.get(LedgerEntry, c["ledger"])
        ledger.version = 2
    with pytest.raises(DomainError, match="revision conflict"):
        async with session() as db:
            await execute(db, c["maker"], c["approval"])
