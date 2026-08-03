import pytest

from app.exceptions import LLMResponseError
from app.glossary import MappingGlossary
from app.llm import _parse_json
from app.models import SourceRow, WorkflowOptions
from app.recovery import _build_corrected_result, _parse_evidence
from app.workflow import NamingWorkflow


class FailingModel:
    async def resolve(self, requests):
        raise TimeoutError("timeout")

    async def review(self, requests):
        raise TimeoutError("timeout")


def _source() -> SourceRow:
    return SourceRow(
        source_id="row-2",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=["UNKNOWN"],
        table_name="T",
        table_description="",
        column_name="UNKNOWN",
        data_type="VARCHAR",
    )


@pytest.mark.asyncio
async def test_non_strict_llm_failure_returns_partial_validation_result():
    workflow = NamingWorkflow(
        MappingGlossary.from_entries([]),
        FailingModel(),
        strict_llm=False,
    )

    results = await workflow.generate(
        [_source()],
        WorkflowOptions(
            batch_size=1,
            max_concurrency=1,
            max_review_rounds=1,
        ),
    )

    assert results[0].status == "검증실패"
    assert "placeholder_korean_name" in results[0].validation_codes


def test_malformed_llm_response_has_clear_error():
    with pytest.raises(LLMResponseError, match="JSON"):
        _parse_json("not-json")


def test_parse_evidence_restores_components():
    components = _parse_evidence(
        "TLM→TELEGRAM MESSAGE→전문[mapping] | "
        "TS→TIME STAMP→타임스탬프[inference]"
    )

    assert [component.source_fragment for component in components] == [
        "TLM",
        "TS",
    ]
    assert components[1].full_name == "TIME STAMP"
    assert components[1].korean_word == "타임스탬프"
    assert components[1].origin == "inference"
    assert components[1].start == 3


def test_build_corrected_result_normalizes_review_values():
    result = _build_corrected_result(
        "TLM_OPNDT",
        "row-3",
        {
            "english_full_name": "Telegram Message Open Date",
            "korean_attribute_name": "전문 개시 일자",
        },
    )

    assert result.english_full_name == "TELEGRAM MESSAGE OPEN DATE"
    assert result.korean_attribute_name == "전문개시일자"
    assert result.components[0].source_fragment == "TLMOPNDT"
    assert result.status == "검토필요"
