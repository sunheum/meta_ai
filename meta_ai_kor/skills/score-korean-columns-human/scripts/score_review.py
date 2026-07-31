from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC = SKILL_DIR / "references" / "rubric.json"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def score(rubric: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = review.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a non-empty array")
    dimensions = {
        item["id"]: item for item in rubric.get("dimensions", [])
    }
    rating_totals = {dimension_id: 0.0 for dimension_id in dimensions}
    severity_counts = Counter()
    strata_counts = Counter()

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"row {index} must be an object")
        ratings = row.get("ratings")
        if not isinstance(ratings, dict):
            raise ValueError(f"row {index} needs ratings")
        for dimension_id in dimensions:
            rating = float(ratings.get(dimension_id))
            if not 1 <= rating <= 5:
                raise ValueError(
                    f"row {index} {dimension_id} must be between 1 and 5"
                )
            rating_totals[dimension_id] += rating
        severity = row.get("severity")
        if severity not in {"pass", "minor", "major", "critical"}:
            raise ValueError(f"row {index} has invalid severity: {severity}")
        if severity in {"major", "critical"} and not str(
            row.get("comment", "")
        ).strip():
            raise ValueError(
                f"row {index} needs a comment for severity {severity}"
            )
        severity_counts[severity] += 1
        strata_counts[str(row.get("review_stratum", ""))] += 1

    dimension_results: dict[str, dict[str, Any]] = {}
    raw_score = 0.0
    for dimension_id, definition in dimensions.items():
        mean = rating_totals[dimension_id] / len(rows)
        weight = float(definition["weight"])
        points = weight * mean / 5
        raw_score += points
        dimension_results[dimension_id] = {
            "label": definition["label"],
            "weight": weight,
            "mean_rating": round(mean, 3),
            "points": round(points, 2),
        }

    sample_rules = rubric["sample"]
    minimum_size_met = len(rows) >= int(sample_rules["minimum_size"])
    missing_strata = [
        stratum
        for stratum in sample_rules["required_strata"]
        if strata_counts[stratum] == 0
    ]
    critical_count = severity_counts["critical"]
    major_count = severity_counts["major"]
    major_error_rate = major_count / len(rows)

    cap = 100.0
    caps = rubric["score_caps"]
    if critical_count:
        cap = min(cap, float(caps["when_critical_issue_exists"]))
    pass_rules = rubric["pass"]
    if major_error_rate > float(pass_rules["maximum_major_error_rate"]):
        cap = min(cap, float(caps["when_major_error_rate_exceeded"]))
    final_score = round(min(raw_score, cap), 2)

    minimum_dimension_mean = float(
        pass_rules["minimum_dimension_mean"]
    )
    low_dimensions = [
        dimension_id
        for dimension_id, result in dimension_results.items()
        if result["mean_rating"] < minimum_dimension_mean
    ]
    passed = (
        final_score >= float(pass_rules["minimum_score"])
        and critical_count <= int(pass_rules["maximum_critical_issues"])
        and major_error_rate
        <= float(pass_rules["maximum_major_error_rate"])
        and minimum_size_met
        and not missing_strata
        and not low_dimensions
    )

    return {
        "rubric": {
            "name": rubric["name"],
            "version": rubric["version"],
        },
        "stage_id": review.get("stage_id"),
        "commit_sha": review.get("commit_sha"),
        "reviewer_id": review.get("reviewer_id"),
        "sample_size": len(rows),
        "raw_score": round(raw_score, 2),
        "score_cap": cap,
        "score": final_score,
        "passed": passed,
        "dimensions": dimension_results,
        "severity_counts": dict(severity_counts),
        "major_error_rate": round(major_error_rate, 4),
        "strata_counts": dict(strata_counts),
        "minimum_sample_size_met": minimum_size_met,
        "missing_required_strata": missing_strata,
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
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        f"human review score={result['score']:.2f} "
        f"passed={str(result['passed']).lower()}"
    )


if __name__ == "__main__":
    main()
