from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from app.models import (
    GenerationResult,
    IssueSeverity,
    KoreanAttributeResult,
    ProcessingAction,
    ProcessingStatus,
    SourceColumn,
    TerminologyDecision,
    ValidationIssue,
    ValidationReport,
)
from app.normalization import (
    digit_sequences,
    forbidden_characters,
    invalid_english_tokens,
    source_processing_key,
    symbols_in,
)


def validate_results(
    sources: Iterable[SourceColumn],
    results: Iterable[GenerationResult],
    *,
    terminology_decisions: Iterable[TerminologyDecision] | None = None,
    auto_confirm_threshold: int = 90,
) -> ValidationReport:
    """Run complete deterministic validation for generated names.

    This function is intentionally pure: it does not repair output, call an LLM,
    or infer missing rows. Every issue contains actionable values for the bounded
    runtime review loop.
    """

    if not 0 <= auto_confirm_threshold <= 100:
        raise ValueError("자동확정 임계값은 0~100이어야 합니다.")

    source_list = list(sources)
    result_list = list(results)
    decision_list = list(terminology_decisions or [])
    issues: list[ValidationIssue] = []

    source_by_id: dict[str, SourceColumn] = {}
    for source in source_list:
        if source.source_id in source_by_id:
            issues.append(
                _issue(
                    "duplicate_source_id",
                    IssueSeverity.ERROR,
                    "입력 source_id가 중복되었습니다.",
                    "원본 Excel 행마다 고유하고 안정적인 source_id를 다시 생성하세요.",
                    [source.source_id],
                )
            )
        else:
            source_by_id[source.source_id] = source

    results_by_id: dict[str, list[GenerationResult]] = defaultdict(list)
    row_issues: dict[str, list[ValidationIssue]] = defaultdict(list)
    for index, result in enumerate(result_list):
        if result.source_id not in source_by_id:
            issues.append(
                _issue(
                    "unknown_source_id",
                    IssueSeverity.ERROR,
                    f"결과에 알 수 없는 source_id가 있습니다: {result.source_id}",
                    "해당 결과를 제거하거나 입력에 존재하는 source_id로 다시 생성하세요.",
                    [result.source_id],
                    {"result_index": index},
                )
            )
            continue
        results_by_id[result.source_id].append(result)

    for source in source_list:
        source_results = results_by_id.get(source.source_id, [])
        if not source_results:
            issues.append(
                _issue(
                    "missing_result",
                    IssueSeverity.ERROR,
                    "원본 행에 대응하는 한글속성명 결과가 없습니다.",
                    "해당 source_id의 결과를 생성하고 전체 source_id 1:1 검증을 반복하세요.",
                    [source.source_id],
                    {"source": source.model_dump()},
                )
            )
            continue
        if len(source_results) > 1:
            duplicate_issue = _issue(
                "duplicate_result",
                IssueSeverity.ERROR,
                "한 원본 행에 둘 이상의 결과가 생성되었습니다.",
                "source_id별 결과를 정확히 하나만 남기세요.",
                [source.source_id],
                {"result_count": len(source_results)},
            )
            issues.append(duplicate_issue)
            row_issues[source.source_id].append(duplicate_issue)

        for result in source_results:
            current = validate_result(source, result)
            issues.extend(current)
            row_issues[source.source_id].extend(current)

    consistency_issues = _validate_duplicate_input_consistency(
        source_list, results_by_id
    )
    issues.extend(consistency_issues)
    for issue in consistency_issues:
        for source_id in issue.source_ids:
            row_issues[source_id].append(issue)

    terminology_issues = _validate_terminology_decisions(
        result_list, decision_list
    )
    issues.extend(terminology_issues)
    for issue in terminology_issues:
        for source_id in issue.source_ids:
            row_issues[source_id].append(issue)

    # Final result objects carry an explicit status. Verify it against the issues
    # that existed before status-contract checks to avoid self-referential errors.
    for result in result_list:
        if not isinstance(result, KoreanAttributeResult):
            continue
        expected = derive_processing_status(
            result,
            row_issues.get(result.source_id, []),
            auto_confirm_threshold=auto_confirm_threshold,
            terminology_decisions=[
                decision
                for decision in decision_list
                if decision.source_id in (None, result.source_id)
            ],
        )
        if result.status is not expected:
            issues.append(
                _issue(
                    "status_mismatch",
                    IssueSeverity.ERROR,
                    "처리상태가 결정적 오류·모호성·신뢰도와 일치하지 않습니다.",
                    f"처리상태를 '{expected.value}'로 변경하세요.",
                    [result.source_id],
                    {
                        "actual_status": result.status.value,
                        "expected_status": expected.value,
                    },
                )
            )

        expected_codes = sorted(
            {
                issue.code
                for issue in row_issues.get(result.source_id, [])
                if issue.severity is IssueSeverity.ERROR
            }
        )
        if sorted(set(result.validation_issue_codes)) != expected_codes:
            issues.append(
                _issue(
                    "validation_issue_codes_mismatch",
                    IssueSeverity.ERROR,
                    "결과의 검증 오류 코드 목록이 실제 전수 검사와 다릅니다.",
                    "validation_issue_codes를 현재 결정적 오류 코드와 일치시키세요.",
                    [result.source_id],
                    {
                        "actual_codes": sorted(set(result.validation_issue_codes)),
                        "expected_codes": expected_codes,
                    },
                )
            )

    error_count = sum(
        issue.severity is IssueSeverity.ERROR for issue in issues
    )
    warning_count = sum(
        issue.severity is IssueSeverity.WARNING for issue in issues
    )
    covered_count = sum(
        len(results_by_id.get(source.source_id, [])) == 1 for source in source_list
    )
    return ValidationReport(
        is_valid=error_count == 0,
        issues=issues,
        stats={
            "source_count": len(source_list),
            "result_count": len(result_list),
            "covered_source_count": covered_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "terminology_decision_count": len(decision_list),
        },
    )


def validate_result(
    source: SourceColumn, result: GenerationResult
) -> list[ValidationIssue]:
    """Validate one source/result pair, excluding global consistency checks."""

    issues: list[ValidationIssue] = []
    details_base = {
        "column_name": source.column_name,
        "column_description": source.column_description,
        "korean_attribute_name": result.korean_attribute_name,
    }

    if not source.column_description:
        issues.append(
            _source_issue(
                "empty_source_description",
                "입력 컬럼설명이 비어 있습니다.",
                "원본 설명을 복구하거나 검증실패로 분류하고 업무 담당자에게 확인하세요.",
                source,
                result,
            )
        )

    if result.original_description != source.column_description:
        issues.append(
            _source_issue(
                "original_description_mismatch",
                "결과가 기록한 원본 설명이 실제 입력과 다릅니다.",
                "original_description을 입력 컬럼설명의 원본 값과 정확히 일치시키세요.",
                source,
                result,
                {
                    "actual_original_description": result.original_description,
                    "expected_original_description": source.column_description,
                },
            )
        )

    if not result.korean_attribute_name:
        issues.append(
            _source_issue(
                "empty_korean_attribute_name",
                "한글속성명이 비어 있습니다.",
                "컬럼설명과 문맥을 사용해 비어 있지 않은 한글속성명을 생성하세요.",
                source,
                result,
                details_base,
            )
        )
        return issues

    whitespace = [
        character
        for character in result.korean_attribute_name
        if character.isspace()
    ]
    if whitespace:
        issues.append(
            _source_issue(
                "whitespace_not_allowed",
                "한글속성명에 공백이 포함되어 있습니다.",
                "의미 단위의 순서는 유지하고 공백 없이 결합하세요.",
                source,
                result,
                {"characters": whitespace},
            )
        )

    if result.korean_attribute_name != unicodedata.normalize(
        "NFKC", result.korean_attribute_name
    ):
        issues.append(
            _source_issue(
                "noncanonical_unicode",
                "한글속성명에 전각 문자 등 비표준 유니코드 표기가 포함되어 있습니다.",
                "NFKC 정규화 후 문자·숫자·ID 정책을 다시 검증하세요.",
                source,
                result,
            )
        )

    invalid_tokens = invalid_english_tokens(result.korean_attribute_name)
    if invalid_tokens:
        issues.append(
            _source_issue(
                "english_not_allowed",
                "한글속성명에는 대문자 ID 외 영문을 사용할 수 없습니다.",
                "영문 토큰을 문맥상 확정된 한글 의미로 완전히 치환하세요.",
                source,
                result,
                {"invalid_english_tokens": list(invalid_tokens)},
            )
        )

    symbols = symbols_in(result.korean_attribute_name)
    if symbols:
        issues.append(
            _source_issue(
                "symbol_not_allowed",
                "한글속성명에 특수문자 또는 기호가 포함되어 있습니다.",
                "기호를 제거하되 슬래시 대안은 문맥상 한 의미만 선택하세요.",
                source,
                result,
                {"symbols": list(symbols)},
            )
        )

    invalid_characters = tuple(
        character
        for character in forbidden_characters(result.korean_attribute_name)
        if not character.isspace()
        and character not in symbols
        and character not in "".join(invalid_tokens)
    )
    if invalid_characters:
        issues.append(
            _source_issue(
                "invalid_character",
                "한글속성명에 한글·숫자·ID 이외 문자가 포함되어 있습니다.",
                "허용되지 않은 문자를 문맥상 정확한 한글 표현으로 치환하세요.",
                source,
                result,
                {"characters": list(invalid_characters)},
            )
        )

    source_digits = digit_sequences(source.column_description)
    result_digits = digit_sequences(result.korean_attribute_name)
    if source_digits != result_digits:
        issues.append(
            _source_issue(
                "numeric_sequence_mismatch",
                "원본 설명의 숫자 시퀀스와 결과의 숫자 시퀀스·순서가 다릅니다.",
                "숫자를 추가·삭제·변환하지 말고 원래 순서와 표기를 복원하세요.",
                source,
                result,
                {
                    "expected_digit_sequences": list(source_digits),
                    "actual_digit_sequences": list(result_digits),
                },
            )
        )

    if result.action is ProcessingAction.KEEP:
        if result.korean_attribute_name != source.column_description:
            issues.append(
                _source_issue(
                    "keep_result_changed",
                    "유지 처리 결과가 원본 컬럼설명과 다릅니다.",
                    "원본과 같게 복원하거나 처리방식을 정규화/재작성으로 변경하세요.",
                    source,
                    result,
                )
            )
    elif not result.reason.strip():
        # This normally cannot pass the Pydantic response model, but retaining the
        # check protects data reconstructed from persisted/unvalidated sources.
        issues.append(
            _source_issue(
                "change_reason_missing",
                "변경된 결과에 변환근거가 없습니다.",
                "원본 대비 변경 내용과 빈도·문맥 판단 근거를 기록하세요.",
                source,
                result,
            )
        )

    if result.added_concepts or result.removed_concepts:
        issues.append(
            _issue(
                "semantic_scope_change_reported",
                IssueSeverity.WARNING,
                "생성 모델이 원본 의미의 추가 또는 삭제를 보고했습니다.",
                "자동확정하지 말고 사람 검토 대상으로 분류하세요.",
                [source.source_id],
                {
                    "added_concepts": result.added_concepts,
                    "removed_concepts": result.removed_concepts,
                },
            )
        )

    if "/" in source.column_description:
        if not result.reason.strip() or not result.review_reasons:
            issues.append(
                _source_issue(
                    "slash_selection_evidence_missing",
                    "슬래시 대안 선택의 근거 또는 검토사유가 없습니다.",
                    "선택한 의미, 제외한 대안과 테이블·컬럼 문맥 근거를 모두 기록하세요.",
                    source,
                    result,
                )
            )
        elif all(
            alternative in result.korean_attribute_name
            for alternative in source.column_description.split("/")
            if alternative
        ):
            issues.append(
                _source_issue(
                    "slash_alternatives_not_resolved",
                    "슬래시 양쪽 대안을 하나로 선택하지 않고 모두 유지했습니다.",
                    "컬럼 문맥을 대표하는 한쪽 의미만 남기세요.",
                    source,
                    result,
                )
            )

    return issues


def derive_processing_status(
    result: GenerationResult,
    issues: Sequence[ValidationIssue],
    *,
    auto_confirm_threshold: int = 90,
    terminology_decisions: Sequence[TerminologyDecision] = (),
) -> ProcessingStatus:
    if any(issue.severity is IssueSeverity.ERROR for issue in issues):
        return ProcessingStatus.VALIDATION_FAILED
    if (
        result.confidence < auto_confirm_threshold
        or result.review_reasons
        or result.reports_semantic_change
        or "/" in result.original_description
        or any(decision.tied for decision in terminology_decisions)
    ):
        return ProcessingStatus.REVIEW_REQUIRED
    return ProcessingStatus.AUTO_CONFIRMED


def finalize_result(
    result: GenerationResult,
    issues: Sequence[ValidationIssue],
    *,
    auto_confirm_threshold: int = 90,
    terminology_decisions: Sequence[TerminologyDecision] = (),
) -> KoreanAttributeResult:
    """Attach a reproducible status and exact deterministic error-code set."""

    status = derive_processing_status(
        result,
        issues,
        auto_confirm_threshold=auto_confirm_threshold,
        terminology_decisions=terminology_decisions,
    )
    return KoreanAttributeResult(
        **result.model_dump(),
        status=status,
        validation_issue_codes=sorted(
            {
                issue.code
                for issue in issues
                if issue.severity is IssueSeverity.ERROR
            }
        ),
        terminology_decisions=[
            f"{decision.group_id}:{decision.selected_term}"
            for decision in terminology_decisions
        ],
    )


def finalize_results(
    results: Sequence[GenerationResult],
    report: ValidationReport,
    *,
    auto_confirm_threshold: int = 90,
    terminology_decisions: Sequence[TerminologyDecision] = (),
) -> list[KoreanAttributeResult]:
    """Finalize all rows using both row-local and global validation findings."""

    return [
        finalize_result(
            result,
            report.issues_for(result.source_id),
            auto_confirm_threshold=auto_confirm_threshold,
            terminology_decisions=[
                decision
                for decision in terminology_decisions
                if decision.source_id in (None, result.source_id)
            ],
        )
        for result in results
    ]


def _validate_duplicate_input_consistency(
    sources: Sequence[SourceColumn],
    results_by_id: dict[str, list[GenerationResult]],
) -> list[ValidationIssue]:
    grouped: dict[tuple[str, ...], list[tuple[SourceColumn, GenerationResult]]] = (
        defaultdict(list)
    )
    for source in sources:
        source_results = results_by_id.get(source.source_id, [])
        if len(source_results) == 1:
            grouped[source_processing_key(source)].append(
                (source, source_results[0])
            )

    issues: list[ValidationIssue] = []
    for key, pairs in grouped.items():
        names = {result.korean_attribute_name for _, result in pairs}
        if len(names) <= 1:
            continue
        source_ids = [source.source_id for source, _ in pairs]
        issues.append(
            _issue(
                "duplicate_input_inconsistent",
                IssueSeverity.ERROR,
                "동일 컬럼명·설명 입력이 서로 다른 한글속성명으로 처리되었습니다.",
                "동일 중복 키를 한 번 처리한 결과로 모든 원본 행에 복제하세요.",
                source_ids,
                {
                    "dedup_key": list(key),
                    "names": sorted(names),
                },
            )
        )
    return issues


def _validate_terminology_decisions(
    results: Sequence[GenerationResult],
    decisions: Sequence[TerminologyDecision],
) -> list[ValidationIssue]:
    result_by_id = {result.source_id: result for result in results}
    issues: list[ValidationIssue] = []
    for decision in decisions:
        if decision.source_id is None:
            continue
        result = result_by_id.get(decision.source_id)
        if result is None:
            issues.append(
                _issue(
                    "terminology_unknown_source",
                    IssueSeverity.ERROR,
                    "용어 결정이 알 수 없는 source_id를 참조합니다.",
                    "현재 결과 모집단에 존재하는 source_id로 용어 결정을 다시 생성하세요.",
                    [decision.source_id],
                )
            )
            continue
        present = set(result.semantic_units).intersection(decision.candidates)
        if present and present != {decision.selected_term}:
            issues.append(
                _issue(
                    "terminology_decision_mismatch",
                    IssueSeverity.ERROR,
                    "최종 의미 단위가 계산된 빈도·문맥 용어 결정과 다릅니다.",
                    f"관련 의미 단위를 '{decision.selected_term}'으로 통일하세요.",
                    [decision.source_id],
                    {
                        "present_terms": sorted(present),
                        "selected_term": decision.selected_term,
                        "frequencies": decision.frequencies,
                    },
                )
            )
    return issues


def _source_issue(
    code: str,
    message: str,
    suggested_action: str,
    source: SourceColumn,
    result: GenerationResult,
    details: dict[str, Any] | None = None,
) -> ValidationIssue:
    payload: dict[str, Any] = {
        "source": source.model_dump(),
        "result": result.model_dump(),
    }
    if details:
        payload.update(details)
    return _issue(
        code,
        IssueSeverity.ERROR,
        message,
        suggested_action,
        [source.source_id],
        payload,
    )


def _issue(
    code: str,
    severity: IssueSeverity,
    message: str,
    suggested_action: str,
    source_ids: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        message=message,
        suggested_action=suggested_action,
        source_ids=source_ids or [],
        details=details or {},
    )


# Compatibility name used by workflows that distinguish generation validation.
validate_generation_results = validate_results
