import asyncio

import pytest

from app.jobs import JobStore, render_heartbeat_line, render_progress_line
from app.models import ProgressEvent


@pytest.mark.asyncio
async def test_job_store_tracks_progress_and_completion():
    store = JobStore()
    job = await store.create()
    await job.publish(
        ProgressEvent(
            stage="input",
            stage_percent=50,
            overall_percent=10,
            message="입력 확인",
        )
    )
    await job.complete({"result_path": "result.xlsx"})

    snapshot = job.snapshot()

    assert snapshot["status"] == "completed"
    assert snapshot["latest_event"]["stage"] == "input"
    assert "입력 확인" in render_progress_line(job.events[0])
    assert "연결 유지" in render_heartbeat_line(job)
    assert await store.get(job.job_id) is job


@pytest.mark.asyncio
async def test_job_event_iterator_yields_heartbeat():
    store = JobStore()
    job = await store.create()
    iterator = job.iter_events(heartbeat_seconds=0.01)

    event = await asyncio.wait_for(anext(iterator), timeout=0.2)

    assert event is None
    await iterator.aclose()

