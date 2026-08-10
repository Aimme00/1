from __future__ import annotations

import json
from pathlib import Path

from mcp_router import MCPRouter, SQLiteMCPExecutor

from .create_demo_db import create_demo_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "runtime_data" / "trade_demo.db"


def main() -> None:
    """
    最小 SQL 执行环境 Demo。

    这个 Demo 不依赖大模型，也不依赖 Schema 检索，
    只用于验证：
    1. SQLite 测试库可以创建。
    2. MCP 路由可以把 SQL 发到 trade_db 执行。
    3. 当前示例 SQL 可以查出结果。
    """
    db_path = create_demo_db(DB_PATH)

    router = MCPRouter()
    router.register_executor(
        database="trade_db",
        executor=SQLiteMCPExecutor(
            database="trade_db",
            db_path=db_path,
            readonly=True,
        ),
    )

    sql = """
    SELECT interest_info.interest_rate
    FROM trade_summary
    JOIN interest_info
      ON trade_summary.user_id = interest_info.user_id
    WHERE trade_summary.total_trade_count > 50000;
    """

    request = {
        "database": "trade_db",
        "sql": sql,
    }

    result = router.execute(request)

    print("=" * 80)
    print("MCP 执行请求")
    print("=" * 80)
    print(json.dumps(request, ensure_ascii=False, indent=2))

    print("\n" + "=" * 80)
    print("MCP 执行结果")
    print("=" * 80)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
