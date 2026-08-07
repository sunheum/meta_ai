from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.models import SourceColumn
from app.rules import (
    DomainRules,
    GlossaryEntry,
    RulesError,
    SynonymGroupRule,
    build_rules_template,
    load_rules,
    load_rules_optional,
)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(dedent(body), encoding="utf-8")
    return path


def test_load_rules_returns_empty_when_path_is_none() -> None:
    rules = load_rules(None)

    assert rules == DomainRules()
    assert rules.is_empty


def test_load_rules_optional_treats_missing_default_path_as_empty(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does-not-exist.yaml"

    assert load_rules_optional(missing).is_empty
    assert load_rules_optional(None).is_empty


def test_load_rules_raises_file_not_found_for_explicit_missing_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        load_rules(tmp_path / "missing.yaml")


def test_load_rules_parses_glossary_and_normalizes_source(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        glossary:
          - source: fy
            target: 회계
            note: 회계연도
          - source: SMS
            target: 문자메시지
        """,
    )

    rules = load_rules(path)

    assert rules.glossary == (
        GlossaryEntry(source="FY", target="회계", note="회계연도"),
        GlossaryEntry(source="SMS", target="문자메시지", note=""),
    )
    assert rules.glossary_lookup() == {"FY": "회계", "SMS": "문자메시지"}


def test_load_rules_parses_synonym_groups(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        synonym_groups:
          - id: payment-action
            candidates: [납입, 납부]
        """,
    )

    rules = load_rules(path)

    assert rules.synonym_groups == (
        SynonymGroupRule(id="payment-action", candidates=("납입", "납부")),
    )


def test_load_rules_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        glossary: []
        unexpected: value
        """,
    )

    with pytest.raises(RulesError, match="알 수 없는 최상위 키"):
        load_rules(path)


def test_load_rules_rejects_duplicate_glossary_source(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        glossary:
          - source: FY
            target: 회계
          - source: fy
            target: 재무
        """,
    )

    with pytest.raises(RulesError, match="glossary.source가 중복"):
        load_rules(path)


def test_load_rules_rejects_missing_glossary_target(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        glossary:
          - source: FY
        """,
    )

    with pytest.raises(RulesError, match="필수 키가 없습니다"):
        load_rules(path)


def test_load_rules_rejects_shared_candidate_across_groups(
    tmp_path: Path,
) -> None:
    path = _write_yaml(
        tmp_path,
        """
        synonym_groups:
          - id: a
            candidates: [율, 요율]
          - id: b
            candidates: [비율, 율]
        """,
    )

    with pytest.raises(RulesError, match="중복 등록"):
        load_rules(path)


def test_load_rules_rejects_top_level_scalar(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        123
        """,
    )

    with pytest.raises(RulesError, match="최상위는 매핑"):
        load_rules(path)


def test_load_rules_accepts_empty_yaml_as_empty_rules(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "")

    assert load_rules(path).is_empty


def _source(source_id: str, name: str, description: str) -> SourceColumn:
    return SourceColumn(
        source_id=source_id,
        column_name=name,
        column_description=description,
    )


def test_build_rules_template_extracts_unknown_english_tokens() -> None:
    sources = [
        _source("row-1", "FY_YR", "FY년도"),
        _source("row-2", "FY_MO", "FY월"),
        _source("row-3", "SMS_YN", "SMS수신여부"),
        _source("row-4", "CUS_ID", "고객ID"),
        _source("row-5", "CUS_NM", "고객명"),
    ]

    template = build_rules_template(sources)

    assert "source: FY" in template
    assert "source: SMS" in template
    # ``ID`` is the always-allowed exception and must not be templated.
    assert "source: ID" not in template
    # Pure-Korean rows contribute nothing.
    assert "고객명" not in template.split("glossary:", 1)[1]
    # Frequency sorting: FY (2) before SMS (1).
    assert template.index("source: FY") < template.index("source: SMS")
    # target is left blank so the user must fill it.
    assert 'target: ""' in template
    # Occurrence count is annotated as a comment.
    assert "2건" in template


def test_build_rules_template_excludes_tokens_already_in_existing_rules() -> None:
    sources = [
        _source("row-1", "FY_YR", "FY년도"),
        _source("row-2", "SMS_YN", "SMS여부"),
    ]
    existing = DomainRules(
        glossary=(GlossaryEntry(source="FY", target="회계"),)
    )

    template = build_rules_template(sources, existing_rules=existing)

    assert "source: SMS" in template
    # FY was already covered, so it must not appear in the new-token list.
    glossary_section = template.split("glossary:", 1)[1].split("synonym_groups", 1)[0]
    assert "source: FY" not in glossary_section
    assert "새 토큰: 1건" in template


def test_build_rules_template_notes_when_no_tokens_found() -> None:
    sources = [_source("row-1", "CUS_NM", "고객명")]

    template = build_rules_template(sources)

    assert "확인된 미확정 영문 토큰이 없습니다" in template
    assert "glossary: []" in template


def test_build_rules_template_is_valid_yaml_and_roundtrips(tmp_path: Path) -> None:
    sources = [
        _source("row-1", "FY_YR", "FY년도"),
        _source("row-2", "SMS_YN", "SMS여부"),
    ]

    template = build_rules_template(sources)
    template_path = tmp_path / "template.yaml"
    template_path.write_text(template, encoding="utf-8")

    # An unedited template loads cleanly (empty target is allowed at parse
    # time to fail later at GlossaryEntry validation).
    with pytest.raises(RulesError, match="target"):
        load_rules(template_path)

    # After the user fills in the targets, the file parses successfully.
    filled = template.replace('target: ""', 'target: PLACEHOLDER')
    template_path.write_text(filled, encoding="utf-8")
    rules = load_rules(template_path)
    assert rules.glossary_lookup() == {"FY": "PLACEHOLDER", "SMS": "PLACEHOLDER"}


def test_build_rules_template_annotates_example_descriptions() -> None:
    sources = [
        _source("row-1", "XYZ_A", "XYZ금액"),
        _source("row-2", "XYZ_B", "XYZ구분코드"),
        _source("row-3", "XYZ_C", "XYZ유형"),
        _source("row-4", "XYZ_D", "XYZ여부"),
    ]

    template = build_rules_template(sources, max_examples_per_token=2)

    # Only the first 2 example descriptions should appear.
    xyz_section = template.split("source: XYZ", 1)[0]
    assert "XYZ금액" in xyz_section
    assert "XYZ구분코드" in xyz_section
    assert "XYZ유형" not in xyz_section
    assert "XYZ여부" not in xyz_section


def test_insurance_preset_roundtrips() -> None:
    preset = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "rules"
        / "examples"
        / "insurance.yaml"
    )

    rules = load_rules(preset)

    lookup = rules.glossary_lookup()
    assert lookup["FY"] == "회계"
    assert lookup["SOFA"] == "주한미군지위협정적용"
    assert lookup["TPMS"] == "타이어공기압감지장치"

    group_ids = {group.id for group in rules.synonym_groups}
    assert group_ids == {
        "payment-action",
        "used-car-rate",
        "vehicle-form",
        "special-contract-rate",
    }
