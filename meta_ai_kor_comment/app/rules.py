from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.models import SourceColumn


class RulesError(ValueError):
    """Raised when a rules YAML file is missing required structure."""


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    source: str
    target: str
    note: str = ""

    def __post_init__(self) -> None:
        source = self.source.strip().upper()
        target = self.target.strip()
        if not source:
            raise RulesError("glossary.source는 비어 있을 수 없습니다.")
        if not target:
            raise RulesError(
                f"glossary.target는 비어 있을 수 없습니다. (source={source})"
            )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "note", self.note.strip())


@dataclass(frozen=True, slots=True)
class SynonymGroupRule:
    id: str
    candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        group_id = self.id.strip()
        if not group_id:
            raise RulesError("synonym_groups[].id는 비어 있을 수 없습니다.")
        normalized = tuple(candidate.strip() for candidate in self.candidates)
        if len(normalized) < 2:
            raise RulesError(
                f"synonym_groups[{group_id}]는 최소 2개의 후보가 필요합니다."
            )
        if any(not candidate for candidate in normalized):
            raise RulesError(
                f"synonym_groups[{group_id}]에 빈 후보가 포함되었습니다."
            )
        if len(set(normalized)) != len(normalized):
            raise RulesError(
                f"synonym_groups[{group_id}] 후보가 중복되었습니다."
            )
        object.__setattr__(self, "id", group_id)
        object.__setattr__(self, "candidates", normalized)


@dataclass(frozen=True, slots=True)
class DomainRules:
    glossary: tuple[GlossaryEntry, ...] = ()
    synonym_groups: tuple[SynonymGroupRule, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.glossary and not self.synonym_groups

    def glossary_lookup(self) -> dict[str, str]:
        return {entry.source: entry.target for entry in self.glossary}


def load_rules(path: Path | str | None) -> DomainRules:
    """Load a rules YAML file. Return an empty ruleset when ``path`` is None.

    - ``None`` → empty ``DomainRules`` (default domain-neutral behavior).
    - Missing file at an explicit path → ``FileNotFoundError``.
    - Malformed / unknown schema → ``RulesError``.
    """

    if path is None:
        return DomainRules()

    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"규칙 파일을 찾을 수 없습니다: {resolved}")

    with resolved.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)

    if raw is None:
        return DomainRules()
    if not isinstance(raw, dict):
        raise RulesError(
            f"규칙 파일의 최상위는 매핑이어야 합니다: {resolved} ({type(raw).__name__})"
        )

    unknown = set(raw) - {"glossary", "synonym_groups"}
    if unknown:
        raise RulesError(
            f"규칙 파일에 알 수 없는 최상위 키가 있습니다: {sorted(unknown)}"
        )

    glossary = _parse_glossary(raw.get("glossary"))
    synonym_groups = _parse_synonym_groups(raw.get("synonym_groups"))
    return DomainRules(glossary=glossary, synonym_groups=synonym_groups)


def load_rules_optional(path: Path | str | None) -> DomainRules:
    """Load rules if the file exists, otherwise return an empty ruleset.

    Use this for the default ``config/rules.yaml`` path so a missing file at
    the conventional location is not an error — a fresh checkout stays fully
    functional in domain-neutral mode.
    """

    if path is None:
        return DomainRules()
    resolved = Path(path)
    if not resolved.is_file():
        return DomainRules()
    return load_rules(resolved)


def _parse_glossary(raw: Any) -> tuple[GlossaryEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RulesError(
            f"glossary는 리스트여야 합니다. ({type(raw).__name__})"
        )
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RulesError(
                f"glossary[{index}]는 매핑이어야 합니다. ({type(item).__name__})"
            )
        allowed = {"source", "target", "note"}
        unknown = set(item) - allowed
        if unknown:
            raise RulesError(
                f"glossary[{index}]에 알 수 없는 키가 있습니다: {sorted(unknown)}"
            )
        try:
            source = item["source"]
            target = item["target"]
        except KeyError as exc:
            raise RulesError(
                f"glossary[{index}]에 필수 키가 없습니다: {exc.args[0]}"
            ) from exc
        entry = GlossaryEntry(
            source=str(source),
            target=str(target),
            note=str(item.get("note", "")),
        )
        if entry.source in seen:
            raise RulesError(
                f"glossary.source가 중복되었습니다: {entry.source}"
            )
        seen.add(entry.source)
        entries.append(entry)
    return tuple(entries)


_ENGLISH_TOKEN_RE = re.compile(r"[A-Za-z]+")


@dataclass
class _TokenStats:
    count: int = 0
    examples: list[str] = field(default_factory=list)


def build_rules_template(
    sources: Iterable[SourceColumn],
    *,
    existing_rules: DomainRules | None = None,
    input_label: str | None = None,
    max_examples_per_token: int = 3,
) -> str:
    """Emit a YAML rules template pre-populated from source descriptions.

    - ``glossary``: every English token found in ``column_description`` values
      that is not already covered by ``existing_rules`` (and not the always-
      allowed ASCII ``ID``) is emitted with ``source`` filled in and ``target``
      left as an empty string. Entries are sorted by descending frequency and
      annotated with occurrence counts and up to ``max_examples_per_token``
      example descriptions.
    - ``synonym_groups``: emitted as an empty stub with a worked example in a
      comment. Reliable synonym detection needs runtime outputs
      (which surfaces the LLM picked for equivalent concepts), so this
      section is meant to be filled from the ``검토필요`` sheet after a run.
    """

    known = (
        {entry.source for entry in existing_rules.glossary}
        if existing_rules is not None
        else set()
    )

    token_stats: dict[str, _TokenStats] = {}
    total_rows = 0
    for source in sources:
        total_rows += 1
        for token in _ENGLISH_TOKEN_RE.findall(source.column_description or ""):
            upper = token.upper()
            if upper == "ID":
                continue
            if upper in known:
                continue
            stats = token_stats.setdefault(upper, _TokenStats())
            stats.count += 1
            if (
                source.column_description not in stats.examples
                and len(stats.examples) < max_examples_per_token
            ):
                stats.examples.append(source.column_description)

    ordered = sorted(
        token_stats.items(),
        key=lambda pair: (-pair[1].count, pair[0]),
    )

    lines: list[str] = ["# 자동 생성된 규칙 템플릿"]
    if input_label:
        lines.append(f"# 입력: {input_label} ({total_rows:,}행)")
    else:
        lines.append(f"# 입력 행 수: {total_rows:,}")
    if existing_rules is not None and not existing_rules.is_empty:
        lines.append(f"# 기존 규칙 대비 새 토큰: {len(ordered)}건")
    lines.extend(
        [
            "#",
            "# 다음 단계:",
            "#   1. glossary의 각 target을 그 약어가 실제로 뜻하는 한글 표준어로 채우세요.",
            "#      target이 비어 있으면 파이프라인은 해당 약어를 한글화하지 못하고",
            "#      결과를 '검토필요'로 남깁니다.",
            "#   2. 파이프라인을 실행한 뒤 결과 XLSX의 '검토필요' 시트에서 '용어 통일'",
            "#      관련 리뷰 사유를 확인하고 synonym_groups를 추가하세요.",
            "#      candidates 목록의 첫 항목이 빈도 동률 시 우선 채택됩니다.",
            "#   3. 이 파일을 config/rules.yaml로 저장하거나 --rules PATH로 지정하세요.",
            "",
        ]
    )

    if ordered:
        lines.append("glossary:")
        for index, (token, stats) in enumerate(ordered):
            if index > 0:
                lines.append("")
            examples = ", ".join(stats.examples)
            lines.append(f"  # {stats.count}건 · 예: {examples}")
            lines.append(f"  - source: {token}")
            lines.append('    target: ""')
    else:
        lines.append("# 확인된 미확정 영문 토큰이 없습니다.")
        lines.append("glossary: []")

    lines.extend(
        [
            "",
            "synonym_groups: []",
            "# 예시(실행 후 검토필요 시트에서 발견되면 아래 형식으로 추가):",
            "# synonym_groups:",
            "#   - id: payment-action",
            "#     candidates: [납입, 납부]  # 빈도 동률 시 첫 후보 우선",
        ]
    )

    return "\n".join(lines) + "\n"


def _parse_synonym_groups(raw: Any) -> tuple[SynonymGroupRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RulesError(
            f"synonym_groups는 리스트여야 합니다. ({type(raw).__name__})"
        )
    groups: list[SynonymGroupRule] = []
    seen_ids: set[str] = set()
    seen_candidates: dict[str, str] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RulesError(
                f"synonym_groups[{index}]는 매핑이어야 합니다. "
                f"({type(item).__name__})"
            )
        allowed = {"id", "candidates"}
        unknown = set(item) - allowed
        if unknown:
            raise RulesError(
                f"synonym_groups[{index}]에 알 수 없는 키가 있습니다: "
                f"{sorted(unknown)}"
            )
        try:
            group_id = item["id"]
            candidates = item["candidates"]
        except KeyError as exc:
            raise RulesError(
                f"synonym_groups[{index}]에 필수 키가 없습니다: {exc.args[0]}"
            ) from exc
        if not isinstance(candidates, list):
            raise RulesError(
                f"synonym_groups[{index}].candidates는 리스트여야 합니다. "
                f"({type(candidates).__name__})"
            )
        group = SynonymGroupRule(
            id=str(group_id),
            candidates=tuple(str(candidate) for candidate in candidates),
        )
        if group.id in seen_ids:
            raise RulesError(
                f"synonym_groups.id가 중복되었습니다: {group.id}"
            )
        seen_ids.add(group.id)
        for candidate in group.candidates:
            owner = seen_candidates.get(candidate)
            if owner is not None and owner != group.id:
                raise RulesError(
                    f"용어 '{candidate}'가 그룹 '{owner}'와 '{group.id}'에 "
                    "중복 등록되었습니다."
                )
            seen_candidates[candidate] = group.id
        groups.append(group)
    return tuple(groups)
