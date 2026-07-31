import pytest

from app.glossary import MappingGlossary
from app.llm import _parse_json
from app.models import (
    LLMResolution,
    MappingEntry,
    NameComponent,
    ResolutionRequest,
    SourceRow,
    WorkflowOptions,
)
from app.workflow import NamingWorkflow


class FakeNamingModel:
    async def resolve(
        self,
        requests: list[ResolutionRequest],
    ) -> list[LLMResolution]:
        return [
            LLMResolution(
                source_id=request.source.source_id,
                components=[
                    NameComponent(
                        source_fragment=request.source.column_name.replace(
                            "_", ""
                        ),
                        full_name="SOCIAL SECURITY NUMBER",
                        korean_word="사회보장번호",
                        origin="inference",
                    )
                ],
                full_name="SOCIAL SECURITY NUMBER",
                korean_attribute_name="사회보장번호",
                reason="테이블 문맥 기반 추론",
            )
            for request in requests
        ]


class ReviewFixingModel:
    def __init__(self):
        self.review_calls = 0

    async def resolve(self, requests):
        return []

    async def review(self, requests):
        self.review_calls += 1
        return [
            LLMResolution(
                source_id=request.request.source.source_id,
                components=[
                    NameComponent(
                        source_fragment="SCSSN",
                        full_name="SOCIAL SECURITY NUMBER",
                        korean_word="사회보장번호",
                        origin="inference",
                    )
                ],
                full_name="SOCIAL SECURITY NUMBER",
                korean_attribute_name="사회보장번호",
                reason="검증 오류 교정",
            )
            for request in requests
        ]


def _source() -> SourceRow:
    return SourceRow(
        source_id="row-2",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=["SCSSN"],
        table_name="CUS_CTM",
        table_description="고객 통합",
        column_name="SCSSN",
        data_type="VARCHAR",
    )


def test_parse_json_removes_reasoning_and_code_fence():
    payload = _parse_json(
        '<think>private</think>```json\n{"resolutions": []}\n```'
    )

    assert payload == {"resolutions": []}


@pytest.mark.asyncio
async def test_workflow_accepts_structured_inference():
    workflow = NamingWorkflow(
        MappingGlossary.from_entries(
            [
                MappingEntry(
                    abbreviation="YN",
                    full_name="YES OR NO",
                    korean_word="여부",
                    occurrence_count=1,
                )
            ]
        ),
        FakeNamingModel(),
    )

    results = await workflow.generate(
        [_source()],
        WorkflowOptions(batch_size=1, max_concurrency=1),
    )

    assert results[0].english_full_name == "SOCIAL SECURITY NUMBER"
    assert results[0].korean_attribute_name == "사회보장번호"
    assert results[0].components[0].origin == "inference"


@pytest.mark.asyncio
async def test_review_loop_sends_only_pending_row_and_stops_after_fix():
    model = ReviewFixingModel()
    workflow = NamingWorkflow(MappingGlossary.from_entries([]), model)

    results = await workflow.generate(
        [_source()],
        WorkflowOptions(
            batch_size=1,
            max_concurrency=1,
            max_review_rounds=2,
        ),
    )

    assert model.review_calls == 1
    assert results[0].korean_attribute_name == "사회보장번호"
    assert results[0].status == "검토필요"
    assert results[0].validation_codes == []
