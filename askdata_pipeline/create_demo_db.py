from __future__ import annotations

import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "runtime_data" / "trade_demo.db"
DEFAULT_SQL_PATH = PROJECT_ROOT / "sql" / "create_trade_demo.sql"


def create_demo_db(
    db_path: str | Path = DEFAULT_DB_PATH,
    sql_path: str | Path = DEFAULT_SQL_PATH,
) -> Path:
    """
    创建用于 Text2SQL 端到端测试的 SQLite 数据库。

    数据库包含：
    - trade_summary：用户交易汇总表
    - interest_info：用户利率信息表
    - customers：客户区域维度表
    - products：商品维度表
    - orders：订单事实表
    - order_items：订单商品明细表
    """
    db_path = Path(db_path)
    sql_path = Path(sql_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        db_path.unlink()

    sql_text = sql_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(sql_text)
    conn.commit()
    conn.close()

    return db_path


def main() -> None:
    db_path = create_demo_db()
    print(f"已创建测试数据库: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    print("\ntrade_summary 数据：")
    for row in conn.execute("SELECT * FROM trade_summary ORDER BY user_id"):
        print(dict(row))

    print("\ninterest_info 数据：")
    for row in conn.execute("SELECT * FROM interest_info ORDER BY user_id"):
        print(dict(row))

    print("\n验证SQL结果：")
    sql = """
    SELECT interest_info.interest_rate
    FROM trade_summary
    JOIN interest_info
      ON trade_summary.user_id = interest_info.user_id
    WHERE trade_summary.total_trade_count > 50000;
    """
    for row in conn.execute(sql):
        print(dict(row))

    conn.close()


if __name__ == "__main__":
    main()
