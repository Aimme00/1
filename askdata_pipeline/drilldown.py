from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence


def build_drill_actions(
    *,
    columns: Sequence[str],
    rows: Sequence[Any],
) -> List[Dict[str, str]]:
    """根据查询结果提供确定性的下钻/上卷动作，不让前端拼接 SQL。"""
    normalized = {str(column).lower(): index for index, column in enumerate(columns)}
    actions: List[Dict[str, str]] = []

    if "region" in normalized:
        region = _first_value(rows, columns, "region", normalized["region"])
        safe_region = _safe_dimension_value(region)
        if safe_region:
            actions.append(
                {
                    "id": "region_to_product",
                    "direction": "down",
                    "label": f"下钻 {safe_region} 的产品销售额",
                    "query": f"查询{safe_region}区域各产品销售额排名",
                }
            )

    temporal_columns = ("order_date", "sales_date")
    if any(column in normalized for column in temporal_columns):
        actions.append(
            {
                "id": "day_to_month",
                "direction": "up",
                "label": "上卷到月度销售额",
                "query": "按月汇总最近180天销售额趋势",
            }
        )

    if "product_name" in normalized:
        product = _first_value(
            rows,
            columns,
            "product_name",
            normalized["product_name"],
        )
        safe_product = _safe_dimension_value(product)
        if safe_product:
            actions.append(
                {
                    "id": "product_to_region",
                    "direction": "down",
                    "label": f"下钻 {safe_product} 的区域分布",
                    "query": f"查询产品{safe_product}在各区域的销售额排名",
                }
            )

    return actions[:3]


def _first_value(
    rows: Sequence[Any],
    columns: Sequence[str],
    key: str,
    index: int,
) -> Any:
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row.get(key)
    if isinstance(row, (list, tuple)) and index < len(row):
        return row[index]
    return None


def _safe_dimension_value(value: Any) -> str:
    text = str(value or "").strip()[:40]
    if not text or not re.fullmatch(r"[\w\u3400-\u9fff .\-]+", text):
        return ""
    return text
