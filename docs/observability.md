# Observability and evaluation

FastAPI emits structured JSON request logs with request ID, route, status and duration. MCP invocation records persist server, tool, tenant/run, duration, status and sanitized error text. Graph lifecycle events persist for SSE replay. No access/service tokens or uploaded CSV contents are logged.

Protected `/metrics` exposes HTTP latency, a cross-process persisted tool gauge, process-local MCP counts/errors/duration, graph invocation time and model-reported input/output tokens. Optional estimated cost uses `LLM_INPUT_COST_PER_MILLION` and `LLM_OUTPUT_COST_PER_MILLION`; zero means no configured price and is labelled as such in logs. Run timing measures an invocation, excluding time spent durably suspended; the demo evidence separately records analysis time before decision and wall time including approval/restart.

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and the LLM configuration to trace actual optional model calls. The LangSmith tracing context is disabled safely in offline mode. No external trace ingestion or paid-model quality result is claimed without credentials and a recorded run. Example local Prometheus/Grafana guidance is in `infrastructure/monitoring/README.md`.

## Separate correctness from language-model quality

- `pytest` verifies exact financial matching, Decimal handling, features, rules, authorization, real MCP, graph persistence/resume and API sessions. It requires no paid API.
- `evaluation/run.py` checks labelled offline routing and structured plan validity, records elapsed time and explicitly reports that live LLM measures are unrun. Live-model and synthetic-indicator quality metrics remain separate work and are not claimed by the routing suite.
- `scripts/demo.py` measures the whole 10,000-record real MCP workflow and verifies governance and PDF evidence.
- `scripts/load_test.py` measures authenticated pagination latency with bounded concurrency.
- `scripts/verify_postgres.py` exercises actual PostgreSQL concurrent votes/execution and immutable-record triggers.

Live LLM evaluation should measure routing/tool selection, evidence-citation validity, unsupported factual claims, score explanation consistency, reconciliation interpretation, instruction injection, task completion, tokens, latency and estimated cost on held-out scenarios. Do not interpret offline route correctness or a model training score as proof of live model quality or fraud detection accuracy. Synthetic indicator recall is a narrow regression measure, not financial fraud recall.

No million-record benchmark or production capacity claim is made. Evidence files record exact dataset size and measured times on this machine; hardware/container startup and caches affect results.
