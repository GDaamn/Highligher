import asyncio

import pytest

from backend.app.jobs import JobManager


@pytest.mark.asyncio
async def test_cancel_waits_for_an_active_job():
    manager = JobManager(settings=None, store=None)
    task = asyncio.create_task(asyncio.Event().wait())
    manager._tasks["book"] = task

    await manager.cancel("book")

    assert task.cancelled()
