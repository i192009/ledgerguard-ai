import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

TEST_DIR = Path(tempfile.mkdtemp(prefix="ledgerguard-tests-"))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (TEST_DIR / "financial.db").as_posix()
os.environ["CHECKPOINT_PATH"] = str(TEST_DIR / "checkpoints.db")
os.environ["ARTIFACTS_DIR"] = str(TEST_DIR / "models")
os.environ["TASK_MODE"] = "inline"
os.environ["LLM_PROVIDER"] = "offline"
os.environ["REDIS_URL"] = "redis://localhost:16379/15"
os.environ["DEMO_MODE"] = "true"
os.environ["APP_ENV"] = "development"

from ledgerguard.config import settings  # noqa: E402
from ledgerguard.db import engine, session  # noqa: E402
from ledgerguard.models import Base  # noqa: E402
from ledgerguard.security import Principal  # noqa: E402


@pytest.fixture
def analyst():
    return Principal("user-analyst", "tenant-demo", "analyst")


@pytest.fixture
def approver():
    return Principal("user-approver", "tenant-demo", "approver")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def database():
    from ledgerguard.risk import train_model
    from ledgerguard.seed import seed

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session() as db:
        await seed(db, 200)
    async with session() as db:
        await train_model(db)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def governed_case():
    from datetime import timedelta
    from decimal import Decimal

    from ledgerguard.models import (
        Account,
        ApprovalRequest,
        BankEntry,
        BankStatement,
        LedgerEntry,
        ProposedAction,
        ReconciliationJob,
        Tenant,
        Transaction,
        User,
        now,
        uid,
    )
    from ledgerguard.reconciliation import action_digest

    async with session() as db:
        tenant = Tenant(id=uid(), name="Isolated governance test")
        db.add(tenant)
        await db.flush()
        users = []
        for role in ("analyst", "approver", "approver", "admin"):
            user = User(
                id=uid(),
                tenant_id=tenant.id,
                email=uid() + "@test.invalid",
                name=role,
                role=role,
                password_hash="not-used",
            )
            db.add(user)
            users.append(Principal(user.id, tenant.id, role))
        account = Account(id=uid(), tenant_id=tenant.id, name="Test GBP", currency="GBP")
        db.add(account)
        await db.flush()
        stamp = now()
        original, duplicate = [
            Transaction(
                id=uid(),
                tenant_id=tenant.id,
                account_id=account.id,
                source_id=uid(),
                amount=Decimal("-250.00"),
                currency="GBP",
                reference="DUP",
                booked_at=stamp,
                description="Synthetic duplicate",
            )
            for _ in range(2)
        ]
        statement = BankStatement(id=uid(), tenant_id=tenant.id, account_id=account.id, name="Test statement")
        db.add_all([original, duplicate, statement])
        await db.flush()
        bank = BankEntry(
            id=uid(),
            tenant_id=tenant.id,
            statement_id=statement.id,
            source_id=uid(),
            amount=original.amount,
            currency="GBP",
            reference="DUP",
            booked_at=stamp,
        )
        ledgers = [
            LedgerEntry(id=uid(), tenant_id=tenant.id, transaction_id=t.id) for t in (original, duplicate)
        ]
        job = ReconciliationJob(
            id=uid(),
            tenant_id=tenant.id,
            key=uid(),
            account_id=account.id,
            statement_id=statement.id,
            summary={},
            status="COMPLETED",
        )
        db.add_all([bank, *ledgers, job])
        await db.flush()
        payload = {
            "original_id": original.id,
            "transaction_id": duplicate.id,
            "bank_id": bank.id,
            "amount": "-250.00",
            "currency": "GBP",
            "ledger_version": 1,
            "exception_id": uid(),
            "job_id": job.id,
            "ledger_id": ledgers[1].id,
            "type": "VOID_DUPLICATE",
            "version": 1,
        }
        action = ProposedAction(
            id=uid(),
            tenant_id=tenant.id,
            job_id=job.id,
            maker_id=users[0].user_id,
            target_id=ledgers[1].id,
            payload=payload,
            digest=action_digest(payload),
        )
        db.add(action)
        await db.flush()
        approval = ApprovalRequest(
            id=uid(),
            tenant_id=tenant.id,
            action_id=action.id,
            digest=action.digest,
            expires_at=now() + timedelta(hours=1),
            required_approvals=1,
            status="PENDING",
            version=1,
        )
        db.add(approval)
        await db.flush()
        return {
            "maker": users[0],
            "checker": users[1],
            "second": users[2],
            "admin": users[3],
            "approval": approval.id,
            "action": action.id,
            "ledger": ledgers[1].id,
            "original": original.id,
        }


@pytest.fixture(scope="session")
def mcp_services(database):
    processes = []
    cfg = settings()
    try:
        for name in ("transactions", "risk", "reconciliation", "approvals"):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            setattr(cfg, f"mcp_{name}_url", url + "/mcp")
            os.environ[f"MCP_{name.upper()}_URL"] = url + "/mcp"
            log = open(TEST_DIR / f"mcp-{name}.log", "w", encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
            process = subprocess.Popen(
                [sys.executable, "-m", "ledgerguard.mcp_server", name, "--port", str(port)],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append((process, log))
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if httpx.get(url + "/health/ready", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None:
                    raise RuntimeError((TEST_DIR / f"mcp-{name}.log").read_text())
                time.sleep(0.2)
            else:
                raise RuntimeError(f"MCP {name} failed startup; see {TEST_DIR}")
        yield
    finally:
        for process, log in processes:
            process.terminate()
            process.wait(timeout=15)
            log.close()
