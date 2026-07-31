from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.llm import LocalChatMappingModel
from app.models import SourceColumn


async def main() -> None:
    model = LocalChatMappingModel(Settings.from_env())
    mappings = await model.generate(
        [
            SourceColumn(
                source_id="row-2",
                column_name="STDT",
                column_description="기준일자",
            ),
            SourceColumn(
                source_id="row-3",
                column_name="YYMM",
                column_description="년월",
            ),
        ]
    )
    print(
        json.dumps(
            [mapping.model_dump() for mapping in mappings],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

