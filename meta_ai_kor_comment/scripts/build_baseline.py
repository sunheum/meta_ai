from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.excel import read_source_columns
from app.normalization import classify_description, source_dedup_key


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 입력 데이터 기준선을 계산합니다.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    sources = read_source_columns(args.input)
    risks = [
        classify_description(
            source.column_description, source_id=source.source_id
        )
        for source in sources
    ]
    risk_counts = Counter(code for risk in risks for code in risk.codes)
    payload = {
        "source_count": len(sources),
        "unique_column_count": len({source.column_name for source in sources}),
        "unique_description_count": len(
            {source.column_description for source in sources}
        ),
        "unique_input_pair_count": len(
            {source_dedup_key(source) for source in sources}
        ),
        "duplicate_input_count": len(sources)
        - len({source_dedup_key(source) for source in sources}),
        "risk_counts": dict(sorted(risk_counts.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

