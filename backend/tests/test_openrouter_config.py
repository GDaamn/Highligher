import pytest

from backend.app.config import Settings
from backend.app.jobs import JobManager
from backend.app.services.jev import JevFocusClient


@pytest.mark.asyncio
async def test_render_key_selects_openrouter_with_legacy_environment(monkeypatch):
    monkeypatch.setenv("FOCUS_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", " test-placeholder ")
    settings = Settings.from_environment()
    assert settings.focus_provider == "jev"
    client = JobManager(settings, None)._focus_analyzer().client
    try:
        assert isinstance(client, JevFocusClient)
        assert client.http.headers["Authorization"] == "Bearer test-placeholder"
    finally:
        await client.close()


def test_local_highlighting_without_server_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("FOCUS_PROVIDER", "deepseek")
    settings = Settings.from_environment()
    assert settings.focus_provider == "heuristic"
    assert JobManager(settings, None)._focus_analyzer().client is None
