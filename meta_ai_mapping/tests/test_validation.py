from app.models import MappingCandidate, SourceColumn
from app.validation import validate_mappings
from app.workflow import partition_failed_mappings


def test_validation_detects_missing_and_invalid_abbreviation() -> None:
    sources = [
        SourceColumn(
            source_id="row-2",
            column_name="STDT",
            column_description="기준일자",
        ),
        SourceColumn(
            source_id="row-3",
            column_name="YYMM",
            column_description="년월",
        ),
    ]
    candidates = [
        MappingCandidate(
            source_id="row-2",
            abbreviation="XYZ",
            full_name="DATE",
            korean_word="일자",
        )
    ]

    report = validate_mappings(sources, candidates)

    assert not report.is_valid
    assert {issue.code for issue in report.errors} == {
        "abbreviation_not_in_column",
        "missing_source_mapping",
    }
    assert all(issue.suggested_action for issue in report.errors)


def test_validation_accepts_valid_mapping() -> None:
    source = SourceColumn(
        source_id="row-2",
        column_name="STDT",
        column_description="기준일자",
    )
    candidate = MappingCandidate(
        source_id="row-2",
        abbreviation="DT",
        full_name="DATE",
        korean_word="일자",
    )

    report = validate_mappings([source], [candidate])

    assert report.is_valid
    assert report.stats["error_count"] == 0


def test_korean_word_outside_description_has_correction_guidance() -> None:
    source = SourceColumn(
        source_id="row-2",
        column_name="STDT",
        column_description="기준일자",
    )
    candidate = MappingCandidate(
        source_id="row-2",
        abbreviation="DT",
        full_name="DATE",
        korean_word="만기",
    )

    report = validate_mappings([source], [candidate])

    issue = next(
        issue
        for issue in report.errors
        if issue.code == "korean_word_not_in_description"
    )
    assert "기준일자" in issue.suggested_action
    assert "교체" in issue.suggested_action
    assert issue.details["actual_korean_word"] == "만기"
    assert issue.details["column_description"] == "기준일자"


def test_validation_requires_canonical_full_name() -> None:
    source = SourceColumn(
        source_id="row-2",
        column_name="USE_YN",
        column_description="사용여부",
    )
    candidate = MappingCandidate(
        source_id="row-2",
        abbreviation="YN",
        full_name="YES NO",
        korean_word="여부",
    )

    report = validate_mappings(
        [source],
        [candidate],
        canonical_resolver=lambda abbreviation, korean_word: (
            "YES OR NO"
            if (abbreviation, korean_word) == ("YN", "여부")
            else None
        ),
    )

    issue = next(
        issue
        for issue in report.errors
        if issue.code == "noncanonical_full_name"
    )
    assert issue.details["canonical_full_name"] == "YES OR NO"
    assert "YES OR NO" in issue.suggested_action


def test_partial_partition_keeps_majority_conflict_candidates() -> None:
    sources = [
        SourceColumn(
            source_id=f"row-{index}",
            column_name=f"FLAG{index}_YN",
            column_description="사용여부",
        )
        for index in range(2, 5)
    ]
    candidates = [
        MappingCandidate(
            source_id="row-2",
            abbreviation="YN",
            full_name="YES OR NO",
            korean_word="여부",
        ),
        MappingCandidate(
            source_id="row-3",
            abbreviation="YN",
            full_name="YES OR NO",
            korean_word="여부",
        ),
        MappingCandidate(
            source_id="row-4",
            abbreviation="YN",
            full_name="YES NO",
            korean_word="여부",
        ),
    ]

    report = validate_mappings(sources, candidates)
    issue = next(
        issue
        for issue in report.errors
        if issue.code == "conflicting_full_name"
    )
    valid, failed = partition_failed_mappings(sources, candidates, report)

    assert issue.details["recommended_full_name"] == "YES OR NO"
    assert [candidate.source_id for candidate in valid] == ["row-2", "row-3"]
    assert [row.source_id for row in failed] == ["row-4"]
