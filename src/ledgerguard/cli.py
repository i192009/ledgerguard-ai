import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import func, select

from ledgerguard.db import engine, session
from ledgerguard.models import Account, BankEntry, LedgerEntry, Transaction


async def execute(args):
    result: dict[str, Any]
    try:
        if args.command == "reset":
            from sqlalchemy import delete, inspect, text

            from ledgerguard.config import settings
            from ledgerguard.models import Base

            if (
                not settings().demo_mode
                or settings().app_env == "production"
                or not args.confirm_sandbox_reset
            ):
                raise ValueError("Reset requires development DEMO_MODE and --confirm-sandbox-reset")
            async with engine.begin() as connection:
                if engine.dialect.name == "postgresql":
                    existing = await connection.run_sync(lambda c: inspect(c).get_table_names())
                    names = list(Base.metadata.tables) + [
                        name
                        for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
                        if name in existing
                    ]
                    await connection.execute(
                        text("TRUNCATE " + ", ".join('"' + name + '"' for name in names) + " CASCADE")
                    )
                else:
                    for table in reversed(Base.metadata.sorted_tables):
                        await connection.execute(delete(table))
            if engine.dialect.name == "sqlite":
                from ledgerguard.orchestrator import checkpointer

                async with checkpointer() as saver:
                    async for checkpoint in saver.alist(None):
                        await saver.adelete_thread(checkpoint.config["configurable"]["thread_id"])
            result = {"reset": "sandbox application records cleared; migrations retained"}
        elif args.command == "seed":
            from ledgerguard.seed import seed

            async with session() as db:
                result = await seed(db, args.count, args.seed, anomaly_rate=args.anomaly_rate)
        elif args.command == "train":
            from ledgerguard.risk import train_model

            async with session() as db:
                result = await train_model(db)
        elif args.command == "checkpoints":
            from ledgerguard.orchestrator import setup_checkpoints

            await setup_checkpoints()
            result = {"checkpoints": "ready"}
        elif args.command == "validate":
            async with session() as db:
                rows = (await db.scalars(select(Transaction))).all()
                bad = [r.id for r in rows if not r.amount.is_finite() or len(r.currency) != 3]
                sources = {(r.tenant_id, r.source_id) for r in rows}
                balances = {}
                from decimal import Decimal

                for account in (await db.scalars(select(Account))).all():
                    amounts = (
                        await db.scalars(
                            select(Transaction.amount)
                            .join(LedgerEntry, LedgerEntry.transaction_id == Transaction.id)
                            .where(
                                Transaction.account_id == account.id,
                                Transaction.tenant_id == account.tenant_id,
                                LedgerEntry.active.is_(True),
                            )
                        )
                    ).all()
                    balances[account.id] = {
                        "opening": str(account.opening_balance),
                        "closing": str(account.opening_balance + sum(amounts, Decimal(0))),
                        "currency": account.currency,
                    }
                if bad or len(sources) != len(rows):
                    raise ValueError("Dataset validation failed")
                result = {
                    "transactions": len(rows),
                    "unique_source_ids": len(sources),
                    "bank_entries": await db.scalar(select(func.count()).select_from(BankEntry)),
                    "balances": balances,
                    "valid": True,
                }
        elif args.command == "bootstrap":
            from ledgerguard.config import settings
            from ledgerguard.orchestrator import setup_checkpoints
            from ledgerguard.risk import train_model
            from ledgerguard.seed import seed

            if settings().demo_mode:
                async with session() as db:
                    result = await seed(db, settings().seed_transaction_count)
                async with session() as db:
                    await train_model(db)
            else:
                result = {"demo_mode": False, "seeded": False}
            await setup_checkpoints()
        else:
            raise ValueError("Unsupported command")
        print(json.dumps(result, indent=2, default=str))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="LedgerGuard sandbox maintenance")
    parser.add_argument("command", choices=["seed", "train", "validate", "checkpoints", "bootstrap", "reset"])
    parser.add_argument("--confirm-sandbox-reset", action="store_true")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--anomaly-rate", type=float, default=0.02)
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
