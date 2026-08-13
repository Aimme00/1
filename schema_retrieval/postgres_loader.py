from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from mcp_router import PostgresExecutorConfig

from .objects import ColumnSchema, TableRelation, TableSchema


@dataclass(frozen=True)
class PostgresConnectionReport:
    connected: bool
    database: str
    current_user: str = ""
    readonly_verified: bool = True
    select_granted: bool = True
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "database": self.database,
            "current_user": self.current_user,
            "readonly_verified": self.readonly_verified,
            "select_granted": self.select_granted,
            "warnings": list(self.warnings),
        }


class PostgresSchemaLoader:
    """从 PostgreSQL information_schema 读取公开业务表 Schema。"""

    def __init__(
        self,
        config: PostgresExecutorConfig,
        *,
        database_name: str = "trade_db",
        business_meta: Optional[Dict[str, Any]] = None,
        sample_size: int = 0,
    ):
        self.config = config
        self.database_name = database_name
        self.business_meta = business_meta or {}
        self.sample_size = max(0, min(int(sample_size), 10))

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("缺少 psycopg，请安装 requirements-web.txt") from exc
        return psycopg.connect(
            self.config.database_url,
            connect_timeout=self.config.connect_timeout,
            row_factory=dict_row,
        )

    def test_connection(self) -> PostgresConnectionReport:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT current_user AS current_user, current_database() AS database_name"
            ).fetchone()
        return PostgresConnectionReport(
            connected=True,
            database=str(row.get("database_name") or self.database_name),
            current_user=str(row.get("current_user") or ""),
            warnings=("所有 Agent SQL 均在 PostgreSQL 只读事务中执行",),
        )

    def load(self) -> Tuple[Dict[str, TableSchema], List[ColumnSchema], List[TableRelation]]:
        with self._connect() as connection:
            table_rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                  AND LEFT(table_name, 9) <> '_askdata_'
                ORDER BY table_name
                """
            ).fetchall()
            column_rows = connection.execute(
                """
                SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
                       COALESCE(tc.constraint_type = 'PRIMARY KEY', FALSE) AS is_primary
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage kcu
                  ON kcu.table_schema = c.table_schema
                 AND kcu.table_name = c.table_name
                 AND kcu.column_name = c.column_name
                LEFT JOIN information_schema.table_constraints tc
                  ON tc.constraint_schema = kcu.constraint_schema
                 AND tc.constraint_name = kcu.constraint_name
                 AND tc.constraint_type = 'PRIMARY KEY'
                WHERE c.table_schema = 'public'
                  AND LEFT(c.table_name, 9) <> '_askdata_'
                ORDER BY c.table_name, c.ordinal_position
                """
            ).fetchall()
            relation_rows = connection.execute(
                """
                SELECT tc.table_name, kcu.column_name,
                       ccu.table_name AS referenced_table_name,
                       ccu.column_name AS referenced_column_name,
                       tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.constraint_schema = kcu.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                  AND LEFT(tc.table_name, 9) <> '_askdata_'
                ORDER BY tc.table_name, tc.constraint_name
                """
            ).fetchall()

            primary_keys: Dict[str, List[str]] = {}
            for row in column_rows:
                if bool(row.get("is_primary")):
                    primary_keys.setdefault(str(row["table_name"]), []).append(
                        str(row["column_name"])
                    )

            tables: Dict[str, TableSchema] = {}
            for row in table_rows:
                table_name = str(row["table_name"])
                meta = self.business_meta.get(table_name, {})
                tables[table_name] = TableSchema(
                    database=self.database_name,
                    table_name=table_name,
                    description=meta.get("description", ""),
                    aliases=list(meta.get("aliases") or []),
                    primary_keys=primary_keys.get(table_name, []),
                )

            foreign_keys: Dict[tuple[str, str], str] = {}
            relations: List[TableRelation] = []
            for row in relation_rows:
                source_table = str(row["table_name"])
                source_column = str(row["column_name"])
                target_table = str(row["referenced_table_name"])
                target_column = str(row["referenced_column_name"])
                foreign_keys[(source_table, source_column)] = f"{target_table}.{target_column}"
                relations.append(
                    TableRelation(
                        database=self.database_name,
                        source_table=source_table,
                        source_column=source_column,
                        target_table=target_table,
                        target_column=target_column,
                        description=str(row.get("constraint_name") or ""),
                    )
                )

            columns: List[ColumnSchema] = []
            for row in column_rows:
                table_name = str(row["table_name"])
                if table_name not in tables:
                    continue
                column_name = str(row["column_name"])
                table_meta = self.business_meta.get(table_name, {})
                column_meta = (table_meta.get("columns") or {}).get(column_name, {})
                samples = self._samples(connection, table_name, column_name)
                is_primary = bool(row.get("is_primary"))
                foreign_key_ref = foreign_keys.get((table_name, column_name))
                columns.append(
                    ColumnSchema(
                        database=self.database_name,
                        table_name=table_name,
                        column_name=column_name,
                        data_type=str(row.get("data_type") or "UNKNOWN"),
                        nullable=str(row.get("is_nullable") or "YES").upper() == "YES",
                        description=column_meta.get("description", ""),
                        aliases=list(column_meta.get("aliases") or []),
                        table_description=table_meta.get("description", ""),
                        table_aliases=list(table_meta.get("aliases") or []),
                        samples=samples,
                        value_range=column_meta.get("value_range", ""),
                        data_distribution=column_meta.get("data_distribution", ""),
                        business_usage=column_meta.get("business_usage", ""),
                        semantic_role=column_meta.get("semantic_role")
                        or self._infer_role(column_name, str(row.get("data_type") or ""), is_primary, bool(foreign_key_ref)),
                        is_primary_key=is_primary,
                        foreign_key_ref=foreign_key_ref,
                    )
                )
        if not tables or not columns:
            raise RuntimeError("PostgreSQL Schema 为空，请先初始化演示数据")
        return tables, columns, relations

    def _samples(self, connection, table_name: str, column_name: str) -> List[str]:
        if not self.sample_size:
            return []
        if not table_name.replace("_", "").isalnum() or not column_name.replace("_", "").isalnum():
            return []
        try:
            rows = connection.execute(
                f'SELECT DISTINCT "{column_name}" AS value FROM "{table_name}" '
                f'WHERE "{column_name}" IS NOT NULL LIMIT %s',
                (self.sample_size,),
            ).fetchall()
            return [str(row["value"]) for row in rows]
        except Exception:
            return []

    @staticmethod
    def _infer_role(name: str, data_type: str, primary: bool, foreign: bool) -> str:
        lowered = name.lower()
        if primary or foreign:
            return "join_key"
        if any(term in lowered for term in ("date", "time", "created", "updated")):
            return "time"
        if any(term in data_type.lower() for term in ("int", "numeric", "real", "double")) and any(
            term in lowered for term in ("amount", "price", "count", "quantity", "rate", "total")
        ):
            return "metric"
        return "dimension"
