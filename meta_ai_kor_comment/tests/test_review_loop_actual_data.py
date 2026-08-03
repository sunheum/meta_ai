from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.models import GenerationResult, ProcessingAction, WorkflowOptions
from app.workflow import KoreanCommentWorkflow


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTUAL_INPUT = REPO_ROOT / "data" / "table_column_template_컬럼코멘트Y.xlsx"


class LowConfidenceThenReviewedModel:
    def __init__(self) -> None:
        self.review_calls = 0
        self.risk_batches = []
        self.terminology_contexts = []

    async def generate(self, sources, risks=None):
        self.risk_batches.append((list(sources), list(risks or [])))
        return [
            GenerationResult(
                source_id=source.source_id,
                original_description=source.column_description,
                korean_attribute_name="주한미군차량여부",
                action=ProcessingAction.REWRITE,
                confidence=70,
                reason="SOFA를 자동차정보 문맥에서 주한미군으로 한글화",
                semantic_units=["주한미군차량여부"],
            )
            for source in sources
            if source.column_name == "SOFA_CR_YN"
        ]

    async def review(
        self,
        sources,
        current_results,
        issues,
        review_round,
        terminology_context=None,
    ):
        self.review_calls += 1
        self.terminology_contexts.append(list(terminology_context or []))
        return [
            result.model_copy(
                update={
                    "confidence": 99,
                    "reason": result.reason + "; 독립 재검토에서 문맥 확인",
                }
            )
            for result in current_results
        ]


@pytest.mark.asyncio
async def test_actual_low_confidence_row_is_reviewed_without_regressing_others() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"test-review-{uuid4().hex}.xlsx"
    model = LowConfidenceThenReviewedModel()
    try:
        result = await KoreanCommentWorkflow(model).run(
            ACTUAL_INPUT,
            output,
            WorkflowOptions(max_review_rounds=2),
        )
        assert model.review_calls == 2
        assert result.review_rounds == 2
        assert result.source_count == 1195
        assert result.validation_report.is_valid
        assert result.validation_failed_count == 0
        assert result.recovery_stats["generation_fallback_count"] > 0
        assert result.recovery_stats["generation_rejected_result_count"] > 0
        assert result.recovery_stats["review_failure_count"] == 0
        assert all(
            [source.source_id for source in sources]
            == [risk.source_id for risk in risks]
            for sources, risks in model.risk_batches
        )
        assert any(model.terminology_contexts)
        workbook = load_workbook(output, read_only=True, data_only=True)
        try:
            sheet = workbook["한글속성명_결과"]
            assert sheet["M190"].value == "주한미군지위협정적용차량여부"
            assert sheet["N28"].value == "자동확정"
            assert sheet["N190"].value == "검토필요"
        finally:
            workbook.close()
    finally:
        output.unlink(missing_ok=True)


class FailingReviewModel:
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
        raise RuntimeError("리뷰 엔드포인트 장애")


@pytest.mark.asyncio
async def test_actual_review_failure_is_traced_without_losing_output() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"test-review-failure-{uuid4().hex}.xlsx"
    try:
        result = await KoreanCommentWorkflow(FailingReviewModel()).run(
            ACTUAL_INPUT,
            output,
            WorkflowOptions(max_review_rounds=2),
        )
        assert result.review_rounds == 1
        assert result.validation_report.is_valid
        assert result.recovery_stats["generation_fallback_count"] > 0
        assert result.recovery_stats["review_failure_count"] > 0
        assert result.recovery_stats["review_unexpected_error_count"] > 0
        assert any(
            event["stage"] == "review"
            and event["code"] == "unexpected_error"
            for event in result.recovery_events
        )

        workbook = load_workbook(output, read_only=True, data_only=True)
        try:
            review_sheet = workbook["검토필요"]
            reasons = [
                str(row[0] or "")
                for row in review_sheet.iter_rows(
                    min_col=13,
                    max_col=13,
                    values_only=True,
                )
            ]
            assert any("로컬 LLM 리뷰 " in reason for reason in reasons)
        finally:
            workbook.close()
    finally:
        output.unlink(missing_ok=True)
