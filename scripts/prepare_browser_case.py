"""Isolated browser-only duplicate case, without altering the 10,000-record demo tenant."""

import asyncio
from decimal import Decimal

from ledgerguard.config import settings
from ledgerguard.db import engine, session
from ledgerguard.models import Account, BankEntry, BankStatement, LedgerEntry, Tenant, Transaction, User, now
from ledgerguard.security import passwords


async def main():
    if not settings().demo_mode or settings().app_env == "production":
        raise RuntimeError("Browser fixture is restricted to the development sandbox")
    async with session() as db:
        tenant_id = "tenant-browser-review"
        if await db.get(Tenant, tenant_id):
            print("Browser tenant already exists; reset sandbox to repeat its initial approval scenario")
            return
        db.add(Tenant(id=tenant_id, name="Browser verification sandbox"))
        await db.flush()
        for name, role in [("maker", "analyst"), ("reviewer", "approver")]:
            db.add(
                User(
                    id=f"browser-{name}",
                    tenant_id=tenant_id,
                    email=f"ui-{name}@ledgerguard.demo",
                    name=f"Browser {name}",
                    role=role,
                    password_hash=passwords.hash("SandboxDemo!2026"),
                )
            )
        account = Account(id="ACC-BROWSER", tenant_id=tenant_id, name="Browser GBP sandbox", currency="GBP")
        db.add(account)
        await db.flush()
        statement = BankStatement(
            id="STMT-BROWSER", tenant_id=tenant_id, account_id=account.id, name="Browser statement"
        )
        stamp = now().replace(microsecond=0)
        sources = [
            Transaction(
                id=f"BROWSER-TXN-{i}",
                tenant_id=tenant_id,
                source_id=f"BROWSER-{i}",
                account_id=account.id,
                amount=Decimal("-250.00"),
                currency="GBP",
                reference="UI-DUP-001",
                booked_at=stamp,
                description="Synthetic browser duplicate proof",
            )
            for i in (1, 2)
        ]
        db.add_all([statement, *sources])
        await db.flush()
        db.add_all(LedgerEntry(tenant_id=tenant_id, transaction_id=tx.id) for tx in sources)
        db.add(
            BankEntry(
                tenant_id=tenant_id,
                statement_id=statement.id,
                source_id="BROWSER-BANK-1",
                amount=Decimal("-250.00"),
                currency="GBP",
                reference="UI-DUP-001",
                booked_at=stamp,
            )
        )
    print("Isolated browser approval scenario prepared")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
