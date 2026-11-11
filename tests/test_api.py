from decimal import Decimal

import httpx
import pytest

from ledgerguard.api import app
from ledgerguard.db import session
from ledgerguard.schemas import TransactionFilters
from ledgerguard.seed import DEMO_PASSWORD
from ledgerguard.transactions import summary


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as value:
        yield value


async def login(client, email="analyst@ledgerguard.demo"):
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


async def test_auth_refresh_rotation_logout_revokes_access(client):
    tokens = await login(client)
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    new = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    updated = {"Authorization": "Bearer " + new.json()["access_token"]}
    assert (await client.post("/api/v1/auth/logout", headers=updated)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=updated)).status_code == 401


async def test_api_auth_and_pagination(client):
    assert (await client.get("/api/v1/transactions")).status_code == 401
    tokens = await login(client)
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    result = await client.get("/api/v1/transactions?page_size=3", headers=headers)
    assert len(result.json()["items"]) == 3
    assert result.headers.get("X-Request-ID")
    assert (await client.get("/api/v1/transactions?page_size=201", headers=headers)).status_code == 422


async def test_public_registration_cannot_choose_role_or_join_tenant(client):
    import uuid

    body = {
        "email": str(uuid.uuid4()) + "@isolated.test",
        "password": "StrongSandbox!12",
        "name": "New analyst",
        "tenant_name": "Isolated",
    }
    forged = await client.post(
        "/api/v1/auth/register", json={**body, "role": "admin", "tenant_id": "tenant-demo"}
    )
    assert forged.status_code == 422
    result = await client.post("/api/v1/auth/register", json=body)
    headers = {"Authorization": "Bearer " + result.json()["access_token"]}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["role"] == "analyst" and me["tenant_id"] != "tenant-demo"
    assert (await client.get("/api/v1/transactions/TXN-000001", headers=headers)).status_code == 404


async def test_currency_totals_never_mixed(analyst):
    async with session() as db:
        result = await summary(db, analyst, TransactionFilters())
    assert set(result["currencies"]) == {"GBP", "EUR", "USD"}
    for totals in result["currencies"].values():
        assert Decimal(totals["credits"]) - Decimal(totals["debits"]) == Decimal(totals["net"])


async def test_metrics_requires_privileged_role(client):
    tokens = await login(client)
    response = await client.get("/metrics", headers={"Authorization": "Bearer " + tokens["access_token"]})
    assert response.status_code == 403


def test_production_rejects_demo_configuration():
    from ledgerguard.config import Settings

    with pytest.raises(ValueError):
        Settings(app_env="production").validate_runtime()


async def test_regular_reads_do_not_consume_login_rate_budget(client):
    tokens = await login(client)
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    for _ in range(35):
        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "analyst@ledgerguard.demo", "password": DEMO_PASSWORD}
        )
    ).status_code == 200
