from app.glossary import MappingGlossary
from app.models import MappingEntry, SourceRow
from app.segmentation import segment_column


def _source(column_name: str) -> SourceRow:
    return SourceRow(
        source_id="row-2",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=[column_name],
        table_name="T",
        table_description="기준 일자 시각 조직 코드",
        column_name=column_name,
        data_type="VARCHAR",
    )


def _glossary() -> MappingGlossary:
    return MappingGlossary.from_entries(
        [
            MappingEntry(
                abbreviation="DT",
                full_name="DATE",
                korean_word="일자",
                occurrence_count=10,
            ),
            MappingEntry(
                abbreviation="HMS",
                full_name="TIME",
                korean_word="시각",
                occurrence_count=10,
            ),
            MappingEntry(
                abbreviation="ORG",
                full_name="ORGANIZATION",
                korean_word="조직",
                occurrence_count=10,
            ),
            MappingEntry(
                abbreviation="CD",
                full_name="CODE",
                korean_word="코드",
                occurrence_count=10,
            ),
        ]
    )


def test_dynamic_segmentation_splits_attached_abbreviations():
    candidates = segment_column(_source("DTHMS"), _glossary())

    assert [
        component.source_fragment for component in candidates[0].components
    ] == ["DT", "HMS"]
    assert candidates[0].coverage == 1


def test_suffix_score_prefers_org_plus_code():
    candidates = segment_column(_source("ORGCD"), _glossary())

    assert [
        component.source_fragment for component in candidates[0].components
    ] == ["ORG", "CD"]


def test_unresolved_characters_are_grouped():
    candidates = segment_column(_source("XYZ_DT"), _glossary())

    assert candidates[0].unresolved_fragments == ["XYZ"]
    assert candidates[0].components[0].source_fragment == "XYZ"

