from __future__ import annotations

from app.models import ValidationReport


class InputWorkbookError(ValueError):
    """입력 엑셀의 구조 또는 값이 올바르지 않을 때 발생한다."""


class LLMResponseError(RuntimeError):
    """LLM 호출 또는 JSON 응답 해석에 실패했을 때 발생한다."""


class WorkflowValidationError(RuntimeError):
    def __init__(self, report: ValidationReport, review_rounds: int) -> None:
        super().__init__("리뷰 루프 후에도 해결되지 않은 검증 오류가 있습니다.")
        self.report = report
        self.review_rounds = review_rounds

