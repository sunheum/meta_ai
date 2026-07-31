"""서비스 경계에서 사용하는 명시적 오류."""


class InputWorkbookError(ValueError):
    """입력 또는 매핑 XLSX가 계약을 만족하지 않는다."""


class LLMResponseError(RuntimeError):
    """로컬 LLM 호출 또는 구조화 응답 해석에 실패했다."""


class WorkflowValidationError(RuntimeError):
    """완료 조건을 만족하지 못한 워크플로 결과다."""

