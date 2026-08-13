from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from .objects import MCPExecutionRequest, MCPExecutionResult
from .readonly_guard import is_obviously_readonly_sql


@dataclass(frozen=True)
class PostgresExecutorConfig:
    database_url: str
    connect_timeout: int = 10
    statement_timeout_ms: int = 30_000
    max_rows: int = 5000

    @classmethod
    def from_env(cls) -> "PostgresExecutorConfig":
        database_url = (
            os.getenv("ASKDATA_POSTGRES_URL", "").strip()
            or os.getenv("POSTGRES_URL", "").strip()
            or os.getenv("DATABASE_URL", "").strip()
        )
        if not database_url:
            raise ValueError("缺少 PostgreSQL 环境变量：ASKDATA_POSTGRES_URL 或 DATABASE_URL")
        return cls(
            database_url=database_url,
            connect_timeout=max(1, int(os.getenv("ASKDATA_POSTGRES_CONNECT_TIMEOUT", "10"))),
            statement_timeout_ms=max(
                1,
                min(int(os.getenv("ASKDATA_POSTGRES_STATEMENT_TIMEOUT_MS", "30000")), 300_000),
            ),
            max_rows=max(1, min(int(os.getenv("ASKDATA_POSTGRES_MAX_ROWS", "5000")), 20_000)),
        )


class PostgresQueryExecutor:
    """PostgreSQL 查询执行器；每次查询都强制使用只读事务和超时。"""

    def __init__(
        self,
        config: PostgresExecutorConfig,
        *,
        database_alias: Optional[str] = None,
    ):
        self.config = config
        self.database = database_alias or "trade_db"

    def execute(self, request: MCPExecutionRequest) -> MCPExecutionResult:
        if request.database != self.database:
            return MCPExecutionResult(
                database=request.database,
                sql=request.sql,
                success=False,
                error=f"数据库路由错误：当前执行器只处理 {self.database}",
            )
        if not is_obviously_readonly_sql(request.sql):
            return MCPExecutionResult(
                database=request.database,
                sql=request.sql,
                success=False,
                error="只允许执行单条 SELECT/只读 CTE 查询。",
            )

        started_at = time.monotonic()
        connection = None
        try:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("缺少 psycopg，请安装 requirements-web.txt") from exc

            connection = psycopg.connect(
                self.config.database_url,
                connect_timeout=self.config.connect_timeout,
                row_factory=dict_row,
            )
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(self.config.statement_timeout_ms),),
                )
                cursor = connection.execute(request.sql)
                rows = cursor.fetchmany(self.config.max_rows + 1)
                truncated = len(rows) > self.config.max_rows
                rows = rows[: self.config.max_rows]
                columns = list(rows[0].keys()) if rows else [
                    item.name for item in (cursor.description or [])
                ]

            return MCPExecutionResult(
                database=request.database,
                sql=request.sql,
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        except Exception as exc:
            return MCPExecutionResult(
                database=request.database,
                sql=request.sql,
                success=False,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error=str(exc)[:500],
            )
        finally:
            if connection is not None:
                connection.close()
