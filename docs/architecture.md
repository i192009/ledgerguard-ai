# System architecture

LedgerGuard is a modular monorepo. `src/ledgerguard` contains reusable financial and governance services; API and MCP processes import them. Each MCP service has its own independently configured process and private network address. Celery hosts LangGraph; it is the MCP host/client, not a fifth tool server.

The browser uses FastAPI through an nginx same-origin proxy. JWT authentication resolves a revocable session and a current user from the database. Domain services constrain queries by that principal's tenant. They never trust a request tenant identifier.

Redis brokers Celery tasks, limits HTTP requests, and caches dashboard data for ten seconds by tenant. PostgreSQL persists domain records, jobs, events, reports, approvals and official LangGraph checkpoints. Model artifacts occupy a shared Docker volume. PDF bytes are generated from the persisted report snapshot on download.

The graph fans out from supervisor to three branches. Each branch writes different typed state keys. A list-based join waits for all branches before governance. Human review is its own interrupted node, so approval creation does not replay around the interrupt. The trusted executor uses persisted authorization rather than the resume message. Source records remain immutable; only a versioned ledger entry can become inactive.

The deployment uses eleven services including the one-shot initializer and scheduler. MCP services have no host port mappings. Database and Redis are exposed on loopback for local maintenance; remove those mappings in a hosted installation. An internal network separates service addressing, but outbound connectivity is retained for optional LLM calls.

## Failures and recovery

Read-only MCP calls have one bounded retry on transport failure. Writes have no blind transport retries. Reconciliation, proposals, approval requests, assessments, reports and ledger execution use persistent identity/uniqueness checks. Worker tasks acknowledge after completion; Redis/Celery re-delivery plus PostgreSQL advisory locks prevent simultaneous graph execution for a thread. The scheduler recovers queued work and approval resumes whose dispatch was interrupted. Failed runs expose an authenticated maker-only retry endpoint and resume from checkpoints.

Production hardening and unverified external infrastructure are described in [deployment](deployment.md). This repository does not claim distributed exactly-once delivery; exactly-once financial effect is enforced through transactions and unique action execution rows.
