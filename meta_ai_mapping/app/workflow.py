from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    Literal,
    Sequence,
    TypedDict,
)

from langgraph.graph import END, START, StateGraph

from app.excel import read_source_columns, write_mapping_workbook
from app.glossary import CanonicalGlossary, canonical_key
from app.llm import MappingModel
from app.models import (
    FailedMappingRow,
    MappingCandidate,
    MappingSummary,
    ProgressEvent,
    SourceColumn,
    ValidationIssue,
    ValidationReport,
    WorkflowOptions,
    WorkflowResult,
)
from app.validation import validate_mappings

ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class WorkflowState(TypedDict, total=False):
    input_path: str
    output_path: str
    options: WorkflowOptions
    sources: list[SourceColumn]
    candidates: list[MappingCandidate]
    validation_report: ValidationReport
    review_round: int
    summaries: list[MappingSummary]
    failed_rows: list[FailedMappingRow]
    failed_source_count: int
    reconciliation_stats: dict[str, int]
    progress_callback: ProgressCallback


class MappingWorkflow:
    def __init__(
        self,
        model: MappingModel,
        glossary: CanonicalGlossary | None = None,
    ) -> None:
        self._model = model
        self._glossary = glossary or CanonicalGlossary.empty()
        self._graph = self._build_graph()

    async def run(
        self,
        input_path: str | Path,
        output_path: str | Path,
        options: WorkflowOptions,
        progress_callback: ProgressCallback | None = None,
    ) -> WorkflowResult:
        initial_state: WorkflowState = {
            "input_path": str(input_path),
            "output_path": str(output_path),
            "options": options,
            "review_round": 0,
        }
        if progress_callback is not None:
            initial_state["progress_callback"] = progress_callback
        final_state = await self._graph.ainvoke(
            initial_state,
            config={"recursion_limit": 20},
        )
        report = final_state["validation_report"]
        summaries = final_state["summaries"]
        return WorkflowResult(
            output_path=final_state["output_path"],
            mapping_count=len(summaries),
            failed_source_count=final_state.get("failed_source_count", 0),
            is_partial=bool(final_state.get("failed_rows")),
            source_count=len(final_state["sources"]),
            review_rounds=final_state["review_round"],
            validation_report=report,
            reconciliation_stats=final_state.get(
                "reconciliation_stats",
                {},
            ),
        )

    def _build_graph(self):
        builder = StateGraph(WorkflowState)
        builder.add_node("load_input", self._load_input)
        builder.add_node("generate", self._generate)
        builder.add_node("reconcile", self._reconcile)
        builder.add_node("validate", self._validate)
        builder.add_node("review", self._review)
        builder.add_node("finalize", self._finalize)
        builder.add_node("finalize_partial", self._finalize_partial)

        builder.add_edge(START, "load_input")
        builder.add_edge("load_input", "generate")
        builder.add_edge("generate", "reconcile")
        builder.add_edge("reconcile", "validate")
        builder.add_conditional_edges(
            "validate",
            self._route_after_validation,
            {
                "review": "review",
                "finalize": "finalize",
                "finalize_partial": "finalize_partial",
            },
        )
        builder.add_edge("review", "reconcile")
        builder.add_edge("finalize", END)
        builder.add_edge("finalize_partial", END)
        return builder.compile()

    async def _load_input(self, state: WorkflowState) -> dict[str, Any]:
        await _emit_progress(
            state,
            ProgressEvent(
                stage="input",
                stage_percent=0,
                overall_percent=0,
                message="입력 XLSX를 읽는 중입니다.",
            ),
        )
        sources = await asyncio.to_thread(read_source_columns, state["input_path"])
        await _emit_progress(
            state,
            ProgressEvent(
                stage="input",
                stage_percent=100,
                overall_percent=5,
                message=f"입력 컬럼 {len(sources):,}건을 읽었습니다.",
                details={"source_count": len(sources)},
            ),
        )
        return {"sources": sources}

    async def _generate(self, state: WorkflowState) -> dict[str, Any]:
        options = state["options"]
        representatives, aliases_by_representative = _deduplicate_sources(
            state["sources"]
        )
        batches = list(_batched(representatives, options.batch_size))
        semaphore = asyncio.Semaphore(options.max_concurrency)
        progress_lock = asyncio.Lock()
        completed_batches = 0
        generated_candidates = 0
        duplicate_source_count = len(state["sources"]) - len(representatives)
        await _emit_progress(
            state,
            ProgressEvent(
                stage="generate",
                stage_percent=0,
                overall_percent=5,
                message=(
                    f"중복 {duplicate_source_count:,}건을 묶어 대표 컬럼 "
                    f"{len(representatives):,}건을 {len(batches):,}개 JSON 배치로 "
                    f"호출합니다."
                ),
                details={
                    "batch_count": len(batches),
                    "batch_size": options.batch_size,
                    "max_concurrency": options.max_concurrency,
                    "source_count": len(state["sources"]),
                    "representative_source_count": len(representatives),
                    "duplicate_source_count": duplicate_source_count,
                },
            ),
        )

        async def run_batch(
            batch: Sequence[SourceColumn],
        ) -> list[MappingCandidate]:
            nonlocal completed_batches, generated_candidates
            batch_ids = {source.source_id for source in batch}
            async with semaphore:
                generated = await self._model.generate(batch)
            accepted = [
                candidate
                for candidate in generated
                if candidate.source_id in batch_ids
            ]
            expanded = [
                candidate.model_copy(update={"source_id": alias.source_id})
                for candidate in accepted
                for alias in aliases_by_representative[candidate.source_id]
            ]
            async with progress_lock:
                completed_batches += 1
                generated_candidates += len(expanded)
                stage_percent = round(100 * completed_batches / len(batches))
                overall_percent = round(5 + 55 * completed_batches / len(batches))
                await _emit_progress(
                    state,
                    ProgressEvent(
                        stage="generate",
                        stage_percent=stage_percent,
                        overall_percent=overall_percent,
                        message=(
                            f"배치 {completed_batches:,}/{len(batches):,} 완료 · "
                            f"후보 {generated_candidates:,}건"
                        ),
                        details={
                            "completed_batches": completed_batches,
                            "total_batches": len(batches),
                            "candidate_count": generated_candidates,
                        },
                    ),
                )
            return expanded

        results = await asyncio.gather(*(run_batch(batch) for batch in batches))
        return {"candidates": [item for batch in results for item in batch]}

    async def _reconcile(self, state: WorkflowState) -> dict[str, Any]:
        review_round = state["review_round"]
        if review_round == 0:
            start_percent = 60
            end_percent = 65
        else:
            checkpoint = min(
                90,
                round(
                    70
                    + 20
                    * review_round
                    / max(state["options"].max_review_rounds, 1)
                ),
            )
            start_percent = checkpoint
            end_percent = checkpoint
        await _emit_progress(
            state,
            ProgressEvent(
                stage="reconcile",
                stage_percent=0,
                overall_percent=start_percent,
                message="표준 사전과 전체 후보를 기준으로 Full Name을 통일합니다.",
                details={"glossary_entry_count": len(self._glossary)},
            ),
        )
        candidates, stats = reconcile_mappings(
            state["candidates"],
            self._glossary,
        )
        await _emit_progress(
            state,
            ProgressEvent(
                stage="reconcile",
                stage_percent=100,
                overall_percent=end_percent,
                message=(
                    f"전역 표준화 완료 · 사전 교정 "
                    f"{stats['glossary_replacement_count']:,}건 · "
                    f"다수결 교정 {stats['majority_replacement_count']:,}건"
                ),
                details=stats,
            ),
        )
        return {
            "candidates": candidates,
            "reconciliation_stats": stats,
        }

    async def _validate(self, state: WorkflowState) -> dict[str, Any]:
        review_round = state["review_round"]
        review_span = 20 / max(state["options"].max_review_rounds, 1)
        validation_start_percent = (
            65
            if review_round == 0
            else min(90, round(70 + review_span * review_round))
        )
        await _emit_progress(
            state,
            ProgressEvent(
                stage="validate",
                stage_percent=0,
                overall_percent=validation_start_percent,
                message=f"{review_round + 1}차 검증을 시작합니다.",
                details={"review_round": review_round},
            ),
        )
        known_source_ids = {source.source_id for source in state["sources"]}
        candidates = [
            candidate
            for candidate in state["candidates"]
            if candidate.source_id in known_source_ids
        ]
        report = validate_mappings(
            state["sources"],
            candidates,
            canonical_resolver=self._glossary.get,
        )
        overall_percent = min(90, round(70 + review_span * review_round))
        issue_counts: dict[str, int] = defaultdict(int)
        for issue in report.issues:
            issue_counts[issue.code] += 1
        message = (
            f"검증 통과 · 후보 {len(candidates):,}건"
            if report.is_valid
            else (
                f"오류 {report.stats['error_count']:,}건 · "
                f"경고 {report.stats['warning_count']:,}건"
            )
        )
        await _emit_progress(
            state,
            ProgressEvent(
                stage="validate",
                stage_percent=100,
                overall_percent=overall_percent,
                message=message,
                details={
                    **report.stats,
                    "review_round": review_round,
                    "issue_codes": dict(issue_counts),
                },
            ),
        )
        return {"candidates": candidates, "validation_report": report}

    def _route_after_validation(
        self, state: WorkflowState
    ) -> Literal["review", "finalize", "finalize_partial"]:
        report = state["validation_report"]
        if report.is_valid:
            return "finalize"
        if state["review_round"] < state["options"].max_review_rounds:
            return "review"
        return "finalize_partial"

    async def _review(self, state: WorkflowState) -> dict[str, Any]:
        report = state["validation_report"]
        source_by_id = {source.source_id: source for source in state["sources"]}
        known_candidates = [
            candidate
            for candidate in state["candidates"]
            if candidate.source_id in source_by_id
        ]
        known_candidates, deterministic_replacement_count = (
            apply_validation_recommendations(known_candidates, report)
        )
        llm_issues = [
            issue
            for issue in report.issues
            if issue.code
            not in {"conflicting_full_name", "noncanonical_full_name"}
        ]
        affected_ids = {
            source_id
            for issue in llm_issues
            if issue.severity == "error"
            for source_id in issue.source_ids
            if source_id in source_by_id
        }
        next_round = state["review_round"] + 1
        if not affected_ids:
            await _emit_progress(
                state,
                ProgressEvent(
                    stage="review",
                    stage_percent=100,
                    overall_percent=min(
                        90,
                        round(
                            70
                            + 20
                            * next_round
                            / max(state["options"].max_review_rounds, 1)
                        ),
                    ),
                    message=(
                        "전역 권고값으로 충돌 후보 "
                        f"{deterministic_replacement_count:,}건을 교정했습니다."
                    ),
                    details={
                        "review_round": next_round,
                        "deterministic_replacement_count": (
                            deterministic_replacement_count
                        ),
                        "llm_call_count": 0,
                    },
                ),
            )
            return {
                "candidates": known_candidates,
                "review_round": next_round,
            }

        options = state["options"]
        affected_sources = [
            source for source in state["sources"] if source.source_id in affected_ids
        ]
        batches = list(_batched(affected_sources, options.batch_size))
        semaphore = asyncio.Semaphore(options.max_concurrency)
        progress_lock = asyncio.Lock()
        completed_batches = 0
        replacement_count = 0
        review_span = 20 / max(options.max_review_rounds, 1)
        round_start = 70 + review_span * (next_round - 1)
        await _emit_progress(
            state,
            ProgressEvent(
                stage="review",
                stage_percent=0,
                overall_percent=min(90, round(round_start)),
                message=(
                    f"{next_round}차 리뷰 시작 · 오류 관련 원본 "
                    f"{len(affected_sources):,}건"
                ),
                details={
                    "review_round": next_round,
                    "affected_source_count": len(affected_sources),
                    "batch_count": len(batches),
                    "deterministic_replacement_count": (
                        deterministic_replacement_count
                    ),
                },
            ),
        )

        async def review_batch(
            batch: Sequence[SourceColumn],
        ) -> list[MappingCandidate]:
            nonlocal completed_batches, replacement_count
            batch_ids = {source.source_id for source in batch}
            current = [
                candidate
                for candidate in known_candidates
                if candidate.source_id in batch_ids
            ]
            issues = [
                issue
                for issue in llm_issues
                if not issue.source_ids or batch_ids.intersection(issue.source_ids)
            ]
            async with semaphore:
                replacements = await self._model.review(
                    batch, current, issues, next_round
                )
            replacements = [
                item for item in replacements if item.source_id in batch_ids
            ]
            async with progress_lock:
                completed_batches += 1
                replacement_count += len(replacements)
                fraction = completed_batches / len(batches)
                await _emit_progress(
                    state,
                    ProgressEvent(
                        stage="review",
                        stage_percent=round(100 * fraction),
                        overall_percent=min(
                            90, round(round_start + review_span * fraction)
                        ),
                        message=(
                            f"{next_round}차 리뷰 배치 "
                            f"{completed_batches:,}/{len(batches):,} 완료 · "
                            f"교체 후보 {replacement_count:,}건"
                        ),
                        details={
                            "review_round": next_round,
                            "completed_batches": completed_batches,
                            "total_batches": len(batches),
                            "replacement_count": replacement_count,
                        },
                    ),
                )
            return replacements

        reviewed = await asyncio.gather(*(review_batch(batch) for batch in batches))
        replacements = [item for batch in reviewed for item in batch]
        unaffected = [
            candidate
            for candidate in known_candidates
            if candidate.source_id not in affected_ids
        ]
        return {
            "candidates": unaffected + replacements,
            "review_round": next_round,
        }

    async def _finalize(self, state: WorkflowState) -> dict[str, Any]:
        summaries = aggregate_mappings(state["candidates"])
        await self._write_output(state, summaries, [])
        return {
            "summaries": summaries,
            "failed_rows": [],
            "failed_source_count": 0,
        }

    async def _finalize_partial(
        self, state: WorkflowState
    ) -> dict[str, Any]:
        valid_candidates, failed_rows = partition_failed_mappings(
            state["sources"],
            state["candidates"],
            state["validation_report"],
        )
        summaries = aggregate_mappings(valid_candidates)
        failed_source_count = len({row.source_id for row in failed_rows})
        await self._write_output(state, summaries, failed_rows)
        return {
            "summaries": summaries,
            "failed_rows": failed_rows,
            "failed_source_count": failed_source_count,
        }

    async def _write_output(
        self,
        state: WorkflowState,
        summaries: list[MappingSummary],
        failed_rows: list[FailedMappingRow],
    ) -> None:
        partial_message = (
            f" · 검증실패 원본 {len({row.source_id for row in failed_rows}):,}건"
            if failed_rows
            else ""
        )
        await _emit_progress(
            state,
            ProgressEvent(
                stage="output",
                stage_percent=20,
                overall_percent=95,
                message=(
                    f"정상 고유 매핑 {len(summaries):,}건을 집계했습니다."
                    f"{partial_message}"
                ),
                details={
                    "mapping_count": len(summaries),
                    "failed_source_count": len(
                        {row.source_id for row in failed_rows}
                    ),
                },
            ),
        )
        await asyncio.to_thread(
            write_mapping_workbook,
            state["output_path"],
            summaries,
            failed_rows,
        )
        await _emit_progress(
            state,
            ProgressEvent(
                stage="output",
                stage_percent=100,
                overall_percent=100,
                message=(
                    f"XLSX 생성 완료 · 정상 매핑 {len(summaries):,}건"
                    f"{partial_message}"
                ),
                details={
                    "mapping_count": len(summaries),
                    "failed_source_count": len(
                        {row.source_id for row in failed_rows}
                    ),
                    "is_partial": bool(failed_rows),
                },
            ),
        )


def aggregate_mappings(
    candidates: Iterable[MappingCandidate],
) -> list[MappingSummary]:
    source_ids_by_mapping: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for candidate in candidates:
        key = (
            candidate.abbreviation,
            candidate.full_name,
            candidate.korean_word,
        )
        source_ids_by_mapping[key].add(candidate.source_id)

    return [
        MappingSummary(
            abbreviation=key[0],
            full_name=key[1],
            korean_word=key[2],
            occurrence_count=len(source_ids),
        )
        for key, source_ids in sorted(
            source_ids_by_mapping.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2]),
        )
    ]


def reconcile_mappings(
    candidates: Sequence[MappingCandidate],
    glossary: CanonicalGlossary,
) -> tuple[list[MappingCandidate], dict[str, int]]:
    groups: dict[tuple[str, str], list[MappingCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[canonical_key(candidate.abbreviation, candidate.korean_word)].append(
            candidate
        )

    targets: dict[tuple[str, str], tuple[str, str]] = {}
    for key, group in groups.items():
        glossary_target = glossary.get(
            group[0].abbreviation,
            group[0].korean_word,
        )
        if glossary_target is not None:
            targets[key] = (glossary_target, "canonical_glossary")
            continue
        source_ids_by_full_name: dict[str, set[str]] = defaultdict(set)
        for candidate in group:
            source_ids_by_full_name[candidate.full_name].add(candidate.source_id)
        majority_target = sorted(
            source_ids_by_full_name,
            key=lambda full_name: (
                -len(source_ids_by_full_name[full_name]),
                full_name,
            ),
        )[0]
        targets[key] = (majority_target, "majority")

    reconciled: list[MappingCandidate] = []
    glossary_replacement_count = 0
    majority_replacement_count = 0
    for candidate in candidates:
        key = canonical_key(candidate.abbreviation, candidate.korean_word)
        target, source = targets[key]
        if candidate.full_name == target:
            reconciled.append(candidate)
            continue
        reconciled.append(candidate.model_copy(update={"full_name": target}))
        if source == "canonical_glossary":
            glossary_replacement_count += 1
        else:
            majority_replacement_count += 1

    return reconciled, {
        "candidate_count": len(candidates),
        "mapping_group_count": len(groups),
        "glossary_entry_count": len(glossary),
        "glossary_replacement_count": glossary_replacement_count,
        "majority_replacement_count": majority_replacement_count,
        "total_replacement_count": (
            glossary_replacement_count + majority_replacement_count
        ),
    }


def apply_validation_recommendations(
    candidates: Sequence[MappingCandidate],
    report: ValidationReport,
) -> tuple[list[MappingCandidate], int]:
    targets: dict[tuple[str, str], str] = {}
    for issue in report.errors:
        if issue.code == "conflicting_full_name":
            abbreviation = issue.details.get("abbreviation")
            korean_word = issue.details.get("korean_word")
            recommended = issue.details.get("recommended_full_name")
        elif issue.code == "noncanonical_full_name":
            abbreviation = issue.details.get("abbreviation")
            korean_word = issue.details.get("korean_word")
            recommended = issue.details.get("canonical_full_name")
        else:
            continue
        if all(isinstance(value, str) and value for value in (
            abbreviation,
            korean_word,
            recommended,
        )):
            targets[canonical_key(abbreviation, korean_word)] = recommended

    replacement_count = 0
    corrected: list[MappingCandidate] = []
    for candidate in candidates:
        target = targets.get(
            canonical_key(candidate.abbreviation, candidate.korean_word)
        )
        if target is None or target == candidate.full_name:
            corrected.append(candidate)
            continue
        corrected.append(candidate.model_copy(update={"full_name": target}))
        replacement_count += 1
    return corrected, replacement_count


def partition_failed_mappings(
    sources: Sequence[SourceColumn],
    candidates: Sequence[MappingCandidate],
    report: ValidationReport,
) -> tuple[list[MappingCandidate], list[FailedMappingRow]]:
    source_by_id = {source.source_id: source for source in sources}
    candidates_by_source: dict[str, list[MappingCandidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_source[candidate.source_id].append(candidate)

    failure_records: dict[
        tuple[str, str | None, str | None, str | None],
        tuple[MappingCandidate | None, list[ValidationIssue]],
    ] = {}

    def add_failure(
        source_id: str,
        candidate: MappingCandidate | None,
        issue: ValidationIssue,
    ) -> None:
        key = (
            source_id,
            candidate.abbreviation if candidate else None,
            candidate.full_name if candidate else None,
            candidate.korean_word if candidate else None,
        )
        if key not in failure_records:
            failure_records[key] = (candidate, [])
        failure_records[key][1].append(issue)

    for issue in report.errors:
        candidate_payload = issue.details.get("candidate")
        if isinstance(candidate_payload, dict):
            candidate = MappingCandidate.model_validate(candidate_payload)
            if candidate.source_id in source_by_id:
                add_failure(candidate.source_id, candidate, issue)
            continue

        if issue.code == "conflicting_full_name":
            abbreviation = issue.details.get("abbreviation")
            korean_word = issue.details.get("korean_word")
            recommended_full_name = issue.details.get("recommended_full_name")
            for source_id in issue.source_ids:
                matched = [
                    candidate
                    for candidate in candidates_by_source.get(source_id, [])
                    if candidate.abbreviation == abbreviation
                    and candidate.korean_word == korean_word
                    and (
                        not isinstance(recommended_full_name, str)
                        or candidate.full_name != recommended_full_name
                    )
                ]
                for candidate in matched:
                    add_failure(source_id, candidate, issue)
            continue

        for source_id in issue.source_ids:
            source_candidates = candidates_by_source.get(source_id, [])
            if source_candidates:
                for candidate in source_candidates:
                    add_failure(source_id, candidate, issue)
            else:
                add_failure(source_id, None, issue)

    failed_candidate_keys = {
        (
            source_id,
            candidate.abbreviation,
            candidate.full_name,
            candidate.korean_word,
        )
        for (
            source_id,
            _,
            _,
            _,
        ), (candidate, _) in failure_records.items()
        if candidate is not None
    }
    valid_candidates = [
        candidate
        for candidate in candidates
        if _candidate_key(candidate) not in failed_candidate_keys
    ]

    source_order = {source.source_id: index for index, source in enumerate(sources)}
    failed_rows: list[FailedMappingRow] = []
    for key, (candidate, issues) in sorted(
        failure_records.items(),
        key=lambda item: (
            source_order.get(item[0][0], len(source_order)),
            item[0][1] or "",
            item[0][2] or "",
            item[0][3] or "",
        ),
    ):
        source_id = key[0]
        source = source_by_id.get(source_id)
        if source is None:
            continue
        failed_rows.append(
            FailedMappingRow(
                source_id=source_id,
                schema_name=source.schema_name,
                table_name=source.table_name,
                column_name=source.column_name,
                column_description=source.column_description,
                abbreviation=candidate.abbreviation if candidate else None,
                full_name=candidate.full_name if candidate else None,
                korean_word=candidate.korean_word if candidate else None,
                issue_codes=", ".join(_unique(issue.code for issue in issues)),
                validation_messages="\n".join(
                    _unique(issue.message for issue in issues)
                ),
                suggested_actions="\n".join(
                    _unique(issue.suggested_action for issue in issues)
                ),
            )
        )
    return valid_candidates, failed_rows


def _candidate_key(
    candidate: MappingCandidate,
) -> tuple[str, str, str, str]:
    return (
        candidate.source_id,
        candidate.abbreviation,
        candidate.full_name,
        candidate.korean_word,
    )


def _deduplicate_sources(
    sources: Sequence[SourceColumn],
) -> tuple[list[SourceColumn], dict[str, list[SourceColumn]]]:
    representative_by_signature: dict[tuple[str, str], SourceColumn] = {}
    aliases_by_representative: dict[str, list[SourceColumn]] = defaultdict(list)
    for source in sources:
        signature = (
            source.column_name.strip().upper(),
            re.sub(r"\s+", "", source.column_description.strip()),
        )
        representative = representative_by_signature.get(signature)
        if representative is None:
            representative = source
            representative_by_signature[signature] = representative
        aliases_by_representative[representative.source_id].append(source)
    return list(representative_by_signature.values()), dict(
        aliases_by_representative
    )


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _batched(
    items: Sequence[SourceColumn], size: int
) -> Iterable[Sequence[SourceColumn]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _emit_progress(
    state: WorkflowState, event: ProgressEvent
) -> None:
    callback = state.get("progress_callback")
    if callback is not None:
        await callback(event)
