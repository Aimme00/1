from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set

from .objects import SQLValidationResult, ValidationIssue, ValidationLevel


@dataclass(frozen=True)
class SQLValidatorConfig:
    dialect: str = "sqlite"
    max_rows: int = 5000
    require_sqlglot: bool = False


class SQLValidator:
    """SQLGlot AST 校验器；本地缺少依赖时仅允许显式的保守降级。"""

    FORBIDDEN_KEYWORDS = {
        "insert", "update", "delete", "drop", "alter", "truncate",
        "create", "replace", "attach", "detach", "pragma", "vacuum",
        "call", "execute", "merge", "copy", "grant", "revoke",
    }
    FORBIDDEN_FUNCTIONS = {
        "benchmark", "load_file", "pg_sleep", "randomblob", "readfile",
        "sleep", "sys_eval", "sys_exec", "writefile", "zeroblob",
    }
    MAX_JOINS = 8
    MAX_CTES = 8

    def __init__(self, config: Optional[SQLValidatorConfig] = None):
        self.config = config or SQLValidatorConfig()
        if self.config.max_rows <= 0:
            raise ValueError("max_rows 必须大于 0")

    def validate(
        self,
        sql: str,
        *,
        allowed_tables: Optional[Iterable[str]] = None,
        allowed_columns: Optional[Dict[str, Iterable[str]]] = None,
    ) -> SQLValidationResult:
        normalized_sql = sql.strip()
        result = SQLValidationResult(original_sql=sql, validated_sql=normalized_sql)
        if not normalized_sql:
            result.issues.append(ValidationIssue("empty_sql", "SQL 不能为空。"))
            return result

        self._check_risky_constructs(result, normalized_sql)
        if not result.is_valid:
            return result

        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            if self.config.require_sqlglot:
                result.parser = "unavailable"
                result.issues.append(
                    ValidationIssue(
                        "sqlglot_unavailable",
                        "生产模式要求安装 sqlglot，当前无法执行 AST 校验。",
                    )
                )
                return result
            return self._validate_conservative(
                normalized_sql,
                allowed_tables=set(allowed_tables or []),
            )

        result.parser = "sqlglot"
        try:
            statements = sqlglot.parse(normalized_sql, read=self.config.dialect)
        except Exception as exc:
            result.issues.append(
                ValidationIssue("parse_error", f"SQL 语法解析失败：{exc}")
            )
            return result

        if len(statements) != 1:
            result.issues.append(
                ValidationIssue("multiple_statements", "只允许执行一条 SQL。")
            )
            return result

        tree = statements[0]
        if not isinstance(tree, exp.Query):
            result.issues.append(
                ValidationIssue("not_readonly_query", "只允许 SELECT/只读 CTE 查询。")
            )

        forbidden_types = tuple(
            node_type
            for name in (
                "Insert", "Update", "Delete", "Drop", "Alter", "Create",
                "TruncateTable", "Merge", "Command", "Copy", "Grant", "Revoke",
            )
            if (node_type := getattr(exp, name, None)) is not None
        )
        if forbidden_types and any(tree.find(node_type) for node_type in forbidden_types):
            lowered = self._remove_comments(normalized_sql).lower()
            matched_keywords = sorted(
                keyword.upper()
                for keyword in self.FORBIDDEN_KEYWORDS
                if re.search(rf"\b{re.escape(keyword)}\b", lowered)
            )
            detail = "、".join(matched_keywords) or "写入、DDL 或管理语句"
            result.issues.append(
                ValidationIssue("forbidden_keyword", f"SQL 包含禁止关键字：{detail}。")
            )

        joins = list(tree.find_all(exp.Join))
        if len(joins) > self.MAX_JOINS:
            result.issues.append(
                ValidationIssue("too_many_joins", f"SQL 关联表过多，最多允许 {self.MAX_JOINS} 个 JOIN。")
            )
        for join in joins:
            kind = str(join.args.get("kind") or "").upper()
            if kind == "CROSS" or (
                not join.args.get("on")
                and not join.args.get("using")
                and kind not in {"NATURAL"}
            ):
                result.issues.append(
                    ValidationIssue("cartesian_join", "不允许 CROSS JOIN 或缺少关联条件的 JOIN。")
                )
                break
        if len(list(tree.find_all(exp.CTE))) > self.MAX_CTES:
            result.issues.append(
                ValidationIssue("too_many_ctes", f"SQL CTE 过多，最多允许 {self.MAX_CTES} 个。")
            )

        cte_names = {
            cte.alias_or_name.lower()
            for cte in tree.find_all(exp.CTE)
            if cte.alias_or_name
        }
        result.tables = {
            self._qualified_table_name(table)
            for table in tree.find_all(exp.Table)
            if table.name.lower() not in cte_names
        }
        select_aliases = {
            alias.alias.lower()
            for alias in tree.find_all(exp.Alias)
            if alias.alias
        }
        result.columns = {
            column.name
            for column in tree.find_all(exp.Column)
            if column.name
            and column.name != "*"
            and column.name.lower() not in select_aliases
        }
        self._check_allowlists(result, allowed_tables, allowed_columns)

        if allowed_tables is not None and not result.tables:
            result.issues.append(
                ValidationIssue("no_business_table", "SQL 必须引用本次授权的业务表。")
            )

        if result.is_valid:
            result.validated_sql = self._apply_ast_limit(tree)
        return result

    def _validate_conservative(
        self,
        sql: str,
        *,
        allowed_tables: Set[str],
    ) -> SQLValidationResult:
        """开发环境降级校验，不可替代生产 AST 校验。"""
        result = SQLValidationResult(
            original_sql=sql,
            validated_sql=sql,
            parser="conservative_fallback",
        )
        cleaned = self._remove_comments(sql)
        statements = self._split_statements(cleaned)
        if len(statements) != 1:
            result.issues.append(
                ValidationIssue("multiple_statements", "只允许执行一条 SQL。")
            )
            return result

        lowered = statements[0].lower().strip()
        if not re.match(r"^(select|with)\b", lowered):
            result.issues.append(
                ValidationIssue("not_readonly_query", "只允许 SELECT/只读 CTE 查询。")
            )
        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                result.issues.append(
                    ValidationIssue(
                        "forbidden_keyword",
                        f"SQL 包含禁止关键字：{keyword.upper()}。",
                    )
                )

        result.tables = {
            match.group(1).split(".")[-1].strip('`"').lower()
            for match in re.finditer(
                r"\b(?:from|join)\s+([`\"\w.]+)",
                lowered,
            )
        }
        if allowed_tables:
            allowed = {table.lower().split(".")[-1] for table in allowed_tables}
            denied = sorted(table for table in result.tables if table not in allowed)
            if denied:
                result.issues.append(
                    ValidationIssue(
                        "table_not_allowed",
                        f"SQL 访问了未授权表：{', '.join(denied)}。",
                    )
                )
            if not result.tables:
                result.issues.append(
                    ValidationIssue("no_business_table", "SQL 必须引用本次授权的业务表。")
                )
        result.issues.append(
            ValidationIssue(
                "ast_validation_degraded",
                "未安装 sqlglot，当前仅执行保守校验；不得用于生产环境。",
                level=ValidationLevel.WARNING,
            )
        )
        if result.is_valid:
            result.validated_sql = self._apply_fallback_limit(statements[0])
        return result

    def _check_allowlists(
        self,
        result: SQLValidationResult,
        allowed_tables: Optional[Iterable[str]],
        allowed_columns: Optional[Dict[str, Iterable[str]]],
    ) -> None:
        if allowed_tables is not None:
            allowed = {table.lower().split(".")[-1] for table in allowed_tables}
            denied = sorted(
                table for table in result.tables
                if table.lower().split(".")[-1] not in allowed
            )
            if denied:
                result.issues.append(
                    ValidationIssue(
                        "table_not_allowed",
                        f"SQL 访问了未授权表：{', '.join(denied)}。",
                    )
                )

        if allowed_columns is not None:
            allowed_column_names = {
                column.lower()
                for columns in allowed_columns.values()
                for column in columns
            }
            denied_columns = sorted(
                column for column in result.columns
                if column.lower() not in allowed_column_names
            )
            if denied_columns:
                result.issues.append(
                    ValidationIssue(
                        "column_not_allowed",
                        f"SQL 访问了未授权字段：{', '.join(denied_columns)}。",
                    )
                )

    def _apply_ast_limit(self, tree) -> str:
        limit = tree.args.get("limit")
        if limit is None:
            tree = tree.limit(self.config.max_rows)
        else:
            expression = getattr(limit, "expression", None)
            try:
                current_limit = int(expression.name)
            except (AttributeError, TypeError, ValueError):
                current_limit = self.config.max_rows
            if current_limit > self.config.max_rows:
                tree = tree.limit(self.config.max_rows)
        return tree.sql(dialect=self.config.dialect)

    def _check_risky_constructs(
        self,
        result: SQLValidationResult,
        sql: str,
    ) -> None:
        """AST 解析前后的共同防线，覆盖锁查询、危险函数和资源放大语句。"""
        cleaned = self._remove_comments(sql)
        normalized = re.sub(r"\s+", " ", self._strip_single_quoted_literals(cleaned)).lower()
        risky_clauses = (
            (r"\bfor\s+update\b", "locking_query", "不允许 SELECT ... FOR UPDATE 锁查询。"),
            (r"\block\s+in\s+share\s+mode\b", "locking_query", "不允许共享锁查询。"),
            (r"\binto\s+(?:out|dump)file\b", "file_access", "不允许 SQL 读写服务器文件。"),
            (r"\bcross\s+join\b", "cartesian_join", "不允许 CROSS JOIN。"),
            (r"\bwith\s+recursive\b", "recursive_query", "不允许递归 CTE。"),
        )
        for pattern, code, message in risky_clauses:
            if re.search(pattern, normalized):
                result.issues.append(ValidationIssue(code, message))

        for function in sorted(self.FORBIDDEN_FUNCTIONS):
            if re.search(rf"\b{re.escape(function)}\s*\(", normalized):
                result.issues.append(
                    ValidationIssue(
                        "forbidden_function",
                        f"SQL 包含禁止函数：{function.upper()}。",
                    )
                )

        join_count = len(re.findall(r"\bjoin\b", normalized))
        if join_count > self.MAX_JOINS:
            result.issues.append(
                ValidationIssue("too_many_joins", f"SQL 关联表过多，最多允许 {self.MAX_JOINS} 个 JOIN。")
            )

    @staticmethod
    def _strip_single_quoted_literals(sql: str) -> str:
        return re.sub(r"'(?:''|[^'])*'", "''", sql)

    def _apply_fallback_limit(self, sql: str) -> str:
        stripped = sql.strip().rstrip(";").strip()
        limit_match = re.search(r"\blimit\s+(\d+)\s*$", stripped, flags=re.I)
        if limit_match:
            if int(limit_match.group(1)) > self.config.max_rows:
                stripped = re.sub(
                    r"\blimit\s+\d+\s*$",
                    f"LIMIT {self.config.max_rows}",
                    stripped,
                    flags=re.I,
                )
        else:
            stripped += f" LIMIT {self.config.max_rows}"
        return stripped + ";"

    @staticmethod
    def _qualified_table_name(table) -> str:
        parts = [getattr(table, "catalog", ""), getattr(table, "db", ""), table.name]
        return ".".join(part for part in parts if part)

    @staticmethod
    def _remove_comments(sql: str) -> str:
        without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
        return re.sub(r"--[^\n]*", " ", without_block)

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        statements: list[str] = []
        buffer: list[str] = []
        quote: Optional[str] = None
        index = 0
        while index < len(sql):
            char = sql[index]
            if quote:
                buffer.append(char)
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        buffer.append(sql[index + 1])
                        index += 1
                    else:
                        quote = None
            elif char in {"'", '"', "`"}:
                quote = char
                buffer.append(char)
            elif char == ";":
                statement = "".join(buffer).strip()
                if statement:
                    statements.append(statement)
                buffer = []
            else:
                buffer.append(char)
            index += 1
        remainder = "".join(buffer).strip()
        if remainder:
            statements.append(remainder)
        return statements
