from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.data_source import (
    DataSourceManager,
    DataSourceSettings,
    DataSourceUnavailableError,
)
from mcp_router import MySQLExecutorConfig
from schema_retrieval import MySQLSchemaLoader


class FakeCursor:
    def __init__(self, *, unsafe_grant: bool = False):
        self.unsafe_grant = unsafe_grant
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split()).upper()
        if normalized.startswith("SHOW GRANTS"):
            privileges = "SELECT, UPDATE" if self.unsafe_grant else "SELECT"
            self.rows = [
                {
                    "grant": (
                        f"GRANT {privileges} ON `analytics`.* "
                        "TO `askdata_readonly`@`%`"
                    )
                }
            ]
        elif "CURRENT_USER()" in normalized:
            self.rows = [
                {"current_user": "askdata_readonly@%", "database_name": "analytics"}
            ]
        elif "INFORMATION_SCHEMA.TABLES" in normalized:
            self.rows = [
                {"TABLE_NAME": "customers", "TABLE_COMMENT": "客户主数据"},
                {"TABLE_NAME": "orders", "TABLE_COMMENT": "订单事实表"},
            ]
        elif "INFORMATION_SCHEMA.COLUMNS" in normalized:
            self.rows = [
                {
                    "TABLE_NAME": "customers",
                    "COLUMN_NAME": "customer_id",
                    "COLUMN_TYPE": "bigint",
                    "IS_NULLABLE": "NO",
                    "COLUMN_KEY": "PRI",
                    "COLUMN_COMMENT": "客户ID",
                },
                {
                    "TABLE_NAME": "orders",
                    "COLUMN_NAME": "order_id",
                    "COLUMN_TYPE": "bigint",
                    "IS_NULLABLE": "NO",
                    "COLUMN_KEY": "PRI",
                    "COLUMN_COMMENT": "订单ID",
                },
                {
                    "TABLE_NAME": "orders",
                    "COLUMN_NAME": "customer_id",
                    "COLUMN_TYPE": "bigint",
                    "IS_NULLABLE": "NO",
                    "COLUMN_KEY": "MUL",
                    "COLUMN_COMMENT": "客户ID",
                },
                {
                    "TABLE_NAME": "orders",
                    "COLUMN_NAME": "total_amount",
                    "COLUMN_TYPE": "decimal(18,2)",
                    "IS_NULLABLE": "NO",
                    "COLUMN_KEY": "",
                    "COLUMN_COMMENT": "订单金额",
                },
            ]
        elif "INFORMATION_SCHEMA.KEY_COLUMN_USAGE" in normalized:
            self.rows = [
                {
                    "TABLE_NAME": "orders",
                    "COLUMN_NAME": "customer_id",
                    "REFERENCED_TABLE_NAME": "customers",
                    "REFERENCED_COLUMN_NAME": "customer_id",
                    "CONSTRAINT_NAME": "fk_orders_customer",
                }
            ]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, *, unsafe_grant: bool = False):
        self.unsafe_grant = unsafe_grant
        self.closed = False

    def cursor(self):
        return FakeCursor(unsafe_grant=self.unsafe_grant)

    def close(self):
        self.closed = True


class MySQLSchemaLoaderTestCase(unittest.TestCase):
    def make_loader(self, *, unsafe_grant: bool = False) -> MySQLSchemaLoader:
        config = MySQLExecutorConfig(
            host="db.internal",
            user="askdata_readonly",
            password="secret",
            database="analytics",
        )
        return MySQLSchemaLoader(
            config,
            database_name="business_db",
            connection_factory=lambda: FakeConnection(unsafe_grant=unsafe_grant),
        )

    def test_connection_report_verifies_select_only_grant(self) -> None:
        report = self.make_loader().test_connection()
        self.assertTrue(report.connected)
        self.assertTrue(report.select_granted)
        self.assertTrue(report.readonly_verified)
        self.assertEqual(report.database, "analytics")

    def test_write_privilege_is_reported_as_unsafe(self) -> None:
        report = self.make_loader(unsafe_grant=True).test_connection()
        self.assertFalse(report.readonly_verified)
        self.assertIn("UPDATE", report.warnings[0])

    def test_schema_loader_builds_tables_columns_and_relations(self) -> None:
        tables, columns, relations = self.make_loader().load()
        self.assertEqual(set(tables), {"customers", "orders"})
        self.assertEqual(tables["orders"].primary_keys, ["order_id"])
        amount = next(item for item in columns if item.column_name == "total_amount")
        customer_key = next(
            item
            for item in columns
            if item.table_name == "orders" and item.column_name == "customer_id"
        )
        self.assertEqual(amount.semantic_role, "metric")
        self.assertEqual(customer_key.foreign_key_ref, "customers.customer_id")
        self.assertEqual(relations[0].source_table, "orders")
        self.assertEqual(relations[0].source_column, "customer_id")
        self.assertEqual(relations[0].target_table, "customers")
        self.assertEqual(relations[0].target_column, "customer_id")

    def test_identifier_quoting_rejects_untrusted_names(self) -> None:
        with self.assertRaises(ValueError):
            MySQLSchemaLoader._quote_identifier("orders; DROP TABLE users")


class DataSourceManagerTestCase(unittest.TestCase):
    def test_sqlite_mode_is_ready_and_can_resync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DataSourceManager(
                temp_dir,
                DataSourceSettings(
                    database_type="sqlite",
                    database_alias="trade_db",
                ),
            )
            status = manager.status()
            self.assertTrue(status["ready"])
            self.assertTrue(status["readonly_verified"])
            self.assertEqual(status["table_count"], 6)
            self.assertGreater(status["column_count"], 10)
            self.assertEqual(manager.test_connection()["database_type"], "sqlite")
            self.assertTrue(manager.sync()["ready"])
            self.assertIsNotNone(manager.get_pipeline())

    def test_mysql_configuration_failure_never_falls_back_to_demo_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {}, clear=True
        ):
            manager = DataSourceManager(
                temp_dir,
                DataSourceSettings(
                    database_type="mysql",
                    database_alias="analytics",
                    enforce_readonly=True,
                    require_sqlglot=True,
                ),
            )
        status = manager.status()
        self.assertEqual(status["database_type"], "mysql")
        self.assertFalse(status["ready"])
        self.assertIn("缺少 MySQL 环境变量", status["error"])
        with self.assertRaises(DataSourceUnavailableError):
            manager.get_pipeline()


if __name__ == "__main__":
    unittest.main()
