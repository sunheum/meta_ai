from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC = SKILL_DIR / "references" / "rubric.json"


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
    observations = review.get("dimensions")
    if not isinstance(observations, dict):
        raise ValueError("dimensions must be an object")
    missing_dimensions = sorted(set(dimensions) - set(observations))
    extra_dimensions = sorted(set(observations) - set(dimensions))
    if missing_dimensions or extra_dimensions:
        raise ValueError(
            f"dimension ids differ: missing={missing_dimensions} extra={extra_dimensions}"
        )

    raw_score = 0.0
    dimension_results: dict[str, dict[str, Any]] = {}
    for dimension_id, definition in dimensions.items():
        observation = observations[dimension_id]
        if not isinstance(observation, dict):
            raise ValueError(f"{dimension_id} must be an object")
        rating_value = observation.get("rating")
        if isinstance(rating_value, bool):
            raise ValueError(f"{dimension_id}.rating must be numeric")
        rating = float(rating_value)
        if not 0 <= rating <= 5:
            raise ValueError(f"{dimension_id}.rating must be between 0 and 5")
        evidence = observation.get("evidence")
        if isinstance(evidence, str):
            evidence = [evidence] if evidence.strip() else []
        if not isinstance(evidence, list) or not evidence or not all(
            str(item).strip() for item in evidence
        ):
            raise ValueError(f"{dimension_id}.evidence must be a non-empty array")
        weight = float(definition["weight"])
        points = weight * rating / 5
        raw_score += points
        dimension_results[dimension_id] = {
            "label": definition["label"],
            "weight": weight,
            "rating": rating,
            "points": round(points, 2),
            "evidence": evidence,
        }

    issues = review.get("issues")
    if not isinstance(issues, list):
        raise ValueError("issues must be an array")
    issue_counts = {"critical": 0, "major": 0, "minor": 0}
    for index, item in enumerate(issues, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"issue {index} must be an object")
        severity = item.get("severity")
        if severity not in issue_counts:
            raise ValueError(f"issue {index} has invalid severity: {severity}")
        if not item.get("source_id") and not item.get("source_ids"):
            raise ValueError(f"issue {index} needs source_id or source_ids")
        for required in ("description", "evidence", "recommendation"):
            if not str(item.get(required, "")).strip():
                raise ValueError(f"issue {index} needs {required}")
        issue_counts[severity] += 1

    required_names = list(rubric.get("required_checks", []))
    checks = review.get("required_checks")
    if not isinstance(checks, dict):
        raise ValueError("required_checks must be an object")
    missing_checks = [name for name in required_names if name not in checks]
    if missing_checks:
        raise ValueError(f"missing required checks: {', '.join(missing_checks)}")
    invalid_checks = [name for name in required_names if not isinstance(checks[name], bool)]
    if invalid_checks:
        raise ValueError(f"required checks must be boolean: {', '.join(invalid_checks)}")
    failed_checks = [name for name in required_names if checks[name] is not True]

    reviewed_count = review.get("reviewed_item_count")
    expected_count = review.get("expected_item_count")
    if (
        isinstance(reviewed_count, bool)
        or isinstance(expected_count, bool)
        or not isinstance(reviewed_count, int)
        or not isinstance(expected_count, int)
        or expected_count <= 0
        or reviewed_count != expected_count
    ):
        if "score_scope_complete" not in failed_checks:
            failed_checks.append("score_scope_complete")
    deterministic_failures = review.get("deterministic_failure_count")
    if (
        isinstance(deterministic_failures, bool)
        or not isinstance(deterministic_failures, int)
        or deterministic_failures < 0
    ):
        raise ValueError("deterministic_failure_count must be a non-negative integer")
    if not str(review.get("stage_id", "")).strip():
        raise ValueError("stage_id is required")
    if not str(review.get("commit_sha", "")).strip():
        raise ValueError("commit_sha is required")

    caps = rubric["score_caps"]
    score_cap = 100.0
    if issue_counts["critical"]:
        score_cap = min(score_cap, float(caps["when_critical_issue_exists"]))
    if deterministic_failures:
        score_cap = min(
            score_cap, float(caps["when_deterministic_failure_exists"])
        )
    if failed_checks:
        score_cap = min(score_cap, float(caps["when_required_check_fails"]))
    final_score = round(min(raw_score, score_cap), 2)

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
        and deterministic_failures
        <= int(pass_rules["maximum_deterministic_failures"])
        and not failed_checks
        and not low_dimensions
    )
    return {
        "rubric": {"name": rubric["name"], "version": rubric["version"]},
        "stage_id": review["stage_id"],
        "commit_sha": review["commit_sha"],
        "reviewed_item_count": reviewed_count,
        "expected_item_count": expected_count,
        "raw_score": round(raw_score, 2),
        "score_cap": score_cap,
        "score": final_score,
        "passed": passed,
        "dimensions": dimension_results,
        "issue_counts": issue_counts,
        "deterministic_failure_count": deterministic_failures,
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
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"AI review score={result['score']:.2f} passed={str(result['passed']).lower()}")


if __name__ == "__main__":
    main()
