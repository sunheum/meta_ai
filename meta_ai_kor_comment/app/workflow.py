from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.excel import read_source_columns, write_result_workbook
from app.exceptions import LLMResponseError
from app.llm import KoreanNamingModel
from app.models import (
    GenerationResult,
    IssueSeverity,
    KoreanAttributeResult,
    ProcessingAction,
    ProgressEvent,
    SourceColumn,
    TerminologyDecision,
    ValidationIssue,
    WorkflowOptions,
    WorkflowResult,
)
from app.normalization import (
    classify_description,
    invalid_english_tokens,
    source_processing_key,
)
from app.rules import DomainRules
from app.terminology import (
    SynonymGroup,
    TerminologyContext,
    reconcile_results,
)
from app.validation import finalize_result, validate_result, validate_results


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


_ENGLISH_TOKEN_RE = re.compile(r"[A-Za-z]+")
_PUNCTUATION_RE = re.compile(r"[^가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9]")


def _semantic_terms(rules: DomainRules) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                candidate
                for group in rules.synonym_groups
                for candidate in group.candidates
            },
            key=lambda value: (-len(value), value),
        )
    )


def _rules_to_synonym_groups(
    rules: DomainRules,
) -> tuple[SynonymGroup, ...]:
    return tuple(
        SynonymGroup(group.id, tuple(group.candidates))
        for group in rules.synonym_groups
    )


def _preferred_first_tie_resolver(
    candidates: tuple[str, ...], context: TerminologyContext | None
) -> str | None:
    """When frequencies tie, prefer the surface listed first in the YAML group.

    ``select_preferred_term`` filters winners in ``SynonymGroup.candidates``
    order, so returning the first winner honors the author's stated
    preference without any hardcoded domain vocabulary.
    """

    return candidates[0] if candidates else None


class KoreanCommentWorkflow:
    """Generate Korean attribute names with deterministic safety rails.

    The workflow deliberately keeps orchestration in ordinary async Python. It
    avoids a runtime dependency on a graph framework while retaining the exact
    stages and progress contract described in HANDOFF.md.
    """

    def __init__(
        self,
        model: KoreanNamingModel,
        *,
        rules: DomainRules | None = None,
    ) -> None:
        self._model = model
        self._rules = rules if rules is not None else DomainRules()
        self._synonym_groups = _rules_to_synonym_groups(self._rules)
        self._semantic_terms = _semantic_terms(self._rules)
        self._glossary = self._rules.glossary_lookup()

    async def aclose(self) -> None:
        close = getattr(self._model, "aclose", None)
        if close is not None:
            await close()

    async def run(
        self,
        input_path: str | Path,
        output_path: str | Path,
        options: WorkflowOptions,
        progress_callback: ProgressCallback | None = None,
    ) -> WorkflowResult:
        started = time.monotonic()
        await self._emit(
            progress_callback,
            "input",
            0,
            0,
            "입력 XLSX를 읽는 중입니다.",
            started,
        )
        sources = await asyncio.to_thread(read_source_columns, input_path)
        await self._emit(
            progress_callback,
            "input",
            100,
            5,
            f"입력 {len(sources):,}행을 읽었습니다.",
            started,
            {"source_count": len(sources)},
        )

        representatives, aliases = _deduplicate_sources(sources)
        weights = {
            representative.source_id: len(aliases[representative.source_id])
            for representative in representatives
        }
        await self._emit(
            progress_callback,
            "normalize",
            100,
            12,
            (
                f"고유 컬럼명·설명 {len(representatives):,}건으로 축약하고 "
                "위험도를 분류했습니다."
            ),
            started,
            {
                "representative_count": len(representatives),
                "duplicate_count": len(sources) - len(representatives),
            },
        )

        generated, generation_fallback_codes = await self._generate(
            representatives,
            options,
            progress_callback,
            started,
        )
        reconciled, decisions = self._reconcile(
            representatives,
            generated,
            weights,
        )
        await self._emit(
            progress_callback,
            "reconcile",
            100,
            72,
            f"빈도 기반 용어 결정 {len(decisions):,}건을 적용했습니다.",
            started,
            {"terminology_decision_count": len(decisions)},
        )

        review_rounds = 0
        review_failure_codes: dict[str, str] = {}
        review_failure_history: list[tuple[int, str, str]] = []
        for review_round in range(1, options.max_review_rounds + 1):
            report = validate_results(
                representatives,
                reconciled,
                terminology_decisions=decisions,
                auto_confirm_threshold=options.auto_confirm_threshold,
            )
            review_ids = _review_source_ids(
                reconciled,
                report.issues,
                decisions,
                options.auto_confirm_threshold,
            )
            if not review_ids:
                break

            review_rounds = review_round
            await self._emit(
                progress_callback,
                "review",
                round(100 * (review_round - 1) / options.max_review_rounds),
                76,
                f"오류·저신뢰 결과 {len(review_ids):,}건을 재검토합니다.",
                started,
                {"review_round": review_round, "source_count": len(review_ids)},
            )
            reviewed, round_failure_codes = await self._review(
                representatives,
                reconciled,
                report.issues,
                decisions,
                review_ids,
                review_round,
                options,
            )
            successful_review_ids = review_ids.difference(round_failure_codes)
            review_failure_history.extend(
                (review_round, source_id, failure_code)
                for source_id, failure_code in sorted(round_failure_codes.items())
            )
            for source_id in successful_review_ids:
                review_failure_codes.pop(source_id, None)
            review_failure_codes.update(round_failure_codes)
            for failure_code in sorted(set(round_failure_codes.values())):
                reviewed = _mark_review_failure(
                    reviewed,
                    {
                        source_id
                        for source_id, code in round_failure_codes.items()
                        if code == failure_code
                    },
                    failure_code,
                )
            reconciled, decisions = self._reconcile(
                representatives,
                reviewed,
                weights,
            )

        prefinal_report = validate_results(
            representatives,
            reconciled,
            terminology_decisions=decisions,
            auto_confirm_threshold=options.auto_confirm_threshold,
        )
        representative_results = _finalize(
            reconciled,
            prefinal_report.issues,
            decisions,
            options.auto_confirm_threshold,
        )
        expanded_results, expanded_decisions = _expand_results(
            representative_results,
            decisions,
            aliases,
        )
        final_report = validate_results(
            sources,
            expanded_results,
            terminology_decisions=expanded_decisions,
            auto_confirm_threshold=options.auto_confirm_threshold,
        )
        await self._emit(
            progress_callback,
            "validate",
            100,
            92,
            (
                f"결정적 검증 완료: 오류 "
                f"{final_report.stats.get('error_count', 0):,}건"
            ),
            started,
            final_report.stats,
        )

        await asyncio.to_thread(
            write_result_workbook,
            input_path,
            output_path,
            sources,
            expanded_results,
        )
        await self._emit(
            progress_callback,
            "output",
            100,
            100,
            "결과 XLSX와 검토필요 시트를 생성했습니다.",
            started,
            {"output_path": str(output_path)},
        )

        counts = defaultdict(int)
        for result in expanded_results:
            counts[result.status.value] += 1
        return WorkflowResult(
            output_path=str(output_path),
            source_count=len(sources),
            auto_confirmed_count=counts["자동확정"],
            review_required_count=counts["검토필요"],
            validation_failed_count=counts["검증실패"],
            review_rounds=review_rounds,
            validation_report=final_report,
            terminology_stats={
                "group_count": len(self._synonym_groups),
                "decision_count": len(expanded_decisions),
            },
            terminology_decisions=expanded_decisions,
            recovery_stats=_recovery_stats(
                weights,
                generation_fallback_codes,
                review_failure_history,
                review_failure_codes,
            ),
            recovery_events=_recovery_events(
                aliases,
                generation_fallback_codes,
                review_failure_history,
            ),
        )

    async def _generate(
        self,
        representatives: Sequence[SourceColumn],
        options: WorkflowOptions,
        callback: ProgressCallback | None,
        started: float,
    ) -> tuple[list[GenerationResult], dict[str, str]]:
        base = {
            source.source_id: _deterministic_candidate(
                source,
                glossary=self._glossary,
                semantic_terms=self._semantic_terms,
            )
            for source in representatives
        }
        risks = {
            source.source_id: classify_description(
                source.column_description, source_id=source.source_id
            )
            for source in representatives
        }
        risky = [
            source
            for source in representatives
            if risks[source.source_id].requires_generation
        ]
        if not risky:
            return [base[source.source_id] for source in representatives], {}

        batches = list(_batched(risky, options.batch_size))
        semaphore = asyncio.Semaphore(options.max_concurrency)
        completed = 0
        progress_lock = asyncio.Lock()
        fallback_codes: dict[str, str] = {}

        async def generate_batch(batch: Sequence[SourceColumn]) -> None:
            nonlocal completed
            failure_code: str | None = None
            try:
                async with semaphore:
                    proposed = await self._model.generate(
                        batch,
                        [risks[source.source_id] for source in batch],
                    )
            except Exception as exc:
                proposed = []
                failure_code = _llm_failure_code(exc)
            source_by_id = {source.source_id: source for source in batch}
            returned_ids = {
                candidate.source_id
                for candidate in proposed
                if candidate.source_id in source_by_id
            }
            accepted_ids: set[str] = set()
            for candidate in proposed:
                source = source_by_id.get(candidate.source_id)
                if source is None:
                    continue
                if candidate.original_description != source.column_description:
                    continue
                if validate_result(source, candidate):
                    continue
                deterministic = base[candidate.source_id]
                # When the deterministic policy already yields a valid answer,
                # the model may enrich evidence but may not silently replace the
                # audited translation or slash choice with a different meaning.
                if (
                    not validate_result(source, deterministic)
                    and candidate.korean_attribute_name
                    != deterministic.korean_attribute_name
                ):
                    continue
                merged_review_reasons = list(
                    dict.fromkeys(
                        deterministic.review_reasons + candidate.review_reasons
                    )
                )
                base[candidate.source_id] = candidate.model_copy(
                    update={
                        "semantic_units": _semantic_units(
                            candidate.korean_attribute_name,
                            self._semantic_terms,
                        ),
                        "review_reasons": merged_review_reasons,
                        "confidence": (
                            min(candidate.confidence, 85)
                            if merged_review_reasons
                            else candidate.confidence
                        ),
                    }
                )
                accepted_ids.add(candidate.source_id)
            for source in batch:
                if source.source_id in accepted_ids:
                    continue
                source_failure_code = (
                    failure_code
                    or (
                        "rejected_result"
                        if source.source_id in returned_ids
                        else "missing_result"
                    )
                )
                fallback_codes[source.source_id] = source_failure_code
                fallback = base[source.source_id]
                reason = (
                    f"로컬 LLM 생성 {_failure_reason(source_failure_code)}로 "
                    "결정적 복구 규칙을 사용"
                )
                base[source.source_id] = fallback.model_copy(
                    update={
                        "confidence": min(fallback.confidence, 85),
                        "review_reasons": list(
                            dict.fromkeys(fallback.review_reasons + [reason])
                        ),
                    }
                )
            async with progress_lock:
                completed += 1
                await self._emit(
                    callback,
                    "generate",
                    round(100 * completed / len(batches)),
                    round(12 + 52 * completed / len(batches)),
                    f"위험 배치 {completed:,}/{len(batches):,} 처리 완료",
                    started,
                    {
                        "risky_source_count": len(risky),
                        "generation_fallback_count": len(fallback_codes),
                    },
                )

        await asyncio.gather(*(generate_batch(batch) for batch in batches))
        return [base[source.source_id] for source in representatives], fallback_codes

    def _reconcile(
        self,
        sources: Sequence[SourceColumn],
        results: Sequence[GenerationResult],
        occurrence_weights: Mapping[str, int],
    ) -> tuple[list[GenerationResult], list[TerminologyDecision]]:
        context_by_id = {
            source.source_id: TerminologyContext(
                source_id=source.source_id,
                column_name=source.column_name,
                original_description=source.column_description,
                table_name=source.table_name or "",
                table_description=source.table_description or "",
                semantic_units=tuple(
                    next(
                        result.semantic_units
                        for result in results
                        if result.source_id == source.source_id
                    )
                ),
            )
            for source in sources
        }
        return reconcile_results(
            results,
            self._synonym_groups,
            frequency_results=[
                _deterministic_candidate(
                    source,
                    glossary=self._glossary,
                    semantic_terms=self._semantic_terms,
                )
                for source in sources
            ],
            contexts=context_by_id,
            occurrence_weights=occurrence_weights,
            tie_resolver=_preferred_first_tie_resolver,
        )

    async def _review(
        self,
        sources: Sequence[SourceColumn],
        results: Sequence[GenerationResult],
        issues: Sequence[ValidationIssue],
        terminology_decisions: Sequence[TerminologyDecision],
        review_ids: set[str],
        review_round: int,
        options: WorkflowOptions,
    ) -> tuple[list[GenerationResult], dict[str, str]]:
        source_by_id = {source.source_id: source for source in sources}
        current_by_id = {result.source_id: result for result in results}
        selected_sources = [
            source for source in sources if source.source_id in review_ids
        ]
        selected_issues = [
            issue
            for issue in issues
            if review_ids.intersection(issue.source_ids)
        ]
        for source in selected_sources:
            result = current_by_id[source.source_id]
            if result.confidence >= options.auto_confirm_threshold:
                continue
            selected_issues.append(
                ValidationIssue(
                    code="low_confidence",
                    severity=IssueSeverity.WARNING,
                    message="한글속성명 생성 신뢰도가 자동확정 임계값보다 낮습니다.",
                    suggested_action="컬럼·테이블 문맥으로 번역과 의미 보존을 재확인하세요.",
                    source_ids=[result.source_id],
                    details={
                        "confidence": result.confidence,
                        "auto_confirm_threshold": options.auto_confirm_threshold,
                    },
                )
            )

        batches = list(_batched(selected_sources, options.batch_size))
        semaphore = asyncio.Semaphore(options.max_concurrency)

        async def review_batch(
            batch: Sequence[SourceColumn],
        ) -> tuple[set[str], list[GenerationResult], str | None]:
            batch_ids = {source.source_id for source in batch}
            batch_results = [current_by_id[source.source_id] for source in batch]
            batch_issues = [
                item.model_copy(
                    update={
                        "source_ids": [
                            source_id
                            for source_id in item.source_ids
                            if source_id in batch_ids
                        ]
                    }
                )
                for item in selected_issues
                if batch_ids.intersection(item.source_ids)
            ]
            batch_decisions = [
                decision
                for decision in terminology_decisions
                if decision.source_id in batch_ids
            ]
            try:
                async with semaphore:
                    reviewed = await self._model.review(
                        batch,
                        batch_results,
                        batch_issues,
                        review_round,
                        terminology_context=batch_decisions,
                    )
                actual_ids = [candidate.source_id for candidate in reviewed]
                if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != batch_ids:
                    raise LLMResponseError(
                        "리뷰 응답 대응 오류: 배치 source_id가 누락·중복되거나 추가됨"
                    )
                return batch_ids, reviewed, None
            except Exception as exc:
                return batch_ids, [], _llm_failure_code(exc)

        reviewed_batches = await asyncio.gather(
            *(review_batch(batch) for batch in batches)
        )
        reviewed = [
            candidate
            for _, batch_results, failure_code in reviewed_batches
            if failure_code is None
            for candidate in batch_results
        ]
        failure_codes = {
            source_id: failure_code
            for batch_ids, _, failure_code in reviewed_batches
            if failure_code is not None
            for source_id in batch_ids
        }

        accepted = dict(current_by_id)
        for candidate in reviewed:
            source = source_by_id.get(candidate.source_id)
            if source is None or candidate.source_id not in review_ids:
                continue
            current = current_by_id[candidate.source_id]
            current = current.model_copy(
                update={
                    "review_reasons": _clear_review_execution_failure_reasons(
                        current.review_reasons
                    )
                }
            )
            accepted[candidate.source_id] = current
            if candidate.original_description != source.column_description:
                failure_codes[candidate.source_id] = "rejected_result"
                continue
            if validate_result(source, candidate):
                failure_codes[candidate.source_id] = "rejected_result"
                continue
            if (
                not validate_result(source, current)
                and candidate.korean_attribute_name
                != current.korean_attribute_name
            ):
                failure_codes[candidate.source_id] = "rejected_result"
                continue
            review_reasons = _persistent_review_reasons(
                current.review_reasons + candidate.review_reasons
            )
            accepted[candidate.source_id] = candidate.model_copy(
                update={
                    "semantic_units": _semantic_units(
                        candidate.korean_attribute_name,
                        self._semantic_terms,
                    ),
                    "review_reasons": review_reasons,
                    "confidence": (
                        min(candidate.confidence, 85)
                        if review_reasons
                        else candidate.confidence
                    ),
                }
            )
        return [accepted[source.source_id] for source in sources], failure_codes

    @staticmethod
    async def _emit(
        callback: ProgressCallback | None,
        stage: str,
        stage_percent: int,
        overall_percent: int,
        message: str,
        started: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        if callback is None:
            return
        await callback(
            ProgressEvent(
                stage=stage,
                stage_percent=stage_percent,
                overall_percent=overall_percent,
                message=message,
                details=details or {},
                total_elapsed_seconds=time.monotonic() - started,
            )
        )


def _deterministic_candidate(
    source: SourceColumn,
    *,
    glossary: Mapping[str, str] | None = None,
    semantic_terms: Sequence[str] = (),
) -> GenerationResult:
    """Produce a deterministic Korean-attribute-name candidate.

    Domain-specific rewrites (ordinal normalization, slash arbitration,
    ambiguous-token review) are deliberately not applied here — the LLM handles
    those from ``column_description``. Only reproducible policies remain:
    optional glossary substitution, whitespace/symbol cleanup, and the
    duplicate-suffix ``등급등급→등급`` simplification.
    """

    lookup = dict(glossary or {})
    original = source.column_description
    result = original
    reasons: list[str] = []
    review_reasons: list[str] = []
    risk = classify_description(original, source_id=source.source_id)

    if "/" in result:
        alternatives = [part for part in result.split("/") if part]
        result = alternatives[0] if alternatives else result.replace("/", "")
        discarded = alternatives[1:] or ["확인 불가"]
        reasons.append(
            f"슬래시 대안 중 첫 의미를 임시 선택하고 {', '.join(discarded)} 제외"
        )
        review_reasons.append("슬래시 선택을 확정할 문맥이 부족함")

    unknown_tokens: list[str] = []

    def replace_english(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "ID":
            return token
        translated = lookup.get(token.upper())
        if translated is None:
            unknown_tokens.append(token)
            return token
        reasons.append(f"영문 {token}을 '{translated}'로 한글화")
        return translated

    result = _ENGLISH_TOKEN_RE.sub(replace_english, result)
    if unknown_tokens:
        review_reasons.append(
            "한글 의미를 확정하지 못한 영문: " + ", ".join(unknown_tokens)
        )

    if "등급등급" in result:
        result = result.replace("등급등급", "등급")
        reasons.append("중복 접미사 '등급등급'을 '등급'으로 정규화")

    compacted = re.sub(r"\s+", "", result)
    if compacted != result:
        reasons.append("공백 제거")
        result = compacted
    cleaned = _PUNCTUATION_RE.sub("", result)
    if cleaned != result:
        reasons.append("허용되지 않은 기호 제거")
        result = cleaned

    # An unresolved English token is still a rewrite attempt even when its
    # literal surface remains. This keeps status/evidence honest and lets the
    # deterministic validator expose the row as a failed, reviewable result.
    changed = result != original or bool(unknown_tokens)
    if not changed:
        action = ProcessingAction.KEEP
        confidence = 100
        reason = "정책을 충족하는 컬럼설명을 원문 그대로 유지"
    else:
        action = (
            ProcessingAction.REWRITE
            if "english_translation_required" in risk.codes
            or "slash_ambiguity" in risk.codes
            else ProcessingAction.NORMALIZE
        )
        confidence = 96 if not unknown_tokens else 55
        if review_reasons:
            confidence = min(confidence, 85)
        reason = "; ".join(reasons) or "문자 정책에 맞게 정규화"

    if review_reasons:
        confidence = min(confidence, 85)

    return GenerationResult(
        source_id=source.source_id,
        original_description=original,
        korean_attribute_name=result,
        action=action,
        confidence=confidence,
        reason=reason,
        semantic_units=_semantic_units(result, semantic_terms),
        added_concepts=[],
        removed_concepts=[],
        review_reasons=review_reasons,
    )


def _semantic_units(
    value: str, semantic_terms: Sequence[str] = ()
) -> list[str]:
    """Split only known synonym surfaces; retain every other substring."""

    if not semantic_terms:
        return [value]

    units: list[str] = []
    plain: list[str] = []
    index = 0
    while index < len(value):
        match = next(
            (term for term in semantic_terms if value.startswith(term, index)),
            None,
        )
        if match is None:
            plain.append(value[index])
            index += 1
            continue
        if plain:
            units.append("".join(plain))
            plain = []
        units.append(match)
        index += len(match)
    if plain:
        units.append("".join(plain))
    return units or [value]


def _deduplicate_sources(
    sources: Sequence[SourceColumn],
) -> tuple[list[SourceColumn], dict[str, list[SourceColumn]]]:
    representative_by_key: dict[tuple[str, ...], SourceColumn] = {}
    aliases: dict[str, list[SourceColumn]] = defaultdict(list)
    for source in sources:
        key = source_processing_key(source)
        representative = representative_by_key.setdefault(key, source)
        aliases[representative.source_id].append(source)
    return list(representative_by_key.values()), dict(aliases)


def _review_source_ids(
    results: Sequence[GenerationResult],
    issues: Sequence[ValidationIssue],
    terminology_decisions: Sequence[TerminologyDecision],
    threshold: int,
) -> set[str]:
    ids = {
        source_id
        for issue in issues
        if issue.severity is IssueSeverity.ERROR
        for source_id in issue.source_ids
    }
    ids.update(
        result.source_id
        for result in results
        if result.confidence < threshold
        or result.review_reasons
        or result.reports_semantic_change
        or invalid_english_tokens(result.korean_attribute_name)
    )
    ids.update(
        decision.source_id
        for decision in terminology_decisions
        if decision.tied and decision.source_id is not None
    )
    return ids


def _persistent_review_reasons(reasons: Sequence[str]) -> list[str]:
    """Keep unresolved business risks but drop transient model outage notes."""

    return list(
        dict.fromkeys(
            reason
            for reason in reasons
            if not reason.startswith("로컬 LLM 생성 ")
            and not reason.startswith("로컬 LLM 리뷰 ")
        )
    )


def _clear_review_execution_failure_reasons(reasons: Sequence[str]) -> list[str]:
    """Drop obsolete review-call failures after a later response is received."""

    return [reason for reason in reasons if not reason.startswith("로컬 LLM 리뷰 ")]


def _mark_review_failure(
    results: Sequence[GenerationResult],
    review_ids: set[str],
    failure_code: str,
) -> list[GenerationResult]:
    reason = (
        f"로컬 LLM 리뷰 {_failure_reason(failure_code)}로 현재 검증 결과를 유지"
    )
    return [
        result.model_copy(
            update={
                "confidence": min(result.confidence, 85),
                "review_reasons": list(
                    dict.fromkeys(result.review_reasons + [reason])
                ),
            }
        )
        if result.source_id in review_ids
        else result
        for result in results
    ]


def _llm_failure_code(error: Exception) -> str:
    """Classify an LLM failure without persisting sensitive exception text."""

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        class_name = type(current).__name__.casefold()
        if isinstance(current, TimeoutError) or "timeout" in class_name:
            return "timeout"
        if "connect" in class_name:
            return "connection_error"
        current = current.__cause__ or current.__context__

    message = str(error)
    if isinstance(error, LLMResponseError) and any(
        marker in message for marker in ("JSON", "스키마", "대응 오류")
    ):
        return "response_error"
    if isinstance(error, LLMResponseError):
        return "model_error"
    return "unexpected_error"


def _failure_reason(code: str) -> str:
    return {
        "timeout": "시간 초과",
        "connection_error": "연결 실패",
        "response_error": "응답 형식 오류",
        "model_error": "호출 실패",
        "missing_result": "결과 누락",
        "rejected_result": "정책 검증 거부",
        "unexpected_error": "예상하지 못한 오류",
    }.get(code, "실패")


def _recovery_stats(
    weights: Mapping[str, int],
    generation_codes: Mapping[str, str],
    review_history: Sequence[tuple[int, str, str]],
    unresolved_review_codes: Mapping[str, str],
) -> dict[str, int]:
    execution_failure_history = [
        item for item in review_history if item[2] != "rejected_result"
    ]
    rejected_history = [
        item for item in review_history if item[2] == "rejected_result"
    ]
    unresolved_execution_failures = {
        source_id: code
        for source_id, code in unresolved_review_codes.items()
        if code != "rejected_result"
    }
    unresolved_rejections = {
        source_id: code
        for source_id, code in unresolved_review_codes.items()
        if code == "rejected_result"
    }
    stats = {
        "generation_fallback_count": sum(
            weights[source_id] for source_id in generation_codes
        ),
        "review_failure_count": sum(
            weights[source_id] for _, source_id, _ in execution_failure_history
        ),
        "review_failure_source_count": sum(
            weights[source_id]
            for source_id in {item[1] for item in execution_failure_history}
        ),
        "review_unresolved_failure_count": sum(
            weights[source_id] for source_id in unresolved_execution_failures
        ),
        "review_rejected_result_source_count": sum(
            weights[source_id]
            for source_id in {item[1] for item in rejected_history}
        ),
        "review_unresolved_rejected_result_count": sum(
            weights[source_id] for source_id in unresolved_rejections
        ),
        "review_rejected_result_count": 0,
    }
    for source_id, code in generation_codes.items():
        key = f"generation_{code}_count"
        stats[key] = stats.get(key, 0) + weights[source_id]
    for _, source_id, code in review_history:
        key = f"review_{code}_count"
        stats[key] = stats.get(key, 0) + weights[source_id]
    return stats


def _recovery_events(
    aliases: Mapping[str, Sequence[SourceColumn]],
    generation_codes: Mapping[str, str],
    review_history: Sequence[tuple[int, str, str]],
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = [
        {
            "source_id": source.source_id,
            "stage": "generate",
            "code": code,
        }
        for representative_id, code in generation_codes.items()
        for source in aliases[representative_id]
    ]
    events.extend(
        {
            "source_id": source.source_id,
            "stage": "review",
            "code": code,
            "round": str(review_round),
        }
        for review_round, representative_id, code in review_history
        for source in aliases[representative_id]
    )
    stage_order = {"generate": 0, "review": 1}
    return sorted(
        events,
        key=lambda event: (
            int(event["source_id"].rsplit("-", 1)[-1]),
            stage_order[event["stage"]],
            int(event.get("round", "0")),
            event["code"],
        ),
    )


def _finalize(
    results: Sequence[GenerationResult],
    issues: Sequence[ValidationIssue],
    decisions: Sequence[TerminologyDecision],
    threshold: int,
) -> list[KoreanAttributeResult]:
    issues_by_id: dict[str, list[ValidationIssue]] = defaultdict(list)
    decisions_by_id: dict[str, list[TerminologyDecision]] = defaultdict(list)
    for issue in issues:
        for source_id in issue.source_ids:
            issues_by_id[source_id].append(issue)
    for decision in decisions:
        if decision.source_id is not None:
            decisions_by_id[decision.source_id].append(decision)
    return [
        finalize_result(
            _with_terminology_review_reason(
                result, decisions_by_id[result.source_id]
            ),
            issues_by_id[result.source_id],
            auto_confirm_threshold=threshold,
            terminology_decisions=decisions_by_id[result.source_id],
        )
        for result in results
    ]


def _with_terminology_review_reason(
    result: GenerationResult,
    decisions: Sequence[TerminologyDecision],
) -> GenerationResult:
    tied = [decision for decision in decisions if decision.tied]
    if not tied:
        return result
    reasons = list(result.review_reasons)
    for decision in tied:
        message = (
            f"동의어 빈도 동률에서 '{decision.selected_term}'을 "
            f"{decision.selection_source} 방식으로 선택"
        )
        if message not in reasons:
            reasons.append(message)
    return result.model_copy(
        update={"review_reasons": reasons, "confidence": min(result.confidence, 89)}
    )


def _expand_results(
    representatives: Sequence[KoreanAttributeResult],
    decisions: Sequence[TerminologyDecision],
    aliases: Mapping[str, Sequence[SourceColumn]],
) -> tuple[list[KoreanAttributeResult], list[TerminologyDecision]]:
    decisions_by_id: dict[str, list[TerminologyDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.source_id is not None:
            decisions_by_id[decision.source_id].append(decision)
    expanded_results: list[KoreanAttributeResult] = []
    expanded_decisions: list[TerminologyDecision] = []
    for result in representatives:
        for alias in aliases[result.source_id]:
            expanded_results.append(result.model_copy(update={"source_id": alias.source_id}))
            expanded_decisions.extend(
                decision.model_copy(update={"source_id": alias.source_id})
                for decision in decisions_by_id[result.source_id]
            )
    return expanded_results, expanded_decisions


def _batched(
    values: Sequence[SourceColumn], size: int
) -> Iterable[Sequence[SourceColumn]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
