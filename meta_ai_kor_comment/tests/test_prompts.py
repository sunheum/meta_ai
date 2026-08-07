from app.prompts import (
    GENERATION_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    render_system_prompt,
)


def test_generation_prompt_forbids_all_ascii_except_exact_id() -> None:
    assert "정확히 연속된 두 글자 `ID`뿐" in GENERATION_SYSTEM_PROMPT
    assert "결과에 원본 영문 토큰 자체를 남기면 안 된다" in GENERATION_SYSTEM_PROMPT
    # The generation prompt must not embed any specific domain glossary; the
    # per-project glossary is injected at runtime via render_system_prompt.
    for token in ("SOFA", "TPMS", "설계사", "리서스인자", "주한미군"):
        assert token not in GENERATION_SYSTEM_PROMPT


def test_review_prompt_forbids_all_ascii_except_exact_id() -> None:
    assert "정확히 연속된 두 글자 `ID`" in REVIEW_SYSTEM_PROMPT
    for token in ("SOFA", "TPMS", "FY", "SMS", "설계사"):
        assert token not in REVIEW_SYSTEM_PROMPT


def test_render_system_prompt_returns_base_when_glossary_is_empty() -> None:
    assert render_system_prompt(GENERATION_SYSTEM_PROMPT) is GENERATION_SYSTEM_PROMPT
    assert render_system_prompt(GENERATION_SYSTEM_PROMPT, {}) is GENERATION_SYSTEM_PROMPT


def test_render_system_prompt_appends_sorted_glossary_lines() -> None:
    rendered = render_system_prompt(
        GENERATION_SYSTEM_PROMPT,
        {"FY": "회계", "SMS": "문자메시지"},
    )

    assert rendered.startswith(GENERATION_SYSTEM_PROMPT)
    assert "- FY → 회계" in rendered
    assert "- SMS → 문자메시지" in rendered
    assert rendered.index("- FY → 회계") < rendered.index("- SMS → 문자메시지")
