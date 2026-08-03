from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    llm_base_url: str = "http://192.168.100.91:8000/v1"
    llm_model: str = "Qwen3.6-27B-FP8"
    llm_api_key: str = "not-needed"
    llm_temperature: float = 0.0
    llm_top_p: float = 0.8
    llm_max_tokens: int = 8192
    llm_enable_thinking: bool = False
    llm_connect_timeout_seconds: float = 15.0
    llm_read_timeout_seconds: float = 1800.0
    llm_write_timeout_seconds: float = 60.0
    llm_pool_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_enabled: bool = True
    strict_llm: bool = False
    default_batch_size: int = 25
    default_max_concurrency: int = 10
    default_max_review_rounds: int = 2
    auto_confirm_threshold: int = 85
    max_segmentation_candidates: int = 8
    progress_heartbeat_seconds: float = 15.0
    max_upload_mb: int = 30
    results_dir: str = "results"
    mapping_workbook_path: str = "result.xlsx"
    source_workbook_path: str = (
        "../data/table_column_template_컬럼코멘트N.xlsx"
    )

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
            llm_enable_thinking=_env_bool(
                "LLM_ENABLE_THINKING",
                defaults.llm_enable_thinking,
            ),
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
            llm_enabled=_env_bool("LLM_ENABLED", defaults.llm_enabled),
            strict_llm=_env_bool("STRICT_LLM", defaults.strict_llm),
            default_batch_size=_env_int(
                "DEFAULT_BATCH_SIZE", defaults.default_batch_size
            ),
            default_max_concurrency=_env_int(
                "DEFAULT_MAX_CONCURRENCY", defaults.default_max_concurrency
            ),
            default_max_review_rounds=_env_int(
                "DEFAULT_MAX_REVIEW_ROUNDS",
                defaults.default_max_review_rounds,
            ),
            auto_confirm_threshold=_env_int(
                "AUTO_CONFIRM_THRESHOLD", defaults.auto_confirm_threshold
            ),
            max_segmentation_candidates=_env_int(
                "MAX_SEGMENTATION_CANDIDATES",
                defaults.max_segmentation_candidates,
            ),
            progress_heartbeat_seconds=_env_float(
                "PROGRESS_HEARTBEAT_SECONDS",
                defaults.progress_heartbeat_seconds,
            ),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", defaults.max_upload_mb),
            results_dir=os.getenv("RESULTS_DIR", defaults.results_dir),
            mapping_workbook_path=os.getenv(
                "MAPPING_WORKBOOK_PATH", defaults.mapping_workbook_path
            ),
            source_workbook_path=os.getenv(
                "SOURCE_WORKBOOK_PATH", defaults.source_workbook_path
            ),
        )
