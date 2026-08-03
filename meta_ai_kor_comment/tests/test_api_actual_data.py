from __future__ import annotations

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import Settings
from app.main import XLSX_MEDIA_TYPE, create_app
from app.workflow import KoreanCommentWorkflow


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTUAL_INPUT = REPO_ROOT / "data" / "table_column_template_컬럼코멘트Y.xlsx"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


class DeterministicModel:
    async def generate(self, sources, risks=None):
        return []

    async def review(
        self,
        sources,
        current_results,
        issues,
        review_round,
        terminology_context=None,
    ):
        return []


def _client() -> TestClient:
    settings = Settings(
        results_dir=str(RESULTS_DIR),
        progress_heartbeat_seconds=0.01,
        default_max_review_rounds=0,
    )
    return TestClient(
        create_app(
            settings=settings,
            workflow=KoreanCommentWorkflow(DeterministicModel()),
        )
    )


def test_health_and_sync_endpoint_with_actual_workbook() -> None:
    with _client() as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        with ACTUAL_INPUT.open("rb") as stream:
            response = client.post(
                "/v1/comment-korean-column-names",
                files={
                    "file": (
                        ACTUAL_INPUT.name,
                        stream,
                        XLSX_MEDIA_TYPE,
                    )
                },
                data={"max_review_rounds": "0"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
        assert response.headers["x-source-count"] == "1195"
        assert response.headers["x-result-count"] == "1195"
        assert response.headers["x-validation-errors"] == "0"

        workbook = load_workbook(io.BytesIO(response.content), read_only=True)
        try:
            assert workbook.sheetnames == ["한글속성명_결과", "검토필요"]
            assert workbook["한글속성명_결과"].max_row == 1196
        finally:
            workbook.close()


def test_async_job_status_and_progress_with_actual_workbook() -> None:
    result_path: Path | None = None
    try:
        with _client() as client:
            with ACTUAL_INPUT.open("rb") as stream:
                created = client.post(
                    "/v1/comment-korean-column-names/jobs",
                    files={
                        "file": (
                            ACTUAL_INPUT.name,
                            stream,
                            XLSX_MEDIA_TYPE,
                        )
                    },
                    data={"max_review_rounds": "0"},
                )
            assert created.status_code == 202
            job_id = created.json()["job_id"]

            snapshot = None
            for _ in range(200):
                response = client.get(
                    f"/v1/comment-korean-column-names/jobs/{job_id}"
                )
                assert response.status_code == 200
                snapshot = response.json()
                if snapshot["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.025)

            assert snapshot is not None
            assert snapshot["status"] == "completed"
            metadata = snapshot["result_metadata"]
            assert metadata["source_count"] == 1195
            assert metadata["result_count"] == 1195
            assert metadata["validation_stats"]["error_count"] == 0
            result_path = Path(metadata["result_path"])
            assert result_path.exists()

            progress = client.get(
                f"/v1/comment-korean-column-names/jobs/{job_id}/progress"
            )
            assert progress.status_code == 200
            assert "완료" in progress.text
            assert "XLSX 저장 경로" in progress.text
    finally:
        if result_path is not None:
            result_path.unlink(missing_ok=True)
