import logging

import structlog
from prometheus_client import Counter, Gauge, Histogram

TOOL_CALLS = Counter("ledgerguard_mcp_calls_total", "Real MCP calls", ["server", "tool", "status"])
TOOL_TIME = Histogram("ledgerguard_mcp_duration_seconds", "MCP tool latency", ["server"])
RUN_TIME = Histogram("ledgerguard_run_duration_seconds", "Graph invocation duration")
HTTP_TIME = Histogram("ledgerguard_http_duration_seconds", "API request duration", ["method", "status"])
LLM_TOKENS = Counter("ledgerguard_llm_tokens_total", "Reported model usage", ["direction"])
LLM_COST = Counter("ledgerguard_llm_estimated_cost_usd_total", "Estimated cost at configured rates")
PERSISTED_TOOLS = Gauge(
    "ledgerguard_persisted_tool_executions",
    "Persisted cross-process tool records",
    ["server", "tool", "status"],
)


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
