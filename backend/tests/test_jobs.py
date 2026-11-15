import asyncio
from types import SimpleNamespace

import pytest

from backend.app.jobs import JobManager
from backend.app.models import DocumentRecord
from backend.app.services.focus import FOCUS_STRATEGY_VERSION


@pytest.mark.asyncio
async def test_cancel_waits_for_an_active_job():
    manager = JobManager(settings=None, store=None)
    task = asyncio.create_task(asyncio.Event().wait())
    manager._tasks["book"] = task

    await manager.cancel("book")

    assert task.cancelled()


def test_resume_refreshes_stale_jev_focus():
    stale = DocumentRecord(
        id="book",
        file_name="book.pdf",
        state="ready",
        focus_provider="jev",
        focus_strategy_version=FOCUS_STRATEGY_VERSION - 1,
    )
    store = SimpleNamespace(list_ready=lambda: [stale], list_incomplete=lambda: [])
    manager = JobManager(SimpleNamespace(focus_provider="jev"), store)
    started = []
    manager.start = started.append

    manager.resume_incomplete()

    assert started == ["book"]
