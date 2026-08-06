import asyncio

from app.jobs import JobRecord, JobStore, render_heartbeat_line, render_progress_line
from app.models import ProgressEvent


def test_job_timing_accumulates_repeated_stages_and_stops_at_completion(
    monkeypatch,
) -> None:
    clock = {"now": 1.0}
    monkeypatch.setattr("app.jobs.time.monotonic", lambda: clock["now"])
    job = JobRecord(job_id="timing-test", started_monotonic=0.0)

    async def execute() -> None:
        await job.publish(_event("queued"))
        clock["now"] = 2.0
        await job.publish(_event("normalize"))
        clock["now"] = 4.0
        await job.publish(_event("validate"))
        clock["now"] = 5.0
        await job.publish(_event("review"))
        clock["now"] = 7.0
        await job.publish(_event("validate"))
        clock["now"] = 10.0
        await job.complete({})

    asyncio.run(execute())
    clock["now"] = 100.0

    timing = job.snapshot()["timing"]
    assert timing["total_elapsed_seconds"] == 10.0
    assert timing["stage_durations_seconds"] == {
        "queued": 2.0,
        "normalize": 2.0,
        "validate": 4.0,
        "review": 2.0,
    }
    assert job.result_metadata["total_elapsed_seconds"] == 10.0


def test_progress_and_heartbeat_are_human_readable() -> None:
    job = JobRecord(job_id="display")
    event = _event("reconcile")
    line = render_progress_line(event)
    heartbeat = render_heartbeat_line(job)
    assert "용어 통일" in line
    assert "████" not in line
    assert "연결 유지" in heartbeat
    assert "작업 응답을 기다리는 중" in heartbeat


def test_store_shutdown_cancels_and_settles_active_jobs() -> None:
    async def execute() -> JobRecord:
        store = JobStore()
        job = await store.create()

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        job.task = asyncio.create_task(wait_forever())
        await store.shutdown()
        return job

    job = asyncio.run(execute())
    assert job.task is not None and job.task.cancelled()
    assert job.status == "failed"
    assert job.error_status_code == 503
    assert job.error_payload == {"detail": "서비스 종료로 작업이 취소되었습니다."}


def _event(stage: str) -> ProgressEvent:
    return ProgressEvent(
        stage=stage,
        stage_percent=0,
        overall_percent=0,
        message=stage,
    )
