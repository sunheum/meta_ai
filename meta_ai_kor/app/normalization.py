from __future__ import annotations

import re
from typing import Iterable

from app.models import NameComponent

ALLOWED_KOREAN_NAME = re.compile(r"^[가-힣0-9]+$")
NORMALIZATION_RULES = (
    ("일자시각", "일시"),
    ("일자시간", "일시"),
    ("날짜시각", "일시"),
)


def normalize_full_name(components: Iterable[NameComponent]) -> str:
    return " ".join(
        re.sub(r"\s+", " ", component.full_name.strip()).upper()
        for component in components
        if component.full_name.strip()
    )


def normalize_korean_name(components: Iterable[NameComponent]) -> str:
    words: list[str] = []
    for component in components:
        word = component.korean_word
        if component.origin == "inference" and not word:
            word = "미정"
        word = re.sub(r"[\s_]+", "", word)
        word = re.sub(r"[^가-힣0-9]", "", word)
        if not word:
            continue
        if words and (words[-1] == word or words[-1].endswith(word)):
            continue
        words.append(word)
    value = "".join(words)
    for before, after in NORMALIZATION_RULES:
        value = value.replace(before, after)
    value = re.sub(r"(코드){2,}$", "코드", value)
    value = re.sub(r"(번호){2,}$", "번호", value)
    value = re.sub(r"(여부){2,}$", "여부", value)
    return value


def is_valid_korean_name(value: str) -> bool:
    return bool(value) and ALLOWED_KOREAN_NAME.fullmatch(value) is not None

