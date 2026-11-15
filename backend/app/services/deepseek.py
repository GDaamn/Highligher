from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx


OUTLINE_PROMPT = """Build compact context for a semantic reading assistant.
Return JSON only with: subject, purpose, major_topics, key_terms, and argument_shape.
Infer conservatively from sampled pages. Do not summarize page by page.
"""

SYSTEM_PROMPT = """You create a layered semantic reading map from positioned document words.
Return JSON only. For every page choose two disjoint word-ID layers:
- essential_word_ids: roughly 25-40%; the minimum proposition skeleton needed to reconstruct the author's main meaning.
- context_word_ids: roughly 15-25% more; evidence, examples, transitions, attribution, or grammatical support that improves comprehension.

First infer discourse structure: governing claim, definition, primary proposition, evidence or elaboration, contrast, qualification, and outcome. Rank those roles before choosing words.
Preserve subjects with their predicates and decisive objects or complements. Fragments may be discontinuous when that removes a secondary clause.
When parallel clauses occur, retain the governing clause and clauses carrying the central claim or final implication. Put useful but nonessential clauses in context and omit restatements.
Never select a phrase merely because it contains many content words; lexical density is not semantic importance.
Always preserve meaning-changing negation, modality, quantities, comparisons, named entities, and causal or contrast links.
Keep important quotations coherent. For tables retain headers, row labels, units, and values that communicate comparisons. For contents pages retain titles with their page numbers.
The essential layer alone must be intelligible. Essential plus context should preserve nuance without recreating the entire paragraph.
Every substantive sentence must contribute a complete essential proposition unless it is genuinely redundant or decorative. Never leave only a noun fragment from a sentence.
Use previousPageTail and nextPageHead to understand sentences split across page boundaries. Treat a cross-page sentence as one argument, while selecting only IDs belonging to the current page.
Keep IDs in reading order. Never invent IDs, merge words, or rewrite text.
Response shape: {"pages":[{"page":1,"essential_word_ids":[1,4],"context_word_ids":[2,7],"confidence":0.9}]}.
"""

VERIFY_PROMPT = """You verify semantic compression.
Compare each original page with its draft essential and context word IDs. Correct the IDs when needed.
The essential layer must preserve the principal claim, predicates, negation, modality, causal or contrast relations, quantities, and conclusion without changing meaning.
Reject drafts that omit an entire substantive sentence or retain only a dangling noun phrase. Verify sentence continuations using previousPageTail and nextPageHead.
Move useful but nonessential evidence or connective wording to context. Remove lexically dense phrases that do not carry the argument.
Return JSON only using the same pages/essential_word_ids/context_word_ids shape. IDs must exist in the original page and the layers must be disjoint.
"""

@dataclass(slots=True)
class DeepSeekFocusClient:
    api_key: str
    base_url: str
    model: str
    user_id: str | None = None
    timeout_seconds: float = 60
    transport: httpx.AsyncBaseTransport | None = None
    document_context: dict[str, Any] = field(default_factory=dict)
    _http_client: httpx.AsyncClient | None = field(init=False, default=None, repr=False)

    async def open(self) -> None:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport)

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def prepare(self, pages: list[dict[str, Any]]) -> dict[str, Any]:
        samples = self._sample_pages(pages)
        try:
            self.document_context = await self._request_json(
                OUTLINE_PROMPT,
                "Infer document context from these sampled pages:\n" + json.dumps({"pages": samples}, ensure_ascii=False),
                max_tokens=1200,
            )
        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
            # Context improves selection but is not required. A transient
            # outline failure must not prevent the page batches from running.
            self.document_context = {}
        return self.document_context

    async def select(self, pages: list[dict[str, Any]]) -> dict[int, dict[str, list[int]]]:
        source = {"document_context": self.document_context, "pages": pages}
        draft = await self._request_json(
            SYSTEM_PROMPT,
            "Create semantic layers from this JSON:\n" + json.dumps(source, ensure_ascii=False),
            max_tokens=7500,
        )
        try:
            verified = await self._request_json(
                VERIFY_PROMPT,
                "Verify this semantic reading map against its source:\n"
                + json.dumps({"source": source, "draft": draft}, ensure_ascii=False),
                max_tokens=7500,
            )
            result = verified if verified.get("pages") else draft
        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
            # The first model pass is already meaningful. A verifier outage
            # should not replace it with the less precise local fallback.
            result = draft
        return self._parse_layers(result)

    async def _request_json(self, system_prompt: str, user_prompt: str, *, max_tokens: int) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.user_id:
            payload["user_id"] = self.user_id
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                if self._http_client is not None:
                    response = await self._http_client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                        response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                if not content:
                    raise ValueError("DeepSeek returned an empty response.")
                return json.loads(content)
            except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as error:
                last_error = error
                retryable = not isinstance(error, httpx.HTTPStatusError) or error.response.status_code in {408, 429} or error.response.status_code >= 500
                if attempt == 1 or not retryable:
                    raise
                await asyncio.sleep(0.75 * (2**attempt))
        raise RuntimeError("DeepSeek request failed.") from last_error

    @staticmethod
    def _sample_pages(pages: list[dict[str, Any]], maximum: int = 18) -> list[dict[str, Any]]:
        if not pages:
            return []
        step = max(1, len(pages) // maximum)
        return [
            {"page": page["page"], "text": " ".join(str(word["text"]) for word in page.get("words", []))[:1200]}
            for page in pages[::step][:maximum]
            if not page.get("dimPage")
        ]

    @staticmethod
    def _parse_layers(payload: dict[str, Any]) -> dict[int, dict[str, list[int]]]:
        result: dict[int, dict[str, list[int]]] = {}
        for page in payload.get("pages", []):
            number = int(page["page"])
            essential = page.get("essential_word_ids", page.get("focus_word_ids", []))
            result[number] = {
                "essential": [int(value) for value in essential],
                "context": [int(value) for value in page.get("context_word_ids", [])],
            }
        return result
