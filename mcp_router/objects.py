from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MCPExecutionRequest:
    """
    MCP 执行请求。

    database 用于路由到对应数据库连接。
    sql 是当前步骤生成的 SQL。
    """

    database: str
    sql: str


@dataclass
class MCPExecutionResult:
    """
    MCP 执行结果。
    """

    database: str
    sql: str
    success: bool
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "database": self.database,
            "sql": self.sql,
            "success": self.success,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }
