from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FieldProfile:
    name: str
    kind: str
    non_null_count: int
    unique_count: int
    statistics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "non_null_count": self.non_null_count,
            "unique_count": self.unique_count,
            "statistics": self.statistics,
        }


@dataclass
class Insight:
    type: str
    title: str
    text: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "text": self.text,
            "evidence": self.evidence,
        }


@dataclass
class AnalysisResult:
    row_count: int
    field_profiles: List[FieldProfile] = field(default_factory=list)
    insights: List[Insight] = field(default_factory=list)
    field_labels: Dict[str, str] = field(default_factory=dict)
    primary_dimension: Optional[str] = None
    primary_metric: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "field_profiles": [profile.to_dict() for profile in self.field_profiles],
            "insights": [insight.to_dict() for insight in self.insights],
            "field_labels": self.field_labels,
            "primary_dimension": self.primary_dimension,
            "primary_metric": self.primary_metric,
        }
