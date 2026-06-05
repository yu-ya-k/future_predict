import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from api.config import get_settings
from api.main import app


@pytest.mark.anyio
async def test_health_endpoint_returns_status_and_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "env": "test"}
    get_settings.cache_clear()
