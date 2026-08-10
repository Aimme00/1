from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcp_router import MySQLExecutorConfig

from .objects import ColumnSchema, TableRelation, TableSchema


WRITE_PRIVILEGES = {
    "ALL PRIVILEGES",
    "ALTER",
    "CREATE",
    "DELETE",
    "DROP",
    "EVENT",
    "EXECUTE",
    "FILE",
    "GRANT OPTION",
    "INDEX",
    "INSERT",
    "LOCK TABLES",
    "PROCESS",
    "REFERENCES",
    "RELOAD",
    "REPLICATION CLIENT",
    "REPLICATION SLAVE",
    "SHUTDOWN",
    "SUPER",
    "TRIGGER",
    "UPDATE",
}


@dataclass(frozen=True)
class MySQLConnectionReport:
    connected: bool
    database: str
    current_user: str = ""
    readonly_verified: bool = False
    select_granted: bool = False
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


class MySQLSchemaLoader:
    """从 information_schema 读取表、字段和外键，不读取业务行数据。"""

    def __init__(
        self,
        config: MySQLExecutorConfig,
        *,
        database_name: Optional[str] = None,
        business_meta: Optional[Dict[str, Any]] = None,
        sample_size: int = 0,
        connection_factory: Optional[Callable[[], object]] = None,
    ):
        self.config = config
        self.database_name = database_name or config.database
        self.business_meta = business_meta or {}
        self.sample_size = max(0, min(int(sample_size), 10))
        self.connection_factory = connection_factory

    def test_connection(self) -> MySQLConnectionReport:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT CURRENT_USER() AS current_user, DATABASE() AS database_name")
                identity = cursor.fetchone() or {}
                cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
                grant_rows = cursor.fetchall() or []
            grants = [str(next(iter(row.values()), "")) for row in grant_rows]
            granted_privileges = set()
            for grant in grants:
                match = re.search(r"\bGRANT\s+(.+?)\s+ON\s+", grant, flags=re.IGNORECASE)
                if not match:
                    continue
                granted_privileges.update(
                    item.strip().upper() for item in match.group(1).split(",")
                )
            write_grants = sorted(WRITE_PRIVILEGES & granted_privileges)
            select_granted = bool(
                {"SELECT", "ALL", "ALL PRIVILEGES"} & granted_privileges
            )
            warnings: List[str] = []
            if write_grants:
                warnings.append("数据库账号包含写入或管理权限：" + ", ".join(write_grants))
            if not select_granted:
                warnings.append("未能从 SHOW GRANTS 明确确认 SELECT 权限，可能通过角色继承")
            return MySQLConnectionReport(
                connected=True,
                database=str(identity.get("database_name") or self.config.database),
                current_user=str(identity.get("current_user") or ""),
                readonly_verified=select_granted and not write_grants,
                select_granted=select_granted,
                warnings=tuple(warnings),
            )
        finally:
            connection.close()

    def load(self) -> Tuple[Dict[str, TableSchema], List[ColumnSchema], List[TableRelation]]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT TABLE_NAME, TABLE_COMMENT
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                    ORDER BY TABLE_NAME
                    """,
                    (self.config.database,),
                )
                table_rows = cursor.fetchall() or []
                cursor.execute(
                    """
                    SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                           COLUMN_KEY, COLUMN_COMMENT
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = %s
                    ORDER BY TABLE_NAME, ORDINAL_POSITION
                    """,
                    (self.config.database,),
                )
                column_rows = cursor.fetchall() or []
                cursor.execute(
                    """
                    SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME,
                           REFERENCED_COLUMN_NAME, CONSTRAINT_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = %s
                      AND REFERENCED_TABLE_NAME IS NOT NULL
                    ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
                    """,
                    (self.config.database,),
                )
                relation_rows = cursor.fetchall() or []

                tables = self._build_tables(table_rows, column_rows)
                relations, foreign_keys = self._build_relations(relation_rows)
                columns = self._build_columns(cursor, column_rows, foreign_keys, tables)
            if not tables or not columns:
                raise RuntimeError("MySQL Schema 为空，请确认数据库名和 information_schema 读取权限")
            return tables, columns, relations
        finally:
            connection.close()

    def _build_tables(self, table_rows, column_rows) -> Dict[str, TableSchema]:
        primary_keys: Dict[str, List[str]] = {}
        for row in column_rows:
            if str(row.get("COLUMN_KEY") or "").upper() == "PRI":
                primary_keys.setdefault(str(row["TABLE_NAME"]), []).append(
                    str(row["COLUMN_NAME"])
                )
        tables: Dict[str, TableSchema] = {}
        for row in table_rows:
            table_name = str(row["TABLE_NAME"])
            meta = self.business_meta.get(table_name, {})
            tables[table_name] = TableSchema(
                database=self.database_name,
                table_name=table_name,
                description=meta.get("description") or str(row.get("TABLE_COMMENT") or ""),
                aliases=list(meta.get("aliases") or []),
                primary_keys=primary_keys.get(table_name, []),
            )
        return tables

    def _build_relations(self, rows):
        relations: List[TableRelation] = []
        foreign_keys: Dict[Tuple[str, str], str] = {}
        for row in rows:
            source_table = str(row["TABLE_NAME"])
            source_column = str(row["COLUMN_NAME"])
            target_table = str(row["REFERENCED_TABLE_NAME"])
            target_column = str(row["REFERENCED_COLUMN_NAME"])
            foreign_keys[(source_table, source_column)] = f"{target_table}.{target_column}"
            relations.append(
                TableRelation(
                    database=self.database_name,
                    source_table=source_table,
                    source_column=source_column,
                    target_table=target_table,
                    target_column=target_column,
                    description=str(row.get("CONSTRAINT_NAME") or ""),
                )
            )
        return relations, foreign_keys

    def _build_columns(self, cursor, rows, foreign_keys, tables):
        columns: List[ColumnSchema] = []
        for row in rows:
            table_name = str(row["TABLE_NAME"])
            column_name = str(row["COLUMN_NAME"])
            table_meta = self.business_meta.get(table_name, {})
            column_meta = (table_meta.get("columns") or {}).get(column_name, {})
            data_type = str(row.get("COLUMN_TYPE") or "UNKNOWN")
            is_primary = str(row.get("COLUMN_KEY") or "").upper() == "PRI"
            foreign_key_ref = foreign_keys.get((table_name, column_name))
            columns.append(
                ColumnSchema(
                    database=self.database_name,
                    table_name=table_name,
                    column_name=column_name,
                    data_type=data_type,
                    nullable=str(row.get("IS_NULLABLE") or "YES").upper() == "YES",
                    description=column_meta.get("description")
                    or str(row.get("COLUMN_COMMENT") or ""),
                    aliases=list(column_meta.get("aliases") or []),
                    table_description=table_meta.get("description")
                    or tables[table_name].description,
                    table_aliases=list(table_meta.get("aliases") or []),
                    samples=self._get_samples(cursor, table_name, column_name),
                    business_usage=column_meta.get("business_usage", ""),
                    semantic_role=column_meta.get("semantic_role")
                    or self._infer_semantic_role(
                        column_name,
                        data_type,
                        is_primary,
                        bool(foreign_key_ref),
                    ),
                    is_primary_key=is_primary,
                    foreign_key_ref=foreign_key_ref,
                )
            )
        return columns

    def _get_samples(self, cursor, table_name: str, column_name: str) -> List[str]:
        if not self.sample_size:
            return []
        table = self._quote_identifier(table_name)
        column = self._quote_identifier(column_name)
        try:
            cursor.execute(
                f"SELECT DISTINCT {column} AS value FROM {table} "
                f"WHERE {column} IS NOT NULL LIMIT %s",
                (self.sample_size,),
            )
            return [
                str(row["value"])
                for row in (cursor.fetchall() or [])
                if row.get("value") is not None
            ]
        except Exception:
            return []

    def _connect(self):
        if self.connection_factory is not None:
            return self.connection_factory()
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError("缺少 PyMySQL，请安装 requirements-core.txt") from exc
        return pymysql.connect(
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

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        if not identifier or not re.fullmatch(r"[A-Za-z0-9_$]+", identifier):
            raise ValueError(f"不安全的 MySQL 标识符：{identifier}")
        return f"`{identifier}`"

    @staticmethod
    def _infer_semantic_role(
        column_name: str,
        data_type: str,
        is_primary: bool,
        is_foreign: bool,
    ) -> str:
        lowered = column_name.lower()
        if is_primary or is_foreign:
            return "join_key"
        if any(term in lowered for term in ("date", "time", "created", "updated")):
            return "time"
        numeric = any(
            term in data_type.lower()
            for term in ("int", "decimal", "numeric", "float", "double")
        )
        if numeric and any(
            term in lowered
            for term in ("amount", "price", "count", "quantity", "rate", "total")
        ):
            return "metric"
        return "dimension"
