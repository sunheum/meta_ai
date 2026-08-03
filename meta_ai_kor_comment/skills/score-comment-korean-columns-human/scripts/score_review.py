from __future__ import annotations

import argparse
import json
from collections import Counter
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


def validate_rubric(rubric: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = rubric.get("dimensions")
    if not isinstance(items, list) or not items:
        raise ValueError("rubric dimensions must be a non-empty array")
    dimensions = {str(item.get("id")): item for item in items if isinstance(item, dict)}
    if len(dimensions) != len(items):
        raise ValueError("rubric dimension ids must be unique")
    if abs(sum(float(item["weight"]) for item in items) - 100.0) > 1e-9:
        raise ValueError("rubric dimension weights must sum to 100")
    return dimensions


def score(rubric: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    dimensions = validate_rubric(rubric)
    provenance = review.get("review_provenance")
    if not isinstance(provenance, dict) or not (
        provenance.get("rating_source") == "human_entered"
        and provenance.get("reviewer_attested") is True
        and provenance.get("ai_generated") is False
    ):
        raise ValueError("ratings require explicit non-AI human review provenance")
    if not str(review.get("reviewer_id", "")).strip():
        raise ValueError("reviewer_id is required")
    if not str(review.get("stage_id", "")).strip():
        raise ValueError("stage_id is required")
    if not str(review.get("commit_sha", "")).strip():
        raise ValueError("commit_sha is required")

    manifest = review.get("sample_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("sample_manifest is required")
    if manifest.get("stage_id") != review.get("stage_id"):
        raise ValueError("sample manifest stage_id does not match")
    expected_ids = manifest.get("source_ids")
    source_strata = manifest.get("source_strata")
    required_counts = manifest.get("required_counts")
    missing_population_strata = manifest.get("missing_population_strata")
    if not all(
        (
            isinstance(expected_ids, list),
            isinstance(source_strata, dict),
            isinstance(required_counts, dict),
            isinstance(missing_population_strata, list),
        )
    ):
        raise ValueError("sample_manifest is incomplete")

    rows = review.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a non-empty array")
    actual_ids = [str(row.get("source_id", "")) for row in rows if isinstance(row, dict)]
    if len(actual_ids) != len(rows) or len(set(actual_ids)) != len(actual_ids):
        raise ValueError("rating rows need unique source_id values")
    sample_ids_match = actual_ids == [str(source_id) for source_id in expected_ids]

    totals = {dimension_id: 0.0 for dimension_id in dimensions}
    severity_counts: Counter[str] = Counter()
    for index, row in enumerate(rows, start=1):
        ratings = row.get("ratings")
        if not isinstance(ratings, dict):
            raise ValueError(f"row {index} needs ratings")
        if set(ratings) != set(dimensions):
            raise ValueError(f"row {index} rating dimension ids differ from the rubric")
        for dimension_id in dimensions:
            value = ratings[dimension_id]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"row {index} {dimension_id} must be numeric")
            rating = float(value)
            if not 1 <= rating <= 5:
                raise ValueError(f"row {index} {dimension_id} must be between 1 and 5")
            totals[dimension_id] += rating
        severity = row.get("severity")
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"row {index} has invalid severity: {severity}")
        if severity in {"major", "critical"} and not str(
            row.get("comment", "")
        ).strip():
            raise ValueError(f"row {index} needs a comment for {severity}")
        severity_counts[severity] += 1

    dimension_results: dict[str, dict[str, Any]] = {}
    raw_score = 0.0
    for dimension_id, definition in dimensions.items():
        mean = totals[dimension_id] / len(rows)
        weight = float(definition["weight"])
        points = weight * mean / 5
        raw_score += points
        dimension_results[dimension_id] = {
            "label": definition["label"],
            "weight": weight,
            "mean_rating": round(mean, 3),
            "points": round(points, 2),
        }

    strata_counts: Counter[str] = Counter()
    for source_id in actual_ids:
        strata = source_strata.get(source_id, [])
        if isinstance(strata, list):
            strata_counts.update(str(item) for item in strata)
    missing_quota = {
        stratum: int(required) - strata_counts[stratum]
        for stratum, required in required_counts.items()
        if strata_counts[stratum] < int(required)
    }
    minimum_size = int(manifest.get("minimum_size", rubric["sample"]["minimum_size"]))
    minimum_size_met = len(rows) >= minimum_size
    sample_complete = (
        sample_ids_match
        and minimum_size_met
        and not missing_quota
        and not missing_population_strata
    )

    critical_count = severity_counts["critical"]
    major_count = severity_counts["major"]
    major_error_rate = major_count / len(rows)
    pass_rules = rubric["pass"]
    caps = rubric["score_caps"]
    score_cap = 100.0
    if critical_count:
        score_cap = min(score_cap, float(caps["when_critical_issue_exists"]))
    if major_error_rate > float(pass_rules["maximum_major_error_rate"]):
        score_cap = min(score_cap, float(caps["when_major_error_rate_exceeded"]))
    if not sample_complete:
        score_cap = min(score_cap, float(caps["when_sample_incomplete"]))
    final_score = round(min(raw_score, score_cap), 2)

    minimum_mean = float(pass_rules["minimum_dimension_mean"])
    low_dimensions = [
        dimension_id
        for dimension_id, result in dimension_results.items()
        if result["mean_rating"] < minimum_mean
    ]
    passed = (
        final_score >= float(pass_rules["minimum_score"])
        and critical_count <= int(pass_rules["maximum_critical_issues"])
        and major_error_rate <= float(pass_rules["maximum_major_error_rate"])
        and sample_complete
        and not low_dimensions
    )
    return {
        "rubric": {"name": rubric["name"], "version": rubric["version"]},
        "stage_id": review["stage_id"],
        "commit_sha": review["commit_sha"],
        "reviewer_id": review["reviewer_id"],
        "sample_size": len(rows),
        "raw_score": round(raw_score, 2),
        "score_cap": score_cap,
        "score": final_score,
        "passed": passed,
        "dimensions": dimension_results,
        "severity_counts": dict(severity_counts),
        "major_error_rate": round(major_error_rate, 4),
        "strata_counts": dict(strata_counts),
        "minimum_sample_size_met": minimum_size_met,
        "sample_ids_match_manifest": sample_ids_match,
        "missing_quota_counts": missing_quota,
        "missing_population_strata": missing_population_strata,
        "dimensions_below_minimum": low_dimensions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    args = parser.parse_args()
    result = score(read_json(args.rubric), read_json(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"human review score={result['score']:.2f} "
        f"passed={str(result['passed']).lower()}"
    )


if __name__ == "__main__":
    main()
