from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict
import uuid


@dataclass
class ChartConfig:
    type: str
    title: str
    option: Dict[str, Any]
    chart_id: str = field(default_factory=lambda: f"chart_{uuid.uuid4().hex}")

    def to_dict(self) -> dict:
        return {
            "id": self.chart_id,
            "type": self.type,
            "title": self.title,
            "option": self.option,
        }
