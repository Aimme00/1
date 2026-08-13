from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from env_settings import env_int, env_text
from .objects import MCPExecutionRequest, MCPExecutionResult
from .readonly_guard import is_obviously_readonly_sql


@dataclass(frozen=True)
class MySQLExecutorConfig:
    host: str
    user: str
    password: str
    database: str
    port: int = 3306
    connect_timeout: int = 10
    read_timeout: int = 30
    max_execution_time_ms: int = 30_000
    max_rows: int = 5000
    charset: str = "utf8mb4"

    @classmethod
    def from_env(cls, prefix: str = "ASKDATA_MYSQL_") -> "MySQLExecutorConfig":
        values = {
            "host": env_text(f"{prefix}HOST"),
            "user": env_text(f"{prefix}USER"),
            "password": env_text(f"{prefix}PASSWORD", strip=False),
            "database": env_text(f"{prefix}DATABASE"),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"缺少 MySQL 环境变量：{', '.join(missing)}")
        return cls(
            **values,
            port=env_int(f"{prefix}PORT", 3306, minimum=1, maximum=65535),
            connect_timeout=env_int(f"{prefix}CONNECT_TIMEOUT", 10, minimum=1),
            read_timeout=env_int(f"{prefix}READ_TIMEOUT", 30, minimum=1),
            max_execution_time_ms=env_int(
                f"{prefix}MAX_EXECUTION_TIME_MS", 30_000, minimum=1, maximum=300_000
            ),
            max_rows=env_int(f"{prefix}MAX_ROWS", 5000, minimum=1, maximum=20_000),
        )


class MySQLQueryExecutor:
    """MySQL 只读查询执行器；数据库账号本身也必须只有 SELECT 权限。"""

    def __init__(
        self,
        config: MySQLExecutorConfig,
        *,
        database_alias: Optional[str] = None,
    ):
        self.config = config
        self.database = database_alias or config.database

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
                import pymysql
                from pymysql.cursors import DictCursor
            except ImportError as exc:
                raise RuntimeError(
                    "缺少 PyMySQL，请先安装生产依赖：pip install PyMySQL"
                ) from exc

            connection = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=self.config.database,
                charset=self.config.charset,
                cursorclass=DictCursor,
                connect_timeout=self.config.connect_timeout,
                read_timeout=self.config.read_timeout,
                write_timeout=self.config.connect_timeout,
                autocommit=True,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET SESSION MAX_EXECUTION_TIME = %s",
                    (max(1, min(self.config.max_execution_time_ms, 300_000)),),
                )
                cursor.execute(request.sql)
                rows = cursor.fetchmany(self.config.max_rows + 1)
                truncated = len(rows) > self.config.max_rows
                rows = rows[: self.config.max_rows]
                columns = list(rows[0].keys()) if rows else [
                    item[0] for item in (cursor.description or [])
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
                error=str(exc),
            )
        finally:
            if connection is not None:
                connection.close()
