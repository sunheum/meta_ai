from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared with the existing mapping service."""

    llm_base_url: str = "http://192.168.100.91:8000/v1"
    llm_model: str = "Qwen3.6-27B-FP8"
    llm_api_key: str = "not-needed"
    llm_temperature: float = 0.2
    llm_top_p: float = 0.8
    llm_max_tokens: int = 8192
    llm_connect_timeout_seconds: float = 15.0
    llm_read_timeout_seconds: float = 1800.0
    llm_write_timeout_seconds: float = 60.0
    llm_pool_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    progress_heartbeat_seconds: float = 15.0
    default_batch_size: int = 25
    default_max_concurrency: int = 10
    default_max_review_rounds: int = 2
    default_auto_confirm_threshold: int = 90
    max_upload_mb: int = 30
    results_dir: str = "results"
    input_sheet_name: str = "테이블_컬럼_정보"
    result_sheet_name: str = "한글속성명_결과"
    review_sheet_name: str = "검토필요"

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            llm_base_url=os.getenv("LLM_BASE_URL", defaults.llm_base_url),
            llm_model=os.getenv("LLM_MODEL", defaults.llm_model),
            llm_api_key=os.getenv("LLM_API_KEY", defaults.llm_api_key),
            llm_temperature=_env_float(
                "LLM_TEMPERATURE", defaults.llm_temperature
            ),
            llm_top_p=_env_float("LLM_TOP_P", defaults.llm_top_p),
            llm_max_tokens=_env_int("LLM_MAX_TOKENS", defaults.llm_max_tokens),
            llm_connect_timeout_seconds=_env_float(
                "LLM_CONNECT_TIMEOUT_SECONDS",
                defaults.llm_connect_timeout_seconds,
            ),
            llm_read_timeout_seconds=_env_float(
                "LLM_READ_TIMEOUT_SECONDS",
                _env_float(
                    "LLM_TIMEOUT_SECONDS", defaults.llm_read_timeout_seconds
                ),
            ),
            llm_write_timeout_seconds=_env_float(
                "LLM_WRITE_TIMEOUT_SECONDS",
                defaults.llm_write_timeout_seconds,
            ),
            llm_pool_timeout_seconds=_env_float(
                "LLM_POOL_TIMEOUT_SECONDS",
                defaults.llm_pool_timeout_seconds,
            ),
            llm_max_retries=_env_int(
                "LLM_MAX_RETRIES", defaults.llm_max_retries
            ),
            progress_heartbeat_seconds=_env_float(
                "PROGRESS_HEARTBEAT_SECONDS",
                defaults.progress_heartbeat_seconds,
            ),
            default_batch_size=_env_int(
                "DEFAULT_BATCH_SIZE", defaults.default_batch_size
            ),
            default_max_concurrency=_env_int(
                "DEFAULT_MAX_CONCURRENCY", defaults.default_max_concurrency
            ),
            default_max_review_rounds=_env_int(
                "DEFAULT_MAX_REVIEW_ROUNDS", defaults.default_max_review_rounds
            ),
            default_auto_confirm_threshold=_env_int(
                "DEFAULT_AUTO_CONFIRM_THRESHOLD",
                defaults.default_auto_confirm_threshold,
            ),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", defaults.max_upload_mb),
            results_dir=os.getenv("RESULTS_DIR", defaults.results_dir),
            input_sheet_name=os.getenv(
                "INPUT_SHEET_NAME", defaults.input_sheet_name
            ),
            result_sheet_name=os.getenv(
                "RESULT_SHEET_NAME", defaults.result_sheet_name
            ),
            review_sheet_name=os.getenv(
                "REVIEW_SHEET_NAME", defaults.review_sheet_name
            ),
        )
