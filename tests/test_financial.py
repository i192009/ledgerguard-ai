from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from ledgerguard.imports import parse_csv
from ledgerguard.reconciliation import match_records
from ledgerguard.risk import evaluate_rules, feature_matrix
from ledgerguard.schemas import ImportRow, TransactionFilters


def record(id="a", amount="-100.01", currency="GBP", reference="REF", day=0, group=None):
    return SimpleNamespace(
        id=id,
        amount=Decimal(amount),
        currency=currency,
        reference=reference,
        account_id="A",
        counterparty_id="C",
        booked_at=datetime(2026, 1, 1, 10, tzinfo=UTC) + timedelta(days=day),
        description="Synthetic payment",
        group_reference=group,
    )


def test_exact_decimal_not_float():
    assert sum([Decimal("0.1")] * 10) == Decimal("1.0")
    result = match_records([record()], [record("bank")])
    assert result["summary"]["matched_ledger_records"] == 1


@pytest.mark.parametrize(
    "ledger,bank,kind",
    [
        (record(), record("b", currency="USD"), "CURRENCY_MISMATCH"),
        (record(), record("b", amount="-50"), "PARTIAL_SETTLEMENT"),
        (record(), record("b", amount="-150"), "AMOUNT_MISMATCH"),
        (record(), record("b", day=5), "DATE_DISCREPANCY"),
        (record(), record("b", amount="100.01"), "AMOUNT_MISMATCH"),
    ],
)
def test_exception_classification(ledger, bank, kind):
    result = match_records([ledger], [bank])
    assert result["exceptions"][0]["kind"] == kind
    assert not result["matches"]


def test_delayed_settlement_within_policy():
    assert match_records([record()], [record("b", day=2)])["matches"][0]["rule"] == "SETTLEMENT_TOLERANT"


def test_explicit_split_settlement():
    result = match_records(
        [record(amount="-100")],
        [record("b1", amount="-60", group="REF"), record("b2", amount="-40", group="REF")],
    )
    assert result["summary"]["matched_bank_records"] == 2
    assert result["summary"]["matched_ledger_records"] == 1


def test_explicit_aggregate_settlement():
    result = match_records(
        [record("a1", amount="-60"), record("a2", amount="-40")], [record("b", amount="-100", group="REF")]
    )
    assert result["summary"]["matched_ledger_records"] == 2


def test_implicit_partial_aggregation_is_not_accepted():
    result = match_records([record(amount="-100")], [record("b1", amount="-60"), record("b2", amount="-40")])
    assert not result["matches"]
    assert result["exceptions"][0]["kind"] == "AMBIGUOUS"


def test_duplicate_matched_once_and_evidence_preserved():
    result = match_records([record("a1"), record("a2")], [record("b")])
    assert result["summary"]["matched_ledger_records"] == 1
    assert result["exceptions"][0]["kind"] == "DUPLICATE"
    assert result["exceptions"][0]["evidence"]["original_id"] == "a1"


def test_ambiguous_two_to_two_not_automatically_matched():
    result = match_records([record("a1"), record("a2")], [record("b1"), record("b2")])
    assert not result["matches"]
    assert result["exceptions"][0]["kind"] == "AMBIGUOUS"


def test_missing_bank_and_fee():
    result = match_records([record()], [record("fee", amount="-12.50", reference="BANK-FEE")])
    assert {x["kind"] for x in result["exceptions"]} == {"MISSING_BANK", "BANK_FEE"}


def test_fuzzy_candidate_requires_review():
    result = match_records([record()], [record("b", reference="DIFFERENT")])
    assert not result["matches"]
    assert result["exceptions"][0]["kind"] == "SUGGESTED_MATCH"
    assert result["exceptions"][0]["evidence"]["requires_manual_review"]


def test_reconciliation_repeatable():
    a, b = [record()], [record("bank")]
    assert match_records(a, b) == match_records(a, b)


def test_features_do_not_use_synthetic_ground_truth():
    rows = [record(), record("b", amount="-25000")]
    features = feature_matrix(rows, [record("h")] * 120)
    assert features.shape == (2, 7)
    assert np.isfinite(features).all()
    assert features[1, 1] > features[0, 1]


def test_risk_weights_and_score_clamp():
    row = record(amount="-20000")
    context = {
        "hourly": 100,
        "beneficiary_seen": 0,
        "duplicate_count": 2,
        "relative": 20,
        "volume_ratio": 20,
        "anomalous": True,
    }
    config = {
        "high_value": "10000",
        "velocity": 20,
        "weights": dict.fromkeys(
            [
                "high_value",
                "velocity",
                "new_beneficiary",
                "duplicate",
                "historical_deviation",
                "volume_increase",
                "anomaly",
            ],
            25,
        ),
    }
    score, rules = evaluate_rules(row, context, config)
    assert score == 100 and "high_value" in rules and "duplicate" in rules


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page": 0},
        {"page_size": 201},
        {"start_date": "2026-02-02T00:00:00Z", "end_date": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_filters(kwargs):
    with pytest.raises(ValidationError):
        TransactionFilters(**kwargs)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "1.001"])
def test_invalid_money(amount):
    with pytest.raises(ValidationError):
        ImportRow(
            source_id="a",
            account_id="a",
            amount=amount,
            currency="GBP",
            reference="x",
            booked_at="2026-01-01T00:00:00Z",
        )


def test_csv_rejects_invalid_schema_and_encoding():
    from ledgerguard.db import DomainError

    for data in (b"wrong,columns\n1,2", b"\xff\xff"):
        with pytest.raises(DomainError):
            parse_csv(data)


async def test_single_and_batch_risk_share_identical_context(analyst):
    from sqlalchemy import select

    from ledgerguard.db import session
    from ledgerguard.models import RiskAssessment
    from ledgerguard.risk import analyse

    async with session() as db:
        single = await analyse(db, analyst, TransactionFilters(), 0, "TXN-000001")
    async with session() as db:
        from ledgerguard.models import now

        batch = await analyse(
            db,
            analyst,
            TransactionFilters(start_date=now().replace(hour=0, minute=0, second=0, microsecond=0)),
            0,
        )
        persisted = await db.scalar(
            select(RiskAssessment).where(
                RiskAssessment.tenant_id == analyst.tenant_id,
                RiskAssessment.transaction_id == "TXN-000001",
                RiskAssessment.model_version == single["model_version"],
            )
        )
    finding = next(row for row in batch["findings"] if row["transaction_id"] == "TXN-000001")
    assert finding["score"] == single["findings"][0]["score"] == persisted.score
    assert finding["rules"] == single["findings"][0]["rules"] == persisted.rules
    assert finding["evidence"] == single["findings"][0]["evidence"] == persisted.evidence


async def test_import_idempotence_rejects_changed_booking_date(governed_case):
    from ledgerguard.db import DomainError, session
    from ledgerguard.imports import import_rows
    from ledgerguard.models import Transaction, now, uid

    c = governed_case
    async with session() as db:
        source = await db.get(Transaction, c["original"])
        row = {
            "source_id": uid(),
            "account_id": source.account_id,
            "amount": "-123.45",
            "currency": "GBP",
            "reference": "IMPORT-EXACT",
            "booked_at": now().isoformat(),
            "description": "Synthetic import",
        }
        first = await import_rows(db, c["maker"], [row])
    async with session() as db:
        replay = await import_rows(db, c["maker"], [row])
    assert first == {"added": 1, "skipped": 0} and replay == {"added": 0, "skipped": 1}
    with pytest.raises(DomainError, match="different financial data"):
        async with session() as db:
            await import_rows(db, c["maker"], [{**row, "booked_at": (now() + timedelta(days=1)).isoformat()}])
