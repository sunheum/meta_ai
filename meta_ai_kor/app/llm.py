from __future__ import annotations

import json
import re
from typing import Any, Protocol, Sequence

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.exceptions import LLMResponseError
from app.models import (
    LLMResolution,
    ResolutionRequest,
    ReviewRequest,
)
from app.prompts import GENERATION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT


class NamingModel(Protocol):
    async def resolve(
        self,
        requests: Sequence[ResolutionRequest],
    ) -> list[LLMResolution]:
        ...

    async def review(
        self,
        requests: Sequence[ReviewRequest],
    ) -> list[LLMResolution]:
        ...


class _ResolutionPayload(BaseModel):
    resolutions: list[LLMResolution]


class LocalChatNamingModel:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = httpx.Timeout(
            timeout=settings.llm_read_timeout_seconds,
            connect=settings.llm_connect_timeout_seconds,
            read=settings.llm_read_timeout_seconds,
            write=settings.llm_write_timeout_seconds,
            pool=settings.llm_pool_timeout_seconds,
        )

    async def resolve(
        self,
        requests: Sequence[ResolutionRequest],
    ) -> list[LLMResolution]:
        payload = {
            "requests": [
                _resolution_request_payload(request) for request in requests
            ]
        }
        text = await self._invoke(GENERATION_SYSTEM_PROMPT, payload)
        try:
            return _decode_resolution_payload(_parse_json(text))
        except ValidationError as exc:
            raise LLMResponseError(f"생성 응답 스키마 오류: {exc}") from exc

    async def review(
        self,
        requests: Sequence[ReviewRequest],
    ) -> list[LLMResolution]:
        payload = {
            "requests": [
                {
                    **_resolution_request_payload(request.request),
                    "current_result": {
                        "components": [
                            component.model_dump(
                                include={
                                    "source_fragment",
                                    "full_name",
                                    "korean_word",
                                    "origin",
                                }
                            )
                            for component in request.current_result.components
                        ],
                        "english_full_name": (
                            request.current_result.english_full_name
                        ),
                        "korean_attribute_name": (
                            request.current_result.korean_attribute_name
                        ),
                        "confidence": request.current_result.confidence,
                    },
                    "validation_issues": [
                        {
                            "code": issue.code,
                            "severity": issue.severity,
                            "message": issue.message,
                            "details": issue.details,
                        }
                        for issue in request.validation_issues
                    ],
                    "review_round": request.review_round,
                }
                for request in requests
            ]
        }
        text = await self._invoke(REVIEW_SYSTEM_PROMPT, payload)
        try:
            return _decode_resolution_payload(_parse_json(text))
        except ValidationError as exc:
            raise LLMResponseError(f"리뷰 응답 스키마 오류: {exc}") from exc

    async def _invoke(
        self,
        system_prompt: str,
        request: dict[str, Any],
    ) -> str:
        payload = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        request,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": self._settings.llm_temperature,
            "top_p": self._settings.llm_top_p,
            "max_tokens": self._settings.llm_max_tokens,
            "chat_template_kwargs": {
                "enable_thinking": self._settings.llm_enable_thinking,
            },
        }
        endpoint = (
            self._settings.llm_base_url.rstrip("/") + "/chat/completions"
        )
        last_error: Exception | None = None
        for _ in range(self._settings.llm_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        endpoint,
                        headers={
                            "Authorization": (
                                f"Bearer {self._settings.llm_api_key}"
                            ),
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    value = response.json()
                    content = value["choices"][0]["message"]["content"]
                    return _content_to_text(content)
            except Exception as exc:
                last_error = exc
        raise LLMResponseError(
            f"로컬 LLM 호출 실패: {last_error}"
        ) from last_error


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


def _decode_resolution_payload(value: Any) -> list[LLMResolution]:
    if not isinstance(value, dict):
        raise LLMResponseError("LLM 응답 최상위 값은 JSON 객체여야 합니다.")
    raw_resolutions = value.get("resolutions")
    if not isinstance(raw_resolutions, list):
        raise LLMResponseError("LLM 응답에 resolutions 배열이 없습니다.")
    normalized: list[dict[str, Any]] = []
    for raw in raw_resolutions:
        if not isinstance(raw, dict):
            raise LLMResponseError("resolution 항목은 JSON 객체여야 합니다.")
        raw_components = raw.get("c", raw.get("components"))
        if not isinstance(raw_components, list):
            raise LLMResponseError("resolution에 components 배열이 없습니다.")
        components: list[dict[str, Any]] = []
        for component in raw_components:
            if isinstance(component, list) and len(component) == 4:
                components.append(
                    {
                        "source_fragment": component[0],
                        "full_name": component[1],
                        "korean_word": component[2],
                        "origin": component[3],
                    }
                )
            elif isinstance(component, dict):
                components.append(component)
            else:
                raise LLMResponseError(
                    "component는 4개 값의 배열 또는 JSON 객체여야 합니다."
                )
        normalized.append(
            {
                "source_id": raw.get("id", raw.get("source_id")),
                "components": components,
                "full_name": raw.get("full_name")
                or " ".join(str(item.get("full_name", "")) for item in components),
                "korean_attribute_name": raw.get("korean_attribute_name")
                or "".join(str(item.get("korean_word", "")) for item in components),
                "reason": raw.get("r", raw.get("reason", "")),
            }
        )
    return _ResolutionPayload.model_validate(
        {"resolutions": normalized}
    ).resolutions


def _resolution_request_payload(
    request: ResolutionRequest,
) -> dict[str, Any]:
    source = request.source
    return {
        "source": {
            "source_id": source.source_id,
            "table_name": source.table_name,
            "table_description": source.table_description,
            "column_name": source.column_name,
            "data_type": source.data_type,
        },
        "candidates": [
            {
                "components": [
                    component.model_dump(
                        include={
                            "source_fragment",
                            "full_name",
                            "korean_word",
                            "origin",
                        }
                    )
                    for component in candidate.components
                ],
                "unresolved_fragments": candidate.unresolved_fragments,
                "coverage": candidate.coverage,
                "score": candidate.score,
            }
            for candidate in request.candidates
        ],
        "mapping_options": {
            fragment: [
                entry.model_dump(
                    include={
                        "abbreviation",
                        "full_name",
                        "korean_word",
                        "occurrence_count",
                    }
                )
                for entry in entries
            ]
            for fragment, entries in request.mapping_options.items()
        },
        "table_peer_columns": request.table_peer_columns,
    }


def _parse_json(text: str) -> Any:
    cleaned = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start_positions = [
            position
            for position in (cleaned.find("{"), cleaned.find("["))
            if position >= 0
        ]
        if not start_positions:
            raise LLMResponseError(
                "LLM 응답에서 JSON 시작 문자를 찾지 못했습니다."
            )
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(cleaned[min(start_positions) :])
            return value
        except json.JSONDecodeError as exc:
            preview = cleaned[:300].replace("\n", " ")
            raise LLMResponseError(
                f"LLM JSON 해석 실패: {preview}"
            ) from exc
