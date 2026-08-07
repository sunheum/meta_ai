from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import Workbook

from app.excel import read_source_columns
from app.models import (
    GenerationResult,
    ProcessingAction,
    SourceColumn,
    WorkflowOptions,
)
from app.workflow import KoreanCommentWorkflow, _deterministic_candidate


def _build_generic_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "테이블_컬럼_정보"
    sheet.append(["컬럼명 (*)", "컬럼설명"])
    sheet.append(["CUSTOMER_ID", "고객식별자"])
    sheet.append(["ORDER_DATE", "주문일자"])
    sheet.append(["ITEM_QTY", "품목수량"])
    sheet.append(["PYM_KND", "결제종류"])
    workbook.save(path)
    workbook.close()


class EchoModel:
    async def generate(self, sources, risks=None):
        return [
            GenerationResult(
                source_id=source.source_id,
                original_description=source.column_description,
                korean_attribute_name=source.column_description,
                action=ProcessingAction.KEEP,
                confidence=98,
                reason="테스트 fake model이 원문을 그대로 유지",
                semantic_units=[source.column_description],
            )
            for source in sources
        ]

    async def review(
        self,
        sources,
        current_results,
        issues,
        review_round,
        terminology_context=None,
    ):
        return list(current_results)


@pytest.mark.asyncio
async def test_generic_workbook_runs_without_any_domain_rules(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "generic.xlsx"
    output_path = tmp_path / f"result-{uuid4().hex}.xlsx"
    _build_generic_workbook(input_path)

    workflow = KoreanCommentWorkflow(EchoModel())

    summary = await workflow.run(
        input_path,
        output_path,
        WorkflowOptions(max_review_rounds=0),
    )

    assert summary.source_count == 4
    assert summary.validation_report.is_valid
    assert summary.validation_failed_count == 0
    # No synonym groups configured, so no terminology decisions were made.
    assert summary.terminology_stats == {
        "group_count": 0,
        "decision_count": 0,
    }
    assert output_path.exists()


def test_deterministic_candidate_without_rules_translates_slash_generically() -> None:
    result = _deterministic_candidate(
        SourceColumn(
            source_id="row-1",
            column_name="A_OR_B",
            column_description="갑/을",
        )
    )

    # No domain rule → take the first alternative and flag for review.
    assert result.korean_attribute_name == "갑"
    assert any("슬래시" in reason for reason in result.review_reasons)


def test_deterministic_candidate_without_rules_keeps_pure_korean_unchanged() -> None:
    result = _deterministic_candidate(
        SourceColumn(
            source_id="row-2",
            column_name="CUSTOMER_NAME",
            column_description="고객명",
        )
    )

    assert result.korean_attribute_name == "고객명"
    assert result.action.value == "keep"
    assert result.confidence == 100


def test_generic_input_generates_valid_result_columns(tmp_path: Path) -> None:
    input_path = tmp_path / "generic.xlsx"
    _build_generic_workbook(input_path)

    sources = read_source_columns(input_path)

    assert len(sources) == 4
    assert all(source.column_description for source in sources)
