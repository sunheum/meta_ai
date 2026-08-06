from app.prompts import GENERATION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT


def test_generation_prompt_forbids_all_ascii_except_exact_id() -> None:
    assert "정확히 연속된 두 글자 `ID`뿐" in GENERATION_SYSTEM_PROMPT
    assert "SOFA→주한미군지위협정적용" in GENERATION_SYSTEM_PROMPT
    assert "결과에 이 영문 토큰 자체를 남기면 안 된다" in GENERATION_SYSTEM_PROMPT
    assert "`제N`은 승인된 문맥에서 `N회차`" in GENERATION_SYSTEM_PROMPT
    assert "`제23조`, `제1호`, `제2종`, `제3급`, `제2판`" in GENERATION_SYSTEM_PROMPT


def test_review_prompt_does_not_treat_standard_abbreviations_as_exceptions() -> None:
    assert "SOFA·FY·SMS 등 표준 약어도 예외가 아니며" in REVIEW_SYSTEM_PROMPT
    assert "조·호·종·급·판의 서수" in REVIEW_SYSTEM_PROMPT
