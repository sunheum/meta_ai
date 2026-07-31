import time
from pathlib import Path

from app.excel import read_source_workbook
from app.glossary import MappingGlossary
from app.models import WorkflowOptions
from app.workflow import build_deterministic_results


ROOT = Path(__file__).parents[2]
SOURCE_PATH = ROOT / "data" / "table_column_template_컬럼코멘트N.xlsx"
MAPPING_PATH = Path(__file__).parents[1] / "result.xlsx"


def test_full_deterministic_benchmark_finishes_within_15_seconds():
    started = time.perf_counter()
    sources = read_source_workbook(SOURCE_PATH)
    glossary = MappingGlossary.from_xlsx(MAPPING_PATH)
    results = build_deterministic_results(
        sources,
        glossary,
        WorkflowOptions(use_llm=False),
    )

    assert len(results) == 694
    assert time.perf_counter() - started < 15

