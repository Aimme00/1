from __future__ import annotations

import re


FORBIDDEN_SQL_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "truncate", "create",
    "replace", "attach", "detach", "pragma", "vacuum", "call", "execute",
    "merge", "copy", "grant", "revoke",
}

FORBIDDEN_SQL_PATTERNS = (
    r"\bfor\s+update\b",
    r"\block\s+in\s+share\s+mode\b",
    r"\binto\s+(?:out|dump)file\b",
    r"\bcross\s+join\b",
    r"\bwith\s+recursive\b",
    r"\b(?:benchmark|load_file|pg_sleep|randomblob|readfile|sleep|sys_eval|sys_exec|writefile|zeroblob)\s*\(",
)


def is_obviously_readonly_sql(sql: str) -> bool:
    """执行器的第二道保守防线；主要安全边界仍是 AST 校验和只读账号。"""
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned).strip()
    without_trailing_semicolon = cleaned.rstrip(";").strip()
    if ";" in without_trailing_semicolon:
        return False
    normalized = re.sub(r"\s+", " ", without_trailing_semicolon).lower()
    if not re.match(r"^(select|with)\b", normalized):
        return False
    if any(re.search(pattern, normalized) for pattern in FORBIDDEN_SQL_PATTERNS):
        return False
    return not any(
        re.search(rf"\b{re.escape(keyword)}\b", normalized)
        for keyword in FORBIDDEN_SQL_KEYWORDS
    )
