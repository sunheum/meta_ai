import pytest

from app.exceptions import TerminologyError
from app.models import GenerationResult, ProcessingAction
from app.terminology import (
    SynonymGroup,
    TerminologyContext,
    build_frequency_tables,
    reconcile_results,
    select_preferred_term,
    validate_synonym_groups,
)


def _result(source_id: str, description: str, units: list[str]) -> GenerationResult:
    return GenerationResult(
        source_id=source_id,
        original_description=description,
        korean_attribute_name="".join(units),
        action=ProcessingAction.KEEP,
        confidence=98,
        semantic_units=units,
    )


def test_frequency_counts_use_duplicate_occurrence_weights() -> None:
    group = SynonymGroup("payment", ("납입", "납부"))
    results = [
        _result("row-2", "납입횟수", ["납입", "횟수"]),
        _result("row-3", "납부횟수", ["납부", "횟수"]),
    ]

    tables = build_frequency_tables(
        results,
        [group],
        occurrence_weights={"row-2": 2, "row-3": 3},
    )
    decision = select_preferred_term(group, tables["payment"])

    assert tables == {"payment": {"납입": 2, "납부": 3}}
    assert decision.selected_term == "납부"
    assert decision.selection_source == "frequency"
    assert not decision.tied


def test_frequency_tie_is_resolved_by_row_context() -> None:
    group = SynonymGroup("vehicle", ("차", "차량"))
    context = TerminologyContext(
        source_id="row-2",
        original_description="차량형태코드",
        table_description="차량 계약 정보",
    )

    decision = select_preferred_term(
        group,
        {"차": 5, "차량": 5},
        context=context,
    )

    assert decision.selected_term == "차량"
    assert decision.tied
    assert decision.selection_source == "context_evidence"


def test_invalid_tie_resolver_cannot_choose_outside_tied_candidates() -> None:
    group = SynonymGroup("rate", ("율", "요율"))

    with pytest.raises(TerminologyError):
        select_preferred_term(
            group,
            {"율": 1, "요율": 1},
            tie_resolver=lambda candidates, context: "비율",
        )


def test_reconciliation_replaces_exact_units_only_and_records_reason() -> None:
    group = SynonymGroup("vehicle", ("차", "차량"))
    results = [
        _result("row-2", "차형태코드", ["차", "형태", "코드"]),
        _result("row-3", "차량형태코드", ["차량", "형태", "코드"]),
        _result("row-4", "차액", ["차액"]),
    ]

    reconciled, decisions = reconcile_results(
        results,
        [group],
        occurrence_weights={"row-2": 1, "row-3": 2, "row-4": 10},
    )

    assert reconciled[0].korean_attribute_name == "차량형태코드"
    assert reconciled[0].action is ProcessingAction.NORMALIZE
    assert "용어 통일" in reconciled[0].reason
    assert reconciled[2].korean_attribute_name == "차액"
    assert len(decisions) == 2


def test_frequency_population_is_independent_from_model_rewrites() -> None:
    group = SynonymGroup("payment", ("납입", "납부"))
    generated = [
        _result("row-2", "납입횟수", ["납입", "횟수"]),
        _result("row-3", "납부횟수", ["납입", "횟수"]),
    ]
    source_frequency = [
        _result("row-2", "납입횟수", ["납입", "횟수"]),
        _result("row-3", "납부횟수", ["납부", "횟수"]),
    ]

    reconciled, decisions = reconcile_results(
        generated,
        [group],
        frequency_results=source_frequency,
        occurrence_weights={"row-2": 1, "row-3": 2},
    )

    assert [result.korean_attribute_name for result in reconciled] == [
        "납부횟수",
        "납부횟수",
    ]
    assert all(
        decision.frequencies == {"납입": 1, "납부": 2}
        for decision in decisions
    )


def test_synonym_surface_cannot_belong_to_two_groups() -> None:
    with pytest.raises(TerminologyError):
        validate_synonym_groups(
            [
                SynonymGroup("one", ("율", "요율")),
                SynonymGroup("two", ("비율", "율")),
            ]
        )
