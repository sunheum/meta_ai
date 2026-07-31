from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app.config import Settings
from app.exceptions import InputWorkbookError, LLMResponseError
from app.glossary import MappingGlossary
from app.jobs import (
    JobRecord,
    JobStore,
    render_heartbeat_line,
    render_progress_line,
    render_timing_summary,
)
from app.llm import LocalChatNamingModel
from app.models import ProgressEvent, WorkflowOptions
from app.workflow import NamingWorkflow

logger = logging.getLogger(__name__)
XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def create_app(
    settings: Settings | None = None,
    workflow: NamingWorkflow | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_workflow = workflow or _default_workflow(resolved_settings)
    job_store = JobStore()
    app = FastAPI(
        title="컬럼 한글속성명 생성 API",
        version="0.1.0",
        description=(
            "컬럼코멘트가 없는 영문 컬럼명에 영문 Full Name과 공백 없는 "
            "한글속성명을 생성하고 검증·리뷰 후 XLSX로 반환합니다."
        ),
    )
    app.state.job_store = job_store
    app.state.workflow = resolved_workflow

    @app.get("/health")
    async def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "model": resolved_settings.llm_model,
            "llm_enabled": resolved_settings.llm_enabled,
        }

    @app.post(
        "/v1/korean-column-names",
        response_class=Response,
        responses={
            200: {
                "content": {XLSX_MEDIA_TYPE: {}},
                "description": "한글속성명 결과 XLSX",
            },
            422: {"description": "입력 파일 검증 실패"},
            502: {"description": "로컬 LLM 호출/응답 실패"},
        },
    )
    async def create_korean_column_names(
        file: UploadFile = File(...),
        batch_size: int = Form(
            resolved_settings.default_batch_size,
            ge=1,
            le=100,
        ),
        max_concurrency: int = Form(
            resolved_settings.default_max_concurrency,
            ge=1,
            le=50,
        ),
        max_review_rounds: int = Form(
            resolved_settings.default_max_review_rounds,
            ge=0,
            le=5,
        ),
        auto_confirm_threshold: int = Form(
            resolved_settings.auto_confirm_threshold,
            ge=0,
            le=100,
        ),
        use_llm: bool = Form(True),
    ) -> Response:
        content = await _read_upload(file, resolved_settings.max_upload_mb)
        options = WorkflowOptions(
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            max_review_rounds=max_review_rounds,
            auto_confirm_threshold=auto_confirm_threshold,
            use_llm=use_llm and resolved_settings.llm_enabled,
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="korean-column-namer-"
            ) as temp_dir:
                input_path = Path(temp_dir) / "input.xlsx"
                output_path = Path(temp_dir) / "korean_column_names.xlsx"
                input_path.write_bytes(content)
                _, results = await resolved_workflow.run(
                    input_path,
                    output_path,
                    options,
                )
                output = output_path.read_bytes()
        except InputWorkbookError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        status_counts = Counter(result.status for result in results)
        report = resolved_workflow.last_validation_report
        report_stats = report.stats if report is not None else {}
        headers = {
            "Content-Disposition": (
                'attachment; filename="korean_column_names.xlsx"; '
                "filename*=UTF-8''%ED%95%9C%EA%B8%80%EC%86%8D%EC%84%B1%EB%AA%85.xlsx"
            ),
            "X-Source-Count": str(len(results)),
            "X-Auto-Confirmed-Count": str(
                status_counts.get("자동확정", 0)
            ),
            "X-Review-Needed-Count": str(
                status_counts.get("검토필요", 0)
            ),
            "X-Validation-Failed-Count": str(
                status_counts.get("검증실패", 0)
            ),
            "X-Review-Rounds": str(
                resolved_workflow.last_review_rounds
            ),
            "X-Validation-Summary": json.dumps(
                report_stats,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        }
        return Response(
            content=output,
            media_type=XLSX_MEDIA_TYPE,
            headers=headers,
        )

    @app.post(
        "/v1/korean-column-names/jobs",
        status_code=202,
    )
    async def create_korean_column_name_job(
        request: Request,
        file: UploadFile = File(...),
        batch_size: int = Form(
            resolved_settings.default_batch_size,
            ge=1,
            le=100,
        ),
        max_concurrency: int = Form(
            resolved_settings.default_max_concurrency,
            ge=1,
            le=50,
        ),
        max_review_rounds: int = Form(
            resolved_settings.default_max_review_rounds,
            ge=0,
            le=5,
        ),
        auto_confirm_threshold: int = Form(
            resolved_settings.auto_confirm_threshold,
            ge=0,
            le=100,
        ),
        use_llm: bool = Form(True),
    ) -> JSONResponse:
        content = await _read_upload(file, resolved_settings.max_upload_mb)
        options = WorkflowOptions(
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            max_review_rounds=max_review_rounds,
            auto_confirm_threshold=auto_confirm_threshold,
            use_llm=use_llm and resolved_settings.llm_enabled,
        )
        job = await job_store.create()
        result_path = _results_dir(resolved_settings) / f"{job.job_id}.xlsx"
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
            _run_job(
                job,
                content,
                options,
                resolved_workflow,
                result_path,
            ),
            name=f"korean-column-name-{job.job_id}",
        )
        base_url = str(request.base_url).rstrip("/")
        path = f"/v1/korean-column-names/jobs/{job.job_id}"
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.job_id,
                "status": job.status,
                "status_url": f"{base_url}{path}",
                "progress_url": f"{base_url}{path}/progress",
                "result_url": f"{base_url}{path}/result",
            },
        )

    @app.get("/v1/korean-column-names/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        return (await _get_job_or_404(job_store, job_id)).snapshot()

    @app.get(
        "/v1/korean-column-names/jobs/{job_id}/progress",
        response_class=StreamingResponse,
    )
    async def stream_progress(job_id: str) -> StreamingResponse:
        job = await _get_job_or_404(job_store, job_id)

        async def stream():
            yield f"작업 ID: {job.job_id}\n"
            yield "단계별 진행 상황\n"
            async for event in job.iter_events(
                heartbeat_seconds=(
                    resolved_settings.progress_heartbeat_seconds
                )
            ):
                if event is None:
                    yield render_heartbeat_line(job)
                else:
                    yield render_progress_line(event)
            yield render_timing_summary(job)
            if job.status == "completed":
                yield f"\n완료: {job.result_metadata.get('result_path', '')}\n"
            else:
                yield "\n작업이 실패했습니다. 상태 API를 확인하세요.\n"

        return StreamingResponse(
            stream(),
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/korean-column-names/jobs/{job_id}/result")
    async def download_result(job_id: str):
        job = await _get_job_or_404(job_store, job_id)
        if job.status != "completed":
            raise HTTPException(
                status_code=409,
                detail="작업이 아직 완료되지 않았습니다.",
            )
        result_path = Path(str(job.result_metadata["result_path"]))
        if not result_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="결과 파일을 찾을 수 없습니다.",
            )
        return FileResponse(
            result_path,
            media_type=XLSX_MEDIA_TYPE,
            filename="korean_column_names.xlsx",
        )

    return app


def _default_workflow(settings: Settings) -> NamingWorkflow:
    mapping_path = Path(settings.mapping_workbook_path)
    if not mapping_path.is_absolute():
        mapping_path = Path(__file__).parents[1] / mapping_path
    glossary = MappingGlossary.from_xlsx(mapping_path)
    model = LocalChatNamingModel(settings) if settings.llm_enabled else None
    return NamingWorkflow(
        glossary,
        model,
        strict_llm=settings.strict_llm,
        max_segmentation_candidates=settings.max_segmentation_candidates,
    )


async def _read_upload(file: UploadFile, max_upload_mb: int) -> bytes:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=415, detail=".xlsx 파일만 지원합니다.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(content) > max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"업로드 제한은 {max_upload_mb}MB입니다.",
        )
    return content


async def _get_job_or_404(store: JobStore, job_id: str) -> JobRecord:
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


async def _run_job(
    job: JobRecord,
    content: bytes,
    options: WorkflowOptions,
    workflow: NamingWorkflow,
    result_path: Path,
) -> None:
    try:
        with tempfile.TemporaryDirectory(
            prefix="korean-column-namer-job-"
        ) as temp_dir:
            input_path = Path(temp_dir) / "input.xlsx"
            output_path = Path(temp_dir) / "korean_column_names.xlsx"
            input_path.write_bytes(content)
            _, results = await workflow.run(
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
        status_counts = Counter(result.status for result in results)
        report = workflow.last_validation_report
        await job.complete(
            {
                "result_path": str(result_path.resolve()),
                "file_size_bytes": result_path.stat().st_size,
                "source_count": len(results),
                "auto_confirmed_count": status_counts.get("자동확정", 0),
                "review_needed_count": status_counts.get("검토필요", 0),
                "validation_failed_count": status_counts.get("검증실패", 0),
                "review_rounds": workflow.last_review_rounds,
                "validation_stats": report.stats if report else {},
            }
        )
    except InputWorkbookError as exc:
        await job.fail(422, {"detail": str(exc)}, str(exc))
    except LLMResponseError as exc:
        await job.fail(502, {"detail": str(exc)}, str(exc))
    except Exception:
        logger.exception("비동기 컬럼 한글화 작업 실패: %s", job.job_id)
        await job.fail(
            500,
            {"detail": "작업 실행 중 예상하지 못한 오류가 발생했습니다."},
            "예상하지 못한 내부 오류가 발생했습니다.",
        )


def _persist_result_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.tmp")
    try:
        shutil.copyfile(source, staging)
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)


def _results_dir(settings: Settings) -> Path:
    path = Path(settings.results_dir)
    if not path.is_absolute():
        path = Path(__file__).parents[1] / path
    return path


app = create_app()

