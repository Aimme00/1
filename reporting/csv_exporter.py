from __future__ import annotations

import csv
import io
from typing import Any, Dict


def export_csv(result: Dict[str, Any]) -> bytes:
    """将 Agent 结果表导出为 UTF-8 BOM CSV，可直接用 Excel 打开。"""
    table = result.get("table") or {}
    columns = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([_cell_value(column) for column in columns])
    for row in rows:
        if isinstance(row, dict):
            writer.writerow([_cell_value(row.get(column)) for column in columns])
        elif isinstance(row, (list, tuple)):
            writer.writerow([_cell_value(value) for value in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _cell_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        import json

        value = json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        # Excel/LibreOffice 会把这些前缀解释为公式。前置单引号强制按文本打开。
        return "'" + value
    return value
