from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import Settings
from app.llm import LocalChatNamingModel
from app.models import WorkflowOptions
from app.recovery import repair_failed_workbook
from app.workflow import result_stats, review_population


async def run(args) -> None:
    settings = Settings.from_env()
    model = LocalChatNamingModel(settings)
    sources, results = await repair_failed_workbook(
        source_path=args.source,
        result_path=args.result,
        mapping_path=args.mapping,
        output_path=args.output,
        model=model,
        options=WorkflowOptions(
            batch_size=args.batch_size,
            max_concurrency=args.max_concurrency,
            max_review_rounds=0,
            auto_confirm_threshold=settings.auto_confirm_threshold,
        ),
    )
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
    args.metadata.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-concurrency", type=int, default=4)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
