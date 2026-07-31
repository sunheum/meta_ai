from pathlib import Path

from app.excel import read_source_workbook
from app.glossary import MappingGlossary
from app.models import WorkflowOptions
from app.workflow import build_deterministic_results, result_stats


ROOT = Path(__file__).parents[2]
SOURCE_PATH = ROOT / "data" / "table_column_template_컬럼코멘트N.xlsx"
MAPPING_PATH = Path(__file__).parents[1] / "result.xlsx"


def test_deterministic_workflow_populates_every_row():
    sources = read_source_workbook(SOURCE_PATH)
    glossary = MappingGlossary.from_xlsx(MAPPING_PATH)

    results = build_deterministic_results(
        sources,
        glossary,
        WorkflowOptions(use_llm=False),
    )

    assert len(results) == 694
    assert all(result.english_full_name for result in results)
    assert all(result.korean_attribute_name for result in results)
    assert all(" " not in result.korean_attribute_name for result in results)
    stats = result_stats(results)
    assert stats["row_count"] == 694
    assert set(stats["review_strata"]).issuperset(
        {
            "deterministic",
            "unmapped_inference",
            "mapping_ambiguity",
            "segmentation_ambiguity",
            "duplicate_context",
            "review_needed",
        }
    )

