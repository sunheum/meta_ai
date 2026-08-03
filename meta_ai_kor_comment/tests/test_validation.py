from app.models import (
    GenerationResult,
    ProcessingAction,
    ProcessingStatus,
    SourceColumn,
)
from app.validation import (
    derive_processing_status,
    finalize_result,
    validate_result,
    validate_results,
)


def _source(
    source_id: str = "row-2",
    description: str = "기준일자",
    column_name: str = "STDT",
) -> SourceColumn:
    return SourceColumn(
        source_id=source_id,
        column_name=column_name,
        column_description=description,
    )


def _result(
    source_id: str = "row-2",
    description: str = "기준일자",
    name: str = "기준일자",
    action: ProcessingAction = ProcessingAction.KEEP,
    **kwargs: object,
) -> GenerationResult:
    return GenerationResult(
        source_id=source_id,
        original_description=description,
        korean_attribute_name=name,
        action=action,
        confidence=kwargs.pop("confidence", 98),
        reason=kwargs.pop("reason", ""),
        semantic_units=kwargs.pop("semantic_units", [name]),
        **kwargs,
    )


def test_valid_clean_keep_result_passes() -> None:
    report = validate_results([_source()], [_result()])

    assert report.is_valid
    assert report.stats["covered_source_count"] == 1
    assert report.stats["error_count"] == 0


def test_character_and_number_policy_errors_are_all_reported() -> None:
    source = _source(description="제2FY년도", column_name="FY2")
    result = _result(
        description="제2FY년도",
        name="FY 년도3",
        action=ProcessingAction.REWRITE,
        reason="잘못된 후보",
    )

    report = validate_results([source], [result])
    codes = {issue.code for issue in report.errors}

    assert "english_not_allowed" in codes
    assert "whitespace_not_allowed" in codes
    assert "numeric_sequence_mismatch" in codes


def test_only_exact_uppercase_id_is_accepted() -> None:
    source = _source(description="고객ID", column_name="CST_ID")
    valid = _result(description="고객ID", name="고객ID")
    invalid = _result(description="고객ID", name="고객id")

    assert validate_results([source], [valid]).is_valid
    assert "english_not_allowed" in {
        issue.code for issue in validate_result(source, invalid)
    }


def test_duplicate_inputs_must_have_identical_results() -> None:
    sources = [
        _source("row-2", "납입횟수", "PYM_CT"),
        _source("row-3", "납입횟수", "PYM_CT"),
    ]
    results = [
        _result("row-2", "납입횟수", "납입횟수"),
        _result(
            "row-3",
            "납입횟수",
            "납부횟수",
            action=ProcessingAction.NORMALIZE,
            reason="용어 통일",
        ),
    ]

    report = validate_results(sources, results)

    issue = next(
        issue
        for issue in report.errors
        if issue.code == "duplicate_input_inconsistent"
    )
    assert issue.source_ids == ["row-2", "row-3"]


def test_risky_duplicate_in_distinct_table_contexts_may_differ() -> None:
    sources = [
        _source("row-2", "OLD차종코드", "OLD_CATCD").model_copy(
            update={"table_name": "INS_CR_CR_DT"}
        ),
        _source("row-3", "OLD차종코드", "OLD_CATCD").model_copy(
            update={"table_name": "INS_CR_CR_FNL"}
        ),
    ]
    results = [
        _result(
            "row-2",
            "OLD차종코드",
            "구차종코드",
            action=ProcessingAction.REWRITE,
            reason="테이블 문맥에서 OLD를 구로 해석",
        ),
        _result(
            "row-3",
            "OLD차종코드",
            "과거차종코드",
            action=ProcessingAction.REWRITE,
            reason="테이블 문맥에서 OLD를 과거로 해석",
        ),
    ]

    report = validate_results(sources, results)

    assert "duplicate_input_inconsistent" not in {
        issue.code for issue in report.errors
    }


def test_keep_action_cannot_hide_a_changed_name() -> None:
    source = _source(description="납입횟수", column_name="PYM_CT")
    result = _result(description="납입횟수", name="납부횟수")

    codes = {issue.code for issue in validate_result(source, result)}

    assert "keep_result_changed" in codes


def test_slash_choice_needs_evidence_and_is_never_auto_confirmed() -> None:
    source = _source(
        description="차대번호/임시번호",
        column_name="CHSNO_OR_TMPNO",
    )
    missing_evidence = _result(
        description="차대번호/임시번호",
        name="차대번호",
        action=ProcessingAction.REWRITE,
        reason="선택",
    )
    assert "slash_selection_evidence_missing" in {
        issue.code for issue in validate_result(source, missing_evidence)
    }

    resolved = _result(
        description="차대번호/임시번호",
        name="차대번호",
        action=ProcessingAction.REWRITE,
        reason="컬럼명의 CHSNO를 근거로 차대번호 선택, 임시번호 제외",
        review_reasons=["슬래시 대안의 업무 문맥 확인 필요"],
    )
    row_issues = validate_result(source, resolved)

    assert not [issue for issue in row_issues if issue.severity == "error"]
    assert (
        derive_processing_status(resolved, row_issues)
        is ProcessingStatus.REVIEW_REQUIRED
    )


def test_finalize_result_records_status_and_exact_error_codes() -> None:
    source = _source(description="제2약관", column_name="TERM2")
    result = _result(
        description="제2약관",
        name="약관",
        action=ProcessingAction.REWRITE,
        reason="숫자를 잘못 누락",
    )
    issues = validate_result(source, result)

    finalized = finalize_result(result, issues)

    assert finalized.status is ProcessingStatus.VALIDATION_FAILED
    assert finalized.validation_issue_codes == ["numeric_sequence_mismatch"]
    report = validate_results([source], [finalized])
    assert {issue.code for issue in report.errors} == {"numeric_sequence_mismatch"}


def test_low_confidence_valid_result_is_review_required() -> None:
    result = _result(confidence=89)

    assert derive_processing_status(result, []) is ProcessingStatus.REVIEW_REQUIRED
