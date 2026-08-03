from app.glossary import MappingGlossary
from app.models import (
    ColumnResult,
    MappingEntry,
    NameComponent,
    SourceRow,
)
from app.validation import apply_validation_status, validate_results


def _source() -> SourceRow:
    return SourceRow(
        source_id="row-2",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=["DT"],
        table_name="T",
        table_description="기준일자",
        column_name="DT",
        data_type="VARCHAR",
    )


def _glossary() -> MappingGlossary:
    return MappingGlossary.from_entries(
        [
            MappingEntry(
                abbreviation="DT",
                full_name="DATE",
                korean_word="일자",
                occurrence_count=1,
            )
        ]
    )


def _result(word: str = "일자") -> ColumnResult:
    return ColumnResult(
        source_id="row-2",
        components=[
            NameComponent(
                source_fragment="DT",
                full_name="DATE",
                korean_word=word,
                origin="mapping",
            )
        ],
        english_full_name="DATE",
        korean_attribute_name=word,
        status="검토필요",
        confidence=90,
        evidence=f"DT→DATE→{word}[mapping]",
    )


def test_validation_accepts_traceable_result():
    report = validate_results([_source()], [_result()], _glossary())

    assert report.is_valid is True
    assert report.stats["error_count"] == 0


def test_validation_rejects_placeholder_and_bad_mapping():
    report = validate_results([_source()], [_result("미정")], _glossary())

    assert report.is_valid is False
    assert {issue.code for issue in report.errors} == {
        "placeholder_korean_name",
        "mapping_evidence_mismatch",
    }
    updated = apply_validation_status([_result("미정")], report)
    assert updated[0].status == "검증실패"


def test_validation_rejects_component_coverage_gap():
    result = _result().model_copy(
        update={
            "components": [
                NameComponent(
                    source_fragment="D",
                    full_name="DATE",
                    korean_word="일자",
                    origin="inference",
                )
            ],
            "evidence": "D→DATE→일자[inference]",
        }
    )

    report = validate_results([_source()], [result], _glossary())

    assert "component_coverage_mismatch" in {
        issue.code for issue in report.errors
    }

