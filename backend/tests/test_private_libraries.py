import asyncio
import hashlib
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.app import main
from backend.app.models import DocumentRecord
from backend.app.store import DocumentStore


@pytest.mark.asyncio
async def test_independent_visitors(tmp_path, monkeypatch):
    monkeypatch.setattr(main.libraries, "root", tmp_path / "libraries")
    legacy = DocumentStore(tmp_path)
    legacy.create(DocumentRecord(id="legacy", file_name="old.pdf", state="ready"))
    legacy.source_path("legacy", "old.pdf").write_bytes(b"old book")
    transport = httpx.ASGITransport(app=main.app)

    def visitor():
        return httpx.AsyncClient(transport=transport, base_url="https://test.local")

    sample = BytesIO()
    Image.new("RGB", (80, 80), "white").save(sample, format="PDF")
    content = sample.getvalue()
    files = {"file": ("sample.pdf", content, "application/pdf")}

    async with visitor() as alice, visitor() as bob, visitor() as anonymous:
        for client in (alice, bob):
            landing = await client.get("/")
            assert "HttpOnly" in landing.headers["set-cookie"]
            assert "Secure" in landing.headers["set-cookie"]
            assert (await client.get("/api/library")).json() == []
            assert (await client.get("/documents/legacy/source.pdf")).status_code == 404
        responses = await asyncio.gather(*(c.post("/api/documents", files=files) for c in (alice, bob)))
        assert all(r.status_code == 202 for r in responses)
        a, b = [r.json()["document"]["id"] for r in responses]
        assert a != b

        for owner, other, doc in ((alice, bob, a), (bob, alice, b)):
            url = f"/api/documents/{doc}"
            for _ in range(300):
                record = (await owner.get(url)).json()
                if record["state"] in {"ready", "error"}:
                    break
                await asyncio.sleep(0.05)
            assert record["state"] == "ready", record
            assert [r["id"] for r in (await owner.get("/api/library")).json()] == [doc]
            for endpoint in (url, url + "/preferences", url + "/focus/1", url + "/structure",
                             url + "/focus-ready", f"/documents/{doc}/source.pdf",
                             f"/documents/{doc}/scene/manifest.json"):
                assert (await owner.get(endpoint)).status_code == 200
                assert (await other.get(endpoint)).status_code == 404
                assert (await anonymous.get(endpoint)).status_code == 403
            assert (await other.put(url + "/preferences/reading", json={"page": 1, "mode": "focus"})).status_code == 404
            assert (await other.delete(url)).status_code == 404
            lookup = await owner.get("/api/documents/lookup", params={"sha256": hashlib.sha256(content).hexdigest()})
            assert lookup.json()["document"]["id"] == doc
        assert (await alice.delete(f"/api/documents/{a}")).status_code == 204
        assert (await bob.get(f"/api/documents/{b}")).status_code == 200
        assert legacy.get("legacy").state == "ready"
