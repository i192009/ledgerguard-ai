# LedgerGuard AI — delivery and verification status

Verified locally on **11 November 2026**. UI: **http://localhost:3000**. API documentation: **http://localhost:8088/docs**. No cloud deployment or paid LLM invocation was performed.

## Implemented and exercised

- [x] FastAPI versioned APIs, OpenAPI, errors, request IDs, rate limits and authenticated persisted SSE.
- [x] React/TypeScript authentication/registration, responsive navigation, light/dark preference, overview, transactions, risk charts/account investigation, reconciliation, AI workspace, monitor, approvals, reports, audit and settings.
- [x] PostgreSQL, 29 application tables, frozen Alembic migrations, indexes, foreign keys and immutable source/governance triggers.
- [x] Argon2 passwords, JWT access tokens, revocable sessions, atomic refresh rotation, RBAC and tenant ownership checks.
- [x] Reproducible 10,000-record daily seed, 2,000 disjoint historical training rows, accounts/currencies/counterparties, bank statements and labelled edge cases.
- [x] Exact Decimal balances/totals partitioned by currency, filters/search/pagination/details, temporal analytics and validated synthetic CSV import/export.
- [x] Deterministic risk policy, separately trained real Isolation Forest, versioned artifacts/features, visible rules-only fallback, consistent single/batch context and persisted assessments/alerts.
- [x] Deterministic reconciliation, signed currency-safe matching, settlement tolerance, explicitly grouped exact-sum settlements, manual suggestions, fees/partial/missing records and duplicate proposals.
- [x] Four independent official-SDK MCP processes: Transactions (7 tools), Risk (6), Reconciliation (6), Approvals (5).
- [x] Actual Streamable HTTP initialization/discovery/schema validation/invocation/results. Per-server service authentication plus audience-bound signed delegation; allowlists, persistent budgets, timeouts and bounded read retries.
- [x] Typed parallel LangGraph, safe join, conditional routing, PostgreSQL checkpoints, explicit interrupt/resume and failure recovery.
- [x] Human REST decisions, maker-checker, two-person policy, expiry scheduler, investigation requests, decision history, revision/digest checks and cancellation.
- [x] Worker-internal executor revalidates human votes/current roles/policy/bank proof/ledger versions. Row locks, compare-and-swap and unique executions enforce one financial effect.
- [x] Celery/Redis background work, concurrency bounds, late acknowledgement, time limits, queued-work/approval-resume recovery, dashboard cache and worker health.
- [x] Reports with persisted facts, proposal evidence, actual human decisions/history and execution outcomes; PDF generation and audited download.
- [x] Structured logs, protected metrics, cross-process persisted tool gauge, optional authenticated Prometheus profile and importable Grafana example.
- [x] Configurable LangChain structured supervisor/specialist reasoning, evidence-ID validation, optional LangSmith tracing and configured-rate cost instrumentation. Live external verification is unrun below.
- [x] Python financial/API/security/protocol/graph tests, frontend/browser tests, PostgreSQL concurrency checks, offline routing evaluation, reproducible demo and load script.
- [x] Working Docker images/networking/health/startup/migrations/volumes. MCP endpoints are private; exposed services bind to loopback.
- [x] Lockfiles, GitHub Actions workflow, architecture/security/API/model/deployment guides, five Mermaid diagrams, actual screenshots and AWS task template.

## Actual checks

| Executed check | Result |
|---|---|
| `.venv/Scripts/python -m pytest -q` on Windows x64 Python 3.12 | **59 passed**, latest run 55.32 seconds |
| `docker compose exec -T api pytest -q` on Linux ARM64 Python 3.12 | **59 passed**, latest run 35.16 seconds |
| `ruff check src tests scripts alembic evaluation` | Passed |
| `ruff format --check src tests scripts alembic evaluation` | 34 files already formatted |
| `mypy src/ledgerguard` | No issues in 21 source files |
| `npm run build` | TypeScript and Vite passed; large-bundle advisory remains |
| `npm test` | **3 passed**; Vitest excludes Playwright files |
| `npm run test:e2e` | **3 passed** in 19.1 seconds; actual Chromium/API/database/MCP/worker |
| `npm audit` | **0 vulnerabilities** reported at verification time |
| `docker compose build api frontend` and startup | Passed; applicable services healthy, initializer exited 0 |
| `python scripts/demo.py --restart-worker` | All thirteen stages completed, with real MCP, ML, durable pause/restart/resume, authorization/replay denial, one correction, PDF and download audit |
| `docker compose exec -T api python scripts/verify_postgres.py` | Concurrent votes: one accepted/one denied; concurrent executions: one row/same ID; source/audit deletion blocked |
| `python evaluation/run.py` | **8/8 offline routing scenarios passed**; not live LLM evaluation |
| `python scripts/load_test.py --requests 100 --concurrency 5` | 100 successful reads; 2.955 seconds elapsed, p50 91.21 ms, p95 694.88 ms |
| Prometheus profile and `/api/v1/targets` | Private authenticated API scrape **up**, no last error |

Evidence: [demo JSON](evaluation/results/demo.json), [downloaded PDF](evaluation/results/demo.pdf), [PostgreSQL](evaluation/results/postgres.json), [dataset](evaluation/results/dataset.json), [offline routing](evaluation/results/offline.json), [load](evaluation/results/load.json), and [screenshots](docs/screenshots/).

## Final 10,000-record demonstration

Run `a4e32f25-ac63-4f75-be6a-27337191ebe7` completed. Model version: `iforest-v1-8f510e16f206:d8e17cca06:d26203623a`, `ml_available=true`. Risk indicators: 183 HIGH, 9,816 MEDIUM and 1 LOW. Reconciliation accepted 9,993 of 10,000 ledger records before the authorized adjustment; other discrepancies stayed explicit exceptions.

Persisted events measured **2.577 seconds** from RUN_STARTED to APPROVAL_REQUIRED, approximately **3,880 selected records/second for that analysis stage** on this local warm-container run. Total run time including decision and worker restart was **29.521 seconds**. This is not a production SLA or a million-record capacity claim.

The independent reviewer approved the duplicate proposal. Original source/original ledger records were retained; `LED-000014` became inactive exactly once. The report included persisted human decisions and execution evidence. The downloaded PDF was **112,240 bytes**, and its download was audited. A separate browser tenant proved the actual two-user UI approval without altering the 10,000-record demo tenant.

## Verification limits and extensions

- Live model calls, model-quality/injection evaluation, real token costs and remote LangSmith ingestion require credentials and were **not run**. Offline routing correctness is not live LLM accuracy.
- GitHub Actions is implemented; equivalent checks ran locally, but a hosted Actions run was **not triggered**.
- AWS ECS/RDS/ElastiCache/Secrets Manager/ALB/CloudWatch guidance/template are supplied. AWS infrastructure, IAM, TLS, S3 export, cross-host model storage, autoscaling and disaster recovery are **not deployed or verified**.
- Grafana JSON is supplied; Grafana is not deployed. Prometheus is running locally. Histograms remain process-local; persisted tool gauges aggregate cross-process records.
- SQLite is a portable test backend. PostgreSQL is required for the documented multi-process locks and immutability triggers. A Windows psycopg async operation stalled during setup; Linux container checkpointing was verified successfully.
- All data is synthetic. Scores are uncalibrated investigation indicators, not fraud probabilities or AML determinations. No FX conversion or live payment execution exists. Currency-specific minor units beyond the two-decimal sandbox, SSO/member onboarding, RLS/composite tenant keys, least-privilege runtime database identities, external immutable audit retention and a smaller production image are hardening extensions.
- Descriptive/non-reference matches remain manual suggestions. Only duplicate-void corrections execute; other discrepancy types remain investigation evidence.
- Frontend build succeeds with Vite's bundle-size advisory (about 878 kB before gzip). Code splitting remains an optimization.

## Reproduction and continuation state

The workspace contains the local sandbox implementation, resolved lockfiles, documentation and measured artifacts. Services are running. Open the UI and use the README's development-only credentials. The demo duplicate has already been corrected; repeat its initial approval sequence using the explicit development reset in the README.

For continuation, inspect this file and evidence before rerunning checks. Preserve user data and do not claim external verification. Optional next work is credentialed live-model evaluation or an explicitly authorized cloud deployment; neither is required to run the verified local demonstration.
