import json
from pathlib import Path

from app.models import MappingEntry, SourceRow


def test_benchmark_contract_is_frozen():
    manifest_path = (
        Path(__file__).parents[1] / "quality" / "benchmark" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["source"]["row_count"] == 694
    assert manifest["source"]["column_count"] == 12
    assert manifest["mapping"]["row_count"] == 838
    assert manifest["source"]["headers"][-1] == "컬럼설명"


def test_source_context_key_includes_table_context():
    source = SourceRow(
        source_id="row-2",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=["AP_DT"],
        table_name="CONTRACT",
        table_description="계약 승인",
        column_name="AP_DT",
        data_type="VARCHAR",
    )

    assert source.context_key == (
        "AP_DT",
        "CONTRACT",
        "계약 승인",
        "VARCHAR",
    )


def test_mapping_entry_normalizes_english():
    entry = MappingEntry(
        abbreviation="dt",
        full_name="date",
        korean_word="일자",
        occurrence_count=1,
    )

    assert entry.abbreviation == "DT"
    assert entry.full_name == "DATE"

