from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.excel import read_source_columns
from app.llm import LocalChatKoreanNamingModel
from app.models import ProgressEvent, WorkflowOptions
from app.rules import (
    DomainRules,
    build_rules_template,
    load_rules,
    load_rules_optional,
)
from app.workflow import KoreanCommentWorkflow


class DeterministicOnlyModel:
    async def generate(self, sources, risks=None):
        return []

    async def review(
        self,
        sources,
        current_results,
        issues,
        review_round,
        terminology_context=None,
    ):
        return []


async def _print_progress(event: ProgressEvent) -> None:
    print(
        f"[{event.overall_percent:3d}%] {event.stage:<10} "
        f"{event.message}",
        flush=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aggregate_terminology_decisions(decisions) -> list[dict[str, object]]:
    grouped: dict[
        tuple[str, str, tuple[tuple[str, int], ...], bool, str], list[str]
    ] = {}
    rationale_by_key: dict[
        tuple[str, str, tuple[tuple[str, int], ...], bool, str], str
    ] = {}
    candidates_by_key: dict[
        tuple[str, str, tuple[tuple[str, int], ...], bool, str], list[str]
    ] = {}
    for decision in decisions:
        frequencies = tuple(sorted(decision.frequencies.items()))
        key = (
            decision.group_id,
            decision.selected_term,
            frequencies,
            decision.tied,
            decision.selection_source,
        )
        if decision.source_id is not None:
            grouped.setdefault(key, []).append(decision.source_id)
        rationale_by_key[key] = decision.rationale
        candidates_by_key[key] = list(decision.candidates)
    return [
        {
            "group_id": key[0],
            "candidates": candidates_by_key[key],
            "candidate_frequencies": dict(key[2]),
            "selected_term": key[1],
            "tied": key[3],
            "selection_source": key[4],
            "rationale": rationale_by_key[key],
            "affected_source_ids": sorted(
                set(source_ids), key=lambda value: int(value.split("-")[-1])
            ),
        }
        for key, source_ids in sorted(grouped.items())
    ]


def _llm_settings_metadata(settings: Settings) -> dict[str, object]:
    """Return reproducible, non-secret LLM execution settings."""

    return {
        "trust_env": settings.llm_trust_env,
        "temperature": settings.llm_temperature,
        "top_p": settings.llm_top_p,
        "max_tokens": settings.llm_max_tokens,
        "connect_timeout_seconds": settings.llm_connect_timeout_seconds,
        "read_timeout_seconds": settings.llm_read_timeout_seconds,
        "write_timeout_seconds": settings.llm_write_timeout_seconds,
        "pool_timeout_seconds": settings.llm_pool_timeout_seconds,
        "max_retries": settings.llm_max_retries,
    }


def _workflow_options(
    args: argparse.Namespace,
    settings: Settings,
) -> WorkflowOptions:
    return WorkflowOptions(
        batch_size=(
            settings.default_batch_size
            if args.batch_size is None
            else args.batch_size
        ),
        max_concurrency=(
            settings.default_max_concurrency
            if args.max_concurrency is None
            else args.max_concurrency
        ),
        max_review_rounds=(
            settings.default_max_review_rounds
            if args.max_review_rounds is None
            else args.max_review_rounds
        ),
        auto_confirm_threshold=(
            settings.default_auto_confirm_threshold
            if args.auto_confirm_threshold is None
            else args.auto_confirm_threshold
        ),
    )


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    options = _workflow_options(args, settings)
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    rules = (
        load_rules(args.rules)
        if args.rules is not None
        else load_rules_optional(settings.rules_path)
    )
    if args.emit_rules_template is not None:
        template_path = args.emit_rules_template.resolve()
        template = build_rules_template(
            read_source_columns(input_path),
            existing_rules=rules,
            input_label=input_path.name,
        )
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(template, encoding="utf-8")
        print(f"규칙 템플릿을 생성했습니다: {template_path}")
    model = (
        DeterministicOnlyModel()
        if args.offline
        else LocalChatKoreanNamingModel(
            settings, glossary=rules.glossary_lookup()
        )
    )
    workflow = KoreanCommentWorkflow(model, rules=rules)
    try:
        result = await workflow.run(
            input_path,
            output_path,
            options,
            progress_callback=_print_progress,
        )
    finally:
        await workflow.aclose()
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "output_path": str(output_path),
        "output_sha256": _sha256(output_path),
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "llm_settings": _llm_settings_metadata(settings),
        "execution_mode": "deterministic-recovery" if args.offline else "local-llm",
        "options": options.model_dump(),
        "result": result.model_dump(mode="json"),
        "terminology_decisions": _aggregate_terminology_decisions(
            result.terminology_decisions
        ),
    }
    metadata_path = args.metadata or output_path.with_suffix(".metadata.json")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata["result"], ensure_ascii=False, indent=2))
    return 0 if result.validation_report.is_valid else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="실제 컬럼코멘트 Y XLSX를 한글속성명 결과로 변환합니다."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--max-review-rounds", type=int)
    parser.add_argument("--auto-confirm-threshold", type=int)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="로컬 모델 호출 없이 결정적 복구 규칙만 실행합니다.",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help=(
            "도메인 규칙 YAML 경로. 지정하지 않으면 Settings.rules_path"
            "(RULES_PATH env)의 파일을 로드하며, 없으면 빈 규칙셋으로 동작합니다."
        ),
    )
    parser.add_argument(
        "--emit-rules-template",
        type=Path,
        default=None,
        help=(
            "입력 XLSX의 미확정 영문 토큰으로 채운 규칙 YAML 템플릿을 이 경로에 씁니다. "
            "target은 비어 있으니 사용자가 채운 뒤 다음 실행에 --rules로 지정하세요."
        ),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))
