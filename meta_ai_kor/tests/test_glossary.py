from app.glossary import MappingGlossary
from app.models import MappingEntry, SourceRow


def _source(description: str) -> SourceRow:
    return SourceRow(
        source_id="row-2",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=["AP"],
        table_name="INS_CONTRACT",
        table_description=description,
        column_name="AP",
        data_type="VARCHAR",
    )


def test_context_prefers_korean_word_in_table_description():
    glossary = MappingGlossary.from_entries(
        [
            MappingEntry(
                abbreviation="AP",
                full_name="APPLICATION",
                korean_word="적용",
                occurrence_count=10,
            ),
            MappingEntry(
                abbreviation="AP",
                full_name="APPROVAL",
                korean_word="승인",
                occurrence_count=1,
            ),
        ]
    )

    selected, ambiguous = glossary.resolve("AP", _source("계약 승인 정보"))

    assert selected is not None
    assert selected.full_name == "APPROVAL"
    assert ambiguous is True
    assert "AP" in glossary.ambiguous_full_name


def test_exact_duplicate_entries_are_removed():
    entry = MappingEntry(
        abbreviation="DT",
        full_name="DATE",
        korean_word="일자",
        occurrence_count=3,
    )
    glossary = MappingGlossary.from_entries([entry, entry])

    assert len(glossary) == 1

