from __future__ import annotations

import math
import re
from functools import lru_cache
from itertools import product

from app.glossary import MappingGlossary
from app.models import (
    NameComponent,
    SegmentationCandidate,
    SourceRow,
)

STANDARD_SUFFIXES = {
    "CD",
    "NO",
    "DT",
    "HMS",
    "AMT",
    "CT",
    "YN",
    "NM",
    "SEQ",
    "SNO",
    "SEQNO",
    "FLGCD",
}


def segment_column(
    source: SourceRow,
    glossary: MappingGlossary,
    max_candidates: int = 8,
) -> list[SegmentationCandidate]:
    raw_tokens = [
        (match.group(), match.start())
        for match in re.finditer(r"[A-Z0-9]+", source.column_name.upper())
    ]
    if not raw_tokens:
        return []
    token_candidates = [
        _segment_token(
            token,
            source,
            glossary,
            max_candidates=max_candidates,
        )
        for token, _ in raw_tokens
    ]
    combined: list[SegmentationCandidate] = []
    for combination in product(*token_candidates):
        components: list[NameComponent] = []
        unresolved: list[str] = []
        score = 0.0
        covered = 0
        total = 0
        ambiguity_count = 0
        for candidate, (token, offset) in zip(
            combination,
            raw_tokens,
            strict=True,
        ):
            total += len(token)
            covered += round(candidate.coverage * len(token))
            score += candidate.score
            ambiguity_count += candidate.ambiguity_count
            unresolved.extend(candidate.unresolved_fragments)
            components.extend(
                component.model_copy(
                    update={
                        "start": component.start + offset,
                        "end": component.end + offset,
                    }
                )
                for component in candidate.components
            )
        combined.append(
            SegmentationCandidate(
                components=components,
                unresolved_fragments=unresolved,
                score=round(score, 6),
                coverage=covered / total if total else 0,
                ambiguity_count=ambiguity_count,
            )
        )
    return _deduplicate_and_rank(combined, max_candidates)


def _segment_token(
    token: str,
    source: SourceRow,
    glossary: MappingGlossary,
    max_candidates: int,
) -> list[SegmentationCandidate]:
    @lru_cache(maxsize=None)
    def walk(position: int) -> tuple[SegmentationCandidate, ...]:
        if position == len(token):
            return (
                SegmentationCandidate(
                    components=[],
                    score=0.0,
                    coverage=1.0,
                ),
            )
        options: list[SegmentationCandidate] = []
        if token[position].isdigit():
            end = position + 1
            while end < len(token) and token[end].isdigit():
                end += 1
            fragment = token[position:end]
            for tail in walk(end):
                options.append(
                    _prepend(
                        NameComponent(
                            source_fragment=fragment,
                            full_name=fragment,
                            korean_word=fragment,
                            origin="numeric",
                            start=position,
                            end=end,
                        ),
                        tail,
                        component_score=0.5 * len(fragment),
                        mapped_chars=len(fragment),
                        token_length=len(token),
                        ambiguous=False,
                    )
                )
        for end in range(len(token), position, -1):
            fragment = token[position:end]
            entry, ambiguous = glossary.resolve(fragment, source)
            if entry is None:
                continue
            component_score = (
                10.0 * len(fragment)
                + float(len(fragment) ** 2)
                + math.log1p(entry.occurrence_count)
                - 1.0
                - (3.0 if ambiguous else 0.0)
                + (
                    4.0
                    if end == len(token) and fragment in STANDARD_SUFFIXES
                    else 0.0
                )
            )
            for tail in walk(end):
                options.append(
                    _prepend(
                        NameComponent(
                            source_fragment=fragment,
                            full_name=entry.full_name,
                            korean_word=entry.korean_word,
                            origin="mapping",
                            start=position,
                            end=end,
                            occurrence_count=entry.occurrence_count,
                        ),
                        tail,
                        component_score=component_score,
                        mapped_chars=len(fragment),
                        token_length=len(token),
                        ambiguous=ambiguous,
                    )
                )
        if not options or all(
            option.components[0].origin != "inference"
            for option in options
        ):
            end = position + 1
            for tail in walk(end):
                options.append(
                    _prepend(
                        NameComponent(
                            source_fragment=token[position:end],
                            full_name=token[position:end],
                            korean_word="",
                            origin="inference",
                            start=position,
                            end=end,
                        ),
                        tail,
                        component_score=-20.0,
                        mapped_chars=0,
                        token_length=len(token),
                        ambiguous=False,
                    )
                )
        merged = [
            candidate.model_copy(
                update={
                    "components": _merge_unresolved(candidate.components),
                }
            )
            for candidate in options
        ]
        for candidate in merged:
            candidate.unresolved_fragments = [
                component.source_fragment
                for component in candidate.components
                if component.origin == "inference"
            ]
        return tuple(_deduplicate_and_rank(merged, max_candidates * 3))

    recalculated = []
    for candidate in walk(0):
        covered = sum(
            len(component.source_fragment)
            for component in candidate.components
            if component.origin != "inference"
        )
        recalculated.append(
            candidate.model_copy(
                update={
                    "coverage": covered / len(token),
                    "unresolved_fragments": [
                        component.source_fragment
                        for component in candidate.components
                        if component.origin == "inference"
                    ],
                }
            )
        )
    return _deduplicate_and_rank(recalculated, max_candidates)


def _prepend(
    component: NameComponent,
    tail: SegmentationCandidate,
    component_score: float,
    mapped_chars: int,
    token_length: int,
    ambiguous: bool,
) -> SegmentationCandidate:
    tail_covered = round(tail.coverage * max(0, token_length - component.end))
    coverage = (mapped_chars + tail_covered) / token_length
    return SegmentationCandidate(
        components=[component, *tail.components],
        unresolved_fragments=list(tail.unresolved_fragments),
        score=round(component_score + tail.score, 6),
        coverage=max(0.0, min(1.0, coverage)),
        ambiguity_count=tail.ambiguity_count + int(ambiguous),
    )


def _merge_unresolved(
    components: list[NameComponent],
) -> list[NameComponent]:
    merged: list[NameComponent] = []
    for component in components:
        if (
            merged
            and component.origin == "inference"
            and merged[-1].origin == "inference"
            and merged[-1].end == component.start
        ):
            previous = merged[-1]
            merged[-1] = previous.model_copy(
                update={
                    "source_fragment": (
                        previous.source_fragment + component.source_fragment
                    ),
                    "full_name": previous.full_name + component.full_name,
                    "end": component.end,
                }
            )
        else:
            merged.append(component)
    return merged


def _deduplicate_and_rank(
    candidates: list[SegmentationCandidate],
    limit: int,
) -> list[SegmentationCandidate]:
    selected: dict[tuple[tuple[str, str, str, str], ...], SegmentationCandidate] = {}
    for candidate in candidates:
        key = tuple(
            (
                component.source_fragment,
                component.full_name,
                component.korean_word,
                component.origin,
            )
            for component in candidate.components
        )
        previous = selected.get(key)
        if previous is None or candidate.score > previous.score:
            selected[key] = candidate
    return sorted(
        selected.values(),
        key=lambda item: (
            -item.coverage,
            -item.score,
            len(item.components),
            tuple(
                (
                    component.source_fragment,
                    component.full_name,
                    component.korean_word,
                )
                for component in item.components
            ),
        ),
    )[:limit]
