from __future__ import annotations

from typing import Protocol

from .objects import MCPExecutionRequest, MCPExecutionResult


class QueryExecutor(Protocol):
    """所有数据库执行器必须实现的统一接口。"""

    database: str

    def execute(self, request: MCPExecutionRequest) -> MCPExecutionResult:
        ...
