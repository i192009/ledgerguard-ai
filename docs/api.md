# REST API and event delivery

Interactive schemas: `http://localhost:8088/docs`; OpenAPI JSON: `/openapi.json`. All application paths use `/api/v1`. Except login/register/refresh and health, endpoints require `Authorization: Bearer <access token>`. Domain errors use `{"error":{"message":"...","request_id":"..."}}`; request-validation errors use FastAPI's 422 schema. Every request receives `X-Request-ID`.

| Area | Routes |
|---|---|
| Authentication | POST auth/register, auth/login, auth/refresh, auth/logout; GET auth/me |
| Accounts | GET accounts, accounts/{id}, accounts/{id}/summary |
| Transactions | GET transactions, transactions/{id}, transactions/analytics/summary, transactions/export; POST transactions/import |
| Risk | POST risk/analyse, risk/train (admin); GET risk/assessments, risk/assessments/{id}, risk/alerts, risk/alerts/{id} |
| Bank statements | GET bank-statements; POST bank-statements/{id}/import |
| Reconciliation | POST/GET reconciliation/jobs; GET jobs/{id}, jobs/{id}/exceptions, jobs/{id}/proposals under reconciliation |
| Approvals | GET approvals, approvals/{id}; POST approvals/{id}/approve, /reject, /request-investigation |
| Agent operations | POST agents/run; GET agents/runs, agents/runs/{id}, agents/runs/{id}/events; POST agents/runs/{id}/cancel, /retry |
| Reports | GET reports, reports/{id}, reports/{id}/pdf; POST reports/generate |
| Governance | GET audit-logs, mcp/tool-executions, mcp/health |
| Background jobs | GET jobs/{id} |
| Configuration | GET settings; PUT settings/risk, settings/approvals (admin) |
| Health/metrics | GET /health/live, /health/ready; GET /metrics (admin/auditor) |

Transaction filters include account, UTC start/end datetime, status, text search, minimum/maximum absolute amount, page/page_size and a constrained sort (`newest`, `oldest`, `amount`). Page sizes are bounded at 200. Collection results contain items, total, page and page_size where applicable. Monetary amounts are strings.

Risk, reconciliation, imports, model training and explicit report generation return a 202 persisted background job ID. Poll `/jobs/{id}` for QUEUED/RUNNING/COMPLETED/FAILED and the result/error. Imports accept multipart field `file`. Bank imports target an existing owned statement. AI requests return a persisted run immediately, with state and status available after browser refresh.

Approval decisions require `{"reason":"Reviewed source evidence","version":1}`. The detail endpoint returns the current version, proposal, evidence, policy and history. Stale revisions and duplicate decisions return 409. Ineligible roles or makers return 403. Approved/rejected decisions commit before a worker resume is queued. Partial two-person quorum remains pending. Investigation leaves the proposal in review and records why.

SSE uses authenticated fetch, not a query token. Important events persist in `agent_messages`; each message has an event ID, event type and JSON data. Reconnect with `Last-Event-ID` or `?after=<id>` to replay after a known cursor. Unknown cursors replay all events. Streams end on completion/failure/cancellation or an approval pause; reconnect after status changes. The frontend also polls durable run status as a recovery path. nginx disables buffering.

Event types include RUN_STARTED, PLAN_CREATED, AGENT_STARTED, MCP_TOOL_INVOKED, MCP_TOOL_COMPLETED, TRANSACTIONS_ANALYSED, RISK_ANALYSIS_COMPLETED, RECONCILIATION_COMPLETED, APPROVAL_REQUIRED, APPROVAL_RECEIVED, ACTION_EXECUTED, REPORT_GENERATED, RUN_COMPLETED and RUN_FAILED. No event text establishes authorization.

PDF download renders the persisted evidence snapshot and audits the action. Source transaction CSV export is capped at 20,000 records. Reports are JSON snapshots in PostgreSQL; production object storage is documented separately.
