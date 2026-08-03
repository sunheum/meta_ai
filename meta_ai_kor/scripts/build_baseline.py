from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.excel import (
    build_s1_baseline,
    read_source_workbook,
    write_result_workbook,
)
from app.glossary import MappingGlossary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population", type=Path)
    args = parser.parse_args()

    sources = read_source_workbook(args.input)
    glossary = MappingGlossary.from_xlsx(args.mapping)
    results = build_s1_baseline(sources, glossary)
    write_result_workbook(args.input, args.output, sources, results)
    if args.population:
        source_by_id = {source.source_id: source for source in sources}
        rows = []
        for result in results:
            source = source_by_id[result.source_id]
            rows.append(
                {
                    "source_id": result.source_id,
                    "review_stratum": result.review_stratum,
                    "컬럼명": source.column_name,
                    "테이블명": source.table_name,
                    "테이블설명": source.table_description,
                    "영문 Full Name": result.english_full_name,
                    "한글속성명": result.korean_attribute_name,
                    "confidence": result.confidence,
                    "처리상태": result.status,
                    "generation_reason": result.reason,
                }
            )
        args.population.parent.mkdir(parents=True, exist_ok=True)
        args.population.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in rows
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        f"baseline rows={len(results)} glossary_entries={len(glossary)} "
        f"output={args.output}"
    )


if __name__ == "__main__":
    main()

