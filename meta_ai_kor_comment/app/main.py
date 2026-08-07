from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import Settings
from app.exceptions import InputWorkbookError, LLMResponseError
from app.jobs import (
    JobRecord,
    JobStore,
    render_heartbeat_line,
    render_progress_line,
    render_timing_summary,
)
from app.llm import LocalChatKoreanNamingModel
from app.models import ProgressEvent, WorkflowOptions
from app.rules import DomainRules, load_rules, load_rules_optional

logger = logging.getLogger(__name__)
XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def create_app(
    settings: Settings | None = None,
    workflow: Any | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    default_rules = load_rules_optional(resolved_settings.rules_path)
    resolved_workflow = workflow or _create_default_workflow(
        resolved_settings, default_rules
    )
    job_store = JobStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await job_store.shutdown()
            close = getattr(resolved_workflow, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(
        title="컬럼설명 기반 한글속성명 생성 API",
        version="0.1.0",
        description=(
            "컬럼설명을 주 근거로 한글속성명을 생성하고 용어 통일, "
            "결정적 검증과 오류행 리뷰를 거쳐 XLSX로 반환합니다."
        ),
        lifespan=lifespan,
    )
    app.state.job_store = job_store
    app.state.settings = resolved_settings
    app.state.workflow = resolved_workflow

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": resolved_settings.llm_model}

    @app.post(
        "/v1/comment-korean-column-names",
        response_class=Response,
        responses={
            200: {
                "content": {XLSX_MEDIA_TYPE: {}},
                "description": "한글속성명 생성 결과 XLSX",
            },
            422: {"description": "입력 파일 검증 실패"},
            502: {"description": "로컬 LLM 호출/응답 실패"},
        },
    )
    async def create_comment_korean_column_names(
        file: UploadFile = File(..., description="컬럼설명 포함 XLSX"),
        batch_size: int = Form(resolved_settings.default_batch_size, ge=1, le=100),
        max_concurrency: int = Form(
            resolved_settings.default_max_concurrency, ge=1, le=50
        ),
        max_review_rounds: int = Form(
            resolved_settings.default_max_review_rounds, ge=0, le=5
        ),
        auto_confirm_threshold: int = Form(
            resolved_settings.default_auto_confirm_threshold, ge=0, le=100
        ),
    ) -> Response:
        content = await _read_upload(file, resolved_settings.max_upload_mb)
        options = WorkflowOptions(
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            max_review_rounds=max_review_rounds,
            auto_confirm_threshold=auto_confirm_threshold,
        )
        try:
            with _temporary_xlsx_paths(
                Path(resolved_settings.results_dir),
                prefix="korean-comment-name",
            ) as (input_path, output_path):
                input_path.write_bytes(content)
                result = await resolved_workflow.run(input_path, output_path, options)
                output = output_path.read_bytes()
        except InputWorkbookError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        report = result.validation_report
        headers = {
            "Content-Disposition": (
                'attachment; filename="korean_attribute_names.xlsx"; '
                "filename*=UTF-8''%ED%95%9C%EA%B8%80%EC%86%8D%EC%84%B1%EB%AA%85.xlsx"
            ),
            "X-Source-Count": str(result.source_count),
            "X-Result-Count": str(result.result_count),
            "X-Auto-Confirmed-Count": str(result.auto_confirmed_count),
            "X-Review-Required-Count": str(result.review_required_count),
            "X-Validation-Failed-Count": str(result.validation_failed_count),
            "X-Review-Rounds": str(result.review_rounds),
            "X-Partial-Result": str(result.is_partial).lower(),
            "X-Validation-Errors": str(report.stats.get("error_count", 0)),
            "X-Validation-Warnings": str(report.stats.get("warning_count", 0)),
            "X-Generation-Fallback-Count": str(
                result.recovery_stats.get("generation_fallback_count", 0)
            ),
            "X-Review-Failure-Count": str(
                result.recovery_stats.get("review_failure_count", 0)
            ),
            "X-Validation-Summary": json.dumps(
                report.stats, ensure_ascii=True, separators=(",", ":")
            ),
        }
        return Response(
            content=output,
            media_type=XLSX_MEDIA_TYPE,
            headers=headers,
        )

    @app.post(
        "/v1/comment-korean-column-names/jobs",
        status_code=202,
        responses={
            202: {"description": "비동기 작업 생성 완료"},
            413: {"description": "업로드 크기 초과"},
            415: {"description": "지원하지 않는 파일 형식"},
        },
    )
    async def create_comment_korean_column_name_job(
        request: Request,
        file: UploadFile = File(..., description="컬럼설명 포함 XLSX"),
        batch_size: int = Form(resolved_settings.default_batch_size, ge=1, le=100),
        max_concurrency: int = Form(
            resolved_settings.default_max_concurrency, ge=1, le=50
        ),
        max_review_rounds: int = Form(
            resolved_settings.default_max_review_rounds, ge=0, le=5
        ),
        auto_confirm_threshold: int = Form(
            resolved_settings.default_auto_confirm_threshold, ge=0, le=100
        ),
    ) -> JSONResponse:
        content = await _read_upload(file, resolved_settings.max_upload_mb)
        options = WorkflowOptions(
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            max_review_rounds=max_review_rounds,
            auto_confirm_threshold=auto_confirm_threshold,
        )
        job = await job_store.create()
        result_path = Path(resolved_settings.results_dir) / f"{job.job_id}.xlsx"
        await job.publish(
            ProgressEvent(
                stage="queued",
                stage_percent=0,
                overall_percent=0,
                message="작업이 생성되어 실행을 기다리고 있습니다.",
                details={"filename": file.filename},
            )
        )
        job.task = asyncio.create_task(
            _run_generation_job(
                job,
                content,
                options,
                resolved_workflow,
                result_path,
            ),
            name=f"comment-korean-column-names-{job.job_id}",
        )
        base_url = str(request.base_url).rstrip("/")
        job_path = f"/v1/comment-korean-column-names/jobs/{job.job_id}"
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.job_id,
                "status": job.status,
                "status_url": f"{base_url}{job_path}",
                "progress_url": f"{base_url}{job_path}/progress",
                "result_path": result_path.as_posix(),
                "curl_progress": f"curl -N {base_url}{job_path}/progress",
            },
        )

    @app.get("/v1/comment-korean-column-names/jobs/{job_id}")
    async def get_comment_korean_column_name_job(job_id: str) -> dict[str, Any]:
        job = await _get_job_or_404(job_store, job_id)
        return job.snapshot()

    @app.get(
        "/v1/comment-korean-column-names/jobs/{job_id}/progress",
        response_class=StreamingResponse,
    )
    async def stream_comment_korean_column_name_progress(
        job_id: str,
    ) -> StreamingResponse:
        job = await _get_job_or_404(job_store, job_id)

        async def stream():
            yield f"작업 ID: {job.job_id}\n"
            yield "단계별 진행 상황\n"
            async for event in job.iter_events(
                heartbeat_seconds=resolved_settings.progress_heartbeat_seconds
            ):
                if event is None:
                    yield render_heartbeat_line(job)
                else:
                    yield render_progress_line(event)
            yield render_timing_summary(job)
            if job.status == "completed":
                result_path = job.result_metadata.get("result_path", "")
                yield f"\n완료: XLSX 저장 경로\n{result_path}\n"
            else:
                yield "\n작업이 실패했습니다. 상태 API에서 오류 내용을 확인하세요.\n"

        return StreamingResponse(
            stream(),
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _create_default_workflow(settings: Settings, rules: DomainRules):
    from app.workflow import KoreanCommentWorkflow

    return KoreanCommentWorkflow(
        LocalChatKoreanNamingModel(settings, glossary=rules.glossary_lookup()),
        rules=rules,
    )


async def _read_upload(file: UploadFile, max_upload_mb: int) -> bytes:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=415, detail=".xlsx 파일만 지원합니다.")
    content = await file.read()
    max_bytes = max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"업로드 제한은 {max_upload_mb}MB입니다.",
        )
    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    return content


async def _get_job_or_404(job_store: JobStore, job_id: str) -> JobRecord:
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


async def _run_generation_job(
    job: JobRecord,
    content: bytes,
    options: WorkflowOptions,
    workflow: Any,
    result_path: Path,
) -> None:
    try:
        with _temporary_xlsx_paths(
            result_path.parent,
            prefix=f"korean-comment-name-job-{job.job_id}",
        ) as (input_path, output_path):
            input_path.write_bytes(content)
            result = await workflow.run(
                input_path,
                output_path,
                options,
                progress_callback=job.publish,
            )
            await asyncio.to_thread(_persist_result_file, output_path, result_path)

        result_path_text = result_path.as_posix()
        file_size = result_path.stat().st_size
        await job.publish(
            ProgressEvent(
                stage="output",
                stage_percent=100,
                overall_percent=100,
                message=f"결과 저장 완료 · {result_path_text}",
                details={
                    "result_path": result_path_text,
                    "file_size_bytes": file_size,
                },
            )
        )
        await job.complete(
            {
                "result_path": result_path_text,
                "file_size_bytes": file_size,
                "source_count": result.source_count,
                "result_count": result.result_count,
                "auto_confirmed_count": result.auto_confirmed_count,
                "review_required_count": result.review_required_count,
                "validation_failed_count": result.validation_failed_count,
                "is_partial": result.is_partial,
                "review_rounds": result.review_rounds,
                "validation_stats": result.validation_report.stats,
                "terminology_stats": result.terminology_stats,
                "recovery_stats": result.recovery_stats,
                "recovery_events": result.recovery_events,
                "terminology_decisions": [
                    decision.model_dump(mode="json")
                    for decision in result.terminology_decisions
                ],
            }
        )
    except InputWorkbookError as exc:
        await job.fail(422, {"detail": str(exc)}, str(exc))
    except LLMResponseError as exc:
        await job.fail(502, {"detail": str(exc)}, str(exc))
    except Exception:
        logger.exception("비동기 한글속성명 작업 실행 실패: %s", job.job_id)
        await job.fail(
            500,
            {"detail": "작업 실행 중 예상하지 못한 오류가 발생했습니다."},
            "예상하지 못한 내부 오류가 발생했습니다.",
        )


def _persist_result_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(
        f".{destination.stem}.{uuid4().hex}.tmp{destination.suffix}"
    )
    try:
        shutil.copyfile(source, staging)
        os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


@contextmanager
def _temporary_xlsx_paths(base_dir: Path, *, prefix: str):
    """Yield unique files without creating a nested temporary directory."""

    base_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    descriptors: list[int] = []
    try:
        for role in ("input", "output"):
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{prefix}-{role}-",
                suffix=".xlsx",
                dir=base_dir,
            )
            descriptors.append(descriptor)
            paths.append(Path(raw_path))
        for descriptor in descriptors:
            os.close(descriptor)
        descriptors.clear()
        yield paths[0], paths[1]
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        for path in paths:
            path.unlink(missing_ok=True)


app = create_app()
