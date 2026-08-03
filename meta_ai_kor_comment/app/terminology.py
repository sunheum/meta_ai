from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.exceptions import TerminologyError
from app.models import GenerationResult, ProcessingAction, TerminologyDecision


@dataclass(frozen=True, slots=True)
class SynonymGroup:
    """A model-proposed equivalence group whose counts are code-owned."""

    group_id: str
    candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized = tuple(candidate.strip() for candidate in self.candidates)
        if not self.group_id.strip():
            raise TerminologyError("용어 그룹 ID는 비어 있을 수 없습니다.")
        if not normalized or any(not candidate for candidate in normalized):
            raise TerminologyError("용어 그룹에는 비어 있지 않은 후보가 필요합니다.")
        if len(set(normalized)) != len(normalized):
            raise TerminologyError("용어 그룹 후보는 중복될 수 없습니다.")
        object.__setattr__(self, "group_id", self.group_id.strip())
        object.__setattr__(self, "candidates", normalized)


@dataclass(frozen=True, slots=True)
class TerminologyContext:
    source_id: str
    column_name: str = ""
    original_description: str = ""
    table_name: str = ""
    table_description: str = ""
    semantic_units: tuple[str, ...] = ()

    @property
    def searchable_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.original_description,
                self.table_name,
                self.table_description,
                self.column_name,
                " ".join(self.semantic_units),
            )
            if part
        )


TieResolver = Callable[
    [tuple[str, ...], TerminologyContext | None], str | None
]


def validate_synonym_groups(groups: Iterable[SynonymGroup]) -> list[SynonymGroup]:
    """Validate that a surface term belongs to at most one semantic group."""

    group_list = list(groups)
    owners: dict[str, str] = {}
    for group in group_list:
        for candidate in group.candidates:
            previous = owners.get(candidate)
            if previous is not None and previous != group.group_id:
                raise TerminologyError(
                    f"용어 '{candidate}'가 그룹 '{previous}'와 "
                    f"'{group.group_id}'에 중복 등록되었습니다."
                )
            owners[candidate] = group.group_id
    return group_list


def count_group_frequencies(
    semantic_units: Iterable[Sequence[str]],
    group: SynonymGroup,
    *,
    weights: Iterable[int] | None = None,
) -> dict[str, int]:
    """Count exact semantic units, optionally weighted by duplicate source rows."""

    unit_rows = list(semantic_units)
    row_weights = list(weights) if weights is not None else [1] * len(unit_rows)
    if len(row_weights) != len(unit_rows):
        raise TerminologyError("용어 행과 빈도 가중치의 개수가 일치하지 않습니다.")
    if any(
        isinstance(weight, bool) or not isinstance(weight, int) or weight < 1
        for weight in row_weights
    ):
        raise TerminologyError("빈도 가중치는 1 이상의 정수여야 합니다.")

    candidates = set(group.candidates)
    counts: Counter[str] = Counter()
    for row, weight in zip(unit_rows, row_weights, strict=True):
        for unit in row:
            if unit in candidates:
                counts[unit] += weight
    return {candidate: counts[candidate] for candidate in group.candidates}


def build_frequency_tables(
    results: Sequence[GenerationResult],
    groups: Iterable[SynonymGroup],
    *,
    occurrence_weights: Mapping[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    """Calculate reproducible corpus frequencies for every synonym group."""

    group_list = validate_synonym_groups(groups)
    weights = occurrence_weights or {}
    row_weights = [weights.get(result.source_id, 1) for result in results]
    return {
        group.group_id: count_group_frequencies(
            [result.semantic_units for result in results],
            group,
            weights=row_weights,
        )
        for group in group_list
    }


def select_preferred_term(
    group: SynonymGroup,
    frequencies: Mapping[str, int],
    *,
    context: TerminologyContext | None = None,
    tie_resolver: TieResolver | None = None,
) -> TerminologyDecision:
    """Select by frequency, then contextual naturalness, then stable ordering."""

    if set(frequencies) != set(group.candidates):
        raise TerminologyError("빈도표 후보가 용어 그룹과 일치하지 않습니다.")
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in frequencies.values()
    ):
        raise TerminologyError("용어 빈도는 0 이상의 정수여야 합니다.")

    maximum = max(frequencies.values())
    winners = tuple(
        candidate
        for candidate in group.candidates
        if frequencies[candidate] == maximum
    )
    tied = len(winners) > 1

    if not tied:
        selected = winners[0]
        source = "frequency"
        rationale = f"전체 입력 빈도 {maximum}건으로 단독 최다 용어를 선택"
    else:
        selected = tie_resolver(winners, context) if tie_resolver else None
        if selected is not None and selected not in winners:
            raise TerminologyError("동률 판정기는 동률 후보 중 하나만 선택해야 합니다.")
        if selected is not None:
            source = "context_resolver"
            rationale = "빈도 동률에서 문맥 판정기가 자연스러운 용어를 선택"
        else:
            selected = _contextual_choice(winners, context)
            if selected is not None:
                source = "context_evidence"
                rationale = "빈도 동률에서 원본·테이블 문맥의 직접 출현을 우선"
            else:
                selected = sorted(winners)[0]
                source = "deterministic_fallback"
                rationale = "빈도와 문맥 근거가 동률이어서 사전순으로 재현 가능하게 선택"

    return TerminologyDecision(
        source_id=context.source_id if context else None,
        group_id=group.group_id,
        candidates=list(group.candidates),
        frequencies={candidate: frequencies[candidate] for candidate in group.candidates},
        selected_term=selected,
        tied=tied,
        selection_source=source,
        rationale=rationale,
    )


def reconcile_results(
    results: Sequence[GenerationResult],
    groups: Iterable[SynonymGroup],
    *,
    contexts: Mapping[str, TerminologyContext] | None = None,
    occurrence_weights: Mapping[str, int] | None = None,
    tie_resolver: TieResolver | None = None,
) -> tuple[list[GenerationResult], list[TerminologyDecision]]:
    """Apply exact-unit terminology decisions and return an auditable decision log.

    The function never performs substring replacement. This prevents short terms
    such as ``차`` from corrupting unrelated longer business words.
    """

    group_list = validate_synonym_groups(groups)
    tables = build_frequency_tables(
        results, group_list, occurrence_weights=occurrence_weights
    )
    context_map = contexts or {}
    reconciled: list[GenerationResult] = []
    decisions: list[TerminologyDecision] = []

    for result in results:
        units = list(result.semantic_units)
        changed = False
        applied_reasons: list[str] = []
        for group in group_list:
            if not set(units).intersection(group.candidates):
                continue
            context = context_map.get(result.source_id) or TerminologyContext(
                source_id=result.source_id,
                original_description=result.original_description,
                semantic_units=tuple(units),
            )
            decision = select_preferred_term(
                group,
                tables[group.group_id],
                context=context,
                tie_resolver=tie_resolver,
            )
            decisions.append(decision)
            replaced = [
                decision.selected_term if unit in group.candidates else unit
                for unit in units
            ]
            if replaced != units:
                changed = True
                units = replaced
                applied_reasons.append(
                    f"{group.group_id}:{decision.selected_term} "
                    f"({decision.selection_source})"
                )

        if not changed:
            reconciled.append(result)
            continue

        reason_suffix = "용어 통일 " + ", ".join(applied_reasons)
        reason = f"{result.reason}; {reason_suffix}" if result.reason else reason_suffix
        reconciled.append(
            result.model_copy(
                update={
                    "korean_attribute_name": "".join(units),
                    "semantic_units": units,
                    "action": (
                        ProcessingAction.NORMALIZE
                        if result.action is ProcessingAction.KEEP
                        else result.action
                    ),
                    "reason": reason,
                }
            )
        )
    return reconciled, decisions


def _contextual_choice(
    candidates: tuple[str, ...], context: TerminologyContext | None
) -> str | None:
    if context is None:
        return None

    # Direct source-description evidence is stronger than broad table context.
    score_by_candidate: dict[str, int] = {}
    for candidate in candidates:
        score = 0
        if _contains_distinct_candidate(
            context.original_description, candidate, candidates
        ):
            score += 8
        if _contains_distinct_candidate(context.table_name, candidate, candidates):
            score += 3
        if _contains_distinct_candidate(
            context.table_description, candidate, candidates
        ):
            score += 2
        if candidate in context.semantic_units:
            score += 1
        score_by_candidate[candidate] = score
    highest = max(score_by_candidate.values())
    winners = [
        candidate
        for candidate, score in score_by_candidate.items()
        if score == highest and score > 0
    ]
    return winners[0] if len(winners) == 1 else None


def _contains_distinct_candidate(
    text: str, candidate: str, candidates: tuple[str, ...]
) -> bool:
    """Avoid treating ``차`` as evidence when the text only contains ``차량``."""

    if candidate not in text:
        return False
    return not any(
        candidate != other
        and candidate in other
        and other in text
        for other in candidates
    )
