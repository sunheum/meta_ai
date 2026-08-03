from __future__ import annotations

from app.config import Settings
from scripts.run_actual import (
    _aggregate_terminology_decisions,
    _llm_settings_metadata,
)
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


def test_llm_metadata_records_reproducible_settings_without_secret() -> None:
    settings = Settings(
        llm_api_key="must-not-be-recorded",
        llm_temperature=0.15,
        llm_top_p=0.75,
        llm_max_tokens=4096,
        llm_connect_timeout_seconds=7.0,
        llm_read_timeout_seconds=900.0,
        llm_write_timeout_seconds=30.0,
        llm_pool_timeout_seconds=20.0,
        llm_max_retries=3,
    )

    metadata = _llm_settings_metadata(settings)

    assert metadata == {
        "trust_env": False,
        "temperature": 0.15,
        "top_p": 0.75,
        "max_tokens": 4096,
        "connect_timeout_seconds": 7.0,
        "read_timeout_seconds": 900.0,
        "write_timeout_seconds": 30.0,
        "pool_timeout_seconds": 20.0,
        "max_retries": 3,
    }
    assert "api_key" not in metadata
