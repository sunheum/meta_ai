from app.models import NameComponent
from app.normalization import (
    is_valid_korean_name,
    normalize_korean_name,
)


def _component(fragment: str, word: str) -> NameComponent:
    return NameComponent(
        source_fragment=fragment,
        full_name=fragment,
        korean_word=word,
        origin="mapping",
    )


def test_korean_words_are_joined_without_spaces():
    value = normalize_korean_name(
        [_component("FNL", "최종 "), _component("LOAD", " 적재")]
    )

    assert value == "최종적재"
    assert is_valid_korean_name(value)


def test_date_and_time_are_normalized_to_datetime():
    value = normalize_korean_name(
        [_component("DT", "일자"), _component("HMS", "시각")]
    )

    assert value == "일시"


def test_adjacent_duplicate_suffix_is_removed():
    value = normalize_korean_name(
        [_component("CD", "코드"), _component("CODE", "코드")]
    )

    assert value == "코드"

