import asyncio
import json
import time
from contextlib import asynccontextmanager

import httpx
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ledgerguard.config import settings
from ledgerguard.db import DomainError, session
from ledgerguard.models import AgentEvent, ToolExecution
from ledgerguard.observability import TOOL_CALLS, TOOL_TIME
from ledgerguard.security import delegation

ALLOWLISTS = {
    "transactions": {
        "get_transactions",
        "get_transaction_details",
        "get_account_transaction_summary",
        "detect_duplicate_transactions",
        "analyse_transaction_patterns",
        "search_transactions",
        "list_accounts",
    },
    "risk": {
        "assess_transaction_risk",
        "detect_anomalous_transactions",
        "analyse_account_risk",
        "get_risk_alerts",
        "explain_risk_assessment",
        "create_risk_alert",
    },
    "reconciliation": {
        "start_reconciliation",
        "get_reconciliation_status",
        "find_unmatched_transactions",
        "detect_reconciliation_discrepancies",
        "suggest_reconciliation_actions",
        "get_reconciliation_report",
    },
    "approvals": {
        "create_approval_request",
        "get_approval_status",
        "list_pending_approvals",
        "get_approval_details",
        "cancel_approval_request",
    },
}
READ_ONLY = {
    "get_transactions",
    "get_transaction_details",
    "get_account_transaction_summary",
    "detect_duplicate_transactions",
    "analyse_transaction_patterns",
    "search_transactions",
    "list_accounts",
    "get_risk_alerts",
    "explain_risk_assessment",
    "get_reconciliation_status",
    "find_unmatched_transactions",
    "detect_reconciliation_discrepancies",
    "get_reconciliation_report",
    "get_approval_status",
    "list_pending_approvals",
    "get_approval_details",
}


class MCPClientManager:
    def __init__(self, principal, run_id=None):
        self.principal, self.run_id = principal, run_id
        self.calls = 0

    def headers(self, server):
        return {
            "Authorization": "Bearer " + getattr(settings(), f"mcp_{server}_token"),
            "X-LedgerGuard-Context": delegation(self.principal, server, self.run_id),
        }

    @asynccontextmanager
    async def transport(self, server):
        async with httpx.AsyncClient(
            headers=self.headers(server),
            timeout=httpx.Timeout(settings().tool_timeout_seconds, connect=10),
            follow_redirects=False,
        ) as http:
            async with streamable_http_client(
                getattr(settings(), f"mcp_{server}_url"), http_client=http
            ) as streams:
                yield streams

    async def discover(self, server):
        async with self.transport(server) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return (await client.list_tools()).tools

    async def call(self, server, tool, arguments):
        if tool not in ALLOWLISTS.get(server, set()):
            raise DomainError("Tool is outside agent capability allowlist", 403)
        self.calls += 1
        if self.calls > settings().tool_budget:
            raise DomainError("Tool-call budget exhausted", 429)
        started = time.monotonic()
        status, error = "FAILED", None
        if self.run_id:
            await self.record_event("MCP_TOOL_INVOKED", {"server": server, "tool": tool})
        try:
            attempts = 2 if tool in READ_ONLY else 1
            for attempt in range(attempts):
                try:
                    result = await asyncio.wait_for(
                        self._safe_invoke(server, tool, arguments), settings().tool_timeout_seconds
                    )
                    status = "COMPLETED"
                    return result
                except (httpx.TransportError, TimeoutError):
                    if attempt + 1 == attempts:
                        raise
                    await asyncio.sleep(0.25)
        except Exception as exc:
            error = str(exc)[:500]
            raise
        finally:
            elapsed = time.monotonic() - started
            TOOL_CALLS.labels(server, tool, status).inc()
            TOOL_TIME.labels(server).observe(elapsed)
            async with session() as db:
                db.add(
                    ToolExecution(
                        tenant_id=self.principal.tenant_id,
                        run_id=self.run_id,
                        server=server,
                        tool=tool,
                        duration_ms=int(elapsed * 1000),
                        status=status,
                        details={"error": error} if error else {},
                    )
                )
            if self.run_id:
                await self.record_event(
                    "MCP_TOOL_COMPLETED",
                    {"server": server, "tool": tool, "status": status, "duration_ms": int(elapsed * 1000)},
                )

    async def _invoke(self, server, tool, arguments):
        async with self.transport(server) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = {t.name: t for t in (await client.list_tools()).tools}
                if tool not in tools:
                    raise DomainError("Server did not advertise requested tool", 502)
                validate(arguments, tools[tool].inputSchema)
                result = await client.call_tool(tool, arguments)
                if result.isError:
                    raise DomainError(
                        "MCP tool rejected request: "
                        + " ".join(c.text for c in result.content if c.type == "text"),
                        502,
                    )
                if result.structuredContent is not None:
                    data = result.structuredContent
                else:
                    data = json.loads(next(c.text for c in result.content if c.type == "text"))
                output_schema = tools[tool].outputSchema
                if output_schema:
                    validate(data, output_schema)
                if not isinstance(data, dict):
                    raise DomainError("Invalid MCP structured output", 502)
                return data

    async def _safe_invoke(self, server, tool, arguments):
        try:
            return await self._invoke(server, tool, arguments)
        except ExceptionGroup as exc:
            leaf: BaseException = exc
            while isinstance(leaf, BaseExceptionGroup):
                leaf = leaf.exceptions[0]
            raise leaf from exc

    async def record_event(self, event, data):
        async with session() as db:
            db.add(AgentEvent(tenant_id=self.principal.tenant_id, run_id=self.run_id, event=event, data=data))
