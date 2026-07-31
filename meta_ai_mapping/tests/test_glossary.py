from pathlib import Path

from openpyxl import Workbook

from app.glossary import CanonicalGlossary


def test_glossary_loads_reference_style_workbook(tmp_path: Path) -> None:
    path = tmp_path / "glossary.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["약어–한글·영문 Full Name 요약"])
    sheet.append(["설명"])
    sheet.append([])
    sheet.append(
        [
            "영문약어",
            "영문 Full Name",
            "한글단어",
            "출현건수",
            "최고신뢰도",
        ]
    )
    sheet.append(["YN", "YES NO", "여부", 1, "낮음"])
    sheet.append(["YN", "YES OR NO", "여부", 139, "높음"])
    workbook.save(path)

    glossary = CanonicalGlossary.from_xlsx(path)

    assert len(glossary) == 1
    assert glossary.get("yn", "여 부") == "YES OR NO"
