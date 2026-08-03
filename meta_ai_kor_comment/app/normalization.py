from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.models import RiskAssessment, RiskLevel, SourceColumn


ASCII_ENGLISH_RE = re.compile(r"[A-Za-z]+")
DIGIT_SEQUENCE_RE = re.compile(r"[0-9]+")
WHITESPACE_RE = re.compile(r"\s+")

# Hangul syllables, Jamo and compatibility Jamo are accepted. The final name
# grammar additionally accepts ASCII digits and the exact token ``ID``.
_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7A3),
    (0xD7B0, 0xD7FF),
)


def normalize_unicode(value: str) -> str:
    """Return the stable Unicode representation used for keys and checks."""

    return unicodedata.normalize("NFKC", value)


def normalize_column_name(value: str) -> str:
    """Normalize a column name for duplicate detection without losing tokens."""

    return WHITESPACE_RE.sub("", normalize_unicode(value)).upper()


def normalize_description_key(value: str) -> str:
    """Normalize layout-only differences while retaining semantic punctuation."""

    return WHITESPACE_RE.sub(" ", normalize_unicode(value)).strip()


def source_dedup_key(source: SourceColumn) -> tuple[str, str]:
    return (
        normalize_column_name(source.column_name),
        normalize_description_key(source.column_description),
    )


def digit_sequences(value: str) -> tuple[str, ...]:
    """Extract significant ASCII digit sequences in their original order."""

    return tuple(DIGIT_SEQUENCE_RE.findall(normalize_unicode(value)))


def digits_are_preserved(source: str, result: str) -> bool:
    return digit_sequences(source) == digit_sequences(result)


def english_tokens(value: str) -> tuple[str, ...]:
    return tuple(ASCII_ENGLISH_RE.findall(normalize_unicode(value)))


def invalid_english_tokens(value: str) -> tuple[str, ...]:
    """Return English runs other than the exact, uppercase ``ID`` token."""

    return tuple(token for token in english_tokens(value) if token != "ID")


def is_hangul(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _HANGUL_RANGES)


def forbidden_characters(value: str) -> tuple[str, ...]:
    """Return every character outside Hangul, digits and exact ``ID`` units.

    Parsing ``ID`` as one unit prevents another English token from being accepted
    just because it happens to contain the letters I or D.
    """

    normalized = normalize_unicode(value)
    invalid: list[str] = []
    index = 0
    while index < len(normalized):
        if normalized.startswith("ID", index):
            index += 2
            continue
        character = normalized[index]
        if character.isascii() and character.isdigit():
            index += 1
            continue
        if is_hangul(character):
            index += 1
            continue
        invalid.append(character)
        index += 1
    return tuple(invalid)


def symbols_in(value: str) -> tuple[str, ...]:
    """Return punctuation/symbol characters, retaining first-seen order."""

    symbols: list[str] = []
    for character in normalize_unicode(value):
        if unicodedata.category(character)[0] not in {"P", "S"}:
            continue
        if character not in symbols:
            symbols.append(character)
    return tuple(symbols)


def has_valid_name_characters(value: str) -> bool:
    return bool(value) and not forbidden_characters(value)


def can_keep_description(value: str) -> bool:
    """Whether the description already satisfies the final name character policy."""

    return (
        bool(value)
        and normalize_unicode(value) == value
        and not forbidden_characters(value)
    )


def compact_whitespace(value: str) -> str:
    """Remove whitespace only; semantic punctuation is never silently deleted."""

    return WHITESPACE_RE.sub("", normalize_unicode(value))


def classify_description(
    value: str,
    *,
    source_id: str | None = None,
    terminology_conflict: bool = False,
) -> RiskAssessment:
    """Classify deterministic risks before generation.

    Codes are emitted in a stable priority order so metadata and tests remain
    reproducible across runs.
    """

    normalized = normalize_unicode(value)
    codes: list[str] = []
    tokens = list(english_tokens(normalized))
    invalid_tokens = [token for token in tokens if token != "ID"]
    symbols = list(symbols_in(normalized))
    digits = list(digit_sequences(normalized))

    if not normalized:
        codes.append("empty_description")
    if normalized != value:
        codes.append("unicode_normalization")
    if WHITESPACE_RE.search(normalized):
        codes.append("whitespace")
    if "/" in normalized:
        codes.append("slash_ambiguity")
    if symbols:
        codes.append("special_symbol")
    if invalid_tokens:
        codes.append("english_translation_required")
    if digits:
        codes.append("numeric_sensitive")
    if terminology_conflict:
        codes.append("terminology_conflict")

    recognized_invalid = set(invalid_tokens)
    invalid_characters = forbidden_characters(normalized)
    unsupported = [
        character
        for character in invalid_characters
        if not character.isspace()
        and character not in symbols
        and not any(character in token for token in recognized_invalid)
    ]
    if unsupported:
        codes.append("unsupported_character")

    if "empty_description" in codes:
        level = RiskLevel.CRITICAL
    elif any(
        code in codes
        for code in ("slash_ambiguity", "english_translation_required")
    ):
        level = RiskLevel.HIGH
    elif codes:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    generation_codes = {
        "empty_description",
        "whitespace",
        "slash_ambiguity",
        "special_symbol",
        "english_translation_required",
        "terminology_conflict",
        "unsupported_character",
        "unicode_normalization",
    }
    review_codes = {
        "empty_description",
        "slash_ambiguity",
        "terminology_conflict",
        "unsupported_character",
    }
    return RiskAssessment(
        source_id=source_id,
        level=level,
        codes=codes,
        english_tokens=tokens,
        digit_sequences=digits,
        symbols=symbols,
        requires_generation=bool(generation_codes.intersection(codes)),
        requires_review=bool(review_codes.intersection(codes)),
    )


def stable_unique(values: Iterable[str]) -> list[str]:
    """Deduplicate while preserving first occurrence; useful for audit metadata."""

    return list(dict.fromkeys(values))
