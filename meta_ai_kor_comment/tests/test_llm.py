import asyncio
import json

import pytest

from app.exceptions import LLMResponseError
from app.config import Settings
from app.llm import LocalChatKoreanNamingModel, _parse_json
from app.models import SourceColumn


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}
            ]
        }


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request = None

    async def post(self, url, json):
        self.request = {"url": url, "json": json}
        return FakeResponse(self.payload)


def test_local_client_bypasses_environment_proxy_by_default(monkeypatch) -> None:
    captured = {}

    class CapturingClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("app.llm.httpx.AsyncClient", CapturingClient)

    LocalChatKoreanNamingModel(Settings())

    assert captured["trust_env"] is False


def test_trust_env_can_be_enabled_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TRUST_ENV", "true")

    assert Settings.from_env().llm_trust_env is True


def test_generate_parses_structured_result_and_sends_context() -> None:
    source = SourceColumn(
        source_id="row-10",
        column_name="FY",
        column_description="FY년도",
        table_name="회계정보",
        original_values={"민감원본": "LLM에 보내면 안 됨"},
    )
    client = FakeClient(
        {
            "results": [
                {
                    "source_id": "row-10",
                    "original_description": "FY년도",
                    "korean_attribute_name": "회계년도",
                    "action": "rewrite",
                    "confidence": 94,
                    "reason": "FY를 회계로 한글화",
                    "semantic_units": ["회계", "년도"],
                    "added_concepts": [],
                    "removed_concepts": [],
                    "review_reasons": [],
                }
            ]
        }
    )
    model = LocalChatKoreanNamingModel.__new__(LocalChatKoreanNamingModel)
    _configure_model(model, client)

    result = asyncio.run(model.generate([source]))

    assert result[0].korean_attribute_name == "회계년도"
    sent = client.request["json"]["messages"][1]["content"]
    assert "회계정보" in sent
    assert "민감원본" not in sent


def test_generate_rejects_missing_source_id() -> None:
    source = SourceColumn(
        source_id="row-10",
        column_name="FY",
        column_description="FY년도",
    )
    model = LocalChatKoreanNamingModel.__new__(LocalChatKoreanNamingModel)
    _configure_model(model, FakeClient({"results": []}))

    with pytest.raises(LLMResponseError, match="누락 source_id"):
        asyncio.run(model.generate([source]))


def test_json_parser_removes_thinking_and_code_fence() -> None:
    assert _parse_json('<think>내부 추론</think>```json\n{"results": []}\n```') == {
        "results": []
    }


def _configure_model(model, client: FakeClient) -> None:
    model._client = client
    model._model = "test-model"
    model._temperature = 0
    model._top_p = 1
    model._max_tokens = 1000
    model._max_retries = 0
