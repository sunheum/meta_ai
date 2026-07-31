from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from app.excel import read_source_workbook, write_result_workbook
from app.glossary import MappingGlossary
from app.models import (
    ColumnResult,
    NameComponent,
    SourceRow,
    WorkflowOptions,
)
from app.normalization import normalize_full_name, normalize_korean_name
from app.segmentation import segment_column


def build_deterministic_results(
    sources: Iterable[SourceRow],
    glossary: MappingGlossary,
    options: WorkflowOptions | None = None,
) -> list[ColumnResult]:
    source_list = list(sources)
    resolved_options = options or WorkflowOptions(use_llm=False)
    by_context: dict[tuple[str, str, str, str], ColumnResult] = {}
    results: list[ColumnResult] = []
    for source in source_list:
        cached = by_context.get(source.context_key)
        if cached is not None:
            results.append(cached.model_copy(update={"source_id": source.source_id}))
            continue
        candidates = segment_column(
            source,
            glossary,
            max_candidates=8,
        )
        if candidates:
            best = candidates[0]
            components = [
                (
                    component.model_copy(update={"korean_word": "미정"})
                    if component.origin == "inference"
                    and not component.korean_word
                    else component
                )
                for component in best.components
            ]
        else:
            components = [
                NameComponent(
                    source_fragment=source.column_name,
                    full_name=source.column_name,
                    korean_word="미정",
                    origin="inference",
                    start=0,
                    end=len(source.column_name),
                )
            ]
            candidates = []
        unresolved = any(
            component.origin == "inference" for component in components
        )
        mapping_ambiguity = bool(
            candidates and candidates[0].ambiguity_count
        )
        segmentation_ambiguity = (
            len(candidates) > 1
            and _candidate_signature(candidates[0])
            != _candidate_signature(candidates[1])
        )
        coverage = candidates[0].coverage if candidates else 0.0
        confidence = round(coverage * 70)
        if coverage == 1:
            confidence += 15
        if unresolved:
            confidence -= 20
        if mapping_ambiguity:
            confidence -= min(24, candidates[0].ambiguity_count * 6)
        if segmentation_ambiguity:
            confidence -= min(16, (len(candidates) - 1) * 4)
        confidence = max(0, min(100, confidence))
        auto_confirmed = (
            not unresolved
            and not mapping_ambiguity
            and not segmentation_ambiguity
            and confidence >= resolved_options.auto_confirm_threshold
        )
        review_stratum = (
            "unmapped_inference"
            if unresolved
            else (
                "mapping_ambiguity"
                if mapping_ambiguity
                else (
                    "segmentation_ambiguity"
                    if segmentation_ambiguity
                    else "deterministic"
                )
            )
        )
        result = ColumnResult(
            source_id=source.source_id,
            components=components,
            english_full_name=normalize_full_name(components),
            korean_attribute_name=normalize_korean_name(components) or "미정",
            status="자동확정" if auto_confirmed else "검토필요",
            confidence=confidence,
            evidence=" | ".join(
                (
                    f"{component.source_fragment}→"
                    f"{component.full_name}→{component.korean_word}"
                    f"[{component.origin}]"
                )
                for component in components
            ),
            reason=(
                f"사전 커버리지={coverage:.3f}; "
                f"후보={len(candidates)}; "
                f"매핑다의={candidates[0].ambiguity_count if candidates else 0}"
            ),
            review_stratum=review_stratum,
        )
        by_context[source.context_key] = result
        results.append(result)
    return assign_review_strata(source_list, results)


def assign_review_strata(
    sources: list[SourceRow],
    results: list[ColumnResult],
) -> list[ColumnResult]:
    result_by_id = {result.source_id: result for result in results}
    sources_by_column: dict[str, list[SourceRow]] = defaultdict(list)
    for source in sources:
        sources_by_column[source.column_name].append(source)
    duplicate_ids = [
        source.source_id
        for source in sources
        if len(
            {
                item.context_key
                for item in sources_by_column[source.column_name]
            }
        )
        > 1
    ]
    selected_duplicate = set(duplicate_ids[: max(5, len(duplicate_ids))])
    review_candidates = [
        result.source_id
        for result in results
        if result.status != "자동확정"
        and result.source_id not in selected_duplicate
        and result.review_stratum == "deterministic"
    ]
    if len(review_candidates) < 5:
        review_candidates.extend(
            result.source_id
            for result in results
            if result.status != "자동확정"
            and result.source_id not in selected_duplicate
            and result.source_id not in review_candidates
        )
    selected_review = set(review_candidates[:5])
    output: list[ColumnResult] = []
    for source in sources:
        result = result_by_id[source.source_id]
        if source.source_id in selected_duplicate:
            result = result.model_copy(
                update={"review_stratum": "duplicate_context"}
            )
        elif source.source_id in selected_review:
            result = result.model_copy(update={"review_stratum": "review_needed"})
        output.append(result)
    return output


def run_deterministic_workbook(
    input_path: str | Path,
    mapping_path: str | Path,
    output_path: str | Path,
    options: WorkflowOptions | None = None,
) -> tuple[list[SourceRow], list[ColumnResult]]:
    sources = read_source_workbook(input_path)
    glossary = MappingGlossary.from_xlsx(mapping_path)
    results = build_deterministic_results(sources, glossary, options)
    write_result_workbook(input_path, output_path, sources, results)
    return sources, results


def review_population(
    sources: list[SourceRow],
    results: list[ColumnResult],
) -> list[dict[str, object]]:
    source_by_id = {source.source_id: source for source in sources}
    return [
        {
            "source_id": result.source_id,
            "review_stratum": result.review_stratum,
            "컬럼명": source_by_id[result.source_id].column_name,
            "테이블명": source_by_id[result.source_id].table_name,
            "테이블설명": source_by_id[result.source_id].table_description,
            "영문 Full Name": result.english_full_name,
            "한글속성명": result.korean_attribute_name,
            "confidence": result.confidence,
            "처리상태": result.status,
            "generation_reason": result.reason,
        }
        for result in results
    ]


def result_stats(results: Iterable[ColumnResult]) -> dict[str, object]:
    result_list = list(results)
    return {
        "row_count": len(result_list),
        "status": dict(Counter(result.status for result in result_list)),
        "review_strata": dict(
            Counter(result.review_stratum for result in result_list)
        ),
        "unresolved_count": sum(
            any(
                component.origin == "inference"
                for component in result.components
            )
            for result in result_list
        ),
    }


def _candidate_signature(candidate) -> tuple[str, ...]:
    return tuple(
        component.source_fragment for component in candidate.components
    )

