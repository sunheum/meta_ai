import pytest

from app.exceptions import LLMResponseError
from app.glossary import MappingGlossary
from app.llm import _parse_json
from app.models import SourceRow, WorkflowOptions
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

