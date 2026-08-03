from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProcessingAction(StrEnum):
    """LLM response actions. Values are part of the JSON contract."""

    KEEP = "keep"
    NORMALIZE = "normalize"
    REWRITE = "rewrite"

    @property
    def korean_label(self) -> str:
        return {
            self.KEEP: "유지",
            self.NORMALIZE: "정규화",
            self.REWRITE: "재작성",
        }[self]


class ProcessingStatus(StrEnum):
    AUTO_CONFIRMED = "자동확정"
    REVIEW_REQUIRED = "검토필요"
    VALIDATION_FAILED = "검증실패"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class SourceColumn(BaseModel):
    """One source workbook row used by the generation workflow.

    The model deliberately does not trim the original description. Whitespace in
    source data is a risk signal and silently removing it here would make the
    deterministic checks unable to explain a transformation.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    column_name: str
    column_description: str
    schema_name: str | None = None
    table_name: str | None = None
    table_description: str | None = None
    column_order: int | str | None = None
    data_type: str | None = None
    original_values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_id는 비어 있을 수 없습니다.")
        return value

    @field_validator("column_name")
    @classmethod
    def validate_column_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("컬럼명은 비어 있을 수 없습니다.")
        return value

    @property
    def context_text(self) -> str:
        parts = (
            self.schema_name,
            self.table_name,
            self.table_description,
            self.column_name,
            self.data_type,
        )
        return " ".join(str(part) for part in parts if part not in (None, ""))


class RiskAssessment(BaseModel):
    source_id: str | None = None
    level: RiskLevel
    codes: list[str] = Field(default_factory=list)
    english_tokens: list[str] = Field(default_factory=list)
    digit_sequences: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    requires_generation: bool
    requires_review: bool


class GenerationResult(BaseModel):
    """Structured generation/review response for one distinct input pair."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    original_description: str
    korean_attribute_name: str
    action: ProcessingAction
    confidence: int = Field(ge=0, le=100, strict=True)
    reason: str = ""
    semantic_units: list[str] = Field(default_factory=list)
    added_concepts: list[str] = Field(default_factory=list)
    removed_concepts: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)

    @field_validator("source_id")
    @classmethod
    def strip_source_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_id는 비어 있을 수 없습니다.")
        return value

    @field_validator(
        "semantic_units", "added_concepts", "removed_concepts", "review_reasons"
    )
    @classmethod
    def clean_list_values(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @model_validator(mode="after")
    def validate_action_evidence(self) -> GenerationResult:
        if self.action is not ProcessingAction.KEEP and not self.reason.strip():
            raise ValueError("정규화 또는 재작성 결과에는 변환근거가 필요합니다.")
        return self

    @property
    def processing_method(self) -> str:
        return self.action.korean_label

    @property
    def reports_semantic_change(self) -> bool:
        return bool(self.added_concepts or self.removed_concepts)


class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[GenerationResult]


class KoreanAttributeResult(GenerationResult):
    """Final row state after deterministic validation and review."""

    status: ProcessingStatus
    validation_issue_codes: list[str] = Field(default_factory=list)
    terminology_decisions: list[str] = Field(default_factory=list)


class TerminologyDecision(BaseModel):
    source_id: str | None = None
    group_id: str
    candidates: list[str]
    frequencies: dict[str, int]
    selected_term: str
    tied: bool = False
    selection_source: str
    rationale: str

    @model_validator(mode="after")
    def validate_decision(self) -> TerminologyDecision:
        if not self.candidates:
            raise ValueError("용어 그룹에는 후보가 하나 이상 필요합니다.")
        if self.selected_term not in self.candidates:
            raise ValueError("선택 용어는 후보 그룹에 포함되어야 합니다.")
        if set(self.frequencies) != set(self.candidates):
            raise ValueError("빈도표는 모든 후보와 정확히 일치해야 합니다.")
        if any(count < 0 for count in self.frequencies.values()):
            raise ValueError("용어 빈도는 음수일 수 없습니다.")
        return self


class ValidationIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str
    suggested_action: str
    source_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    stats: dict[str, int]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity is IssueSeverity.WARNING
        ]

    def issues_for(self, source_id: str) -> list[ValidationIssue]:
        return [issue for issue in self.issues if source_id in issue.source_ids]


class WorkflowOptions(BaseModel):
    batch_size: int = Field(default=25, ge=1, le=100)
    max_concurrency: int = Field(default=10, ge=1, le=50)
    max_review_rounds: int = Field(default=2, ge=0, le=5)
    auto_confirm_threshold: int = Field(default=90, ge=0, le=100)


class WorkflowResult(BaseModel):
    output_path: str
    source_count: int
    auto_confirmed_count: int = 0
    review_required_count: int = 0
    validation_failed_count: int = 0
    review_rounds: int = 0
    validation_report: ValidationReport
    terminology_stats: dict[str, int] = Field(default_factory=dict)
    terminology_decisions: list[TerminologyDecision] = Field(default_factory=list)

    @property
    def result_count(self) -> int:
        return (
            self.auto_confirmed_count
            + self.review_required_count
            + self.validation_failed_count
        )

    @property
    def is_partial(self) -> bool:
        return self.validation_failed_count > 0


class ProgressEvent(BaseModel):
    stage: Literal[
        "queued",
        "input",
        "normalize",
        "generate",
        "reconcile",
        "validate",
        "review",
        "output",
        "failed",
    ]
    stage_percent: int = Field(ge=0, le=100)
    overall_percent: int = Field(ge=0, le=100)
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    stage_elapsed_seconds: float = Field(default=0, ge=0)
    total_elapsed_seconds: float = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Small compatibility aliases keep workflow code readable without duplicating models.
GeneratedName = GenerationResult
ProcessedColumn = KoreanAttributeResult
