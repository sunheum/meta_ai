from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from build_review_population import (
    HEADER_ALIASES,
    Table,
    field,
    header_lookup,
    load_table,
    load_terminology,
    normalized_key,
    normalize_header,
)


RESULT_COLUMNS = (
    "한글속성명",
    "처리상태",
    "신뢰도",
    "처리방식",
    "변환근거",
    "검토사유",
)
VALID_STATUSES = {"자동확정", "검토필요", "검증실패"}
VALID_METHODS = {"유지", "정규화", "재작성"}
REQUIRED_TERMINOLOGY_GROUPS: dict[str, tuple[str, ...]] = {
    "payment-action": ("납입", "납부"),
    "used-car-rate": ("중고차요율", "중고차율"),
    "vehicle-form": ("차량형태", "차형태"),
    "special-contract-rate": ("특약율", "특약요율"),
}


def text(value: Any) -> str:
    return "" if value is None else str(value)


def valid_korean_name(value: Any) -> bool:
    name = text(value)
    if not name or any(character.isspace() for character in name):
        return False
    remainder = name.replace("ID", "")
    return bool(remainder or name == "ID") and all(
        "가" <= character <= "힣" or "0" <= character <= "9"
        for character in remainder
    )


def issue(
    check: str,
    severity: str,
    message: str,
    source_id: str | None = None,
    expected: Any = None,
    actual: Any = None,
) -> dict[str, Any]:
    item = {"check": check, "severity": severity, "message": message}
    if source_id is not None:
        item["source_id"] = source_id
    if expected is not None:
        item["expected"] = expected
    if actual is not None:
        item["actual"] = actual
    return item


def check_terminology(
    decisions: list[dict[str, Any]],
    original_by_source: dict[str, dict[str, Any]],
    original_lookup: dict[str, str],
    result_by_source: dict[str, dict[str, Any]],
    result_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    decision_group_ids = {
        text(decision.get("group_id"))
        for decision in decisions
        if text(decision.get("group_id"))
    }
    required_active_groups = {
        group_id
        for group_id, candidates in REQUIRED_TERMINOLOGY_GROUPS.items()
        if sum(
            _count_terminology_population(
                original_by_source,
                original_lookup,
                candidates,
            )[0].values()
        )
        > 0
    }
    missing_groups = sorted(required_active_groups.difference(decision_group_ids))
    if missing_groups:
        failures.append(
            issue(
                "terminology_frequency_verified",
                "major",
                "active required terminology groups are missing",
                expected=sorted(required_active_groups),
                actual=sorted(decision_group_ids),
            )
        )
    if not decisions:
        failures.append(
            issue(
                "terminology_frequency_verified",
                "major",
                "terminology decision metadata is missing",
            )
        )
        return failures
    for index, decision in enumerate(decisions, start=1):
        group_id = str(decision.get("group_id") or f"group-{index}")
        selected = text(decision.get("selected_term"))
        candidates = decision.get("candidates")
        frequencies = decision.get("candidate_frequencies")
        affected = decision.get("affected_source_ids")
        tied = decision.get("tied") is True
        if not selected or not isinstance(frequencies, dict) or not frequencies:
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} has an invalid decision contract",
                )
            )
            continue
        if (
            not isinstance(candidates, list)
            or not candidates
            or any(not text(candidate) for candidate in candidates)
            or len({text(candidate) for candidate in candidates}) != len(candidates)
            or {text(candidate) for candidate in candidates} != set(frequencies)
        ):
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} candidates do not match the frequency table",
                    expected=sorted(str(candidate) for candidate in frequencies),
                    actual=candidates,
                )
            )
            continue
        required_candidates = REQUIRED_TERMINOLOGY_GROUPS.get(group_id)
        if required_candidates and set(map(text, candidates)) != set(required_candidates):
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} candidates differ from the required policy registry",
                    expected=list(required_candidates),
                    actual=candidates,
                )
            )
            continue
        numeric_frequencies: dict[str, int] = {}
        try:
            for candidate, count in frequencies.items():
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError
                numeric_frequencies[str(candidate)] = count
        except (TypeError, ValueError):
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} contains a non-integer frequency",
                )
            )
            continue

        computed_frequencies, computed_affected = _count_terminology_population(
            original_by_source,
            original_lookup,
            tuple(text(candidate) for candidate in candidates),
        )
        if numeric_frequencies != computed_frequencies:
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} frequency metadata differs from the source corpus",
                    expected=computed_frequencies,
                    actual=numeric_frequencies,
                )
            )

        maximum = max(computed_frequencies.values())
        winners = {
            candidate for candidate, count in computed_frequencies.items() if count == maximum
        }
        computed_tied = len(winners) > 1
        if tied != computed_tied:
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} tie metadata differs from the source corpus",
                    expected=computed_tied,
                    actual=tied,
                )
            )
        if selected not in winners:
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} did not select the most frequent term",
                    expected=sorted(winners),
                    actual=selected,
                )
            )
        if not isinstance(affected, list) or not affected:
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} has no affected_source_ids",
                )
            )
            continue
        affected_ids = [str(source_value) for source_value in affected]
        if len(set(affected_ids)) != len(affected_ids) or set(affected_ids) != computed_affected:
            failures.append(
                issue(
                    "terminology_frequency_verified",
                    "major",
                    f"{group_id} affected_source_ids differ from the source corpus",
                    expected=sorted(computed_affected),
                    actual=affected_ids,
                )
            )
        for source_id in affected_ids:
            row = result_by_source.get(source_id)
            if row is None:
                failures.append(
                    issue(
                        "terminology_frequency_verified",
                        "major",
                        f"{group_id} references an unknown source",
                        source_id=source_id,
                    )
                )
                continue
            korean_name = text(field(row, result_lookup, "korean_name"))
            if selected not in korean_name:
                failures.append(
                    issue(
                        "terminology_frequency_verified",
                        "major",
                        f"{group_id} selected term is absent from the result",
                        source_id=source_id,
                        expected=selected,
                        actual=korean_name,
                    )
                )
    return failures


def _count_terminology_population(
    original_by_source: dict[str, dict[str, Any]],
    original_lookup: dict[str, str],
    candidates: tuple[str, ...],
) -> tuple[dict[str, int], set[str]]:
    """Recalculate exact, longest-first synonym counts from source descriptions."""

    ordered = tuple(sorted(candidates, key=lambda value: (-len(value), value)))
    counts = {candidate: 0 for candidate in candidates}
    affected: set[str] = set()
    for source_id, row in original_by_source.items():
        description = text(field(row, original_lookup, "column_description"))
        index = 0
        matched = False
        while index < len(description):
            candidate = next(
                (item for item in ordered if description.startswith(item, index)),
                None,
            )
            if candidate is None:
                index += 1
                continue
            counts[candidate] += 1
            matched = True
            index += len(candidate)
        if matched:
            affected.add(source_id)
    return counts, affected


def run_checks(
    original: Table,
    result: Table,
    terminology: list[dict[str, Any]],
) -> dict[str, Any]:
    failures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    original_lookup = header_lookup(original.headers)
    result_lookup = header_lookup(result.headers)

    if len(original.rows) != len(result.rows):
        failures["row_preservation"].append(
            issue(
                "row_preservation",
                "critical",
                "row count differs",
                expected=len(original.rows),
                actual=len(result.rows),
            )
        )
    normalized_result_headers = {normalize_header(item) for item in result.headers}
    missing_result_columns = [
        column for column in RESULT_COLUMNS if column not in normalized_result_headers
    ]
    if missing_result_columns:
        failures["required_columns_complete"].append(
            issue(
                "required_columns_complete",
                "critical",
                "required result columns are missing",
                expected=list(RESULT_COLUMNS),
                actual=missing_result_columns,
            )
        )
    for required in ("column_name", "column_description"):
        if not any(
            normalize_header(alias) in original_lookup for alias in HEADER_ALIASES[required]
        ):
            failures["required_columns_complete"].append(
                issue(
                    "required_columns_complete",
                    "critical",
                    f"original is missing {required}",
                )
            )

    if len(result.headers) < len(original.headers) or result.headers[
        : len(original.headers)
    ] != original.headers:
        failures["row_preservation"].append(
            issue(
                "row_preservation",
                "critical",
                "original headers or their order changed",
                expected=original.headers,
                actual=result.headers[: len(original.headers)],
            )
        )

    original_by_source: dict[str, dict[str, Any]] = {}
    result_by_source: dict[str, dict[str, Any]] = {}
    duplicate_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    pair_count = min(len(original.rows), len(result.rows))
    for offset in range(pair_count):
        excel_row = offset + 2
        source_id = f"row-{excel_row}"
        source = original.rows[offset]
        output = result.rows[offset]
        explicit_source_id = field(output, result_lookup, "source_id")
        if explicit_source_id:
            source_id = str(explicit_source_id)
        original_by_source[source_id] = source
        result_by_source[source_id] = output

        for header in original.headers:
            if source.get(header) != output.get(header):
                failures["row_preservation"].append(
                    issue(
                        "row_preservation",
                        "critical",
                        f"original value changed in {header}",
                        source_id,
                        source.get(header),
                        output.get(header),
                    )
                )

        if missing_result_columns:
            continue
        description = text(field(source, original_lookup, "column_description"))
        korean_name = field(output, result_lookup, "korean_name")
        if not valid_korean_name(korean_name):
            failures["character_policy_complete"].append(
                issue(
                    "character_policy_complete",
                    "critical" if not text(korean_name) else "major",
                    "name is empty or contains characters other than Hangul, digits, and ID",
                    source_id,
                    actual=korean_name,
                )
            )
        source_numbers = re.findall(r"[0-9]+", description)
        result_numbers = re.findall(r"[0-9]+", text(korean_name))
        if source_numbers != result_numbers:
            failures["numeric_preservation_complete"].append(
                issue(
                    "numeric_preservation_complete",
                    "critical",
                    "numeric sequences differ",
                    source_id,
                    source_numbers,
                    result_numbers,
                )
            )

        status = text(field(output, result_lookup, "status"))
        method = text(field(output, result_lookup, "method"))
        confidence = field(output, result_lookup, "confidence")
        reason = text(field(output, result_lookup, "reason")).strip()
        review_reason = text(field(output, result_lookup, "review_reason")).strip()
        if status not in VALID_STATUSES:
            failures["evidence_traceable"].append(
                issue("evidence_traceable", "major", "invalid status", source_id, actual=status)
            )
        if method not in VALID_METHODS:
            failures["evidence_traceable"].append(
                issue("evidence_traceable", "major", "invalid method", source_id, actual=method)
            )
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int)
            or not 0 <= confidence <= 100
        ):
            failures["evidence_traceable"].append(
                issue(
                    "evidence_traceable",
                    "major",
                    "confidence must be an integer from 0 to 100",
                    source_id,
                    actual=confidence,
                )
            )
        if method == "유지" and text(korean_name) != description:
            failures["evidence_traceable"].append(
                issue(
                    "evidence_traceable",
                    "major",
                    "keep result differs from the original description",
                    source_id,
                    description,
                    korean_name,
                )
            )
        if method in {"정규화", "재작성"} and not reason:
            failures["evidence_traceable"].append(
                issue(
                    "evidence_traceable",
                    "major",
                    "changed result has no conversion reason",
                    source_id,
                )
            )
        if status != "자동확정" and not review_reason:
            failures["evidence_traceable"].append(
                issue(
                    "evidence_traceable",
                    "major",
                    "non-confirmed result has no review reason",
                    source_id,
                )
            )
        if status == "자동확정" and review_reason:
            failures["evidence_traceable"].append(
                issue(
                    "evidence_traceable",
                    "minor",
                    "auto-confirmed result unexpectedly has a review reason",
                    source_id,
                    actual=review_reason,
                )
            )
        key = normalized_key(
            field(source, original_lookup, "column_name"),
            field(source, original_lookup, "column_description"),
        )
        duplicate_names[key].add(text(korean_name))

    for key, names in duplicate_names.items():
        if len(names) > 1:
            failures["duplicate_consistency"].append(
                issue(
                    "duplicate_consistency",
                    "major",
                    "identical normalized inputs produced different names",
                    expected=1,
                    actual=sorted(names),
                )
            )

    failures["terminology_frequency_verified"].extend(
        check_terminology(
            terminology,
            original_by_source,
            original_lookup,
            result_by_source,
            result_lookup,
        )
    )
    check_names = (
        "row_preservation",
        "required_columns_complete",
        "character_policy_complete",
        "numeric_preservation_complete",
        "evidence_traceable",
        "duplicate_consistency",
        "terminology_frequency_verified",
    )
    checks = {
        name: {
            "passed": not failures[name],
            "failure_count": len(failures[name]),
        }
        for name in check_names
    }
    all_issues = [item for name in check_names for item in failures[name]]
    return {
        "schema_version": "1.0.0",
        "original_row_count": len(original.rows),
        "result_row_count": len(result.rows),
        "checks": checks,
        "deterministic_failure_count": len(all_issues),
        "passed": not all_issues,
        "issues": all_issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original-sheet")
    parser.add_argument("--result-sheet")
    parser.add_argument("--terminology-decisions", type=Path)
    args = parser.parse_args()
    report = run_checks(
        load_table(args.original, args.original_sheet),
        load_table(args.result, args.result_sheet),
        load_terminology(args.terminology_decisions),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"deterministic checks passed={str(report['passed']).lower()} "
        f"failures={report['deterministic_failure_count']}"
    )


if __name__ == "__main__":
    main()
