from __future__ import annotations

import json
import re
from typing import Any, Protocol, Sequence

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.exceptions import LLMResponseError
from app.models import MappingCandidate, SourceColumn, ValidationIssue
from app.prompts import GENERATION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT


class MappingModel(Protocol):
    async def generate(self, sources: Sequence[SourceColumn]) -> list[MappingCandidate]:
        ...

    async def review(
        self,
        sources: Sequence[SourceColumn],
        current_mappings: Sequence[MappingCandidate],
        issues: Sequence[ValidationIssue],
        review_round: int,
    ) -> list[MappingCandidate]:
        ...


class _GenerationPayload(BaseModel):
    mappings: list[MappingCandidate]


class _ReplacementMapping(BaseModel):
    abbreviation: str
    full_name: str
    korean_word: str


class _Replacement(BaseModel):
    source_id: str
    mappings: list[_ReplacementMapping] = Field(min_length=1)


class _ReviewPayload(BaseModel):
    replacements: list[_Replacement]


class LocalChatMappingModel:
    def __init__(self, settings: Settings) -> None:
        timeout = httpx.Timeout(
            timeout=settings.llm_read_timeout_seconds,
            connect=settings.llm_connect_timeout_seconds,
            read=settings.llm_read_timeout_seconds,
            write=settings.llm_write_timeout_seconds,
            pool=settings.llm_pool_timeout_seconds,
        )
        self._chat = ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            max_tokens=settings.llm_max_tokens,
            timeout=timeout,
            max_retries=settings.llm_max_retries,
        )

    async def generate(
        self, sources: Sequence[SourceColumn]
    ) -> list[MappingCandidate]:
        request = {
            "columns": [source.model_dump(exclude_none=True) for source in sources]
        }
        text = await self._invoke(GENERATION_SYSTEM_PROMPT, request)
        try:
            return _GenerationPayload.model_validate(_parse_json(text)).mappings
        except ValidationError as exc:
            raise LLMResponseError(f"생성 응답 스키마 오류: {exc}") from exc

    async def review(
        self,
        sources: Sequence[SourceColumn],
        current_mappings: Sequence[MappingCandidate],
        issues: Sequence[ValidationIssue],
        review_round: int,
    ) -> list[MappingCandidate]:
        request = {
            "review_round": review_round,
            "sources": [source.model_dump(exclude_none=True) for source in sources],
            "current_mappings": [mapping.model_dump() for mapping in current_mappings],
            "validation_issues": [issue.model_dump() for issue in issues],
        }
        text = await self._invoke(REVIEW_SYSTEM_PROMPT, request)
        try:
            payload = _ReviewPayload.model_validate(_parse_json(text))
        except ValidationError as exc:
            raise LLMResponseError(f"리뷰 응답 스키마 오류: {exc}") from exc

        return [
            MappingCandidate(source_id=replacement.source_id, **mapping.model_dump())
            for replacement in payload.replacements
            for mapping in replacement.mappings
        ]

    async def _invoke(self, system_prompt: str, request: dict[str, Any]) -> str:
        try:
            response = await self._chat.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(
                        content=json.dumps(request, ensure_ascii=False, separators=(",", ":"))
                    ),
                ]
            )
        except Exception as exc:
            raise LLMResponseError(f"로컬 LLM 호출 실패: {exc}") from exc
        return _content_to_text(response.content)


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
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start_positions = [pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0]
        if not start_positions:
            raise LLMResponseError("LLM 응답에서 JSON 시작 문자를 찾지 못했습니다.")
        start = min(start_positions)
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(cleaned[start:])
            return value
        except json.JSONDecodeError as exc:
            preview = cleaned[:300].replace("\n", " ")
            raise LLMResponseError(f"LLM JSON 해석 실패: {preview}") from exc
