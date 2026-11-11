# Explainable risk engine and anomaly model

Risk is an investigation priority, not a probability of fraud. The engine computes reproducible policy evidence and an optional statistical anomaly signal. It never delegates scoring to a language model.

| Signal | Definition | Weight |
|---|---|---:|
| high_value | absolute signed amount ≥ configured threshold (default 10,000) | 35 |
| velocity | account/hour count ≥ configured threshold (default 20) | 20 |
| unusual_hour | UTC hour before 07:00 or after 20:00 | 15 |
| new_beneficiary | no observed counterparty in account training history | 10 |
| duplicate | same account/reference/signed amount/currency/date repeated | 20 |
| historical_deviation | amount >5× account historical median | 20 |
| volume_increase | account/day volume >5× historical average daily count | 10 |
| anomaly | Isolation Forest predicts -1 | 15 |

Score = min(100, sum(triggered weights)). LOW: <30; MEDIUM: 30–59; HIGH: ≥60. Risk rules are versioned; the scoring configuration and immutable source-context hashes form part of the assessment model version. Single-record and batch inference share the same account activity context. Admin policy edits create new records, never silently rewrite prior assessments. Alerts are idempotent per assessment.

## Reproducible training

`python -m ledgerguard.cli train` uses only explicitly labelled historical training records from the selected tenant. At least 100 rows are required. `IsolationForest(n_estimators=100, contamination=0.03, random_state=42, n_jobs=1)` is fit independently of inference. Joblib artifact and JSON metadata contain training row count, ID hash, seed, feature names and timestamp. The dataset fingerprint forms the artifact version; retraining refreshes its deployment-local path.

Features: log absolute amount, amount/historical median, deviation from historical median/std, account/hour frequency, sine/cosine time-of-day, and historical beneficiary frequency. Ground-truth scenario labels, evaluation IDs and instructions in descriptions are excluded. The anomaly signal is the model's `predict == -1`; it contributes fifteen points rather than replacing policy evidence.

Inference loads only administrator-generated local artifacts, never uploaded pickle files. Missing artifacts or insufficient history visibly produce `ml_available=false` and a `rules-only` version. The full demonstration asserts `ml_available=true` so a fallback cannot be mistaken for a verified ML path. Training and inference both use the same feature function.

Historical fallback baselines (median 1000 and deviation scale 500) allow rule-only analysis without sufficient history, but do not establish confidence. Synthetic bursts and the large daily seed can make normal records trigger volume/velocity rules. This policy is deliberately explainable and uncalibrated. Temporal validation, customer segmentation, drift checks, currency-aware thresholds and labelled outcome calibration are needed before any real operational use.

Multi-month requests expose monthly category counts from the actual filtered records. No unobserved month or fraud outcome is invented. See the evaluation runner for recorded synthetic indicator recall; it is separate from LLM evaluation.
