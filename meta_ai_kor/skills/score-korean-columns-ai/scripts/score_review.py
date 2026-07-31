from __future__ import annotations

import argparse
import json
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
    dimensions = {
        item["id"]: item for item in rubric.get("dimensions", [])
    }
    ratings = review.get("dimensions")
    if not isinstance(ratings, dict):
        raise ValueError("dimensions must be an object")

    dimension_results: dict[str, dict[str, Any]] = {}
    raw_score = 0.0
    for dimension_id, definition in dimensions.items():
        observation = ratings.get(dimension_id)
        if not isinstance(observation, dict):
            raise ValueError(f"missing dimension: {dimension_id}")
        rating = float(observation.get("rating"))
        if not 0 <= rating <= 5:
            raise ValueError(f"{dimension_id}.rating must be between 0 and 5")
        weight = float(definition["weight"])
        points = weight * rating / 5
        raw_score += points
        dimension_results[dimension_id] = {
            "label": definition["label"],
            "weight": weight,
            "rating": rating,
            "points": round(points, 2),
            "evidence": observation.get("evidence", []),
        }

    issues = review.get("issues", [])
    if not isinstance(issues, list):
        raise ValueError("issues must be an array")
    issue_counts = {"critical": 0, "major": 0, "minor": 0}
    for issue in issues:
        severity = issue.get("severity") if isinstance(issue, dict) else None
        if severity not in issue_counts:
            raise ValueError(f"invalid issue severity: {severity}")
        issue_counts[severity] += 1

    required = rubric.get("required_checks", [])
    checks = review.get("required_checks")
    if not isinstance(checks, dict):
        raise ValueError("required_checks must be an object")
    missing_checks = [name for name in required if name not in checks]
    if missing_checks:
        raise ValueError(f"missing required checks: {', '.join(missing_checks)}")
    failed_checks = [name for name in required if checks[name] is not True]
    reviewed_count = review.get("reviewed_item_count")
    expected_count = review.get("expected_item_count")
    if (
        not isinstance(reviewed_count, int)
        or not isinstance(expected_count, int)
        or expected_count <= 0
        or reviewed_count != expected_count
    ):
        if "score_scope_complete" not in failed_checks:
            failed_checks.append("score_scope_complete")

    cap = 100.0
    caps = rubric["score_caps"]
    if issue_counts["critical"]:
        cap = min(cap, float(caps["when_critical_issue_exists"]))
    if failed_checks:
        cap = min(cap, float(caps["when_required_check_fails"]))
    final_score = round(min(raw_score, cap), 2)

    pass_rules = rubric["pass"]
    minimum_dimension = float(pass_rules["minimum_dimension_rating"])
    low_dimensions = [
        dimension_id
        for dimension_id, result in dimension_results.items()
        if result["rating"] < minimum_dimension
    ]
    passed = (
        final_score >= float(pass_rules["minimum_score"])
        and issue_counts["critical"]
        <= int(pass_rules["maximum_critical_issues"])
        and not failed_checks
        and not low_dimensions
    )

    return {
        "rubric": {
            "name": rubric["name"],
            "version": rubric["version"],
        },
        "stage_id": review.get("stage_id"),
        "commit_sha": review.get("commit_sha"),
        "reviewed_item_count": reviewed_count,
        "expected_item_count": expected_count,
        "raw_score": round(raw_score, 2),
        "score_cap": cap,
        "score": final_score,
        "passed": passed,
        "dimensions": dimension_results,
        "issue_counts": issue_counts,
        "failed_required_checks": failed_checks,
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
        f"AI review score={result['score']:.2f} "
        f"passed={str(result['passed']).lower()}"
    )


if __name__ == "__main__":
    main()
