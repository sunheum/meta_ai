from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.models import WorkflowOptions
from app.workflow import (
    result_stats,
    review_population,
    run_deterministic_workbook,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population", type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()

    sources, results = run_deterministic_workbook(
        args.input,
        args.mapping,
        args.output,
        WorkflowOptions(use_llm=False),
    )
    if args.population:
        args.population.parent.mkdir(parents=True, exist_ok=True)
        args.population.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in review_population(sources, results)
            )
            + "\n",
            encoding="utf-8",
        )
    stats = result_stats(results)
    if args.metadata:
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        args.metadata.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()

