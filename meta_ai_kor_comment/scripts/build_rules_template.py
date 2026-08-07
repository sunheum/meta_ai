"""입력 XLSX에서 도메인 규칙 YAML 템플릿을 생성합니다.

LLM 호출 없이 정적 분석만 수행합니다. 새 도메인 데이터를 처음 처리할 때
아래 절차로 부트스트랩하세요.

    python scripts/build_rules_template.py ../data/foo.xlsx config/rules.yaml

이미 부분적으로 채운 규칙이 있으면 ``--extend`` 로 기존 파일을 넘겨서
중복 없이 새 토큰만 추출할 수 있습니다.

    python scripts/build_rules_template.py ../data/foo.xlsx new-tokens.yaml \\
        --extend config/rules.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow ``python scripts/build_rules_template.py ...`` without requiring an
# editable install by prepending the project root to sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.excel import read_source_columns
from app.rules import build_rules_template, load_rules


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        help="컬럼설명이 포함된 입력 XLSX 경로.",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="생성할 규칙 YAML 템플릿 경로.",
    )
    parser.add_argument(
        "--extend",
        type=Path,
        default=None,
        help=(
            "이미 존재하는 규칙 YAML 경로. 지정하면 이 파일에 이미 있는 "
            "glossary source는 템플릿에서 제외합니다."
        ),
    )
    parser.add_argument(
        "--sheet",
        default=None,
        help="입력 XLSX의 시트 이름 (미지정 시 기본값 자동 감지).",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    read_kwargs = {}
    if args.sheet:
        read_kwargs["sheet_name"] = args.sheet
    sources = read_source_columns(input_path, **read_kwargs)

    existing_rules = load_rules(args.extend) if args.extend else None
    template = build_rules_template(
        sources,
        existing_rules=existing_rules,
        input_label=str(input_path.name),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template, encoding="utf-8")
    print(f"규칙 템플릿을 생성했습니다: {output_path}")
    print(f"입력 행: {len(sources):,}")
    if existing_rules is not None and not existing_rules.is_empty:
        print(
            f"기존 규칙({args.extend}) 참고 후 새 토큰만 추출했습니다."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
