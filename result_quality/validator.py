from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Dict

from .objects import QualityIssue, QualityLevel, ResultQualityReport


class ResultQualityValidator:
    """在生成结论前检查查询结果是否为空、截断或结构异常。"""

    def validate(self, result: Dict[str, Any]) -> ResultQualityReport:
        success = bool(result.get("success"))
        rows = result.get("rows") or []
        columns = result.get("columns") or []
        reported_count = int(result.get("row_count") or 0)
        truncated = bool(result.get("truncated"))
        issues: list[QualityIssue] = []

        if not success:
            issues.append(
                QualityIssue(
                    "query_failed",
                    str(result.get("error") or "数据库查询失败。"),
                    QualityLevel.ERROR,
                )
            )
            return ResultQualityReport(
                usable=False,
                empty=True,
                truncated=truncated,
                row_count=0,
                issues=issues,
            )

        actual_count = len(rows)
        if "row_count" in result and reported_count != actual_count:
            issues.append(
                QualityIssue(
                    "row_count_mismatch",
                    "返回行数元信息与实际数据不一致，已按实际数据分析。",
                    QualityLevel.WARNING,
                    {"reported": reported_count, "actual": actual_count},
                )
            )

        if not rows:
            issues.append(
                QualityIssue(
                    "no_data",
                    "当前查询条件下没有数据。",
                    QualityLevel.INFO,
                )
            )

        if rows and not columns:
            issues.append(
                QualityIssue(
                    "missing_columns",
                    "查询返回了数据，但没有字段元信息。",
                    QualityLevel.ERROR,
                )
            )

        malformed_rows = [index for index, row in enumerate(rows) if not isinstance(row, dict)]
        if malformed_rows:
            issues.append(
                QualityIssue(
                    "malformed_rows",
                    "部分查询结果不是对象结构，无法可靠分析。",
                    QualityLevel.ERROR,
                    {"row_indexes": malformed_rows[:20]},
                )
            )

        invalid_numeric_cells = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for column, value in row.items():
                if isinstance(value, (float, Decimal)):
                    try:
                        if not math.isfinite(float(value)):
                            invalid_numeric_cells.append(
                                {"row": row_index, "column": column}
                            )
                    except (TypeError, ValueError, OverflowError):
                        invalid_numeric_cells.append({"row": row_index, "column": column})
        if invalid_numeric_cells:
            issues.append(
                QualityIssue(
                    "invalid_numeric_values",
                    "结果中包含 NaN 或无限值，相关统计可能不可靠。",
                    QualityLevel.WARNING,
                    {"cells": invalid_numeric_cells[:20]},
                )
            )

        if truncated:
            issues.append(
                QualityIssue(
                    "result_truncated",
                    "查询结果已达到返回上限，结论仅基于已返回的数据。",
                    QualityLevel.WARNING,
                )
            )

        usable = not any(issue.level == QualityLevel.ERROR for issue in issues)
        return ResultQualityReport(
            usable=usable,
            empty=not rows,
            truncated=truncated,
            row_count=actual_count,
            issues=issues,
        )
