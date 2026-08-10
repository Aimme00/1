from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GeneratedResponse:
    answer: str
    suggested_questions: List[str] = field(default_factory=list)
    scope: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "suggested_questions": self.suggested_questions,
            "scope": self.scope,
        }
