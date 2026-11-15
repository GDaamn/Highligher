from __future__ import annotations

import json

import httpx
import pytest

from backend.app.services.deepseek import DeepSeekFocusClient


@pytest.mark.asyncio
async def test_deepseek_client_uses_json_mode_and_parses_ids():
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.example/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret-for-test"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["user_id"] == "booklamp-test"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        prompt = payload["messages"][0]["content"]
        calls.append(prompt)
        if "compact context" in prompt:
            result = {"subject": "astronomy", "purpose": "explain planets", "major_topics": ["Saturn"]}
        else:
            if "layered semantic reading map" in prompt:
                assert "essential_word_ids" in prompt
                assert "discourse structure" in prompt
                assert "lexical density is not semantic importance" in prompt
            else:
                assert "verify semantic compression" in prompt
            result = {"pages": [{"page": 1, "essential_word_ids": [2], "context_word_ids": [3]}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    client = DeepSeekFocusClient(
        api_key="secret-for-test",
        base_url="https://api.example",
        model="test-model",
        user_id="booklamp-test",
        transport=httpx.MockTransport(respond),
    )

    pages = [{"page": 1, "words": [{"id": 2, "text": "Saturn"}, {"id": 3, "text": "moves"}]}]
    context = await client.prepare(pages)
    selected = await client.select(pages)

    assert context["subject"] == "astronomy"
    assert selected == {1: {"essential": [2], "context": [3]}}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_deepseek_client_keeps_draft_when_verifier_fails():
    call_count = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            result = {"pages": [{"page": 1, "essential_word_ids": [2], "context_word_ids": [3]}]}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
        return httpx.Response(400, json={"error": {"message": "invalid verifier request"}})

    client = DeepSeekFocusClient(
        api_key="secret-for-test",
        base_url="https://api.example",
        model="test-model",
        transport=httpx.MockTransport(respond),
    )
    pages = [{"page": 1, "words": [{"id": 2, "text": "Saturn"}, {"id": 3, "text": "moves"}]}]

    assert await client.select(pages) == {1: {"essential": [2], "context": [3]}}
