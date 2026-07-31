from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ResultStatus = Literal["자동확정", "검토필요", "검증실패"]
ComponentOrigin = Literal["mapping", "inference", "numeric"]
ReviewStratum = Literal[
    "deterministic",
    "segmentation_ambiguity",
    "mapping_ambiguity",
    "unmapped_inference",
    "duplicate_context",
    "review_needed",
]


class SourceRow(BaseModel):
    source_id: str
    excel_row: int = Field(ge=2)
    original_headers: list[str]
    original_values: list[Any]
    schema_name: str | None = None
    table_name: str
    table_description: str = ""
    column_ordinal: int | str | None = None
    column_name: str
    data_type: str
    column_description: str = ""

    @field_validator("column_name")
    @classmethod
    def uppercase_column_name(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def context_key(self) -> tuple[str, str, str, str]:
        return (
            self.column_name,
            self.table_name,
            self.table_description,
            self.data_type,
        )


class MappingEntry(BaseModel):
    abbreviation: str
    full_name: str
    korean_word: str
    occurrence_count: int = Field(default=0, ge=0)

    @field_validator("abbreviation", "full_name")
    @classmethod
    def uppercase_english(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("korean_word")
    @classmethod
    def strip_korean_word(cls, value: str) -> str:
        return value.strip()


class NameComponent(BaseModel):
    source_fragment: str
    full_name: str
    korean_word: str
    origin: ComponentOrigin
    start: int = Field(default=0, ge=0)
    end: int = Field(default=0, ge=0)
    occurrence_count: int = Field(default=0, ge=0)

    @field_validator("source_fragment", "full_name")
    @classmethod
    def uppercase_english(cls, value: str) -> str:
        return value.strip().upper()


class SegmentationCandidate(BaseModel):
    components: list[NameComponent]
    unresolved_fragments: list[str] = Field(default_factory=list)
    score: float = 0.0
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_count: int = Field(default=0, ge=0)


class ColumnResult(BaseModel):
    source_id: str
    components: list[NameComponent]
    english_full_name: str
    korean_attribute_name: str
    status: ResultStatus
    confidence: int = Field(ge=0, le=100)
    evidence: str
    reason: str = ""
    review_stratum: ReviewStratum = "deterministic"
    validation_codes: list[str] = Field(default_factory=list)


class LLMResolution(BaseModel):
    source_id: str
    components: list[NameComponent]
    full_name: str
    korean_attribute_name: str
    reason: str


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    source_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    stats: dict[str, int | float]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]


class WorkflowOptions(BaseModel):
    batch_size: int = Field(default=25, ge=1, le=100)
    max_concurrency: int = Field(default=10, ge=1, le=50)
    max_review_rounds: int = Field(default=2, ge=0, le=5)
    auto_confirm_threshold: int = Field(default=85, ge=0, le=100)
    use_llm: bool = True


class WorkflowResult(BaseModel):
    output_path: str
    source_count: int
    auto_confirmed_count: int
    review_needed_count: int
    validation_failed_count: int
    review_rounds: int
    validation_report: ValidationReport
    stage_durations_seconds: dict[str, float] = Field(default_factory=dict)


class ProgressEvent(BaseModel):
    stage: Literal[
        "queued",
        "input",
        "segment",
        "generate",
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
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

