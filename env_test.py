#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Text2SQL 项目环境测试脚本

用法：
    python test_text2sql_env.py

这个脚本会检查：
1. Python 版本
2. 常用依赖是否安装
3. SQLite 是否可用
4. 能否创建测试数据库并解析 Schema
5. 能否执行一个最小版字段检索流程
"""

from __future__ import annotations

import sys
import sqlite3
import tempfile
import importlib.util
from pathlib import Path
from collections import Counter
import re
import math


def print_title(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def check_python_version() -> bool:
    print_title("1. Python 版本检查")

    version = sys.version_info
    print(f"当前 Python 版本: {version.major}.{version.minor}.{version.micro}")

    if version.major == 3 and version.minor >= 9:
        print("✅ Python 版本满足建议要求：>= 3.9")
        return True

    print("⚠️ 建议使用 Python 3.9 或以上版本")
    return False


def check_package(package_name: str, import_name: str | None = None, required: bool = False) -> bool:
    name = import_name or package_name
    spec = importlib.util.find_spec(name)

    if spec is not None:
        print(f"✅ {package_name} 已安装")
        return True

    if required:
        print(f"❌ {package_name} 未安装，建议执行: pip install {package_name}")
    else:
        print(f"⚠️ {package_name} 未安装，可选安装: pip install {package_name}")

    return False


def check_dependencies() -> dict:
    print_title("2. 依赖检查")

    results = {
        "numpy": check_package("numpy", required=True),
        "sentence-transformers": check_package("sentence-transformers", "sentence_transformers", required=False),
        "torch": check_package("torch", required=False),
        "sklearn": check_package("scikit-learn", "sklearn", required=False),
    }

    print("\n建议安装命令：")
    print("pip install numpy sentence-transformers torch scikit-learn")

    return results


def check_sqlite() -> bool:
    print_title("3. SQLite 检查")

    try:
        print(f"SQLite 版本: {sqlite3.sqlite_version}")

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO test_table (name) VALUES (?)", ("hello",))
        row = conn.execute("SELECT name FROM test_table WHERE id = 1").fetchone()
        conn.close()

        assert row[0] == "hello"
        print("✅ SQLite 创建表、写入、查询均正常")
        return True

    except Exception as exc:
        print(f"❌ SQLite 测试失败: {exc}")
        return False


def create_demo_database() -> Path:
    temp_dir = Path(tempfile.gettempdir())
    db_path = temp_dir / "text2sql_env_test.db"

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            city TEXT,
            gender TEXT,
            register_time TEXT
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            order_amount REAL NOT NULL,
            order_time TEXT NOT NULL,
            status TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        """
    )

    conn.executemany(
        "INSERT INTO users (user_id, city, gender, register_time) VALUES (?, ?, ?, ?)",
        [
            (1, "北京", "男", "2024-01-01"),
            (2, "上海", "女", "2024-02-01"),
            (3, "深圳", "女", "2024-03-01"),
        ],
    )

    conn.executemany(
        "INSERT INTO orders (order_id, user_id, order_amount, order_time, status) VALUES (?, ?, ?, ?, ?)",
        [
            (101, 1, 99.0, "2024-04-01", "已支付"),
            (102, 1, 199.0, "2024-04-02", "已支付"),
            (103, 2, 88.0, "2024-04-02", "已取消"),
            (104, 3, 299.0, "2024-04-03", "已支付"),
        ],
    )

    conn.commit()
    conn.close()

    return db_path


def load_schema_from_sqlite(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    tables = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()

    columns = []

    for table in tables:
        table_name = table["name"]
        table_info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        fk_info = conn.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()

        fk_map = {
            fk["from"]: f'{fk["table"]}.{fk["to"]}'
            for fk in fk_info
        }

        for col in table_info:
            col_name = col["name"]

            sample_sql = f'''SELECT DISTINCT "{col_name}" AS value
FROM "{table_name}"
WHERE "{col_name}" IS NOT NULL
LIMIT 5'''

            sample_rows = conn.execute(sample_sql).fetchall()
            samples = [str(row["value"]) for row in sample_rows]

            columns.append(
                {
                    "table": table_name,
                    "column": col_name,
                    "type": col["type"],
                    "is_pk": bool(col["pk"]),
                    "fk_ref": fk_map.get(col_name),
                    "samples": samples,
                }
            )

    conn.close()
    return columns


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-zA-Z0-9_]+", text)

    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.append(span)
        tokens.extend(list(span))
        for i in range(len(span) - 1):
            tokens.append(span[i:i + 2])

    return tokens


def bm25_search(query: str, docs: list[str], top_k: int = 5) -> list[tuple[int, float]]:
    tokenized_docs = [tokenize(doc) for doc in docs]
    query_terms = tokenize(query)

    n_docs = len(docs)
    avgdl = sum(len(doc) for doc in tokenized_docs) / max(n_docs, 1)

    term_freqs = [Counter(doc) for doc in tokenized_docs]
    doc_freq = Counter()

    for doc in tokenized_docs:
        for term in set(doc):
            doc_freq[term] += 1

    scores = []

    k1 = 1.5
    b = 0.75

    for idx, tf in enumerate(term_freqs):
        dl = len(tokenized_docs[idx]) or 1
        score = 0.0

        for term in query_terms:
            if term not in tf:
                continue

            df = doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            freq = tf[term]

            score += idf * (freq * (k1 + 1)) / (
                freq + k1 * (1 - b + b * dl / max(avgdl, 1))
            )

        scores.append((idx, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [(idx, score) for idx, score in scores[:top_k] if score > 0]


def test_schema_loading_and_retrieval() -> bool:
    print_title("4. 测试数据库 Schema 解析与字段检索")

    try:
        db_path = create_demo_database()
        print(f"已创建测试数据库: {db_path}")

        columns = load_schema_from_sqlite(db_path)

        print("\n解析到的字段：")
        for col in columns:
            fk_text = f", FK -> {col['fk_ref']}" if col["fk_ref"] else ""
            pk_text = ", PK" if col["is_pk"] else ""
            print(f"- {col['table']}.{col['column']} ({col['type']}){pk_text}{fk_text}, samples={col['samples']}")

        business_meta = {
            "orders.order_amount": "订单金额 销售额 GMV 实付金额 revenue amount",
            "orders.order_time": "订单时间 下单时间 交易时间 日期 time date",
            "orders.status": "订单状态 支付状态 已支付 已取消 status",
            "orders.user_id": "用户ID 客户ID 下单用户 关联用户表",
            "users.city": "城市 地区 用户城市 city region",
            "users.gender": "性别 男 女 gender",
            "users.register_time": "注册时间 开户时间 register time",
        }

        docs = []
        for col in columns:
            full_name = f"{col['table']}.{col['column']}"
            sample_text = " ".join(col["samples"])
            meta_text = business_meta.get(full_name, "")
            docs.append(
                f"{full_name} {col['type']} {meta_text} samples: {sample_text}"
            )

        query = "每个城市的销售额是多少？"
        hits = bm25_search(query, docs, top_k=5)

        print(f"\n测试 Query: {query}")
        print("召回字段：")

        for idx, score in hits:
            col = columns[idx]
            print(f"- {col['table']}.{col['column']} score={score:.4f}")

        expected_fields = {
            "users.city",
            "orders.order_amount",
        }

        hit_fields = {
            f"{columns[idx]['table']}.{columns[idx]['column']}"
            for idx, _ in hits
        }

        if expected_fields & hit_fields:
            print("\n✅ 最小字段检索流程正常")
            return True

        print("\n⚠️ 检索流程跑通，但没有命中预期字段。可以后续优化分词和元数据。")
        return True

    except Exception as exc:
        print(f"❌ Schema 解析或检索测试失败: {exc}")
        return False


def test_sentence_transformers_import(deps: dict) -> bool:
    print_title("5. Embedding 依赖可用性检查")

    if not deps.get("sentence-transformers"):
        print("⚠️ sentence-transformers 未安装，暂时跳过真实向量模型测试")
        print("安装命令: pip install sentence-transformers")
        return False

    try:
        from sentence_transformers import SentenceTransformer

        print("✅ sentence-transformers 可以 import")
        print("说明：这里不强制下载模型，避免无网络环境卡住。")
        print("后续你可以手动测试：")
        print('python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer(\'BAAI/bge-small-zh-v1.5\')"')
        return True

    except Exception as exc:
        print(f"❌ sentence-transformers import 失败: {exc}")
        return False


def main() -> None:
    print_title("Text2SQL 环境测试开始")

    results = {}

    results["python"] = check_python_version()
    deps = check_dependencies()
    results.update(deps)

    results["sqlite"] = check_sqlite()
    results["schema_retrieval"] = test_schema_loading_and_retrieval()
    results["embedding_import"] = test_sentence_transformers_import(deps)

    print_title("测试结果汇总")

    required_checks = ["python", "numpy", "sqlite", "schema_retrieval"]

    for name, ok in results.items():
        status = "✅ PASS" if ok else "⚠️ CHECK"
        if name in required_checks and not ok:
            status = "❌ FAIL"
        print(f"{name:20s}: {status}")

    required_ok = all(results.get(name) for name in required_checks)

    if required_ok:
        print("\n✅ 基础环境可用于开发 Text2SQL Schema 检索模块。")
    else:
        print("\n❌ 基础环境还有问题，请先处理 FAIL 项。")

    if not results.get("embedding_import"):
        print("\n提示：向量检索需要 sentence-transformers。")
        print("建议执行：pip install sentence-transformers torch")

    print("\n完成。")


if __name__ == "__main__":
    main()
