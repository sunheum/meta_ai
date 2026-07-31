from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SourceColumn(BaseModel):
    source_id: str
    column_name: str
    column_description: str
    table_name: str | None = None
    schema_name: str | None = None


class MappingCandidate(BaseModel):
    source_id: str
    abbreviation: str
    full_name: str
    korean_word: str

    @field_validator("source_id", "abbreviation", "full_name", "korean_word")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("abbreviation", "full_name")
    @classmethod
    def uppercase_english(cls, value: str) -> str:
        return value.upper()


class MappingSummary(BaseModel):
    abbreviation: str
    full_name: str
    korean_word: str
    occurrence_count: int = Field(ge=1)


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
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
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]


class WorkflowOptions(BaseModel):
    batch_size: int = Field(default=25, ge=1, le=100)
    max_concurrency: int = Field(default=10, ge=1, le=50)
    max_review_rounds: int = Field(default=2, ge=0, le=5)


class WorkflowResult(BaseModel):
    output_path: str
    mapping_count: int
    failed_source_count: int = 0
    is_partial: bool = False
    source_count: int
    review_rounds: int
    validation_report: ValidationReport
    reconciliation_stats: dict[str, int] = Field(default_factory=dict)


class FailedMappingRow(BaseModel):
    source_id: str
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str
    column_description: str
    abbreviation: str | None = None
    full_name: str | None = None
    korean_word: str | None = None
    issue_codes: str
    validation_messages: str
    suggested_actions: str


class ProgressEvent(BaseModel):
    stage: Literal[
        "queued",
        "input",
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
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
