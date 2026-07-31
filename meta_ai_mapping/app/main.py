from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import Settings
from app.exceptions import (
    InputWorkbookError,
    LLMResponseError,
)
from app.glossary import load_canonical_glossary
from app.jobs import (
    JobRecord,
    JobStore,
    render_heartbeat_line,
    render_progress_line,
    render_timing_summary,
)
from app.llm import LocalChatMappingModel
from app.models import ProgressEvent, WorkflowOptions
from app.workflow import MappingWorkflow

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    workflow: MappingWorkflow | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_workflow = workflow or MappingWorkflow(
        LocalChatMappingModel(resolved_settings),
        glossary=load_canonical_glossary(
            resolved_settings.canonical_glossary_path
        ),
    )
    job_store = JobStore()
    app = FastAPI(
        title="컬럼 약어 매핑 API",
        version="0.1.0",
        description=(
            "컬럼명·컬럼설명에서 영문약어/Full Name/한글단어 매핑을 생성하고 "
            "검증·LLM 리뷰 루프 후 XLSX로 반환합니다."
        ),
    )
    app.state.job_store = job_store

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": resolved_settings.llm_model}

    @app.post(
        "/v1/abbreviation-mappings",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
                },
                "description": "생성된 약어 매핑 XLSX",
            },
            422: {"description": "입력 파일 검증 실패"},
            502: {"description": "로컬 LLM 호출/응답 실패"},
        },
    )
    async def create_abbreviation_mappings(
        file: UploadFile = File(..., description="table_column_template.xlsx 형식"),
        batch_size: int = Form(resolved_settings.default_batch_size, ge=1, le=100),
        max_concurrency: int = Form(
            resolved_settings.default_max_concurrency, ge=1, le=50
        ),
        max_review_rounds: int = Form(
            resolved_settings.default_max_review_rounds, ge=0, le=5
        ),
    ) -> Response:
        content = await _read_upload(file, resolved_settings.max_upload_mb)
        options = WorkflowOptions(
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            max_review_rounds=max_review_rounds,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="abbreviation-mapper-") as temp_dir:
                input_path = Path(temp_dir) / "input.xlsx"
                output_path = Path(temp_dir) / "abbreviation_mappings.xlsx"
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
                'attachment; filename="abbreviation_mappings.xlsx"; '
                "filename*=UTF-8''%EC%95%BD%EC%96%B4%EB%A7%A4%ED%95%91.xlsx"
            ),
            "X-Source-Count": str(result.source_count),
            "X-Mapping-Count": str(result.mapping_count),
            "X-Review-Rounds": str(result.review_rounds),
            "X-Partial-Result": str(result.is_partial).lower(),
            "X-Failed-Source-Count": str(result.failed_source_count),
            "X-Validation-Errors": str(report.stats["error_count"]),
            "X-Validation-Warnings": str(report.stats["warning_count"]),
            "X-Validation-Summary": json.dumps(
                report.stats, ensure_ascii=True, separators=(",", ":")
            ),
        }
        return Response(
            content=output,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers=headers,
        )

    @app.post(
        "/v1/abbreviation-mappings/jobs",
        status_code=202,
        responses={
            202: {"description": "비동기 작업 생성 완료"},
            413: {"description": "업로드 크기 초과"},
            415: {"description": "지원하지 않는 파일 형식"},
        },
    )
    async def create_abbreviation_mapping_job(
        request: Request,
        file: UploadFile = File(..., description="table_column_template.xlsx 형식"),
        batch_size: int = Form(resolved_settings.default_batch_size, ge=1, le=100),
        max_concurrency: int = Form(
            resolved_settings.default_max_concurrency, ge=1, le=50
        ),
        max_review_rounds: int = Form(
            resolved_settings.default_max_review_rounds, ge=0, le=5
        ),
    ) -> JSONResponse:
        content = await _read_upload(file, resolved_settings.max_upload_mb)
        options = WorkflowOptions(
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            max_review_rounds=max_review_rounds,
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
            _run_mapping_job(
                job,
                content,
                options,
                resolved_workflow,
                result_path,
            ),
            name=f"abbreviation-mapping-{job.job_id}",
        )
        base_url = str(request.base_url).rstrip("/")
        job_path = f"/v1/abbreviation-mappings/jobs/{job.job_id}"
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

    @app.get("/v1/abbreviation-mappings/jobs/{job_id}")
    async def get_abbreviation_mapping_job(job_id: str) -> dict:
        job = await _get_job_or_404(job_store, job_id)
        return job.snapshot()

    @app.get(
        "/v1/abbreviation-mappings/jobs/{job_id}/progress",
        response_class=StreamingResponse,
    )
    async def stream_abbreviation_mapping_progress(
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
            elif job.status == "failed":
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


async def _run_mapping_job(
    job: JobRecord,
    content: bytes,
    options: WorkflowOptions,
    workflow: MappingWorkflow,
    result_path: Path,
) -> None:
    try:
        with tempfile.TemporaryDirectory(prefix="abbreviation-mapper-job-") as temp_dir:
            input_path = Path(temp_dir) / "input.xlsx"
            output_path = Path(temp_dir) / "abbreviation_mappings.xlsx"
            input_path.write_bytes(content)
            result = await workflow.run(
                input_path,
                output_path,
                options,
                progress_callback=job.publish,
            )
            await asyncio.to_thread(
                _persist_result_file,
                output_path,
                result_path,
            )
        result_path_text = result_path.as_posix()
        await job.publish(
            ProgressEvent(
                stage="output",
                stage_percent=100,
                overall_percent=100,
                message=f"결과 저장 완료 · {result_path_text}",
                details={
                    "result_path": result_path_text,
                    "file_size_bytes": result_path.stat().st_size,
                },
            )
        )
        await job.complete(
            {
                "result_path": result_path_text,
                "file_size_bytes": result_path.stat().st_size,
                "source_count": result.source_count,
                "mapping_count": result.mapping_count,
                "failed_source_count": result.failed_source_count,
                "is_partial": result.is_partial,
                "review_rounds": result.review_rounds,
                "validation_stats": result.validation_report.stats,
                "reconciliation_stats": result.reconciliation_stats,
            },
        )
    except InputWorkbookError as exc:
        await job.fail(422, {"detail": str(exc)}, str(exc))
    except LLMResponseError as exc:
        await job.fail(502, {"detail": str(exc)}, str(exc))
    except Exception:
        logger.exception("비동기 약어 매핑 작업 실행 실패: %s", job.job_id)
        await job.fail(
            500,
            {"detail": "작업 실행 중 예상하지 못한 오류가 발생했습니다."},
            "예상하지 못한 내부 오류가 발생했습니다.",
        )


def _persist_result_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_path = destination.with_name(f".{destination.name}.tmp")
    try:
        shutil.copyfile(source, staging_path)
        staging_path.replace(destination)
    finally:
        staging_path.unlink(missing_ok=True)


app = create_app()
