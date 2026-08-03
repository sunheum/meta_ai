from __future__ import annotations

from app.models import ValidationReport


class InputWorkbookError(ValueError):
    """Raised when the input workbook schema or source values are invalid."""


class LLMResponseError(RuntimeError):
    """Raised when an LLM call or its structured response cannot be accepted."""


class TerminologyError(ValueError):
    """Raised when a synonym group or frequency decision is inconsistent."""


class WorkflowValidationError(RuntimeError):
    def __init__(self, report: ValidationReport, review_rounds: int) -> None:
        super().__init__("리뷰 루프 후에도 해결되지 않은 검증 오류가 있습니다.")
        self.report = report
        self.review_rounds = review_rounds

