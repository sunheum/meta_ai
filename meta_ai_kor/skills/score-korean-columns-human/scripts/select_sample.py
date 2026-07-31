from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC = SKILL_DIR / "references" / "rubric.json"
HIDDEN_FIELDS = {
    "ai_score",
    "ai_issues",
    "confidence",
    "신뢰도",
    "처리상태",
    "previous_result",
    "previous_score",
    "generation_reason",
}


def load_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError("JSON input must be an array")
        rows = value
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("every population row must be an object")
    return rows


def select(
    rows: list[dict[str, Any]],
    stage_id: str,
    rubric: dict[str, Any],
) -> list[dict[str, Any]]:
    seed = int.from_bytes(
        hashlib.sha256(stage_id.encode("utf-8")).digest()[:8],
        "big",
    )
    randomizer = random.Random(seed)
    by_stratum: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        stratum = str(row.get("review_stratum", "")).strip()
        if not stratum:
            raise ValueError("every row needs review_stratum")
        by_stratum.setdefault(stratum, []).append(row)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    quotas = rubric["sample"]["quotas"]
    for stratum, quota in quotas.items():
        candidates = list(by_stratum.get(stratum, []))
        randomizer.shuffle(candidates)
        for row in candidates[: int(quota)]:
            source_id = str(row.get("source_id"))
            if source_id not in selected_ids:
                selected.append(row)
                selected_ids.add(source_id)

    remaining = [
        row for row in rows if str(row.get("source_id")) not in selected_ids
    ]
    randomizer.shuffle(remaining)
    minimum_size = int(rubric["sample"]["minimum_size"])
    selected.extend(remaining[: max(0, minimum_size - len(selected))])
    return selected


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    visible_rows = [
        {key: value for key, value in row.items() if key not in HIDDEN_FIELDS}
        for row in rows
    ]
    source_fields: list[str] = []
    for row in visible_rows:
        for key in row:
            if key not in source_fields:
                source_fields.append(key)
    review_fields = [
        "business_semantic_accuracy",
        "meaning_completeness",
        "korean_naturalness",
        "terminology_consistency",
        "business_ambiguity_safety",
        "severity",
        "comment",
    ]
    fieldnames = source_fields + [
        field for field in review_fields if field not in source_fields
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in visible_rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    args = parser.parse_args()

    rubric = json.loads(args.rubric.read_text(encoding="utf-8"))
    selected = select(load_rows(args.input), args.stage_id, rubric)
    write_csv(args.output, selected)
    print(f"human review sample rows={len(selected)} stage_id={args.stage_id}")


if __name__ == "__main__":
    main()

