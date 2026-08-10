from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class QualityLevel(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    level: QualityLevel
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "level": self.level.value,
            "details": self.details,
        }


@dataclass
class ResultQualityReport:
    usable: bool
    empty: bool
    truncated: bool
    row_count: int
    issues: List[QualityIssue] = field(default_factory=list)

    @property
    def warnings(self) -> List[str]:
        return [
            issue.message
            for issue in self.issues
            if issue.level == QualityLevel.WARNING
        ]

    def to_dict(self) -> dict:
        return {
            "usable": self.usable,
            "empty": self.empty,
            "truncated": self.truncated,
            "row_count": self.row_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }
