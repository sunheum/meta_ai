from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC = SKILL_DIR / "references" / "rubric.json"
VALID_SEVERITIES = {"pass", "minor", "major", "critical"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def convert(
    csv_path: Path,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    stage_id: str,
    commit_sha: str,
    reviewer_id: str,
    human_attested: bool,
) -> dict[str, Any]:
    if not human_attested:
        raise ValueError(
            "human review attestation is required; an agent must not generate ratings"
        )
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    if not commit_sha.strip():
        raise ValueError("commit_sha is required")
    if manifest.get("stage_id") != stage_id:
        raise ValueError("manifest stage_id does not match")
    expected_ids = manifest.get("source_ids")
    source_strata = manifest.get("source_strata")
    if not isinstance(expected_ids, list) or not isinstance(source_strata, dict):
        raise ValueError("invalid sample manifest")
    dimension_ids = [str(item["id"]) for item in rubric["dimensions"]]
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    actual_ids = [str(row.get("source_id", "")).strip() for row in rows]
    expected_ids = [str(source_id) for source_id in expected_ids]
    if actual_ids != expected_ids:
        raise ValueError("sample source IDs or order differ from the locked manifest")
    converted_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        ratings: dict[str, int] = {}
        for dimension_id in dimension_ids:
            value = str(row.get(dimension_id, "")).strip()
            if not re.fullmatch(r"[1-5]", value):
                raise ValueError(
                    f"CSV row {index} {dimension_id} must be a human-entered integer 1-5"
                )
            ratings[dimension_id] = int(value)
        severity = str(row.get("severity", "")).strip().lower()
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"CSV row {index} has invalid severity: {severity}")
        comment = str(row.get("comment", "")).strip()
        if severity in {"major", "critical"} and not comment:
            raise ValueError(f"CSV row {index} needs a comment for {severity}")
        source_id = actual_ids[index - 2]
        converted_rows.append(
            {
                "source_id": source_id,
                "review_strata": source_strata.get(source_id, []),
                "ratings": ratings,
                "severity": severity,
                "comment": comment,
            }
        )
    return {
        "schema_version": "1.0.0",
        "stage_id": stage_id,
        "commit_sha": commit_sha,
        "reviewer_id": reviewer_id,
        "review_provenance": {
            "rating_source": "human_entered",
            "reviewer_attested": True,
            "ai_generated": False,
        },
        "sample_manifest": manifest,
        "rows": converted_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--attest-human-review", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    args = parser.parse_args()
    result = convert(
        args.input,
        read_json(args.manifest),
        read_json(args.rubric),
        args.stage_id,
        args.commit_sha,
        args.reviewer_id,
        args.attest_human_review,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"validated human ratings rows={len(result['rows'])}")


if __name__ == "__main__":
    main()
