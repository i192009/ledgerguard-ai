import csv
import io

from pydantic import ValidationError
from sqlalchemy import select

from ledgerguard.db import DomainError, owned
from ledgerguard.models import Account, BankEntry, BankStatement, LedgerEntry, Transaction
from ledgerguard.schemas import ImportRow
from ledgerguard.security import WRITE_ROLES, audit

MAX_BYTES = 5 * 1024 * 1024


def parse_csv(content):
    if len(content) > MAX_BYTES:
        raise DomainError("CSV exceeds 5 MiB limit", 413)
    try:
        decoded = content.decode("utf-8-sig", errors="strict")
        reader = csv.DictReader(io.StringIO(decoded))
        required = {"source_id", "account_id", "amount", "currency", "reference", "booked_at"}
        if not required <= set(reader.fieldnames or []):
            raise DomainError("CSV requires " + ", ".join(sorted(required)))
        rows = []
        for index, row in enumerate(reader, 2):
            if index > 20001:
                raise DomainError("Maximum 20,000 rows per import", 413)
            try:
                rows.append(ImportRow.model_validate(row))
            except ValidationError as exc:
                raise DomainError(f"Invalid CSV row {index}: {exc.errors()[0]['msg']}") from exc
        if not rows:
            raise DomainError("CSV contains no records")
        identifiers = [r.source_id for r in rows]
        if len(set(identifiers)) != len(identifiers):
            raise DomainError("Duplicate source IDs within CSV")
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise DomainError("CSV must use valid UTF-8 encoding and CSV syntax") from exc


async def import_rows(db, principal, raw_rows, kind="transactions", statement_id=None):
    principal.require(WRITE_ROLES)
    rows = [ImportRow.model_validate(row) for row in raw_rows]
    accounts = {}
    for account_id in {r.account_id for r in rows}:
        accounts[account_id] = await owned(db, Account, account_id, principal.tenant_id)
    statement = None
    if kind == "bank":
        statement = await owned(db, BankStatement, statement_id, principal.tenant_id)
    added, skipped = 0, 0
    for row in rows:
        if row.currency != accounts[row.account_id].currency:
            raise DomainError("Imported currency must match account currency")
        if statement and statement.account_id != row.account_id:
            raise DomainError("Bank entry account does not match statement")
        model = BankEntry if statement else Transaction
        q = select(model).where(model.tenant_id == principal.tenant_id, model.source_id == row.source_id)
        if statement:
            q = q.where(BankEntry.statement_id == statement.id)
        existing = await db.scalar(q)
        if existing:
            from ledgerguard.security import aware

            if (
                (existing.amount, existing.currency, existing.reference)
                != (
                    row.amount,
                    row.currency,
                    row.reference,
                )
                or aware(existing.booked_at) != row.booked_at
                or (not statement and existing.account_id != row.account_id)
            ):
                raise DomainError("Source ID already exists with different financial data", 409)
            skipped += 1
            continue
        fields = row.model_dump()
        entity: BankEntry | Transaction
        if statement:
            fields.pop("account_id")
            entity = BankEntry(tenant_id=principal.tenant_id, statement_id=statement.id, **fields)
        else:
            entity = Transaction(
                tenant_id=principal.tenant_id, labels={"synthetic": True, "split": "import"}, **fields
            )
        db.add(entity)
        await db.flush()
        if not statement:
            db.add(LedgerEntry(tenant_id=principal.tenant_id, transaction_id=entity.id))
        added += 1
    audit(db, principal, "CSV_IMPORTED", statement_id or "transactions", {"added": added, "skipped": skipped})
    return {"added": added, "skipped": skipped}
