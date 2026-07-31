from __future__ import annotations

import asyncio
import json
import sys

from app.config import Settings
from app.glossary import MappingGlossary
from app.llm import LocalChatNamingModel
from app.models import SourceRow
from app.workflow import NamingWorkflow


async def main() -> None:
    settings = Settings.from_env()
    glossary = MappingGlossary.from_xlsx(settings.mapping_workbook_path)
    workflow = NamingWorkflow(
        glossary,
        LocalChatNamingModel(settings),
        strict_llm=True,
    )
    source = SourceRow(
        source_id="row-smoke",
        excel_row=2,
        original_headers=["컬럼명"],
        original_values=["TLM_OPNDT"],
        table_name="INS_CRDIS_PY_INFO_TLM_INQ",
        table_description="계약_신용정보원지급정보조회전문상세조회",
        column_name="TLM_OPNDT",
        data_type="VARCHAR",
    )
    request = workflow._resolution_request(source)
    resolutions = await workflow._model.resolve([request])  # type: ignore[union-attr]
    print(
        json.dumps(
            [resolution.model_dump() for resolution in resolutions],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
