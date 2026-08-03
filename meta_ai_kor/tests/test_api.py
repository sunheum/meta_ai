import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import XLSX_MEDIA_TYPE, create_app
from app.models import (
    ColumnResult,
    NameComponent,
    ValidationReport,
)


ROOT = Path(__file__).parents[2]
SOURCE_PATH = ROOT / "data" / "table_column_template_컬럼코멘트N.xlsx"


class FakeWorkflow:
    def __init__(self):
        self.last_review_rounds = 1
        self.last_validation_report = ValidationReport(
            is_valid=True,
            issues=[],
            stats={"error_count": 0, "warning_count": 0},
        )

    async def run(
        self,
        input_path,
        output_path,
        options,
        progress_callback=None,
    ):
        if progress_callback:
            from app.models import ProgressEvent

            await progress_callback(
                ProgressEvent(
                    stage="input",
                    stage_percent=100,
                    overall_percent=20,
                    message="입력 완료",
                )
            )
        output_path.write_bytes(Path(input_path).read_bytes())
        result = ColumnResult(
            source_id="row-2",
            components=[
                NameComponent(
                    source_fragment="DT",
                    full_name="DATE",
                    korean_word="일자",
                    origin="mapping",
                )
            ],
            english_full_name="DATE",
            korean_attribute_name="일자",
            status="자동확정",
            confidence=90,
            evidence="DT→DATE→일자[mapping]",
        )
        return [], [result]


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                llm_enabled=False,
                results_dir=str(tmp_path / "results"),
                progress_heartbeat_seconds=0.01,
            ),
            FakeWorkflow(),  # type: ignore[arg-type]
        )
    )


def test_health_and_sync_xlsx_response(tmp_path):
    client = _client(tmp_path)

    health = client.get("/health")
    with SOURCE_PATH.open("rb") as handle:
        response = client.post(
            "/v1/korean-column-names",
            files={
                "file": (
                    "input.xlsx",
                    handle,
                    XLSX_MEDIA_TYPE,
                )
            },
            data={"use_llm": "false"},
        )

    assert health.status_code == 200
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert response.headers["x-source-count"] == "1"


def test_rejects_non_xlsx_upload(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/v1/korean-column-names",
        files={"file": ("input.csv", b"a,b", "text/csv")},
    )

    assert response.status_code == 415


def test_async_job_completes_and_downloads(tmp_path):
    with _client(tmp_path) as client:
        with SOURCE_PATH.open("rb") as handle:
            response = client.post(
                "/v1/korean-column-names/jobs",
                files={
                    "file": (
                        "input.xlsx",
                        handle,
                        XLSX_MEDIA_TYPE,
                    )
                },
                data={"use_llm": "false"},
            )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        snapshot = None
        for _ in range(50):
            snapshot = client.get(
                f"/v1/korean-column-names/jobs/{job_id}"
            ).json()
            if snapshot["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)

        assert snapshot is not None
        assert snapshot["status"] == "completed"
        download = client.get(
            f"/v1/korean-column-names/jobs/{job_id}/result"
        )
        assert download.status_code == 200
        assert download.content[:2] == b"PK"
