import pytest
from httpx import ASGITransport, AsyncClient

from tradepilot.api.app import create_app
from tradepilot.container import build_container
from tradepilot.core.config import Settings
from tradepilot.core.events import EventBus
from tradepilot.infrastructure.brokers.mock import MockBridge

TOKEN = "test-token"


@pytest.fixture
def container():
    cfg = Settings(ENGINE_MODE="mock", API_TOKEN=TOKEN, DB_PATH=":memory:", WEB_DIST="/nonexistent")
    bus = EventBus()
    bridge = MockBridge(bus, emit_events=False)
    return build_container(cfg, bridge=bridge)


@pytest.fixture
async def client(container):
    app = create_app(container)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {TOKEN}"}) as ac:
            yield ac
