from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DIMENSIONS = [
    "business_semantic_accuracy",
    "meaning_completeness",
    "korean_naturalness",
    "terminology_consistency",
    "business_ambiguity_safety",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ratings = []
    for index, row in enumerate(rows, start=2):
        values = {}
        for dimension in DIMENSIONS:
            try:
                rating = int(row.get(dimension, ""))
            except ValueError as exc:
                raise ValueError(
                    f"{index}행 {dimension}은 1~5 정수여야 합니다."
                ) from exc
            if not 1 <= rating <= 5:
                raise ValueError(
                    f"{index}행 {dimension}은 1~5 정수여야 합니다."
                )
            values[dimension] = rating
        severity = str(row.get("severity", "")).strip().lower()
        if severity not in {"pass", "minor", "major", "critical"}:
            raise ValueError(f"{index}행 severity 값이 잘못되었습니다.")
        comment = str(row.get("comment", "")).strip()
        if severity in {"major", "critical"} and not comment:
            raise ValueError(
                f"{index}행 {severity} 평가는 comment가 필요합니다."
            )
        ratings.append(
            {
                "source_id": row["source_id"],
                "review_stratum": row["review_stratum"],
                "ratings": values,
                "severity": severity,
                "comment": comment,
            }
        )
    payload = {
        "stage_id": args.stage_id,
        "commit_sha": args.commit_sha,
        "reviewer_id": args.reviewer_id,
        "rows": ratings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"human ratings rows={len(ratings)} reviewer={args.reviewer_id}")


if __name__ == "__main__":
    main()

