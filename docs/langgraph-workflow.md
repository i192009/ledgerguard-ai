# Persistent review workflow

`RunState` contains run/request IDs, a validated plan, discovered account context, separate transaction/risk/reconciliation results, specialist narratives, approval IDs, executions and report ID. The database run ID is the LangGraph `thread_id`.

| Graph node | Responsibility | Side-effect control |
|---|---|---|
| supervisor | Parse request; discover owned accounts/statements through MCP | Validated `Plan`; no model-selected tool names |
| transactions | Summaries, patterns, detail/search evidence through Transaction MCP | Read-only, bounded retries |
| risk | Versioned deterministic/ML assessment through Risk MCP | Tenant lock and unique transaction/model assessment |
| reconciliation | Matching, exceptions and proposals through Reconciliation MCP | Snapshot key, advisory lock and proposal uniqueness |
| governance | Gather proposal evidence and request human review through Approval MCP | One approval per action |
| human_review | `interrupt()` and on resume inspect persisted decisions through MCP | Separate from proposal creation; resume text grants no authority |
| authorized_executor | Recheck identity, policy, digest, source evidence and versions | Database transaction, row locks, compare-and-swap and unique execution |
| reporter | Assemble source facts, decisions, executions and tool history | One report per run |

Three branches fan out in one graph superstep and join before governance. Each writes disjoint state keys; no conflicting list mutation occurs. Conditional routing skips the interrupt if there are no proposed corrections. Rejected, cancelled or expired decisions produce a report with no execution for that action. Runtime statuses are QUEUED, RUNNING, WAITING_APPROVAL, COMPLETED, FAILED and CANCELLED.

The official `AsyncPostgresSaver` is initialized during bootstrap. Every invocation uses `durability="sync"`. Offline tests use the official durable SQLite saver. Reconstructing a graph/saver after worker restart reloads the same thread. Completed runs return immediately, preventing an accidental second invocation from re-executing the workflow.

Human decisions arrive only through authenticated REST endpoints. The endpoint checks tenant, role, maker-checker separation, expiry, proposal hash, expected revision and duplicate votes; it commits before dispatching resume. The scheduler also resumes waiting runs with no remaining pending approvals, covering a lost enqueue after the decision commit. Resume payload is a wake-up signal. The executor reloads and validates every decision, including current user roles and any stricter current threshold.

Failures mark the run FAILED and retain the checkpoint. `POST /agents/runs/{id}/retry` is maker-only and re-enters the failed graph superstep. Bounded network retries and tool budgets limit loops. Approval/execution writes remain idempotent when nodes replay. Cancellation is checked both on delegated MCP calls and before financial execution.

Optional LLM reasoning uses LangChain structured output and separate supervisor/specialist responsibilities. Offline mode uses a bounded deterministic planner and labelled factual summaries; it does not pretend to be model reasoning. Report assembly and financial calculations never require an LLM.
