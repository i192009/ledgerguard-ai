"""Real PostgreSQL row-lock, idempotence and immutable-record verification."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from ledgerguard.approvals import decide, execute
from ledgerguard.db import DomainError, engine, session
from ledgerguard.models import (
    Account,
    ActionExecution,
    ApprovalRequest,
    AuditLog,
    BankEntry,
    BankStatement,
    LedgerEntry,
    ProposedAction,
    ReconciliationJob,
    Tenant,
    Transaction,
    User,
    now,
    uid,
)
from ledgerguard.reconciliation import action_digest
from ledgerguard.security import Principal


async def main():
    assert engine.dialect.name == "postgresql", "This verification requires actual PostgreSQL"
    tenant_id, maker_id, checker_id = uid(), uid(), uid()
    async with session() as db:
        db.add(Tenant(id=tenant_id, name="PostgreSQL verification"))
        await db.flush()
        db.add_all(
            [
                User(
                    id=maker_id,
                    tenant_id=tenant_id,
                    email=maker_id + "@test.invalid",
                    name="maker",
                    role="analyst",
                    password_hash="unused",
                ),
                User(
                    id=checker_id,
                    tenant_id=tenant_id,
                    email=checker_id + "@test.invalid",
                    name="checker",
                    role="approver",
                    password_hash="unused",
                ),
            ]
        )
        account = Account(id=uid(), tenant_id=tenant_id, name="Concurrency sandbox", currency="GBP")
        db.add(account)
        await db.flush()
        original, duplicate = [
            Transaction(
                id=uid(),
                tenant_id=tenant_id,
                source_id=uid(),
                account_id=account.id,
                amount=Decimal("-250.00"),
                currency="GBP",
                reference="LOCK-TEST",
                booked_at=now().replace(microsecond=0),
            )
            for _ in range(2)
        ]
        statement = BankStatement(
            id=uid(), tenant_id=tenant_id, account_id=account.id, name="Concurrency test"
        )
        db.add_all([original, duplicate, statement])
        await db.flush()
        ledgers = [
            LedgerEntry(id=uid(), tenant_id=tenant_id, transaction_id=t.id) for t in (original, duplicate)
        ]
        bank = BankEntry(
            id=uid(),
            tenant_id=tenant_id,
            statement_id=statement.id,
            source_id=uid(),
            amount=original.amount,
            currency="GBP",
            reference="LOCK-TEST",
            booked_at=original.booked_at,
        )
        job = ReconciliationJob(
            id=uid(),
            tenant_id=tenant_id,
            key=uid(),
            account_id=account.id,
            statement_id=statement.id,
            status="COMPLETED",
            summary={},
        )
        db.add_all([*ledgers, bank, job])
        await db.flush()
        payload = {
            "original_id": original.id,
            "transaction_id": duplicate.id,
            "bank_id": bank.id,
            "amount": "-250.00",
            "currency": "GBP",
            "ledger_version": 1,
            "exception_id": uid(),
            "job_id": job.id,
            "ledger_id": ledgers[1].id,
            "type": "VOID_DUPLICATE",
            "version": 1,
        }
        action = ProposedAction(
            id=uid(),
            tenant_id=tenant_id,
            job_id=job.id,
            maker_id=maker_id,
            target_id=ledgers[1].id,
            payload=payload,
            digest=action_digest(payload),
        )
        db.add(action)
        await db.flush()
        approval = ApprovalRequest(
            id=uid(),
            tenant_id=tenant_id,
            action_id=action.id,
            digest=action.digest,
            expires_at=now() + timedelta(hours=1),
        )
        db.add(approval)
        await db.flush()
        approval_id, action_id = approval.id, action.id
    maker, checker = Principal(maker_id, tenant_id, "analyst"), Principal(checker_id, tenant_id, "approver")

    async def vote():
        try:
            async with session() as db:
                await decide(db, checker, approval_id, "APPROVE", "Verified concurrent review", 1)
            return "accepted"
        except DomainError:
            return "denied"

    votes = await asyncio.gather(vote(), vote())
    assert sorted(votes) == ["accepted", "denied"], votes

    async def apply():
        async with session() as db:
            return await execute(db, maker, approval_id)

    executions = await asyncio.gather(apply(), apply())
    assert executions[0]["id"] == executions[1]["id"]
    async with session() as db:
        count = await db.scalar(
            select(func.count()).select_from(ActionExecution).where(ActionExecution.action_id == action_id)
        )
        log = await db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant_id))
        audit_id = log.id
    assert count == 1
    blocked = []
    for table, id_value in [("audit_logs", audit_id), ("transactions", original.id)]:
        try:
            async with session() as db:
                await db.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": id_value})
        except DBAPIError:
            blocked.append(table)
    assert set(blocked) == {"audit_logs", "transactions"}
    print(
        json.dumps(
            {
                "postgresql": True,
                "concurrent_votes": votes,
                "concurrent_execution_rows": count,
                "execution_ids_equal": True,
                "immutable_delete_blocked": blocked,
            },
            indent=2,
        )
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
