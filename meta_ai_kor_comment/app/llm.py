from __future__ import annotations

import json
import re
import asyncio
from typing import Any, Protocol, Sequence

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.exceptions import LLMResponseError
from app.models import (
    GenerationResponse,
    GenerationResult,
    RiskAssessment,
    SourceColumn,
    TerminologyDecision,
    ValidationIssue,
)
from app.prompts import GENERATION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT


class KoreanNamingModel(Protocol):
    async def generate(
        self,
        sources: Sequence[SourceColumn],
        risks: Sequence[RiskAssessment] | None = None,
    ) -> list[GenerationResult]:
        ...

    async def review(
        self,
        sources: Sequence[SourceColumn],
        current_results: Sequence[GenerationResult],
        issues: Sequence[ValidationIssue],
        review_round: int,
        terminology_context: Sequence[TerminologyDecision] | None = None,
    ) -> list[GenerationResult]:
        ...


class LocalChatKoreanNamingModel:
    """OpenAI-compatible local chat adapter with strict response validation."""

    def __init__(self, settings: Settings) -> None:
        timeout = httpx.Timeout(
            timeout=settings.llm_read_timeout_seconds,
            connect=settings.llm_connect_timeout_seconds,
            read=settings.llm_read_timeout_seconds,
            write=settings.llm_write_timeout_seconds,
            pool=settings.llm_pool_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/") + "/",
            timeout=timeout,
            # The default endpoint is an RFC1918 address.  In managed desktop
            # environments HTTP_PROXY can otherwise route it through an
            # unreachable corporate proxy even though direct TCP is healthy.
            trust_env=settings.llm_trust_env,
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
        )
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._top_p = settings.llm_top_p
        self._max_tokens = settings.llm_max_tokens
        self._max_retries = settings.llm_max_retries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(
        self,
        sources: Sequence[SourceColumn],
        risks: Sequence[RiskAssessment] | None = None,
    ) -> list[GenerationResult]:
        risk_by_id = {
            risk.source_id: risk.model_dump(mode="json")
            for risk in (risks or ())
            if risk.source_id
        }
        request = {
            "columns": [
                {
                    **_source_payload(source),
                    **(
                        {"risk": risk_by_id[source.source_id]}
                        if source.source_id in risk_by_id
                        else {}
                    ),
                }
                for source in sources
            ]
        }
        return await self._invoke_results(
            GENERATION_SYSTEM_PROMPT,
            request,
            sources,
            operation="생성",
        )

    async def review(
        self,
        sources: Sequence[SourceColumn],
        current_results: Sequence[GenerationResult],
        issues: Sequence[ValidationIssue],
        review_round: int,
        terminology_context: Sequence[TerminologyDecision] | None = None,
    ) -> list[GenerationResult]:
        request = {
            "review_round": review_round,
            "sources": [_source_payload(source) for source in sources],
            "current_results": [
                result.model_dump(mode="json") for result in current_results
            ],
            "validation_issues": [
                issue.model_dump(mode="json") for issue in issues
            ],
            "terminology_context": [
                decision.model_dump(mode="json")
                for decision in (terminology_context or ())
            ],
        }
        return await self._invoke_results(
            REVIEW_SYSTEM_PROMPT,
            request,
            sources,
            operation="리뷰",
        )

    async def _invoke_results(
        self,
        system_prompt: str,
        request: dict[str, Any],
        sources: Sequence[SourceColumn],
        *,
        operation: str,
    ) -> list[GenerationResult]:
        """Retry transport, JSON, schema, and source-correspondence failures."""

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                text = await self._invoke(system_prompt, request)
                results = _parse_generation_response(text, operation=operation)
                _validate_expected_results(sources, results, operation=operation)
                return results
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    break
            except (
                httpx.HTTPError,
                LLMResponseError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
            if attempt < self._max_retries:
                await asyncio.sleep(min(2**attempt, 4))
        raise LLMResponseError(
            f"로컬 LLM {operation} 호출 또는 구조화 응답 실패: {last_error}"
        ) from last_error

    async def _invoke(self, system_prompt: str, request: dict[str, Any]) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        request,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            ],
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
        }
        response = await self._client.post("chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        return _content_to_text(content)

def _source_payload(source: SourceColumn) -> dict[str, Any]:
    return source.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"original_values"},
    )


def _parse_generation_response(text: str, operation: str) -> list[GenerationResult]:
    try:
        return GenerationResponse.model_validate(_parse_json(text)).results
    except ValidationError as exc:
        raise LLMResponseError(f"{operation} 응답 스키마 오류: {exc}") from exc


def _validate_expected_results(
    sources: Sequence[SourceColumn],
    results: Sequence[GenerationResult],
    *,
    operation: str,
) -> None:
    expected = {source.source_id: source for source in sources}
    actual_ids = [result.source_id for result in results]
    duplicates = sorted(
        source_id for source_id in set(actual_ids) if actual_ids.count(source_id) > 1
    )
    missing = sorted(set(expected).difference(actual_ids))
    extras = sorted(set(actual_ids).difference(expected))
    description_mismatches = sorted(
        result.source_id
        for result in results
        if result.source_id in expected
        and result.original_description
        != expected[result.source_id].column_description
    )
    errors: list[str] = []
    if duplicates:
        errors.append(f"중복 source_id {duplicates}")
    if missing:
        errors.append(f"누락 source_id {missing}")
    if extras:
        errors.append(f"알 수 없는 source_id {extras}")
    if description_mismatches:
        errors.append(f"원본 설명 불일치 {description_mismatches}")
    if errors:
        raise LLMResponseError(f"{operation} 응답 대응 오류: " + "; ".join(errors))


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


def _parse_json(text: str) -> Any:
    cleaned = re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [
            position
            for position in (cleaned.find("{"), cleaned.find("["))
            if position >= 0
        ]
        if not starts:
            raise LLMResponseError("LLM 응답에서 JSON 시작 문자를 찾지 못했습니다.")
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(cleaned[min(starts) :])
            return value
        except json.JSONDecodeError as exc:
            preview = cleaned[:300].replace("\n", " ")
            raise LLMResponseError(f"LLM JSON 해석 실패: {preview}") from exc


# A short alias is convenient for existing callers and tests.
LocalChatNamingModel = LocalChatKoreanNamingModel
