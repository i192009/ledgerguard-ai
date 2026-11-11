import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ledgerguard.config import settings
from ledgerguard.db import DomainError
from ledgerguard.models import (
    Account,
    ApprovalPolicy,
    BankEntry,
    BankStatement,
    Counterparty,
    LedgerEntry,
    Membership,
    RiskRule,
    Role,
    Tenant,
    Transaction,
    User,
    uid,
)
from ledgerguard.security import passwords

DEMO_PASSWORD = "SandboxDemo!2026"


async def seed(db, count=10000, random_seed=42, day=None, anomaly_rate=0.02):
    if not settings().demo_mode or settings().app_env == "production":
        raise DomainError("Seeding is restricted to development sandbox", 403)
    if await db.scalar(select(func.count()).select_from(Transaction)):
        return {
            "status": "existing",
            "transactions": await db.scalar(select(func.count()).select_from(Transaction)),
        }
    if count < 50 or not 0 <= anomaly_rate <= 0.2:
        raise DomainError("Use at least 50 records and anomaly rate between 0 and 0.2")
    rng = random.Random(random_seed)
    day = day or datetime.now(UTC).date()
    base = datetime.combine(day, datetime.min.time(), UTC)
    tenant = Tenant(id="tenant-demo", name="Meridian Capital · Sandbox")
    db.add(tenant)
    await db.flush()
    roles = {}
    for role in ("admin", "analyst", "risk_analyst", "approver", "auditor"):
        obj = Role(id=f"role-{role}", name=role)
        db.add(obj)
        roles[role] = obj.id
    await db.flush()
    for name, role in (
        ("analyst", "analyst"),
        ("approver", "approver"),
        ("checker", "approver"),
        ("admin", "admin"),
        ("auditor", "auditor"),
    ):
        user = User(
            id=f"user-{name}",
            tenant_id=tenant.id,
            email=f"{name}@ledgerguard.demo",
            name=name.title(),
            role=role,
            password_hash=passwords.hash(DEMO_PASSWORD),
        )
        db.add(user)
        await db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role_id=roles[role]))
    accounts = []
    for i, currency in enumerate(("GBP", "GBP", "USD", "EUR")):
        account = Account(
            id=f"ACC-{1042 + i}",
            tenant_id=tenant.id,
            name=("Operating account", "Settlement reserve", "US treasury", "Euro clearing")[i],
            currency=currency,
            opening_balance=Decimal("1000000.00"),
        )
        accounts.append(account)
        db.add(account)
    for i in range(60):
        db.add(Counterparty(id=f"CP-{i:03}", tenant_id=tenant.id, name=f"Synthetic partner {i:03}"))
    await db.flush()
    statements = []
    for account in accounts:
        statement = BankStatement(
            id=f"STMT-{account.id}",
            tenant_id=tenant.id,
            account_id=account.id,
            name=f"{day} sandbox statement",
        )
        statements.append(statement)
        db.add(statement)
    await db.flush()
    transactions, ledgers, banks = [], [], []
    labels: Counter[str] = Counter()
    for i in range(count):
        account = accounts[i % len(accounts)]
        amount = Decimal(rng.randint(200, 350000)) / 100
        amount = amount if i % 5 == 0 else -amount
        booked = base + timedelta(hours=9, seconds=i % 28800)
        reference = f"PAY-{i:07}"
        label = "normal"
        cp = f"CP-{i % 50:03}"
        # The first 40 records form explicitly labelled reproducible edge cases.
        scenarios = {
            0: "high_value",
            1: "duplicate",
            2: "missing_bank",
            3: "amount_mismatch",
            4: "delayed",
            5: "partial",
            6: "split",
            7: "currency_mismatch",
            8: "refund",
            9: "reversal",
            10: "unusual_hour",
            11: "new_beneficiary",
            12: "aggregate",
            16: "aggregate",
            20: "ambiguous",
            24: "ambiguous",
        }
        label = scenarios.get(i, label)
        if 40 <= i < 160:
            label = "rapid_burst"
            booked = base + timedelta(hours=10, seconds=(i - 40) // 4)
        if i >= 160 and rng.random() < anomaly_rate:
            label = "high_value"
        if label == "high_value":
            amount = -Decimal(rng.randint(1500000, 4500000)) / 100
        if label == "unusual_hour":
            booked = base + timedelta(hours=2)
        if label == "new_beneficiary":
            cp = "CP-059"
        if label in {"refund", "reversal"}:
            amount = Decimal("345.67") * (1 if label == "refund" else -1)
            reference = "REVERSAL-PAIR"
        if label == "aggregate":
            reference, amount = "AGG-001", -Decimal("120.00") if i == 12 else -Decimal("80.00")
        if label == "ambiguous":
            reference, amount = "AMBIGUOUS-001", -Decimal("77.77")
        txid = f"TXN-{i + 1:06}"
        t = Transaction(
            id=txid,
            tenant_id=tenant.id,
            source_id=txid,
            account_id=account.id,
            counterparty_id=cp,
            amount=amount,
            currency=account.currency,
            reference=reference,
            description=f"Synthetic {label} payment",
            booked_at=booked,
            status="POSTED",
            labels={"scenario": label, "split": "evaluation", "synthetic": True},
        )
        transactions.append(t)
        ledgers.append(LedgerEntry(id=f"LED-{i + 1:06}", tenant_id=tenant.id, transaction_id=txid))
        labels[label] += 1
        if label in {"missing_bank", "duplicate"}:
            if label == "duplicate":
                t.reference = "DUPLICATE-DEMO"
                t.amount = -Decimal("250.00")
            continue
        if label == "aggregate" and i == 16:
            continue
        bank_amount = amount
        if label == "amount_mismatch":
            bank_amount += Decimal("10.00")
        if label == "partial":
            bank_amount = (amount / 2).quantize(Decimal("0.01"))
        if label == "aggregate":
            bank_amount = -Decimal("200.00")
        group = reference if label in {"split", "aggregate"} else None
        parts = [bank_amount]
        if label == "split":
            first = (amount / 2).quantize(Decimal("0.01"))
            parts = [first, amount - first]
        for part, value in enumerate(parts):
            banks.append(
                BankEntry(
                    id=uid(),
                    tenant_id=tenant.id,
                    statement_id=statements[i % 4].id,
                    source_id=f"BNK-{i:07}-{part}",
                    amount=value,
                    currency="USD" if label == "currency_mismatch" else account.currency,
                    reference=reference,
                    group_reference=group,
                    booked_at=booked + timedelta(days=2 if label == "delayed" else 0),
                    description=f"Synthetic {label} settlement",
                )
            )
    # A proven duplicate: two source records, one bank record, same immutable financial fields.
    original = transactions[1]
    replacement = transactions[13]
    replacement.account_id, replacement.amount = original.account_id, original.amount
    replacement.currency, replacement.reference = original.currency, original.reference
    replacement.booked_at, replacement.counterparty_id = original.booked_at, original.counterparty_id
    replacement.labels = {"scenario": "duplicate", "split": "evaluation", "synthetic": True}
    banks = [b for b in banks if b.source_id != "BNK-0000013-0"]
    banks.append(
        BankEntry(
            tenant_id=tenant.id,
            statement_id=statements[1].id,
            source_id="BNK-DUP-ORIGINAL",
            amount=original.amount,
            currency=original.currency,
            reference=original.reference,
            booked_at=original.booked_at,
            description="One settlement for duplicate pair",
        )
    )
    # Historical data is separately labelled and cannot overlap the evaluation period.
    for i in range(max(800, count // 5)):
        account = accounts[i % 4]
        txid = f"HIST-{i:06}"
        transactions.append(
            Transaction(
                id=txid,
                tenant_id=tenant.id,
                source_id=txid,
                account_id=account.id,
                counterparty_id=f"CP-{i % 50:03}",
                amount=-Decimal(rng.randint(500, 300000)) / 100,
                currency=account.currency,
                reference=f"HIST-REF-{i}",
                booked_at=base - timedelta(days=1 + i % 60) + timedelta(hours=9 + i % 8),
                description="Synthetic training history",
                labels={"scenario": "normal", "split": "training"},
            )
        )
        ledgers.append(LedgerEntry(tenant_id=tenant.id, transaction_id=txid))
    db.add_all(transactions)
    await db.flush()
    db.add_all(ledgers)
    db.add_all(banks)
    for statement in statements:
        db.add(
            BankEntry(
                tenant_id=tenant.id,
                statement_id=statement.id,
                source_id=f"FEE-{statement.id}",
                amount=Decimal("-12.50"),
                currency=accounts[statements.index(statement)].currency,
                reference="BANK-FEE",
                booked_at=base + timedelta(hours=17),
                description="Bank fee",
            )
        )
        db.add(
            BankEntry(
                tenant_id=tenant.id,
                statement_id=statement.id,
                source_id=f"ORPHAN-{statement.id}",
                amount=Decimal("-68.04"),
                currency=accounts[statements.index(statement)].currency,
                reference="BANK-ONLY-PAYMENT",
                booked_at=base + timedelta(hours=14),
                description="Synthetic missing-ledger scenario",
            )
        )
    db.add(
        RiskRule(
            tenant_id=tenant.id,
            name="sandbox-v1",
            config={
                "high_value": "10000",
                "velocity": 20,
                "weights": {
                    "high_value": 35,
                    "velocity": 20,
                    "unusual_hour": 15,
                    "new_beneficiary": 10,
                    "duplicate": 20,
                    "historical_deviation": 20,
                    "volume_increase": 10,
                    "anomaly": 15,
                },
            },
        )
    )
    db.add(
        ApprovalPolicy(
            tenant_id=tenant.id,
            config={
                "two_person_threshold": settings().two_person_threshold,
                "expires_hours": settings().approval_hours,
            },
        )
    )
    await db.flush()
    return {
        "status": "seeded",
        "evaluation_transactions": count,
        "history_transactions": len(transactions) - count,
        "bank_entries": len(banks) + 8,
        "seed": random_seed,
        "day": str(day),
        "scenarios": dict(
            Counter(t.labels["scenario"] for t in transactions if t.labels["split"] == "evaluation")
        ),
    }
