from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import func, or_, select

from ledgerguard.db import owned, public
from ledgerguard.models import Account, LedgerEntry, Transaction
from ledgerguard.schemas import TransactionFilters


def filtered(tenant, filters: TransactionFilters):
    q = select(Transaction).where(Transaction.tenant_id == tenant)
    for name, col in (("account_id", Transaction.account_id), ("transaction_status", Transaction.status)):
        value = getattr(filters, name)
        if value:
            q = q.where(col == value)
    if filters.start_date:
        q = q.where(Transaction.booked_at >= filters.start_date)
    if filters.end_date:
        q = q.where(Transaction.booked_at <= filters.end_date)
    if filters.min_amount is not None:
        q = q.where(func.abs(Transaction.amount) >= filters.min_amount)
    if filters.max_amount is not None:
        q = q.where(func.abs(Transaction.amount) <= filters.max_amount)
    if filters.search:
        term = "%" + filters.search.replace("%", "\\%").replace("_", "\\_") + "%"
        q = q.where(
            or_(
                *[
                    c.ilike(term, escape="\\")
                    for c in (
                        Transaction.source_id,
                        Transaction.reference,
                        Transaction.description,
                        Transaction.id,
                    )
                ]
            )
        )
    return q


async def get_transactions(db, principal, filters):
    q = filtered(principal.tenant_id, filters)
    count = await db.scalar(select(func.count()).select_from(q.subquery()))
    order = {
        "newest": Transaction.booked_at.desc(),
        "oldest": Transaction.booked_at,
        "amount": func.abs(Transaction.amount).desc(),
    }[filters.sort]
    rows = (
        await db.scalars(
            q.order_by(order, Transaction.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    ).all()
    return {
        "items": [public(t) for t in rows],
        "total": count,
        "page": filters.page,
        "page_size": filters.page_size,
    }


async def get_details(db, principal, transaction_id):
    return public(await owned(db, Transaction, transaction_id, principal.tenant_id))


async def summary(db, principal, filters):
    rows = (await db.scalars(filtered(principal.tenant_id, filters))).all()
    totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"debits": Decimal(0), "credits": Decimal(0), "volume": Decimal(0)}
    )
    for row in rows:
        t = totals[row.currency]
        t["debits" if row.amount < 0 else "credits"] += abs(row.amount)
        t["volume"] += abs(row.amount)
    currencies = {}
    for currency, sums in totals.items():
        count = sum(t.currency == currency for t in rows)
        currencies[currency] = {
            **{k: str(v) for k, v in sums.items()},
            "net": str(sums["credits"] - sums["debits"]),
            "average": str((sums["volume"] / count).quantize(Decimal("0.01"))),
            "count": count,
        }
    daily = Counter(t.booked_at.date().isoformat() for t in rows)
    return {
        "count": len(rows),
        "currencies": currencies,
        "daily": dict(sorted(daily.items())),
        "largest": public(max(rows, key=lambda t: abs(t.amount))) if rows else None,
    }


async def duplicates(db, principal, filters):
    rows = (
        await db.scalars(
            filtered(principal.tenant_id, filters).order_by(Transaction.booked_at, Transaction.id)
        )
    ).all()
    groups = defaultdict(list)
    for row in rows:
        groups[(row.account_id, row.reference, str(row.amount), row.currency, row.booked_at.date())].append(
            row
        )
    return {
        "items": [
            {
                "transaction_ids": [t.id for t in group],
                "reference": key[1],
                "amount": key[2],
                "currency": key[3],
                "criteria": "same account/reference/signed amount/currency/booking date",
            }
            for key, group in groups.items()
            if len(group) > 1
        ]
    }


async def patterns(db, principal, filters):
    rows = (await db.scalars(filtered(principal.tenant_id, filters))).all()
    import pandas as pd

    # Pandas handles temporal analytics only; monetary totals remain Decimal.
    timestamps = pd.Series(pd.to_datetime([t.booked_at for t in rows], utc=True))
    hourly = (
        {int(hour): int(count) for hour, count in timestamps.dt.hour.value_counts().items()} if rows else {}
    )
    beneficiaries = Counter(t.counterparty_id for t in rows if t.counterparty_id)
    amounts = sorted(abs(t.amount) for t in rows)
    median = amounts[len(amounts) // 2] if amounts else Decimal(0)
    return {
        "count": len(rows),
        "hourly": dict(sorted(hourly.items())),
        "beneficiaries": dict(beneficiaries.most_common(10)),
        "median_amount": str(median),
        "unusual_amount_ids": [t.id for t in rows if abs(t.amount) > max(median * 10, Decimal("10000"))][:30],
        "daily": (await summary(db, principal, filters))["daily"],
        "monthly": dict(sorted(Counter(t.booked_at.strftime("%Y-%m") for t in rows).items())),
    }


async def account_summary(db, principal, account_id):
    account = await owned(db, Account, account_id, principal.tenant_id)
    amounts = (
        await db.scalars(
            select(Transaction.amount)
            .join(LedgerEntry, LedgerEntry.transaction_id == Transaction.id)
            .where(
                Transaction.tenant_id == principal.tenant_id,
                Transaction.account_id == account_id,
                LedgerEntry.tenant_id == principal.tenant_id,
                LedgerEntry.active.is_(True),
            )
        )
    ).all()
    # Sum exact Decimal values rather than relying on SQLite's numeric aggregation.
    balance = account.opening_balance + sum(amounts, Decimal(0))
    return {
        "account": public(account),
        "balance": str(balance),
        **await summary(db, principal, TransactionFilters(account_id=account_id)),
    }
