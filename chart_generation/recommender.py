from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from data_analysis import AnalysisResult

from .objects import ChartConfig


class EChartsRecommender:
    """只生成受限制的 ECharts JSON 子集，不接受任意脚本或 formatter 函数。"""

    def __init__(self, max_points: int = 200, max_categories: int = 20):
        self.max_points = max_points
        self.max_categories = max_categories

    def recommend(
        self,
        query: str,
        result: Dict[str, Any],
        analysis: AnalysisResult,
    ) -> list[ChartConfig]:
        rows = [row for row in (result.get("rows") or []) if isinstance(row, dict)]
        if not rows:
            return []

        kinds = {profile.name: profile.kind for profile in analysis.field_profiles}
        temporal_fields = [name for name, kind in kinds.items() if kind == "temporal"]
        numeric_fields = [name for name, kind in kinds.items() if kind == "numeric"]
        categorical_fields = [name for name, kind in kinds.items() if kind == "categorical"]

        if temporal_fields and numeric_fields:
            chart = self._line_chart(
                rows,
                temporal_fields[0],
                numeric_fields[0],
                analysis.field_labels,
            )
            return [chart] if chart else []

        normalized_query = query.lower()
        if categorical_fields and numeric_fields:
            dimension = self._best_category(categorical_fields, rows)
            if dimension:
                if any(term in normalized_query for term in ("占比", "比例", "构成", "份额")):
                    chart = self._pie_chart(
                        rows, dimension, numeric_fields[0], analysis.field_labels
                    )
                else:
                    chart = self._bar_chart(
                        rows, dimension, numeric_fields[0], analysis.field_labels
                    )
                return [chart] if chart else []

        if len(numeric_fields) >= 2 and any(
            term in normalized_query for term in ("关系", "相关", "分布", "散点")
        ):
            chart = self._scatter_chart(
                rows,
                numeric_fields[0],
                numeric_fields[1],
                analysis.field_labels,
            )
            return [chart] if chart else []

        return []

    def _line_chart(
        self,
        rows: list[dict],
        dimension: str,
        metric: str,
        field_labels: Dict[str, str],
    ) -> Optional[ChartConfig]:
        points = []
        for row in rows:
            value = self._number(row.get(metric))
            if row.get(dimension) is not None and value is not None:
                points.append(
                    (
                        self._time_sort_key(row[dimension]),
                        self._label(row[dimension]),
                        value,
                    )
                )
        if len(points) < 2:
            return None
        points.sort(key=lambda item: item[0])
        points = points[: self.max_points]
        metric_label = field_labels.get(metric, metric)
        title = f"{metric_label}趋势"
        return ChartConfig(
            type="line",
            title=title,
            option={
                "title": {"text": title},
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": [item[1] for item in points]},
                "yAxis": {"type": "value", "name": metric_label},
                "series": [
                    {
                        "name": metric_label,
                        "type": "line",
                        "smooth": True,
                        "showSymbol": len(points) <= 50,
                        "data": [item[2] for item in points],
                    }
                ],
            },
        )

    def _bar_chart(
        self,
        rows: list[dict],
        dimension: str,
        metric: str,
        field_labels: Dict[str, str],
    ) -> Optional[ChartConfig]:
        values = self._aggregate(rows, dimension, metric)
        if not values:
            return None
        values = values[: self.max_categories]
        dimension_label = field_labels.get(dimension, dimension)
        metric_label = field_labels.get(metric, metric)
        title = f"按{dimension_label}对比{metric_label}"
        return ChartConfig(
            type="bar",
            title=title,
            option={
                "title": {"text": title},
                "tooltip": {"trigger": "axis"},
                "grid": {"containLabel": True},
                "xAxis": {"type": "category", "data": [item[0] for item in values]},
                "yAxis": {"type": "value", "name": metric_label},
                "series": [
                    {
                        "name": metric_label,
                        "type": "bar",
                        "data": [item[1] for item in values],
                    }
                ],
            },
        )

    def _pie_chart(
        self,
        rows: list[dict],
        dimension: str,
        metric: str,
        field_labels: Dict[str, str],
    ) -> Optional[ChartConfig]:
        values = self._aggregate(rows, dimension, metric)
        if not values:
            return None
        values = values[: min(self.max_categories, 12)]
        metric_label = field_labels.get(metric, metric)
        title = f"{metric_label}构成"
        return ChartConfig(
            type="pie",
            title=title,
            option={
                "title": {"text": title},
                "tooltip": {"trigger": "item"},
                "legend": {"type": "scroll", "bottom": 0},
                "series": [
                    {
                        "name": metric_label,
                        "type": "pie",
                        "radius": ["40%", "70%"],
                        "data": [
                            {"name": name, "value": value}
                            for name, value in values
                        ],
                    }
                ],
            },
        )

    def _scatter_chart(
        self,
        rows: list[dict],
        x_field: str,
        y_field: str,
        field_labels: Dict[str, str],
    ) -> Optional[ChartConfig]:
        points = []
        for row in rows:
            x_value = self._number(row.get(x_field))
            y_value = self._number(row.get(y_field))
            if x_value is not None and y_value is not None:
                points.append([x_value, y_value])
        if len(points) < 2:
            return None
        points = points[: self.max_points]
        x_label = field_labels.get(x_field, x_field)
        y_label = field_labels.get(y_field, y_field)
        title = f"{x_label}与{y_label}分布"
        return ChartConfig(
            type="scatter",
            title=title,
            option={
                "title": {"text": title},
                "tooltip": {"trigger": "item"},
                "xAxis": {"type": "value", "name": x_label},
                "yAxis": {"type": "value", "name": y_label},
                "series": [{"type": "scatter", "data": points}],
            },
        )

    def _aggregate(
        self,
        rows: list[dict],
        dimension: str,
        metric: str,
    ) -> list[tuple[str, float]]:
        totals: dict[str, float] = defaultdict(float)
        for row in rows:
            name = row.get(dimension)
            value = self._number(row.get(metric))
            if name is not None and value is not None:
                totals[self._label(name)] += value
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)

    def _best_category(self, fields: list[str], rows: list[dict]) -> Optional[str]:
        candidates = []
        for field in fields:
            unique = {self._label(row.get(field)) for row in rows if row.get(field) is not None}
            if 1 < len(unique) <= self.max_categories * 2:
                candidates.append((len(unique), field))
        return min(candidates)[1] if candidates else None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float, Decimal)):
            return float(value)
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _label(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _time_sort_key(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
