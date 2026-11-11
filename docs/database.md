# Financial persistence and synthetic data

The initial Alembic migration freezes 29 application tables. A second migration upgrades early model metadata with tenant ownership. Official LangGraph migrations maintain their own checkpoint tables. PostgreSQL source/decision/audit triggers reject UPDATE/DELETE of immutable records.

| Domain | Tables |
|---|---|
| Identity | tenants, users, roles, tenant_memberships, refresh_token_sessions |
| Financial source | accounts, counterparties, transactions, ledger_entries, bank_statements, bank_statement_entries |
| Risk | risk_rules, risk_assessments, risk_alerts, anomaly_model_versions |
| Reconciliation | reconciliation_jobs, reconciliation_matches, reconciliation_exceptions |
| Governance | proposed_actions, approval_requests, approval_decisions, approval_policy_versions, action_executions, audit_logs |
| Operations | agent_runs, agent_messages, tool_executions, research_or_analysis_reports, background_jobs |

Proposed actions serve as reconciliation proposals, avoiding two conflicting proposal stores. Official checkpoint tables replace a separate hand-written `agent_checkpoints` table. Ledger entries reference immutable transaction amounts; an approved duplicate void toggles the ledger entry's active flag and increments its version. Source transaction IDs do not change.

Amounts use `NUMERIC(20,2)` and Python Decimal, serialized as strings. Each account has one currency. Balance is opening balance plus signed amounts of active entries. Summaries partition debits, credits, net and gross movement by currency. No FX conversion or cross-currency total is presented. Statistical model features use floats outside monetary calculations.

Tenant/date and tenant/account/reference indexes support deterministic queries. Source IDs are unique per tenant, statement source IDs per statement, assessments per transaction/model, decisions per approval/actor, approvals per proposal, and executions per action and approval. Foreign keys preserve source and evidence relationships. PostgreSQL row/advisory locks serialize sensitive decisions and runs; version compare-and-swap catches stale changes. SQLite tests retain foreign keys, WAL and a busy timeout, but do not establish PostgreSQL concurrency guarantees.

## Generator

`python -m ledgerguard.cli seed --count 10000 --seed 42 --anomaly-rate 0.02` creates the daily evaluation dataset and separate historical training records (at least 800, normally 20% of the requested count). UTC generation dates, PRNG seed, scenario labels and source IDs are reproducible. Counterparty/account patterns are synthetic. Every transaction has a ledger entry; bank-only rows represent missing internal records. Starting balances and computed closing balances are exposed by the validation command.

Explicit edge cases cover high value, rapid activity, duplicate ledger/source records with one settlement, missing bank/ledger rows, amount/currency differences, delayed settlements, partial payments, split/aggregate settlements with an explicit group reference, ambiguous duplicate references, refunds/reversals, bank fees, unusual hours and new beneficiaries. Training and evaluation labels are never included in features.

## CSV

Required columns: `source_id,account_id,amount,currency,reference,booked_at`; optional `description`. UTF-8 with optional BOM only. Maximum 5 MiB and 20,000 records. Timezones are mandatory. Values must be finite with at most two decimal places; supported codes are GBP/USD/EUR/JPY/CHF/CAD/AUD, and imported currency must equal the account's currency. This demonstration uses two-decimal accounting even for JPY; currency-specific minor-unit scaling is a documented extension.

Import source IDs are immutable. Reimporting the same account, booking date, amount, currency and reference skips an existing source; conflicting financial fields fail the transaction. Data import is ingestion, not an approval bypass for corrective ledger actions. Imports run as persisted background jobs. CSV exports escape formula prefixes in textual fields.

Sample schema is provided in `data/samples/transactions.csv`. There are no real customers, bank numbers or personal data.
