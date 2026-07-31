from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from pathlib import Path

from app.excel import read_source_workbook
from app.glossary import MappingGlossary
from app.models import WorkflowOptions
from app.workflow import build_deterministic_results, result_stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    tracemalloc.start()
    started = time.perf_counter()
    sources = read_source_workbook(args.input)
    glossary = MappingGlossary.from_xlsx(args.mapping)
    results = build_deterministic_results(
        sources,
        glossary,
        WorkflowOptions(use_llm=False),
    )
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    payload = {
        "elapsed_seconds": round(elapsed, 3),
        "peak_memory_mb": round(peak / (1024 * 1024), 3),
        **result_stats(results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()

