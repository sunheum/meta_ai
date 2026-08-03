from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.llm import LocalChatKoreanNamingModel
from app.models import SourceColumn
from app.normalization import classify_description
from app.validation import validate_result


async def main() -> int:
    source = SourceColumn(
        source_id="row-190",
        schema_name="BASE",
        table_name="INS_CR_CR_INFO",
        table_description="계약_계약자동차정보",
        column_name="SOFA_CR_YN",
        column_description="SOFA차량여부",
        data_type="VARCHAR",
    )
    model = LocalChatKoreanNamingModel(Settings.from_env())
    try:
        risk = classify_description(
            source.column_description,
            source_id=source.source_id,
        )
        results = await model.generate([source], [risk])
        issues = validate_result(source, results[0])
        print(
            json.dumps(
                {
                    "result": results[0].model_dump(mode="json"),
                    "issues": [issue.model_dump(mode="json") for issue in issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if not issues else 2
    finally:
        await model.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
