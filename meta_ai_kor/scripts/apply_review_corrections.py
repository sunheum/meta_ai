from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.recovery import apply_review_corrections
from app.workflow import result_stats, review_population


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    payload = json.loads(args.corrections.read_text(encoding="utf-8"))
    corrections = payload.get("corrections")
    if not isinstance(corrections, list) or not corrections:
        raise ValueError("corrections는 비어 있지 않은 배열이어야 합니다.")
    sources, results = apply_review_corrections(
        source_path=args.source,
        result_path=args.result,
        mapping_path=args.mapping,
        output_path=args.output,
        corrections=corrections,
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


if __name__ == "__main__":
    main()
