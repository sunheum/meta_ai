from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Iterable

from app.models import (
    MappingCandidate,
    SourceColumn,
    ValidationIssue,
    ValidationReport,
)

ABBREVIATION_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*$")
FULL_NAME_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 '&/().+-]*$")
CanonicalResolver = Callable[[str, str], str | None]


def validate_mappings(
    sources: Iterable[SourceColumn],
    candidates: Iterable[MappingCandidate],
    canonical_resolver: CanonicalResolver | None = None,
) -> ValidationReport:
    source_list = list(sources)
    candidate_list = list(candidates)
    source_by_id = {source.source_id: source for source in source_list}
    by_source: dict[str, list[MappingCandidate]] = defaultdict(list)
    issues: list[ValidationIssue] = []

    for index, candidate in enumerate(candidate_list):
        if candidate.source_id not in source_by_id:
            issues.append(
                ValidationIssue(
                    code="unknown_source_id",
                    severity="error",
                    message=f"알 수 없는 source_id: {candidate.source_id}",
                    suggested_action=(
                        "이 후보를 삭제하거나 source_id를 입력 JSON에 존재하는 값으로 "
                        "교체한 뒤, 해당 원본 컬럼의 전체 매핑을 다시 반환하세요."
                    ),
                    details={
                        "candidate_index": index,
                        "actual_source_id": candidate.source_id,
                        "expected_source_ids": sorted(source_by_id),
                    },
                )
            )
            continue
        by_source[candidate.source_id].append(candidate)
        source = source_by_id[candidate.source_id]
        normalized_column = re.sub(r"[^A-Z0-9]", "", source.column_name.upper())

        if not ABBREVIATION_PATTERN.fullmatch(candidate.abbreviation):
            issues.append(
                _source_issue(
                    "invalid_abbreviation",
                    "error",
                    candidate,
                    "약어는 대문자 영문으로 시작하고 영문·숫자만 포함해야 합니다.",
                    (
                        f"'{candidate.abbreviation}'을 대문자 영문으로 시작하는 "
                        "영문·숫자 약어로 수정하세요. 수정한 약어는 원본 컬럼명에 "
                        "실제로 존재해야 합니다."
                    ),
                    {
                        "actual_abbreviation": candidate.abbreviation,
                        "expected_pattern": "^[A-Z][A-Z0-9]*$",
                        "column_name": source.column_name,
                    },
                )
            )
        elif candidate.abbreviation not in normalized_column:
            issues.append(
                _source_issue(
                    "abbreviation_not_in_column",
                    "error",
                    candidate,
                    "약어가 원본 컬럼명에 연속 문자열로 존재하지 않습니다.",
                    (
                        f"'{candidate.abbreviation}'을 원본 컬럼명 "
                        f"'{source.column_name}' 안에 실제로 연속해서 존재하는 의미 "
                        "단위로 교체하세요. 대응 약어가 없다면 이 후보를 삭제하되 "
                        "해당 source_id에는 최소 1개 이상의 올바른 매핑을 남기세요."
                    ),
                    {
                        "actual_abbreviation": candidate.abbreviation,
                        "expected_condition": (
                            "abbreviation must be a contiguous substring of "
                            "normalized column_name"
                        ),
                        "column_name": source.column_name,
                    },
                )
            )

        if not candidate.full_name or not FULL_NAME_PATTERN.fullmatch(
            candidate.full_name
        ):
            issues.append(
                _source_issue(
                    "invalid_full_name",
                    "error",
                    candidate,
                    "영문 Full Name이 비어 있거나 허용되지 않은 문자를 포함합니다.",
                    (
                        f"약어 '{candidate.abbreviation}'의 문맥상 정확한 영문 원형을 "
                        "확인해 대문자 영문으로 수정하세요. 한글단어와 컬럼설명의 "
                        "의미도 함께 일치해야 합니다."
                    ),
                    {
                        "actual_full_name": candidate.full_name,
                        "expected_pattern": FULL_NAME_PATTERN.pattern,
                        "abbreviation": candidate.abbreviation,
                    },
                )
            )
        elif canonical_resolver is not None:
            canonical_full_name = canonical_resolver(
                candidate.abbreviation,
                candidate.korean_word,
            )
            if (
                canonical_full_name is not None
                and candidate.full_name != canonical_full_name
            ):
                issues.append(
                    _source_issue(
                        "noncanonical_full_name",
                        "error",
                        candidate,
                        "표준 사전과 다른 영문 Full Name이 할당되었습니다.",
                        (
                            f"약어 '{candidate.abbreviation}'와 한글단어 "
                            f"'{candidate.korean_word}'의 full_name을 표준 사전값 "
                            f"'{canonical_full_name}'으로 교체하세요."
                        ),
                        {
                            "actual_full_name": candidate.full_name,
                            "canonical_full_name": canonical_full_name,
                            "abbreviation": candidate.abbreviation,
                            "korean_word": candidate.korean_word,
                        },
                    )
                )

        if not candidate.korean_word:
            issues.append(
                _source_issue(
                    "empty_korean_word",
                    "error",
                    candidate,
                    "한글단어가 비어 있습니다.",
                    (
                        f"컬럼설명 '{source.column_description}'에서 약어 "
                        f"'{candidate.abbreviation}'에 대응하는 조사 없는 최소 의미 "
                        "단위를 찾아 korean_word에 입력하세요."
                    ),
                    {
                        "actual_korean_word": candidate.korean_word,
                        "column_description": source.column_description,
                    },
                )
            )
        elif source.column_description and not _meaning_contained(
            candidate.korean_word, source.column_description
        ):
            issues.append(
                _source_issue(
                    "korean_word_not_in_description",
                    "error",
                    candidate,
                    "한글단어가 컬럼설명에서 직접 확인되지 않습니다.",
                    (
                        f"현재 한글단어 '{candidate.korean_word}'을 컬럼설명 "
                        f"'{source.column_description}'에서 직접 확인되는 최소 의미 "
                        "단어로 교체하세요. 대응되는 설명 단어가 없다면 이 매핑을 "
                        "삭제하고 컬럼명·설명에 근거한 다른 매핑으로 대체하세요."
                    ),
                    {
                        "actual_korean_word": candidate.korean_word,
                        "expected_condition": (
                            "korean_word must be directly contained in "
                            "column_description after normalization"
                        ),
                        "column_description": source.column_description,
                    },
                )
            )

    covered_source_count = sum(
        bool(by_source.get(source.source_id)) for source in source_list
    )
    missing_ids = [
        source.source_id
        for source in source_list
        if not by_source.get(source.source_id)
    ]
    for source_id in missing_ids:
        issues.append(
            ValidationIssue(
                code="missing_source_mapping",
                severity="error",
                message="원본 컬럼에 대한 매핑이 한 건도 없습니다.",
                suggested_action=(
                    f"컬럼명 '{source_by_id[source_id].column_name}'과 컬럼설명 "
                    f"'{source_by_id[source_id].column_description}'을 의미 단위로 "
                    "분해하여 최소 1개 이상의 전체 매핑을 새로 생성하세요."
                ),
                source_ids=[source_id],
                details={
                    "source": source_by_id[source_id].model_dump(),
                    "expected_minimum_mapping_count": 1,
                },
            )
        )

    seen_per_source: set[tuple[str, str, str, str]] = set()
    duplicate_count = 0
    for candidate in candidate_list:
        key = (
            candidate.source_id,
            candidate.abbreviation,
            candidate.full_name,
            candidate.korean_word,
        )
        if key in seen_per_source:
            duplicate_count += 1
        seen_per_source.add(key)
    if duplicate_count:
        issues.append(
            ValidationIssue(
                code="duplicate_mapping",
                severity="warning",
                message=f"동일 원본 내 완전 중복 매핑이 {duplicate_count}건 있습니다.",
                suggested_action=(
                    "동일 source_id 안에서 abbreviation, full_name, korean_word가 "
                    "모두 같은 항목은 하나만 남기고 나머지를 제거하세요."
                ),
                details={"duplicate_count": duplicate_count},
            )
        )

    meanings: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for candidate in candidate_list:
        if candidate.source_id in source_by_id:
            meanings[(candidate.abbreviation, candidate.korean_word)][
                candidate.full_name
            ].add(candidate.source_id)

    for (abbreviation, korean_word), full_names in meanings.items():
        if len(full_names) <= 1:
            continue
        glossary_full_name = (
            canonical_resolver(abbreviation, korean_word)
            if canonical_resolver is not None
            else None
        )
        recommended_full_name = glossary_full_name or sorted(
            full_names,
            key=lambda full_name: (
                -len(full_names[full_name]),
                full_name,
            ),
        )[0]
        source_ids = sorted(
            {source_id for ids in full_names.values() for source_id in ids}
        )
        issues.append(
            ValidationIssue(
                code="conflicting_full_name",
                severity="error",
                message=(
                    "같은 영문약어·한글단어 조합에 서로 다른 영문 Full Name이 "
                    "할당되었습니다."
                ),
                suggested_action=(
                    f"약어 '{abbreviation}'와 한글단어 '{korean_word}'의 원본 "
                    f"문맥을 비교한 결과, 모든 관련 매핑의 full_name을 "
                    f"'{recommended_full_name}'으로 통일하세요. 실제 의미가 다른 "
                    "후보만 컬럼설명에서 직접 확인되는 서로 다른 korean_word로 "
                    "수정하세요."
                ),
                source_ids=source_ids,
                details={
                    "abbreviation": abbreviation,
                    "korean_word": korean_word,
                    "full_names": sorted(full_names),
                    "full_name_source_counts": {
                        full_name: len(source_ids_for_name)
                        for full_name, source_ids_for_name in sorted(
                            full_names.items()
                        )
                    },
                    "recommended_full_name": recommended_full_name,
                    "recommendation_source": (
                        "canonical_glossary"
                        if glossary_full_name is not None
                        else "majority"
                    ),
                },
            )
        )

    error_count = sum(issue.severity == "error" for issue in issues)
    warning_count = sum(issue.severity == "warning" for issue in issues)
    return ValidationReport(
        is_valid=error_count == 0,
        issues=issues,
        stats={
            "source_count": len(source_list),
            "candidate_count": len(candidate_list),
            "covered_source_count": covered_source_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "duplicate_count": duplicate_count,
        },
    )


def _source_issue(
    code: str,
    severity: str,
    candidate: MappingCandidate,
    message: str,
    suggested_action: str,
    details: dict[str, object] | None = None,
) -> ValidationIssue:
    payload = {"candidate": candidate.model_dump()}
    if details:
        payload.update(details)
    return ValidationIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        suggested_action=suggested_action,
        source_ids=[candidate.source_id],
        details=payload,
    )


def _meaning_contained(word: str, description: str) -> bool:
    normalize = lambda value: re.sub(r"[\s_\-/·(),.]", "", value)
    return normalize(word) in normalize(description)
