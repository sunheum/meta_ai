from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import Settings
from app.glossary import MappingGlossary
from app.llm import LocalChatNamingModel
from app.models import WorkflowOptions
from app.workflow import NamingWorkflow, result_stats, review_population


async def run(args) -> None:
    settings = Settings.from_env()
    glossary = MappingGlossary.from_xlsx(args.mapping)
    model = (
        LocalChatNamingModel(settings)
        if not args.no_llm and settings.llm_enabled
        else None
    )
    workflow = NamingWorkflow(
        glossary,
        model,
        strict_llm=settings.strict_llm,
        max_segmentation_candidates=settings.max_segmentation_candidates,
    )
    async def report_progress(event) -> None:
        if not args.quiet:
            print(
                json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )

    sources, results = await workflow.run(
        args.input,
        args.output,
        WorkflowOptions(
            batch_size=args.batch_size or settings.default_batch_size,
            max_concurrency=(
                args.max_concurrency or settings.default_max_concurrency
            ),
            max_review_rounds=(
                settings.default_max_review_rounds
                if args.max_review_rounds is None
                else args.max_review_rounds
            ),
            auto_confirm_threshold=settings.auto_confirm_threshold,
            use_llm=not args.no_llm,
        ),
        progress_callback=report_progress,
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--max-review-rounds", type=int)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
