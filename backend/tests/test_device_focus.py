from dataclasses import replace

import httpx
import pytest

from backend.app import main


@pytest.mark.asyncio
async def test_device_scoring_uses_server_key_and_preserves_word_ids(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, openrouter_api_key="test-only"))
    monkeypatch.setitem(main.app.dependency_overrides, main.libraries.for_request, lambda: object())

    async def score(client, pages):
        assert client.http.headers["Authorization"] == "Bearer test-only"
        assert pages[0]["words"] == [{"id": 0, "text": "First"}, {"id": 1, "text": "page."}]
        return {1: {"essential": [1]}}

    monkeypatch.setattr(main.JevFocusClient, "select", score)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://app.test") as browser:
        response = await browser.post("/api/device-focus", json={"words": ["First", "page."]})
        assert response.json() == {"focusWordIds": [1]}
        assert (await browser.post("/api/device-focus", json={"words": []})).status_code == 422
