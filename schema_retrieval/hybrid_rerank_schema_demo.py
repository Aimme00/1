from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

from schema_retrieval.bm25 import BM25Index
from schema_retrieval.graph_builder import build_schema_graph
from schema_retrieval.objects import SchemaHit
from schema_retrieval.rrf_fusion_client import (
    RRFFusionClient,
    RRFFusionConfig,
    RouteRecallResult,
)
from schema_retrieval.rerank_client import (
    AliyunRerankClient,
    AliyunRerankConfig,
    RerankDocument,
)
from schema_retrieval.sqlite_loader import SQLiteSchemaLoader
from schema_retrieval.vector_recall_demo import (
    AliyunEmbeddingClient,
    AliyunEmbeddingConfig,
    KeywordExtractor,
    KeywordExtractorConfig,
    VectorIndex,
)


@dataclass
class HybridFieldDocument:
    """
    混合检索字段文档。

    keyword_text 用于 BM25。
    vector_text 用于 Embedding。
    rerank_text 用于 Rerank。
    """

    doc_id: str
    column: object
    keyword_text: str
    vector_text: str
    rerank_text: str


def create_demo_database() -> Path:
    """
    创建 Demo 数据库。
    """
    db_path = Path(tempfile.gettempdir()) / "text2sql_hybrid_rerank_schema_demo.db"

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(
        """
        CREATE TABLE trade_summary (
            user_id INTEGER PRIMARY KEY,
            total_trade_count INTEGER NOT NULL,
            total_trade_amount REAL NOT NULL,
            active_days INTEGER NOT NULL,
            last_trade_time TEXT
        );

        CREATE TABLE interest_info (
            user_id INTEGER PRIMARY KEY,
            interest_rate REAL NOT NULL,
            rate_type TEXT,
            effective_status TEXT,
            FOREIGN KEY (user_id) REFERENCES trade_summary(user_id)
        );
        """
    )

    conn.executemany(
        """
        INSERT INTO trade_summary (
            user_id,
            total_trade_count,
            total_trade_amount,
            active_days,
            last_trade_time
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1001, 12, 5300.50, 5, "2024-05-01"),
            (1002, 5800, 230000.00, 180, "2024-05-06"),
            (1003, 56000, 1800000.00, 320, "2024-05-10"),
            (1004, 102430, 3700000.00, 365, "2024-05-11"),
            (1005, 0, 0.00, 0, None),
        ],
    )

    conn.executemany(
        """
        INSERT INTO interest_info (
            user_id,
            interest_rate,
            rate_type,
            effective_status
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (1001, 2.35, "standard", "active"),
            (1002, 3.12, "vip", "active"),
            (1003, 4.58, "high_value", "active"),
            (1004, 4.95, "high_value", "active"),
            (1005, 1.80, "standard", "inactive"),
        ],
    )

    conn.commit()
    conn.close()

    return db_path


def get_business_meta() -> dict:
    """
    Demo 业务元数据。

    三类文本：
    - keyword_text: 关键词召回
    - vector_text: 向量召回
    - rerank_text: Rerank 精排
    """
    return {
        "trade_summary": {
            "description": "该表记录用户交易汇总信息，包括用户累计交易笔数、累计交易金额、活跃交易天数以及最近交易时间。",
            "aliases": ["用户交易汇总表", "交易统计表"],
            "columns": {
                "user_id": {
                    "description": "用户唯一标识，用于关联用户交易汇总信息与用户利率信息。",
                    "aliases": ["用户ID", "客户ID"],
                    "semantic_role": "join_key",
                    "business_usage": "用于表关联。",
                    "keyword_text": "user_id 用户ID 客户ID 用户唯一标识 关联字段",
                    "vector_text": (
                        "字段名：user_id。所属表：trade_summary。"
                        "该字段表示用户唯一标识，用于关联用户交易汇总信息与用户利率信息。"
                    ),
                    "rerank_text": (
                        "字段：trade_summary.user_id。"
                        "含义：用户唯一标识。"
                        "角色：join_key。"
                        "用途：用于关联 trade_summary 和 interest_info。"
                    ),
                },
                "total_trade_count": {
                    "description": "用户累计交易总笔数，表示用户在统计周期内完成的交易次数。",
                    "aliases": ["总交易笔数", "累计交易笔数", "交易笔数", "交易次数"],
                    "semantic_role": "metric_filter",
                    "value_range": "整数，>= 0",
                    "data_distribution": "多数用户集中在0-50区间，高频交易用户可能超过50000。",
                    "business_usage": "用于衡量用户交易活跃程度，筛选高频交易用户。",
                    "samples": ["0", "12", "5800", "56000", "102430"],
                    "keyword_text": (
                        "total_trade_count 总交易笔数 累计交易笔数 交易笔数 "
                        "交易次数 高频交易 大于50000 用户交易汇总表 交易统计表"
                    ),
                    "vector_text": (
                        "字段名：total_trade_count。所属表：trade_summary，"
                        "该表记录用户交易汇总信息。该字段表示用户在统计周期内的累计交易总笔数，"
                        "用于衡量用户交易活跃程度，可用于筛选高频交易用户。"
                    ),
                    "rerank_text": (
                        "字段：trade_summary.total_trade_count。"
                        "含义：用户累计交易总笔数，表示用户在统计周期内完成的交易次数。"
                        "别名：总交易笔数、累计交易笔数、交易笔数、交易次数。"
                        "角色：metric_filter。"
                        "取值范围：整数，>= 0。"
                        "数据分布：高频交易用户可能超过50000。"
                        "用途：用于筛选总交易笔数大于某个阈值的用户。"
                    ),
                },
                "total_trade_amount": {
                    "description": "用户累计交易金额，表示用户在统计周期内完成交易的总金额。",
                    "aliases": ["交易金额", "累计交易金额", "成交金额", "交易总额"],
                    "semantic_role": "metric",
                    "value_range": ">= 0",
                    "business_usage": "用于衡量用户交易价值、统计交易金额或识别高价值用户。",
                    "samples": ["0.00", "5300.50", "230000.00", "1800000.00"],
                    "keyword_text": (
                        "total_trade_amount 交易金额 累计交易金额 成交金额 交易总额 "
                        "交易规模 高价值用户"
                    ),
                    "vector_text": (
                        "字段名：total_trade_amount。所属表：trade_summary。"
                        "该字段表示用户在统计周期内完成交易的累计金额，"
                        "用于衡量用户交易价值或交易规模。"
                    ),
                    "rerank_text": (
                        "字段：trade_summary.total_trade_amount。"
                        "含义：用户累计交易金额，表示交易总额或成交金额。"
                        "角色：metric。"
                        "用途：统计金额，不表示交易笔数。"
                    ),
                },
                "active_days": {
                    "description": "用户在统计周期内发生交易的活跃天数。",
                    "aliases": ["活跃天数", "交易活跃天数"],
                    "semantic_role": "metric",
                    "business_usage": "用于衡量用户交易频率和持续活跃程度。",
                    "keyword_text": "active_days 活跃天数 交易活跃天数 交易频率 持续活跃",
                    "vector_text": (
                        "字段名：active_days。所属表：trade_summary。"
                        "该字段表示用户在统计周期内发生交易的活跃天数，用于衡量交易频率。"
                    ),
                    "rerank_text": (
                        "字段：trade_summary.active_days。"
                        "含义：用户发生交易的活跃天数。"
                        "角色：metric。"
                        "用途：衡量交易频率，不表示交易笔数或利率。"
                    ),
                },
                "last_trade_time": {
                    "description": "用户最近一次交易发生时间。",
                    "aliases": ["最近交易时间", "最后交易时间"],
                    "semantic_role": "time",
                    "business_usage": "用于判断用户最近活跃情况。",
                    "keyword_text": "last_trade_time 最近交易时间 最后交易时间 交易时间 最近活跃",
                    "vector_text": (
                        "字段名：last_trade_time。所属表：trade_summary。"
                        "该字段表示用户最近一次交易发生时间，用于判断用户最近活跃情况。"
                    ),
                    "rerank_text": (
                        "字段：trade_summary.last_trade_time。"
                        "含义：最近一次交易时间。"
                        "角色：time。"
                        "用途：时间筛选，不表示交易笔数或利率。"
                    ),
                },
            },
        },
        "interest_info": {
            "description": "该表记录用户利率信息，包括用户利率类型、年化利率数值以及利率生效状态。",
            "aliases": ["用户利率表", "利率信息表"],
            "columns": {
                "user_id": {
                    "description": "用户唯一标识，用于关联交易汇总表。",
                    "aliases": ["用户ID", "客户ID"],
                    "semantic_role": "join_key",
                    "business_usage": "用于表关联。",
                    "keyword_text": "user_id 用户ID 客户ID 用户唯一标识 关联字段",
                    "vector_text": (
                        "字段名：user_id。所属表：interest_info。"
                        "该字段表示用户唯一标识，用于关联交易汇总表。"
                    ),
                    "rerank_text": (
                        "字段：interest_info.user_id。"
                        "含义：用户唯一标识。"
                        "角色：join_key。"
                        "用途：用于关联 trade_summary 和 interest_info。"
                    ),
                },
                "interest_rate": {
                    "description": "用户对应的年化利率数值。",
                    "aliases": ["利率", "年化利率", "用户利率", "利率数值"],
                    "semantic_role": "output_metric",
                    "value_range": "0-100",
                    "business_usage": "用于展示或分析用户当前适用的年化利率。",
                    "samples": ["2.35", "3.12", "4.58", "4.95"],
                    "keyword_text": (
                        "interest_rate 利率 年化利率 用户利率 利率数值 "
                        "利率是多少 用户利率表 利率信息表"
                    ),
                    "vector_text": (
                        "字段名：interest_rate。所属表：interest_info，"
                        "该表记录用户利率信息。该字段表示用户当前对应的年化利率数值，"
                        "通常作为查询结果输出。"
                    ),
                    "rerank_text": (
                        "字段：interest_info.interest_rate。"
                        "含义：用户对应的年化利率数值。"
                        "别名：利率、年化利率、用户利率、利率数值。"
                        "角色：output_metric。"
                        "用途：当用户询问利率是多少时，通常作为查询输出字段。"
                    ),
                },
                "rate_type": {
                    "description": "用户适用的利率类型，例如standard、vip、high_value。",
                    "aliases": ["利率类型", "费率类型"],
                    "semantic_role": "dimension",
                    "business_usage": "用于区分不同用户分层下的利率类型。",
                    "keyword_text": "rate_type 利率类型 费率类型 standard vip high_value",
                    "vector_text": (
                        "字段名：rate_type。所属表：interest_info。"
                        "该字段表示用户适用的利率类型，用于区分不同用户分层下的利率。"
                    ),
                    "rerank_text": (
                        "字段：interest_info.rate_type。"
                        "含义：利率类型或费率类型。"
                        "角色：dimension。"
                        "用途：分类字段，不是利率数值本身。"
                    ),
                },
                "effective_status": {
                    "description": "利率生效状态，例如active、inactive。",
                    "aliases": ["生效状态", "利率状态"],
                    "semantic_role": "filter",
                    "business_usage": "用于筛选当前有效或无效的利率记录。",
                    "keyword_text": "effective_status 生效状态 利率状态 active inactive 有效 无效",
                    "vector_text": (
                        "字段名：effective_status。所属表：interest_info。"
                        "该字段表示利率生效状态，用于筛选当前有效或无效的利率记录。"
                    ),
                    "rerank_text": (
                        "字段：interest_info.effective_status。"
                        "含义：利率生效状态。"
                        "角色：filter。"
                        "用途：筛选有效或无效利率，不是利率数值。"
                    ),
                },
            },
        },
    }


def enrich_columns_with_business_meta(columns: list, business_meta: dict) -> None:
    """
    将业务元数据补充回 ColumnSchema。
    """
    for column in columns:
        table_meta = business_meta.get(column.table_name, {})
        column_meta = table_meta.get("columns", {}).get(column.column_name, {})

        if not column_meta:
            continue

        column.description = column_meta.get("description", getattr(column, "description", ""))
        column.aliases = column_meta.get("aliases", getattr(column, "aliases", []))
        column.semantic_role = column_meta.get("semantic_role", getattr(column, "semantic_role", ""))
        column.value_range = column_meta.get("value_range", getattr(column, "value_range", ""))
        column.data_distribution = column_meta.get(
            "data_distribution",
            getattr(column, "data_distribution", ""),
        )
        column.business_usage = column_meta.get(
            "business_usage",
            getattr(column, "business_usage", ""),
        )

        if "samples" in column_meta:
            column.samples = [str(item) for item in column_meta["samples"]]


def build_hybrid_documents(columns: list, business_meta: dict) -> List[HybridFieldDocument]:
    """
    构建混合检索字段文档。
    """
    documents: List[HybridFieldDocument] = []

    for column in columns:
        table_meta = business_meta.get(column.table_name, {})
        column_meta = table_meta.get("columns", {}).get(column.column_name, {})

        keyword_text = column_meta.get("keyword_text") or build_default_keyword_text(column)
        vector_text = column_meta.get("vector_text") or build_default_vector_text(column)
        rerank_text = column_meta.get("rerank_text") or build_default_rerank_text(column)

        documents.append(
            HybridFieldDocument(
                doc_id=column.full_name,
                column=column,
                keyword_text=keyword_text,
                vector_text=vector_text,
                rerank_text=rerank_text,
            )
        )

    return documents


def build_default_keyword_text(column: object) -> str:
    """
    构建默认关键词索引文本。
    """
    parts = [
        getattr(column, "database", ""),
        getattr(column, "table_name", ""),
        getattr(column, "column_name", ""),
        getattr(column, "data_type", ""),
        getattr(column, "description", ""),
        " ".join(getattr(column, "aliases", []) or []),
        " ".join(getattr(column, "samples", [])[:5] or []),
    ]

    return "\n".join(part for part in parts if part)


def build_default_vector_text(column: object) -> str:
    """
    构建默认向量索引文本。
    """
    parts = [
        f"字段名：{getattr(column, 'column_name', '')}",
        f"所属表：{getattr(column, 'table_name', '')}",
        f"字段含义：{getattr(column, 'description', '')}",
    ]

    aliases = getattr(column, "aliases", []) or []
    if aliases:
        parts.append(f"字段别名：{', '.join(aliases)}")

    business_usage = getattr(column, "business_usage", "")
    if business_usage:
        parts.append(f"业务用途：{business_usage}")

    semantic_role = getattr(column, "semantic_role", "")
    if semantic_role:
        parts.append(f"字段角色：{semantic_role}")

    return "\n".join(part for part in parts if part)


def build_default_rerank_text(column: object) -> str:
    """
    构建默认 Rerank 文本。

    Rerank 文本可以比 keyword_text 和 vector_text 更详细。
    """
    parts = [
        f"字段：{getattr(column, 'table_name', '')}.{getattr(column, 'column_name', '')}",
        f"数据类型：{getattr(column, 'data_type', '')}",
        f"字段含义：{getattr(column, 'description', '')}",
    ]

    aliases = getattr(column, "aliases", []) or []
    if aliases:
        parts.append(f"字段别名：{', '.join(aliases)}")

    semantic_role = getattr(column, "semantic_role", "")
    if semantic_role:
        parts.append(f"字段角色：{semantic_role}")

    value_range = getattr(column, "value_range", "")
    if value_range:
        parts.append(f"取值范围：{value_range}")

    data_distribution = getattr(column, "data_distribution", "")
    if data_distribution:
        parts.append(f"数据分布：{data_distribution}")

    business_usage = getattr(column, "business_usage", "")
    if business_usage:
        parts.append(f"业务用途：{business_usage}")

    samples = getattr(column, "samples", []) or []
    if samples:
        parts.append(f"样例值：{', '.join(samples[:8])}")

    return "\n".join(part for part in parts if part)


def build_route_results(
    keywords: List[str],
    keyword_index: BM25Index,
    vector_index: VectorIndex,
    per_keyword_top_k: int,
) -> List[RouteRecallResult]:
    """
    执行关键词召回和向量召回。
    """
    route_results: List[RouteRecallResult] = []

    for keyword in keywords:
        keyword_hits = keyword_index.search(
            keyword,
            top_k=per_keyword_top_k,
        )

        route_results.append(
            RouteRecallResult(
                route_name="keyword",
                query_term=keyword,
                ranked_doc_indices=[
                    doc_index
                    for doc_index, _ in keyword_hits
                ],
            )
        )

        vector_hits = vector_index.search(
            keyword,
            top_k=per_keyword_top_k,
        )

        route_results.append(
            RouteRecallResult(
                route_name="vector",
                query_term=keyword,
                ranked_doc_indices=[
                    doc_index
                    for doc_index, _ in vector_hits
                ],
            )
        )

    return route_results


def calculate_rerank_top_n(
    keyword_count: int,
    multiplier: int,
    min_top_n: int,
    max_top_n: int,
    candidate_count: int,
) -> int:
    """
    根据关键词数量动态计算 Rerank 输出数量。

    rerank_top_n = keyword_count * multiplier
    """
    dynamic_top_n = keyword_count * multiplier

    top_n = max(
        min_top_n,
        min(dynamic_top_n, max_top_n),
    )

    return min(top_n, candidate_count)


def main() -> None:
    db_path = create_demo_database()
    business_meta = get_business_meta()

    print("=" * 80)
    print("混合检索 + RRF 融合 + Rerank + Schema 图 Demo")
    print("=" * 80)
    print(f"Demo database: {db_path}")

    loader = SQLiteSchemaLoader(
        db_path=db_path,
        database_name="demo_finance",
        business_meta=business_meta,
        sample_size=5,
    )

    tables, columns, relations = loader.load()
    enrich_columns_with_business_meta(columns, business_meta)

    documents = build_hybrid_documents(
        columns=columns,
        business_meta=business_meta,
    )

    print("\n[1] Hybrid documents:")
    for index, doc in enumerate(documents):
        print(f"{index}. {doc.doc_id}")
        print(f"   keyword_text: {doc.keyword_text}")
        print(f"   vector_text : {doc.vector_text}")
        print(f"   rerank_text : {doc.rerank_text}")

    # -------------------------------------------------------------------------
    # 构建关键词索引
    # -------------------------------------------------------------------------
    keyword_texts = [
        doc.keyword_text
        for doc in documents
    ]

    keyword_index = BM25Index(keyword_texts)

    # -------------------------------------------------------------------------
    # 构建向量索引
    # -------------------------------------------------------------------------
    embedding_client = AliyunEmbeddingClient(
        AliyunEmbeddingConfig(
            api_key="",
            embedding_url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
            model="text-embedding-v4",
            dimensions=1024,
        )
    )

    vector_index = VectorIndex(embedding_client)

    vector_texts = [
        doc.vector_text
        for doc in documents
    ]

    print("\n[2] Building vector index...")
    vector_index.build(vector_texts)

    if embedding_client.config.api_key:
        print("Vector index built by Aliyun Embedding API.")
    else:
        print("Vector index built by local mock embedding because DASHSCOPE_API_KEY is not set.")

    # -------------------------------------------------------------------------
    # LLM 抽取关键词
    # -------------------------------------------------------------------------
    query = "查询总交易笔数大于50000的利率是多少"

    print("\n[3] Query:")
    print(query)

    keyword_extractor = KeywordExtractor(
        KeywordExtractorConfig(
            api_key="",
            chat_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            model="qwen-plus",
        )
    )

    keywords = keyword_extractor.extract(query)

    print("\n[4] Extracted keywords:")
    print("，".join(keywords))

    # -------------------------------------------------------------------------
    # 可调参数位置
    # -------------------------------------------------------------------------
    per_keyword_top_k = 20

    rrf_client = RRFFusionClient(
        RRFFusionConfig(
            rrf_k=60,

            # RRF 候选截断：
            # fused_top_k = 关键词数量 * truncate_multiplier
            truncate_multiplier=6,

            min_fused_top_k=10,
            max_fused_top_k=50,
            final_top_k=20,

            route_weights={
                "keyword": 1.0,
                "vector": 1.0,
            },
        )
    )

    rerank_top_multiplier = 2
    """
    Rerank 输出数量倍数。

    这里就是你说的：
        rerank_top_k = 关键词数量 * 2

    可改为 3 或 4。
    """

    rerank_min_top_n = 2
    rerank_max_top_n = 20

    print("\n[5] Config:")
    print(f"per_keyword_top_k = {per_keyword_top_k}")
    print(f"rrf_truncate_multiplier = {rrf_client.config.truncate_multiplier}")
    print(f"rrf_fused_top_k = {rrf_client.calculate_fused_top_k(len(keywords))}")
    print(f"rrf_final_top_k = {rrf_client.config.final_top_k}")
    print(f"rerank_top_multiplier = {rerank_top_multiplier}")
    print(f"rerank_min_top_n = {rerank_min_top_n}")
    print(f"rerank_max_top_n = {rerank_max_top_n}")

    # -------------------------------------------------------------------------
    # 关键词召回 + 向量召回
    # -------------------------------------------------------------------------
    route_results = build_route_results(
        keywords=keywords,
        keyword_index=keyword_index,
        vector_index=vector_index,
        per_keyword_top_k=per_keyword_top_k,
    )

    print("\n[6] Route recall results:")
    for route_result in route_results:
        print(
            f"route={route_result.route_name}, "
            f"term={route_result.query_term}, "
            f"ranked_doc_indices={route_result.ranked_doc_indices}"
        )

    # -------------------------------------------------------------------------
    # RRF 融合
    # -------------------------------------------------------------------------
    fusion_hits = rrf_client.fuse(
        route_results=route_results,
        keyword_count=len(keywords),
    )

    print("\n[7] RRF fused results:")
    for rank, hit in enumerate(fusion_hits, start=1):
        doc = documents[hit.doc_index]
        col = doc.column

        print(
            f"{rank}. {col.table_name}.{col.column_name} "
            f"rrf_score={hit.score:.6f} "
            f"sources={hit.sources} "
            f"matched_terms={hit.matched_terms} "
            f"best_rank_by_source={hit.best_rank_by_source}"
        )

    # -------------------------------------------------------------------------
    # Rerank
    # -------------------------------------------------------------------------
    rerank_candidates = [
        RerankDocument(
            doc_index=hit.doc_index,
            text=documents[hit.doc_index].rerank_text,
        )
        for hit in fusion_hits
    ]

    rerank_top_n = calculate_rerank_top_n(
        keyword_count=len(keywords),
        multiplier=rerank_top_multiplier,
        min_top_n=rerank_min_top_n,
        max_top_n=rerank_max_top_n,
        candidate_count=len(rerank_candidates),
    )

    rerank_client = AliyunRerankClient(
        AliyunRerankConfig(
            api_key="",

            # qwen3-rerank 需要业务空间 ID。
            # 建议通过环境变量设置：
            # export DASHSCOPE_WORKSPACE_ID="你的WorkspaceId"
            workspace_id="",

            # 如果你想直接写完整 URL，也可以设置 rerank_url。
            rerank_url="",

            # 官方当前模型名为 qwen3-rerank。
            # 如果你账号里有 qwen-rerank 别名，可以改成 "qwen-rerank"。
            model="qwen3-rerank",
        )
    )

    rerank_results = rerank_client.rerank(
        query=query,
        documents=rerank_candidates,
        top_n=rerank_top_n,
    )

    print("\n[8] Rerank results:")
    for rank, result in enumerate(rerank_results, start=1):
        doc = documents[result.doc_index]
        col = doc.column

        print(
            f"{rank}. {col.table_name}.{col.column_name} "
            f"rerank_score={result.rerank_score:.6f}"
        )
        print(f"   rerank_text: {doc.rerank_text}")

    # -------------------------------------------------------------------------
    # Rerank 结果转 SchemaHit
    # -------------------------------------------------------------------------
    schema_hits: List[SchemaHit] = []

    for result in rerank_results:
        doc = documents[result.doc_index]

        schema_hits.append(
            SchemaHit(
                doc_id=doc.doc_id,
                score=result.rerank_score,
                column=doc.column,
                rerank_score=result.rerank_score,
            )
        )

    # -------------------------------------------------------------------------
    # 构建 Schema 图
    # -------------------------------------------------------------------------
    schema_graph = build_schema_graph(
        tables=tables,
        all_columns=columns,
        relations=relations,
        hits=schema_hits,
        include_join_columns=True,
    )

    print("\n[9] Schema graph prompt context:")
    print(schema_graph.to_prompt_context())


if __name__ == "__main__":
    main()