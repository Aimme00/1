from __future__ import annotations

import os
from itertools import combinations
from typing import List

from .embedding_client import AliyunEmbeddingClient, AliyunEmbeddingConfig
from .index_builder import SchemaIndexBuilder
from .milvus_client import MilvusSchemaIndexClient, MilvusSchemaIndexConfig
from .objects import ColumnSchema, IndexTextBundle, TableRelation
from .text_builder import SchemaIndexTextBuilder


def build_demo_columns() -> List[ColumnSchema]:
    """
    构造 Demo 字段元数据。

    字段只描述字段本身，表关系单独由 TableRelation 表达。
    """
    return [
        ColumnSchema(
            database="trade_db",
            table_name="trade_summary",
            column_name="user_id",
            data_type="int",
            nullable=False,
            table_description="该表记录用户交易汇总信息，包括用户累计交易笔数、交易金额以及交易活跃度等内容。",
            table_aliases=["用户交易汇总表", "交易统计表"],
            description="用户唯一标识。",
            aliases=["用户ID", "客户ID"],
            samples=["1001", "1002", "1003"],
            business_usage="用于唯一标识交易汇总表中的用户。",
            semantic_role="join_key",
            is_primary_key=True,
        ),
        ColumnSchema(
            database="trade_db",
            table_name="trade_summary",
            column_name="total_trade_count",
            data_type="int",
            nullable=False,
            table_description="该表记录用户交易汇总信息，包括用户累计交易笔数、交易金额以及交易活跃度等内容。",
            table_aliases=["用户交易汇总表", "交易统计表"],
            description="用户累计交易总笔数。",
            aliases=["总交易笔数", "累计交易笔数", "交易笔数", "交易次数"],
            samples=["120", "5800", "56000", "102430"],
            value_range="整数，>= 0",
            data_distribution="多数用户集中在0-50区间，高频交易用户可能超过50000。",
            business_usage="用于衡量用户交易活跃程度，筛选高频交易用户。",
            semantic_role="metric_filter",
            index_texts=IndexTextBundle(
                keyword_text=(
                    "total_trade_count 总交易笔数 累计交易笔数 交易笔数 "
                    "交易次数 高频交易 用户交易汇总表 交易统计表"
                ),
                vector_text=(
                    "字段名：total_trade_count。所属表：trade_summary。"
                    "该字段表示用户累计交易总笔数，用于衡量用户交易活跃程度，"
                    "可用于筛选高频交易用户。"
                ),
                rerank_text=(
                    "数据库：trade_db\n"
                    "表名：trade_summary\n"
                    "字段名：total_trade_count\n"
                    "数据类型：int\n"
                    "字段含义：用户累计交易总笔数。\n"
                    "字段别名：总交易笔数、累计交易笔数、交易笔数、交易次数。\n"
                    "取值范围：整数，>= 0。\n"
                    "数据分布：高频交易用户可能超过50000。\n"
                    "业务用途：用于筛选总交易笔数大于某个阈值的用户。"
                ),
            ),
        ),
        ColumnSchema(
            database="trade_db",
            table_name="interest_info",
            column_name="user_id",
            data_type="int",
            nullable=False,
            table_description="该表记录用户利率信息，包括用户利率类型、利率数值以及利率生效状态等内容。",
            table_aliases=["用户利率表", "利率信息表"],
            description="用户唯一标识。",
            aliases=["用户ID", "客户ID"],
            samples=["1001", "1002", "1003"],
            business_usage="用于唯一标识利率信息表中的用户。",
            semantic_role="join_key",
            is_primary_key=True,
        ),
        ColumnSchema(
            database="trade_db",
            table_name="interest_info",
            column_name="interest_rate",
            data_type="decimal",
            nullable=False,
            table_description="该表记录用户利率信息，包括用户利率类型、利率数值以及利率生效状态等内容。",
            table_aliases=["用户利率表", "利率信息表"],
            description="用户对应年化利率。",
            aliases=["利率", "年化利率", "用户利率", "利率数值"],
            samples=["2.35", "3.12", "4.58"],
            value_range="0-100",
            data_distribution="不同用户分层对应不同利率。",
            business_usage="用于展示或分析用户当前适用的年化利率。",
            semantic_role="output_metric",
            index_texts=IndexTextBundle(
                keyword_text=(
                    "interest_rate 利率 年化利率 用户利率 利率数值 "
                    "用户利率表 利率信息表"
                ),
                vector_text=(
                    "字段名：interest_rate。所属表：interest_info。"
                    "该字段表示用户对应年化利率，通常作为查询结果输出。"
                ),
                rerank_text=(
                    "数据库：trade_db\n"
                    "表名：interest_info\n"
                    "字段名：interest_rate\n"
                    "数据类型：decimal\n"
                    "字段含义：用户对应年化利率。\n"
                    "字段别名：利率、年化利率、用户利率、利率数值。\n"
                    "取值范围：0-100。\n"
                    "业务用途：当用户询问利率是多少时，通常作为输出字段。"
                ),
            ),
        ),
    ]


def build_demo_relations() -> List[TableRelation]:
    """
    构造 Demo 表关系。

    表间 join 信息不要写进字段 business_usage。
    关系应该作为独立的图边存储。
    """
    return [
        TableRelation(
            database="trade_db",
            source_table="trade_summary",
            source_column="user_id",
            target_table="interest_info",
            target_column="user_id",
            relation_type="business_relation",
            description="通过用户ID关联用户交易汇总信息与用户利率信息。",
        )
    ]


def main() -> None:
    """
    运行示例。

    安装依赖：
        pip install pymilvus milvus-lite numpy

    执行：
        python -m schema_indexing.build_index_demo

    如果要使用阿里云真实 Embedding：
        export DASHSCOPE_API_KEY="你的APIKey"
    """
    embedding_dim = 1024

    embedding_client = AliyunEmbeddingClient(
        AliyunEmbeddingConfig(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            model="text-embedding-v4",
            dimensions=embedding_dim,
            use_mock_when_no_api_key=True,
        )
    )

    milvus_client = MilvusSchemaIndexClient(
        MilvusSchemaIndexConfig(
            uri=os.getenv("SCHEMA_MILVUS_URI", "./schema_index_demo.db"),
            token=os.getenv("SCHEMA_MILVUS_TOKEN", ""),
            field_collection_name=os.getenv("SCHEMA_FIELD_COLLECTION", "schema_field_index_demo"),
            relation_collection_name=os.getenv("SCHEMA_RELATION_COLLECTION", "schema_relation_index_demo"),
            embedding_dim=embedding_dim,
            recreate_collection=True,
            metric_type="COSINE",
        )
    )

    index_builder = SchemaIndexBuilder(
        text_builder=SchemaIndexTextBuilder(),
        embedding_client=embedding_client,
        milvus_client=milvus_client,
    )

    columns = build_demo_columns()
    relations = build_demo_relations()

    print("=" * 80)
    print("Schema Indexing Demo")
    print("=" * 80)

    print("\n[1] 构建字段三级索引文本：")
    documents = index_builder.build_documents_only(columns)

    for index, doc in enumerate(documents, start=1):
        print(f"\n{index}. {doc.doc_id}")
        print(f"keyword_text: {doc.keyword_text}")
        print(f"vector_text : {doc.vector_text}")
        print(f"rerank_text : {doc.rerank_text}")

    print("\n[2] 写入字段索引和表关系索引：")
    result = index_builder.build(
        columns=columns,
        relations=relations,
    )

    print(f"field_collection_name   : {result.field_collection_name}")
    print(f"relation_collection_name: {result.relation_collection_name}")
    print(f"field_document_count    : {result.field_document_count}")
    print(f"relation_count          : {result.relation_count}")

    print("\n[3] 简单字段向量检索验证：")
    query_vector = embedding_client.embed_texts(["总交易笔数"])[0].astype(float).tolist()
    search_results = milvus_client.search_fields(
        query_embedding=query_vector,
        top_k=3,
    )

    hit_tables = []

    for rank, item in enumerate(search_results, start=1):
        entity = item.get("entity", {})
        table_name = entity.get("table_name")
        column_name = entity.get("column_name")

        if table_name and table_name not in hit_tables:
            hit_tables.append(table_name)

        print(
            f"{rank}. "
            f"{table_name}.{column_name} "
            f"score={item.get('distance')}"
        )
        print(f"   vector_text: {entity.get('vector_text')}")

    print("\n[4] 根据命中字段所属表查询表关系：")

    # Demo 中人为加入 interest_info，模拟两阶段检索同时命中了交易字段和利率字段。
    for table_name in ["interest_info"]:
        if table_name not in hit_tables:
            hit_tables.append(table_name)

    table_pairs = list(combinations(hit_tables, 2))

    print(f"命中表集合: {hit_tables}")
    print(f"两两组合  : {table_pairs}")

    graph_relations = milvus_client.query_relations_by_table_pairs(
        database="trade_db",
        table_pairs=table_pairs,
    )

    for relation in graph_relations:
        print(f"- {relation.join_condition}；{relation.description}")


if __name__ == "__main__":
    main()
