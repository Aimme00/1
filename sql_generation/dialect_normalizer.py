from __future__ import annotations


def normalize_sql_for_dialect(sql: str, dialect: str) -> str:
    """Normalize safe, known SQL-dialect compatibility differences.

    PostgreSQL only supports ``round(value, digits)`` when ``value`` is
    ``numeric``. Model-generated queries commonly aggregate a
    ``double precision`` column and therefore need an explicit cast.

    This helper does not replace validation. If parsing fails, the original
    SQL is returned so the existing fail-closed validator can reject it.
    """

    normalized_dialect = (dialect or "").strip().lower()
    if normalized_dialect not in {"postgres", "postgresql"}:
        return sql

    try:
        import sqlglot
        from sqlglot import exp

        statements = sqlglot.parse(sql, read="postgres")
        if len(statements) != 1 or statements[0] is None:
            return sql

        statement = statements[0]
        changed = False
        for node in statement.find_all(exp.Round):
            if node.args.get("decimals") is None:
                continue
            value = node.this
            if isinstance(value, exp.Cast) and _is_numeric_type(value.args.get("to")):
                continue
            node.set(
                "this",
                exp.Cast(
                    this=value.copy(),
                    to=exp.DataType.build("NUMERIC"),
                ),
            )
            changed = True

        return statement.sql(dialect="postgres") if changed else sql
    except Exception:
        return sql


def _is_numeric_type(data_type: object) -> bool:
    rendered = str(data_type or "").upper()
    return "NUMERIC" in rendered or "DECIMAL" in rendered
