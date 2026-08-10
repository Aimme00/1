from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Set


class ValidationLevel(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    level: ValidationLevel = ValidationLevel.ERROR

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "level": self.level.value,
        }


@dataclass
class SQLValidationResult:
    original_sql: str
    validated_sql: str = ""
    parser: str = ""
    tables: Set[str] = field(default_factory=set)
    columns: Set[str] = field(default_factory=set)
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(issue.level == ValidationLevel.ERROR for issue in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == ValidationLevel.ERROR]

    def feedback_text(self) -> str:
        if self.is_valid:
            return "SQL 校验通过。"
        return "；".join(issue.message for issue in self.errors)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "original_sql": self.original_sql,
            "validated_sql": self.validated_sql,
            "parser": self.parser,
            "tables": sorted(self.tables),
            "columns": sorted(self.columns),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class SQLAttempt:
    number: int
    sql: str
    validation: SQLValidationResult

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "sql": self.sql,
            "validation": self.validation.to_dict(),
        }


@dataclass
class SQLRepairOutcome:
    sql: str
    validation: SQLValidationResult
    attempts: List[SQLAttempt] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.validation.is_valid

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "sql": self.sql,
            "validation": self.validation.to_dict(),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }
