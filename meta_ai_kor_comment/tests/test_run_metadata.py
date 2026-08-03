from __future__ import annotations

from scripts.run_actual import _aggregate_terminology_decisions
from app.models import TerminologyDecision


def test_terminology_metadata_groups_affected_sources_for_review_contract() -> None:
    decisions = [
        TerminologyDecision(
            source_id=source_id,
            group_id="payment-action",
            candidates=["납입", "납부"],
            frequencies={"납입": 24, "납부": 3},
            selected_term="납입",
            tied=False,
            selection_source="frequency",
            rationale="최빈 표현 선택",
        )
        for source_id in ("row-10", "row-2", "row-10")
    ]

    aggregated = _aggregate_terminology_decisions(decisions)

    assert aggregated == [
        {
            "group_id": "payment-action",
            "candidates": ["납입", "납부"],
            "candidate_frequencies": {"납입": 24, "납부": 3},
            "selected_term": "납입",
            "tied": False,
            "selection_source": "frequency",
            "rationale": "최빈 표현 선택",
            "affected_source_ids": ["row-2", "row-10"],
        }
    ]

