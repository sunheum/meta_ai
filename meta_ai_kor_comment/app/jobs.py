from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

from app.models import ProgressEvent

JobStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES = {"completed", "failed"}
STAGE_LABELS = {
    "queued": "대기",
    "input": "입력",
    "normalize": "정규화",
    "generate": "LLM 생성",
    "reconcile": "용어 통일",
    "validate": "검증",
    "review": "LLM 리뷰",
    "output": "XLSX 출력",
    "failed": "실패",
}
STAGE_ORDER = tuple(STAGE_LABELS)


@dataclass(slots=True)
class JobRecord:
    job_id: str
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[ProgressEvent] = field(default_factory=list)
    result_metadata: dict[str, Any] = field(default_factory=dict)
    error_status_code: int | None = None
    error_payload: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    started_monotonic: float = field(default_factory=time.monotonic)
    finished_monotonic: float | None = None
    active_stage: str | None = None
    active_stage_started_monotonic: float | None = None
    stage_durations_seconds: dict[str, float] = field(default_factory=dict)

    async def publish(self, event: ProgressEvent) -> None:
        async with self.condition:
            now = time.monotonic()
            if self.status == "queued" and event.stage != "queued":
                self.status = "running"
            self._switch_stage(event.stage, now)
            timing = self._timing_snapshot(now)
            timed_event = event.model_copy(
                update={
                    "stage_elapsed_seconds": timing[
                        "current_stage_elapsed_seconds"
                    ],
                    "total_elapsed_seconds": timing["total_elapsed_seconds"],
                }
            )
            self.events.append(timed_event)
            self.updated_at = datetime.now(timezone.utc)
            self.condition.notify_all()

    async def complete(self, metadata: dict[str, Any]) -> None:
        async with self.condition:
            now = time.monotonic()
            self._finish_active_stage(now)
            self.finished_monotonic = now
            timing = self._timing_snapshot(now)
            self.result_metadata = {
                **metadata,
                "total_elapsed_seconds": timing["total_elapsed_seconds"],
                "stage_durations_seconds": timing["stage_durations_seconds"],
            }
            self.status = "completed"
            self.updated_at = datetime.now(timezone.utc)
            self.condition.notify_all()

    async def fail(
        self, status_code: int, payload: dict[str, Any], message: str
    ) -> None:
        await self.publish(
            ProgressEvent(
                stage="failed",
                stage_percent=100,
                overall_percent=_last_overall_percent(self.events),
                message=message,
                details={"status_code": status_code},
            )
        )
        async with self.condition:
            now = time.monotonic()
            self._finish_active_stage(now)
            self.finished_monotonic = now
            self.error_status_code = status_code
            self.error_payload = payload
            self.status = "failed"
            self.updated_at = datetime.now(timezone.utc)
            self.condition.notify_all()

    async def iter_events(
        self, heartbeat_seconds: float = 15.0
    ) -> AsyncIterator[ProgressEvent | None]:
        index = 0
        while True:
            heartbeat = False
            async with self.condition:
                try:
                    await asyncio.wait_for(
                        self.condition.wait_for(
                            lambda: len(self.events) > index
                            or self.status in TERMINAL_STATUSES
                        ),
                        timeout=heartbeat_seconds,
                    )
                except TimeoutError:
                    heartbeat = True
                new_events = self.events[index:]
                index = len(self.events)
                terminal = self.status in TERMINAL_STATUSES
            if heartbeat and not new_events:
                yield None
                continue
            for event in new_events:
                yield event
            if terminal and index >= len(self.events):
                return

    def snapshot(self) -> dict[str, Any]:
        latest = self.events[-1].model_dump(mode="json") if self.events else None
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "latest_event": latest,
            "timing": self.timing_snapshot(),
            "result_metadata": self.result_metadata,
            "error": self.error_payload,
        }

    def timing_snapshot(self) -> dict[str, Any]:
        return self._timing_snapshot(time.monotonic())

    def _switch_stage(self, stage: str, now: float) -> None:
        if self.active_stage == stage:
            return
        self._finish_active_stage(now)
        self.active_stage = stage
        self.active_stage_started_monotonic = (
            self.started_monotonic if not self.events else now
        )

    def _finish_active_stage(self, now: float) -> None:
        if (
            self.active_stage is None
            or self.active_stage_started_monotonic is None
        ):
            return
        elapsed = max(0.0, now - self.active_stage_started_monotonic)
        self.stage_durations_seconds[self.active_stage] = (
            self.stage_durations_seconds.get(self.active_stage, 0.0) + elapsed
        )
        self.active_stage = None
        self.active_stage_started_monotonic = None

    def _timing_snapshot(self, now: float) -> dict[str, Any]:
        effective_now = (
            self.finished_monotonic
            if self.finished_monotonic is not None
            else now
        )
        durations = dict(self.stage_durations_seconds)
        if (
            self.active_stage is not None
            and self.active_stage_started_monotonic is not None
        ):
            elapsed = max(0.0, effective_now - self.active_stage_started_monotonic)
            durations[self.active_stage] = durations.get(self.active_stage, 0.0) + elapsed
        return {
            "total_elapsed_seconds": round(
                max(0.0, effective_now - self.started_monotonic), 3
            ),
            "current_stage": self.active_stage,
            "current_stage_elapsed_seconds": round(
                durations.get(self.active_stage, 0.0)
                if self.active_stage is not None
                else 0.0,
                3,
            ),
            "stage_durations_seconds": {
                stage: round(duration, 3) for stage, duration in durations.items()
            },
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> JobRecord:
        job = JobRecord(job_id=uuid4().hex)
        async with self._lock:
            self._jobs[job.job_id] = job
        return job

    async def get(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def shutdown(self) -> None:
        """Cancel and settle jobs before shared workflow resources are closed."""

        async with self._lock:
            active = [
                job
                for job in self._jobs.values()
                if job.task is not None and not job.task.done()
            ]
        for job in active:
            job.task.cancel()
        if active:
            await asyncio.gather(
                *(job.task for job in active if job.task is not None),
                return_exceptions=True,
            )
        for job in active:
            if job.status not in TERMINAL_STATUSES:
                await job.fail(
                    503,
                    {"detail": "서비스 종료로 작업이 취소되었습니다."},
                    "서비스 종료로 실행 중인 작업을 안전하게 중단했습니다.",
                )


def render_progress_line(event: ProgressEvent, width: int = 30) -> str:
    filled = round(width * event.stage_percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    label = STAGE_LABELS.get(event.stage, event.stage)
    return (
        f"[{label:<9}] [{bar}] {event.stage_percent:>3}% "
        f"(전체 {event.overall_percent:>3}%) | {event.message} | "
        f"단계 누적 {format_duration(event.stage_elapsed_seconds)} · "
        f"전체 소요 {format_duration(event.total_elapsed_seconds)}\n"
    )


def render_heartbeat_line(job: JobRecord, width: int = 30) -> str:
    overall_percent = _last_overall_percent(job.events)
    timing = job.timing_snapshot()
    filled = round(width * overall_percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return (
        f"[연결 유지  ] [{bar}]     "
        f"(전체 {overall_percent:>3}%) | 작업 응답을 기다리는 중입니다. | "
        f"단계 누적 {format_duration(timing['current_stage_elapsed_seconds'])} · "
        f"전체 소요 {format_duration(timing['total_elapsed_seconds'])}\n"
    )


def render_timing_summary(job: JobRecord) -> str:
    timing = job.timing_snapshot()
    durations = timing["stage_durations_seconds"]
    lines = ["\n단계별 소요시간\n"]
    for stage in STAGE_ORDER:
        if stage in durations:
            lines.append(
                f"- {STAGE_LABELS[stage]}: {format_duration(durations[stage])}\n"
            )
    for stage in sorted(set(durations).difference(STAGE_ORDER)):
        lines.append(f"- {stage}: {format_duration(durations[stage])}\n")
    lines.append(f"- 전체: {format_duration(timing['total_elapsed_seconds'])}\n")
    return "".join(lines)


def format_duration(seconds: float) -> str:
    safe_seconds = max(0.0, seconds)
    hours = int(safe_seconds // 3600)
    minutes = int((safe_seconds % 3600) // 60)
    remaining_seconds = safe_seconds % 60
    if hours:
        return f"{hours:02}:{minutes:02}:{remaining_seconds:04.1f}"
    return f"{minutes:02}:{remaining_seconds:04.1f}"


def _last_overall_percent(events: list[ProgressEvent]) -> int:
    return events[-1].overall_percent if events else 0
