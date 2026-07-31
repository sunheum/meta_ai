import asyncio

from app.jobs import JobRecord
from app.models import ProgressEvent


def test_job_timing_accumulates_repeated_stages_and_stops_at_completion(
    monkeypatch,
) -> None:
    clock = {"now": 1.0}
    monkeypatch.setattr(
        "app.jobs.time.monotonic",
        lambda: clock["now"],
    )
    job = JobRecord(job_id="timing-test", started_monotonic=0.0)

    async def execute() -> None:
        await job.publish(_event("queued"))
        clock["now"] = 2.0
        await job.publish(_event("validate"))
        clock["now"] = 4.0
        await job.publish(_event("validate"))
        clock["now"] = 5.0
        await job.publish(_event("review"))
        clock["now"] = 7.0
        await job.publish(_event("validate"))
        clock["now"] = 10.0
        await job.publish(_event("validate"))
        clock["now"] = 11.0
        await job.complete({})

    asyncio.run(execute())
    clock["now"] = 100.0

    timing = job.snapshot()["timing"]
    assert timing["total_elapsed_seconds"] == 11.0
    assert timing["stage_durations_seconds"] == {
        "queued": 2.0,
        "validate": 7.0,
        "review": 2.0,
    }
    assert job.result_metadata["total_elapsed_seconds"] == 11.0


def _event(stage: str) -> ProgressEvent:
    return ProgressEvent(
        stage=stage,
        stage_percent=0,
        overall_percent=0,
        message=stage,
    )
