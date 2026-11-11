import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select, text

from ledgerguard.config import settings
from ledgerguard.db import DomainError, owned, public
from ledgerguard.models import (
    BankEntry,
    BankStatement,
    LedgerEntry,
    ProposedAction,
    ReconciliationException,
    ReconciliationJob,
    ReconciliationMatch,
    Transaction,
    uid,
)
from ledgerguard.schemas import TransactionFilters
from ledgerguard.security import WRITE_ROLES, audit
from ledgerguard.transactions import filtered


def action_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def advisory_lock(db, key):
    if db.bind.dialect.name == "postgresql":
        number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})


def match_records(ledger, banks, tolerance=3):
    """Conservative one-to-one or explicitly grouped exact-sum matching."""
    matches, exceptions = [], []
    remaining_l = {t.id: t for t in ledger}
    remaining_b = {b.id: b for b in banks}
    lg, bg = defaultdict(list), defaultdict(list)
    for row in ledger:
        lg[row.reference].append(row)
    for row in banks:
        bg[row.reference].append(row)

    def accept(ls, bs, rule):
        matches.append(
            {
                "ledger_ids": [r.id for r in ls],
                "bank_ids": [r.id for r in bs],
                "rule": rule,
                "explanation": "Exact signed Decimal amount and currency; trusted reference and settlement window",
            }
        )
        for row in ls:
            remaining_l.pop(row.id, None)
        for row in bs:
            remaining_b.pop(row.id, None)

    def close(a, b):
        return abs((a.booked_at.date() - b.booked_at.date()).days) <= tolerance

    for ref, ls in lg.items():
        bs = bg.get(ref, [])
        if not bs:
            continue
        # Explicit bank group authorization is required to sum multiple source records.
        if (len(ls) > 1 or len(bs) > 1) and all(b.group_reference == ref for b in bs):
            currencies = {r.currency for r in ls + bs}
            if (
                len(currencies) == 1
                and sum((r.amount for r in ls), Decimal(0)) == sum((r.amount for r in bs), Decimal(0))
                and all(close(a, b) for a in ls for b in bs)
            ):
                accept(ls, bs, "EXPLICIT_GROUP_EXACT_SUM")
                continue
        if len(ls) > 1 and len(bs) == 1:
            # Two identical immutable entries and only one settlement: retain original, propose review.
            same = all(
                (r.amount, r.currency, r.booked_at.date(), r.account_id, r.counterparty_id)
                == (
                    ls[0].amount,
                    ls[0].currency,
                    ls[0].booked_at.date(),
                    ls[0].account_id,
                    ls[0].counterparty_id,
                )
                for r in ls
            )
            if (
                same
                and ls[0].amount == bs[0].amount
                and ls[0].currency == bs[0].currency
                and close(ls[0], bs[0])
            ):
                ordered = sorted(ls, key=lambda r: (r.booked_at, r.id))
                accept([ordered[0]], bs, "REFERENCE_EXACT_ORIGINAL")
                for duplicate in ordered[1:]:
                    exceptions.append(
                        {
                            "kind": "DUPLICATE",
                            "evidence": {
                                "transaction_id": duplicate.id,
                                "original_id": ordered[0].id,
                                "bank_id": bs[0].id,
                                "reference": ref,
                                "amount": str(duplicate.amount),
                                "currency": duplicate.currency,
                            },
                        }
                    )
                continue
        if len(ls) != 1 or len(bs) != 1:
            exceptions.append(
                {
                    "kind": "AMBIGUOUS",
                    "evidence": {
                        "ledger_ids": [r.id for r in ls],
                        "bank_ids": [r.id for r in bs],
                        "reference": ref,
                    },
                }
            )
            continue
        a, b = ls[0], bs[0]
        if a.currency != b.currency:
            kind = "CURRENCY_MISMATCH"
        elif a.amount != b.amount:
            kind = (
                "PARTIAL_SETTLEMENT"
                if a.amount * b.amount > 0 and abs(b.amount) < abs(a.amount)
                else "AMOUNT_MISMATCH"
            )
        elif not close(a, b):
            kind = "DATE_DISCREPANCY"
        else:
            accept(
                ls,
                bs,
                "REFERENCE_EXACT" if a.booked_at.date() == b.booked_at.date() else "SETTLEMENT_TOLERANT",
            )
            continue
        exceptions.append(
            {
                "kind": kind,
                "evidence": {
                    "transaction_id": a.id,
                    "bank_id": b.id,
                    "reference": ref,
                    "ledger_amount": str(a.amount),
                    "bank_amount": str(b.amount),
                    "ledger_currency": a.currency,
                    "bank_currency": b.currency,
                },
            }
        )
    # Strong unique amount/date candidates are suggestions; descriptions never authorize posting.
    for row in remaining_l.values():
        if row.reference in bg:
            continue
        candidates = [
            b
            for b in remaining_b.values()
            if b.currency == row.currency and b.amount == row.amount and close(row, b)
        ]
        exceptions.append(
            {
                "kind": "SUGGESTED_MATCH" if candidates else "MISSING_BANK",
                "evidence": {
                    "transaction_id": row.id,
                    "reference": row.reference,
                    "candidate_ids": [b.id for b in candidates[:10]],
                    "similarity": [
                        round(SequenceMatcher(None, row.description, b.description).ratio(), 3)
                        for b in candidates[:10]
                    ],
                    "requires_manual_review": True,
                },
            }
        )
    classified_b = {b.id for ref, group in bg.items() if ref in lg for b in group}
    for row in remaining_b.values():
        if row.id in classified_b:
            continue
        exceptions.append(
            {
                "kind": "BANK_FEE" if row.reference == "BANK-FEE" else "MISSING_LEDGER",
                "evidence": {
                    "bank_id": row.id,
                    "reference": row.reference,
                    "amount": str(row.amount),
                    "currency": row.currency,
                },
            }
        )
    return {
        "matches": matches,
        "exceptions": exceptions,
        "summary": {
            "ledger_records": len(ledger),
            "bank_records": len(banks),
            "matched_ledger_records": len(ledger) - len(remaining_l),
            "matched_bank_records": len(banks) - len(remaining_b),
            "match_groups": len(matches),
            "unmatched_ledger_records": len(remaining_l),
            "unmatched_bank_records": len(remaining_b),
            "exception_count": len(exceptions),
        },
    }


async def reconcile(db, principal, statement_id, account_id, filters):
    principal.require(WRITE_ROLES)
    statement = await owned(db, BankStatement, statement_id, principal.tenant_id)
    if statement.account_id != account_id:
        raise DomainError("Statement does not belong to account")
    filters = TransactionFilters(**{**filters.model_dump(), "account_id": account_id})
    ledger = (
        await db.scalars(
            filtered(principal.tenant_id, filters)
            .join(LedgerEntry, LedgerEntry.transaction_id == Transaction.id)
            .where(LedgerEntry.active.is_(True), LedgerEntry.tenant_id == principal.tenant_id)
            .order_by(Transaction.id)
        )
    ).all()
    # Statement scope includes delayed settlement dates; ledger period is filtered separately.
    banks = (
        await db.scalars(
            select(BankEntry)
            .where(BankEntry.statement_id == statement_id, BankEntry.tenant_id == principal.tenant_id)
            .order_by(BankEntry.id)
        )
    ).all()
    snapshot = {
        "tenant": principal.tenant_id,
        "statement": statement_id,
        "ledger": [(t.id, str(t.amount), t.reference, t.currency, t.booked_at.isoformat()) for t in ledger],
        "bank": [(t.id, str(t.amount), t.reference, t.currency, t.booked_at.isoformat()) for t in banks],
        "policy": settings().settlement_tolerance_days,
    }
    key = action_digest(snapshot)
    await advisory_lock(db, f"reconcile:{key}")
    existing = await db.scalar(
        select(ReconciliationJob).where(
            ReconciliationJob.key == key, ReconciliationJob.tenant_id == principal.tenant_id
        )
    )
    if existing:
        return public(existing)
    result = match_records(ledger, banks, settings().settlement_tolerance_days)
    job = ReconciliationJob(
        id=uid(),
        tenant_id=principal.tenant_id,
        key=key,
        account_id=account_id,
        statement_id=statement_id,
        status="COMPLETED",
        summary=result["summary"],
    )
    db.add(job)
    await db.flush()
    db.add_all(
        ReconciliationMatch(tenant_id=principal.tenant_id, job_id=job.id, **match)
        for match in result["matches"]
    )
    db.add_all(
        ReconciliationException(tenant_id=principal.tenant_id, job_id=job.id, **ex)
        for ex in result["exceptions"]
    )
    audit(db, principal, "RECONCILIATION_COMPLETED", job.id, job.summary)
    await db.flush()
    return public(job)


async def propose(db, principal, job_id):
    principal.require(WRITE_ROLES)
    await owned(db, ReconciliationJob, job_id, principal.tenant_id)
    await advisory_lock(db, f"proposal:{job_id}")
    exceptions = (
        await db.scalars(
            select(ReconciliationException).where(
                ReconciliationException.tenant_id == principal.tenant_id,
                ReconciliationException.job_id == job_id,
                ReconciliationException.kind == "DUPLICATE",
            )
        )
    ).all()
    proposals = []
    for ex in exceptions:
        ledger = await db.scalar(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == principal.tenant_id,
                LedgerEntry.transaction_id == ex.evidence["transaction_id"],
            )
        )
        if not ledger or not ledger.active:
            continue
        existing = await db.scalar(
            select(ProposedAction).where(
                ProposedAction.tenant_id == principal.tenant_id,
                ProposedAction.job_id == job_id,
                ProposedAction.target_id == ledger.id,
            )
        )
        if existing:
            proposals.append(public(existing))
            continue
        payload = {
            **ex.evidence,
            "ledger_id": ledger.id,
            "ledger_version": ledger.version,
            "exception_id": ex.id,
            "job_id": job_id,
            "type": "VOID_DUPLICATE",
            "version": 1,
        }
        action = ProposedAction(
            tenant_id=principal.tenant_id,
            job_id=job_id,
            maker_id=principal.user_id,
            target_id=ledger.id,
            payload=payload,
            digest=action_digest(payload),
            action_type="VOID_DUPLICATE",
        )
        db.add(action)
        await db.flush()
        audit(db, principal, "ACTION_PROPOSED", action.id, payload)
        proposals.append(public(action))
    return {"items": proposals}
