import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sqlalchemy import select

from ledgerguard.config import settings
from ledgerguard.db import DomainError, owned, public
from ledgerguard.models import ModelVersion, RiskAlert, RiskAssessment, RiskRule, Transaction, now
from ledgerguard.security import WRITE_ROLES
from ledgerguard.transactions import filtered


def feature_matrix(rows, history, activity=None):
    """Float features are statistical inputs only, never ledger arithmetic."""
    historical = defaultdict(list)
    for row in history:
        historical[row.account_id].append(float(abs(row.amount)))
    baselines = {
        account: (float(np.median(values)), float(np.std(values)) + 1.0)
        for account, values in historical.items()
    }
    activity = rows if activity is None else activity
    frequency = Counter((r.account_id, r.booked_at.date(), r.booked_at.hour) for r in activity)
    beneficiaries = Counter((r.account_id, r.counterparty_id) for r in history)
    vectors = []
    for row in rows:
        amount = float(abs(row.amount))
        median, std = baselines.get(row.account_id, (1000.0, 500.0))
        hour = row.booked_at.hour
        vectors.append(
            [
                np.log1p(amount),
                amount / max(median, 1),
                (amount - median) / std,
                frequency[(row.account_id, row.booked_at.date(), hour)],
                np.sin(2 * np.pi * hour / 24),
                np.cos(2 * np.pi * hour / 24),
                beneficiaries[(row.account_id, row.counterparty_id)],
            ]
        )
    return np.array(vectors, dtype=np.float64).reshape((-1, 7))


async def train_model(db, tenant_id="tenant-demo"):
    # Only explicitly labelled historical records enter training; evaluation IDs are excluded.
    rows = (
        await db.scalars(
            select(Transaction).where(Transaction.tenant_id == tenant_id).order_by(Transaction.id)
        )
    ).all()
    history = [t for t in rows if t.labels.get("split") == "training"]
    if len(history) < 100:
        raise DomainError("At least 100 historical training rows required")
    features = feature_matrix(history, history)
    model = IsolationForest(n_estimators=100, contamination=0.03, random_state=42, n_jobs=1)
    model.fit(features)
    source_hash = hashlib.sha256("|".join(t.id for t in history).encode()).hexdigest()
    model_hash = hashlib.sha256(
        features.tobytes() + source_hash.encode() + b"iforest-v1:100:0.03:42"
    ).hexdigest()
    version = f"iforest-v1-{model_hash[:12]}"
    path = Path(settings().artifacts_dir) / f"{version}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    details = {
        "train_count": len(history),
        "training_id_hash": source_hash,
        "training_feature_hash": model_hash,
        "seed": 42,
        "features": [
            "log_amount",
            "relative_amount",
            "deviation",
            "hourly_frequency",
            "hour_sin",
            "hour_cos",
            "beneficiary_history",
        ],
        "evaluation_excluded": True,
        "trained_at": now().isoformat(),
    }
    path.with_suffix(".json").write_text(json.dumps(details, indent=2))
    existing = await db.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if not existing:
        db.add(ModelVersion(tenant_id=tenant_id, version=version, artifact_path=str(path), details=details))
    else:
        # Re-training can move an artifact between Windows and Linux or storage volumes.
        existing.artifact_path = str(path)
        existing.details = details
    return {"version": version, **details}


def evaluate_rules(row, context, config):
    from decimal import Decimal

    rules = []
    weights = config["weights"]
    if abs(row.amount) >= Decimal(str(config["high_value"])):
        rules.append("high_value")
    if context["hourly"] >= config["velocity"]:
        rules.append("velocity")
    if row.booked_at.hour < 7 or row.booked_at.hour > 20:
        rules.append("unusual_hour")
    if context["beneficiary_seen"] == 0:
        rules.append("new_beneficiary")
    if context["duplicate_count"] > 1:
        rules.append("duplicate")
    if context["relative"] > 5:
        rules.append("historical_deviation")
    if context["volume_ratio"] > 5:
        rules.append("volume_increase")
    if context["anomalous"]:
        rules.append("anomaly")
    score = min(100, sum(weights.get(rule, 0) for rule in rules))
    return score, rules


async def analyse(db, principal, filters, threshold=60, transaction_id=None):
    principal.require(WRITE_ROLES)
    from ledgerguard.reconciliation import advisory_lock

    await advisory_lock(db, f"risk:{principal.tenant_id}")
    q = filtered(principal.tenant_id, filters)
    if transaction_id:
        await owned(db, Transaction, transaction_id, principal.tenant_id)
        q = q.where(Transaction.id == transaction_id)
    rows = (await db.scalars(q.order_by(Transaction.id))).all()
    all_rows = (
        await db.scalars(select(Transaction).where(Transaction.tenant_id == principal.tenant_id))
    ).all()
    history = [t for t in all_rows if t.labels.get("split") == "training"]
    config_row = await db.scalar(
        select(RiskRule)
        .where(RiskRule.tenant_id == principal.tenant_id)
        .order_by(RiskRule.version.desc())
        .limit(1)
    )
    config = (
        config_row.config
        if config_row
        else {
            "high_value": settings().high_value_threshold,
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
        }
    )
    model_meta = await db.scalar(
        select(ModelVersion)
        .where(ModelVersion.tenant_id == principal.tenant_id)
        .order_by(ModelVersion.created_at.desc())
        .limit(1)
    )
    model = None
    if model_meta and Path(model_meta.artifact_path).is_file() and len(history) >= 100:
        # Model artifacts are administrator-generated local files, never uploaded pickles.
        model = joblib.load(model_meta.artifact_path)
    policy_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:10]
    context_hash = hashlib.sha256("|".join(sorted(t.id for t in all_rows)).encode()).hexdigest()[:10]
    version = f"{model_meta.version if model is not None else 'rules-only'}:{policy_hash}:{context_hash}"
    predictions = (
        model.predict(feature_matrix(rows, history, all_rows))
        if rows and model is not None
        else [1] * len(rows)
    )
    hourly = Counter((r.account_id, r.booked_at.date(), r.booked_at.hour) for r in all_rows)
    duplicate = Counter(
        (r.account_id, r.reference, r.amount, r.currency, r.booked_at.date()) for r in all_rows
    )
    beneficiary = Counter((r.account_id, r.counterparty_id) for r in history)
    hist_amounts = defaultdict(list)
    hist_days = defaultdict(set)
    for row in history:
        hist_amounts[row.account_id].append(float(abs(row.amount)))
        hist_days[row.account_id].add(row.booked_at.date())
    current_daily = Counter((r.account_id, r.booked_at.date()) for r in all_rows)
    existing = {
        a.transaction_id: a
        for a in (
            await db.scalars(
                select(RiskAssessment).where(
                    RiskAssessment.tenant_id == principal.tenant_id, RiskAssessment.model_version == version
                )
            )
        ).all()
    }
    alert_ids = set(
        (
            await db.scalars(
                select(RiskAlert.assessment_id).where(RiskAlert.tenant_id == principal.tenant_id)
            )
        ).all()
    )
    categories: Counter[str] = Counter()
    findings = []
    pending_alerts = []
    monthly: dict[str, Counter[str]] = defaultdict(Counter)
    for row, prediction in zip(rows, predictions, strict=True):
        values = hist_amounts[row.account_id]
        median = float(np.median(values)) if values else 1000.0
        daily_avg = len(values) / max(len(hist_days[row.account_id]), 1)
        context = {
            "hourly": hourly[(row.account_id, row.booked_at.date(), row.booked_at.hour)],
            "beneficiary_seen": beneficiary[(row.account_id, row.counterparty_id)],
            "duplicate_count": duplicate[
                (row.account_id, row.reference, row.amount, row.currency, row.booked_at.date())
            ],
            "relative": float(abs(row.amount)) / max(median, 1),
            "volume_ratio": current_daily[(row.account_id, row.booked_at.date())] / max(daily_avg, 1),
            "anomalous": bool(prediction == -1),
            "historical_count": len(values),
        }
        score, rules = evaluate_rules(row, context, config)
        category = "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW"
        categories[category] += 1
        monthly[row.booked_at.strftime("%Y-%m")][category] += 1
        assessment = existing.get(row.id)
        if not assessment:
            from ledgerguard.models import uid

            assessment = RiskAssessment(
                id=uid(),
                tenant_id=principal.tenant_id,
                transaction_id=row.id,
                score=score,
                category=category,
                rules=rules,
                evidence={
                    **context,
                    "amount": str(row.amount),
                    "currency": row.currency,
                    "notice": "Risk indicators require investigation; not proven fraud.",
                },
                model_version=version,
            )
            db.add(assessment)
        if score >= 60 and assessment.id not in alert_ids:
            pending_alerts.append(
                RiskAlert(tenant_id=principal.tenant_id, assessment_id=assessment.id, severity=category)
            )
            alert_ids.add(assessment.id)
        if score >= threshold:
            findings.append(
                {
                    "assessment_id": assessment.id,
                    "transaction_id": row.id,
                    "score": score,
                    "category": category,
                    "rules": rules,
                    "evidence": assessment.evidence,
                }
            )
    await db.flush()
    db.add_all(pending_alerts)
    await db.flush()
    return {
        "analysed": len(rows),
        "counts": dict(categories),
        "findings": findings[:200],
        "finding_count": len(findings),
        "model_version": version,
        "ml_available": model is not None,
        "monthly_categories": {month: dict(counts) for month, counts in sorted(monthly.items())},
        "limitation": "Statistical indicators, not a fraud or AML determination",
    }


async def create_alert(db, principal, assessment_id):
    principal.require(WRITE_ROLES)
    assessment = await owned(db, RiskAssessment, assessment_id, principal.tenant_id)
    existing = await db.scalar(
        select(RiskAlert).where(
            RiskAlert.assessment_id == assessment.id, RiskAlert.tenant_id == principal.tenant_id
        )
    )
    if existing:
        return public(existing)
    alert = RiskAlert(
        tenant_id=principal.tenant_id, assessment_id=assessment.id, severity=assessment.category
    )
    db.add(alert)
    await db.flush()
    return public(alert)
