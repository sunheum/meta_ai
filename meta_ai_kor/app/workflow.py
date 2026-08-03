from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from contextvars import ContextVar
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from app.excel import read_source_workbook, write_result_workbook
from app.glossary import MappingGlossary
from app.llm import NamingModel
from app.models import (
    ColumnResult,
    LLMResolution,
    NameComponent,
    ProgressEvent,
    ResolutionRequest,
    ReviewRequest,
    SourceRow,
    WorkflowOptions,
)
from app.normalization import normalize_full_name, normalize_korean_name
from app.segmentation import segment_column
from app.review_graph import ReviewGraphState, build_review_graph
from app.validation import (
    apply_validation_status,
    validate_output_workbook,
    validate_results,
)

ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class NamingWorkflow:
    def __init__(
        self,
        glossary: MappingGlossary,
        model: NamingModel | None = None,
        *,
        strict_llm: bool = False,
        max_segmentation_candidates: int = 8,
    ) -> None:
        self._glossary = glossary
        self._model = model
        self._strict_llm = strict_llm
        self._max_segmentation_candidates = max_segmentation_candidates
        self._validation_report_context: ContextVar = ContextVar(
            f"validation_report_{id(self)}",
            default=None,
        )
        self._review_rounds_context: ContextVar[int] = ContextVar(
            f"review_rounds_{id(self)}",
            default=0,
        )

    @property
    def last_validation_report(self):
        return self._validation_report_context.get()

    @property
    def last_review_rounds(self) -> int:
        return self._review_rounds_context.get()

    async def generate(
        self,
        sources: list[SourceRow],
        options: WorkflowOptions,
        progress_callback: ProgressCallback | None = None,
    ) -> list[ColumnResult]:
        self._review_rounds_context.set(0)
        await _emit_progress(
            progress_callback,
            ProgressEvent(
                stage="segment",
                stage_percent=0,
                overall_percent=15,
                message="사전 기반 약어 후보를 분해합니다.",
            ),
        )
        deterministic = build_deterministic_results(
            sources,
            self._glossary,
            options,
        )
        if self._model is None or not options.use_llm:
            return self._finalize(sources, deterministic, options)
        source_by_id = {source.source_id: source for source in sources}
        requests = [
            self._resolution_request(source_by_id[result.source_id])
            for result in deterministic
            if result.status != "자동확정"
        ]
        if not requests:
            return self._finalize(sources, deterministic, options)
        await _emit_progress(
            progress_callback,
            ProgressEvent(
                stage="generate",
                stage_percent=0,
                overall_percent=30,
                message=f"LLM 검토 대상 {len(requests)}건을 처리합니다.",
            ),
        )
        resolutions = await self._resolve_batches(
            requests,
            options,
            progress_callback,
        )
        result_by_id = {result.source_id: result for result in deterministic}
        for resolution in resolutions:
            source = source_by_id.get(resolution.source_id)
            if source is None:
                continue
            accepted = self._accept_resolution(source, resolution)
            if accepted is not None:
                result_by_id[source.source_id] = accepted
        merged = [result_by_id[source.source_id] for source in sources]
        merged = assign_review_strata(sources, merged)
        if (
            options.max_review_rounds > 0
            and hasattr(self._model, "review")
        ):
            merged = await self._review_results(
                sources,
                merged,
                options,
                progress_callback,
            )
        return self._finalize(sources, merged, options)

    async def run(
        self,
        input_path: str | Path,
        output_path: str | Path,
        options: WorkflowOptions,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[SourceRow], list[ColumnResult]]:
        await _emit_progress(
            progress_callback,
            ProgressEvent(
                stage="input",
                stage_percent=0,
                overall_percent=0,
                message="입력 XLSX를 읽습니다.",
            ),
        )
        sources = read_source_workbook(input_path)
        results = await self.generate(sources, options, progress_callback)
        await _emit_progress(
            progress_callback,
            ProgressEvent(
                stage="output",
                stage_percent=50,
                overall_percent=95,
                message="결과 XLSX를 작성합니다.",
            ),
        )
        write_result_workbook(input_path, output_path, sources, results)
        output_report = validate_output_workbook(input_path, output_path)
        if not output_report.is_valid:
            raise ValueError(
                f"출력 XLSX 보존 검증 실패: {output_report.stats}"
            )
        return sources, results

    def _resolution_request(self, source: SourceRow) -> ResolutionRequest:
        candidates = segment_column(
            source,
            self._glossary,
            max_candidates=self._max_segmentation_candidates,
        )
        fragments = {
            component.source_fragment
            for candidate in candidates
            for component in candidate.components
        }
        compact = re.sub(r"[^A-Z0-9]", "", source.column_name)
        fragments.add(compact)
        mapping_options = {
            fragment: list(self._glossary.entries_for(fragment))
            for fragment in sorted(fragments)
            if self._glossary.entries_for(fragment)
        }
        return ResolutionRequest(
            source=source,
            candidates=candidates[:4],
            mapping_options=mapping_options,
            table_peer_columns=[],
        )

    async def _resolve_batches(
        self,
        requests: list[ResolutionRequest],
        options: WorkflowOptions,
        progress_callback: ProgressCallback | None,
    ) -> list[LLMResolution]:
        assert self._model is not None
        semaphore = asyncio.Semaphore(options.max_concurrency)
        batches = [
            requests[index : index + options.batch_size]
            for index in range(0, len(requests), options.batch_size)
        ]
        completed = 0
        lock = asyncio.Lock()

        async def resolve_batch(
            batch: list[ResolutionRequest],
        ) -> list[LLMResolution]:
            nonlocal completed
            try:
                async with semaphore:
                    values = await self._model.resolve(batch)
            except Exception:
                if self._strict_llm:
                    raise
                values = []
            async with lock:
                completed += len(batch)
                percent = round(100 * completed / len(requests))
            await _emit_progress(
                progress_callback,
                ProgressEvent(
                    stage="generate",
                    stage_percent=percent,
                    overall_percent=30 + round(percent * 0.45),
                    message=f"LLM 생성 {completed}/{len(requests)}건",
                ),
            )
            return values

        nested = await asyncio.gather(
            *(resolve_batch(batch) for batch in batches)
        )
        return [resolution for batch in nested for resolution in batch]

    async def _review_batches(
        self,
        requests: list[ReviewRequest],
        options: WorkflowOptions,
    ) -> list[LLMResolution]:
        assert self._model is not None
        semaphore = asyncio.Semaphore(options.max_concurrency)
        batch_size = min(25, options.batch_size)
        batches = [
            requests[index : index + batch_size]
            for index in range(0, len(requests), batch_size)
        ]

        async def review_batch(
            batch: list[ReviewRequest],
        ) -> list[LLMResolution]:
            try:
                async with semaphore:
                    return await self._model.review(batch)
            except Exception:
                if self._strict_llm:
                    raise
                return []

        nested = await asyncio.gather(
            *(review_batch(batch) for batch in batches)
        )
        return [resolution for batch in nested for resolution in batch]

    def _accept_resolution(
        self,
        source: SourceRow,
        resolution: LLMResolution,
    ) -> ColumnResult | None:
        compact_column = re.sub(r"[^A-Z0-9]", "", source.column_name.upper())
        compact_result = "".join(
            component.source_fragment
            for component in resolution.components
        )
        if compact_result != compact_column or not resolution.components:
            return None
        components: list[NameComponent] = []
        cursor = 0
        for component in resolution.components:
            origin = component.origin
            if origin == "mapping":
                exact = any(
                    entry.full_name == component.full_name
                    and entry.korean_word == component.korean_word
                    for entry in self._glossary.entries_for(
                        component.source_fragment
                    )
                )
                if not exact:
                    origin = "inference"
            components.append(
                component.model_copy(
                    update={
                        "origin": origin,
                        "start": cursor,
                        "end": cursor + len(component.source_fragment),
                    }
                )
            )
            cursor += len(component.source_fragment)
        korean_name = normalize_korean_name(components)
        full_name = normalize_full_name(components)
        if not korean_name or "미정" in korean_name or not full_name:
            return None
        inferred = any(
            component.origin == "inference" for component in components
        )
        confidence = 76 if inferred else 82
        return ColumnResult(
            source_id=source.source_id,
            components=components,
            english_full_name=full_name,
            korean_attribute_name=korean_name,
            status="검토필요",
            confidence=confidence,
            evidence=" | ".join(
                (
                    f"{component.source_fragment}→"
                    f"{component.full_name}→{component.korean_word}"
                    f"[{component.origin}]"
                )
                for component in components
            ),
            reason=resolution.reason,
            review_stratum=(
                "unmapped_inference" if inferred else "mapping_ambiguity"
            ),
        )

    async def _review_results(
        self,
        sources: list[SourceRow],
        results: list[ColumnResult],
        options: WorkflowOptions,
        progress_callback: ProgressCallback | None,
    ) -> list[ColumnResult]:
        assert self._model is not None
        source_by_id = {source.source_id: source for source in sources}

        async def review_node(
            state: ReviewGraphState,
        ) -> ReviewGraphState:
            current_results = [
                ColumnResult.model_validate(value)
                for value in state["payload"]["results"]
            ]
            report = validate_results(
                sources,
                current_results,
                self._glossary,
                auto_confirm_threshold=options.auto_confirm_threshold,
            )
            issues_by_id: dict[str, list] = defaultdict(list)
            for issue in report.issues:
                if issue.source_id is not None and (
                    issue.severity == "error"
                    or issue.code == "low_confidence"
                ):
                    issues_by_id[issue.source_id].append(issue)
            review_round = state.get("review_round", 0) + 1
            requests = [
                ReviewRequest(
                    request=self._resolution_request(
                        source_by_id[source_id]
                    ),
                    current_result=next(
                        result
                        for result in current_results
                        if result.source_id == source_id
                    ),
                    validation_issues=issues,
                    review_round=review_round,
                )
                for source_id, issues in issues_by_id.items()
            ]
            await _emit_progress(
                progress_callback,
                ProgressEvent(
                    stage="review",
                    stage_percent=round(
                        100 * review_round / options.max_review_rounds
                    ),
                    overall_percent=80 + round(
                        10 * review_round / options.max_review_rounds
                    ),
                    message=(
                        f"오류·저신뢰 {len(requests)}건을 "
                        f"{review_round}차 리뷰합니다."
                    ),
                ),
            )
            resolutions = await self._review_batches(requests, options)
            current_by_id = {
                result.source_id: result for result in current_results
            }
            for resolution in resolutions:
                source = source_by_id.get(resolution.source_id)
                if source is None:
                    continue
                accepted = self._accept_resolution(source, resolution)
                if accepted is not None:
                    accepted = accepted.model_copy(
                        update={
                            "confidence": max(accepted.confidence, 86),
                            "reason": (
                                f"{accepted.reason}; "
                                f"review_round={review_round}"
                            ),
                        }
                    )
                    current_by_id[source.source_id] = accepted
            updated = [
                current_by_id[source.source_id] for source in sources
            ]
            updated_report = validate_results(
                sources,
                updated,
                self._glossary,
                auto_confirm_threshold=options.auto_confirm_threshold,
            )
            pending = {
                issue.source_id
                for issue in updated_report.issues
                if issue.source_id is not None
                and (
                    issue.severity == "error"
                    or issue.code == "low_confidence"
                )
            }
            return {
                "review_round": review_round,
                "max_review_rounds": options.max_review_rounds,
                "pending_count": len(pending),
                "payload": {
                    "results": [
                        result.model_dump() for result in updated
                    ]
                },
            }

        initial_report = validate_results(
            sources,
            results,
            self._glossary,
            auto_confirm_threshold=options.auto_confirm_threshold,
        )
        pending = {
            issue.source_id
            for issue in initial_report.issues
            if issue.source_id is not None
            and (
                issue.severity == "error"
                or issue.code == "low_confidence"
            )
        }
        graph = build_review_graph(review_node)
        final_state = await graph.ainvoke(
            {
                "review_round": 0,
                "max_review_rounds": options.max_review_rounds,
                "pending_count": len(pending),
                "payload": {
                    "results": [result.model_dump() for result in results]
                },
            }
        )
        self._review_rounds_context.set(final_state.get("review_round", 0))
        return [
            ColumnResult.model_validate(value)
            for value in final_state["payload"]["results"]
        ]

    def _finalize(
        self,
        sources: list[SourceRow],
        results: list[ColumnResult],
        options: WorkflowOptions,
    ) -> list[ColumnResult]:
        report = validate_results(
            sources,
            results,
            self._glossary,
            auto_confirm_threshold=options.auto_confirm_threshold,
        )
        self._validation_report_context.set(report)
        return apply_validation_status(
            results,
            report,
            auto_confirm_threshold=options.auto_confirm_threshold,
        )


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
        complete_candidates = [
            candidate
            for candidate in candidates
            if candidate.coverage == 1
            and not candidate.unresolved_fragments
        ]
        segmentation_ambiguity = (
            len(complete_candidates) > 1
            and _candidate_signature(complete_candidates[0])
            != _candidate_signature(complete_candidates[1])
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
            confidence -= min(16, (len(complete_candidates) - 1) * 4)
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


async def _emit_progress(
    callback: ProgressCallback | None,
    event: ProgressEvent,
) -> None:
    if callback is not None:
        await callback(event)
