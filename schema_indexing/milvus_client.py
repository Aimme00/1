from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

from .objects import FieldDocument, TableRelation


@dataclass
class MilvusSchemaIndexConfig:
    """Milvus Schema 索引配置。"""

    # 目前milvus不启用服务，保存在本地生成一个db文件
    uri: str = "./schema_index_demo.db"
    token: str = ""

    field_collection_name: str = "schema_field_index_demo"
    relation_collection_name: str = "schema_relation_index_demo"

    embedding_dim: int = 1024
    recreate_collection: bool = True
    metric_type: str = "COSINE"


class MilvusSchemaIndexClient:
    """
    Milvus Schema 索引 Client。

    这里拆成两个 Collection：
    - field_collection：字段级索引，用于两阶段召回字段。
    - relation_collection：表关系索引，用于根据表名 pair 反查关联键。

    两阶段召回只召回字段。
    SchemaGraph 的边，需要在拿到字段所属表之后，再从 relation_collection 中查。
    """

    def __init__(self, config: MilvusSchemaIndexConfig):
        self.config = config

        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise ImportError("缺少 pymilvus，请先安装：pip install pymilvus milvus-lite") from exc

        self.client = MilvusClient(
            uri=self.config.uri,
            token=self.config.token or None,
        )

    def prepare_collections(self) -> None:
        """创建字段 Collection 和关系 Collection。"""
        self.prepare_field_collection()
        self.prepare_relation_collection()

    def prepare_field_collection(self) -> None:
        """创建字段索引 Collection。"""
        from pymilvus import DataType, MilvusClient

        name = self.config.field_collection_name

        if self.client.has_collection(name):
            if self.config.recreate_collection:
                self.client.drop_collection(name)
            else:
                return

        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
        )

        schema.add_field("doc_id", DataType.VARCHAR, is_primary=True, max_length=512)
        schema.add_field("database", DataType.VARCHAR, max_length=128)
        schema.add_field("table_name", DataType.VARCHAR, max_length=128)
        schema.add_field("column_name", DataType.VARCHAR, max_length=128)
        schema.add_field("data_type", DataType.VARCHAR, max_length=128)
        schema.add_field("semantic_role", DataType.VARCHAR, max_length=128)
        schema.add_field("keyword_text", DataType.VARCHAR, max_length=4096)
        schema.add_field("vector_text", DataType.VARCHAR, max_length=4096)
        schema.add_field("rerank_text", DataType.VARCHAR, max_length=8192)
        schema.add_field(
            "embedding",
            DataType.FLOAT_VECTOR,
            dim=self.config.embedding_dim,
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type=self.config.metric_type,
        )

        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )

    def prepare_relation_collection(self) -> None:
        """
        创建表关系 Collection。

        relation_key 是核心索引字段，格式：
            database:table_a__table_b

        例如：
            trade_db:interest_info__trade_summary
        """
        from pymilvus import DataType, MilvusClient

        name = self.config.relation_collection_name

        if self.client.has_collection(name):
            if self.config.recreate_collection:
                self.client.drop_collection(name)
            else:
                return

        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
        )

        schema.add_field("relation_key", DataType.VARCHAR, is_primary=True, max_length=512)
        schema.add_field("database", DataType.VARCHAR, max_length=128)
        schema.add_field("source_table", DataType.VARCHAR, max_length=128)
        schema.add_field("source_column", DataType.VARCHAR, max_length=128)
        schema.add_field("target_table", DataType.VARCHAR, max_length=128)
        schema.add_field("target_column", DataType.VARCHAR, max_length=128)
        schema.add_field("relation_type", DataType.VARCHAR, max_length=128)
        schema.add_field("join_condition", DataType.VARCHAR, max_length=512)
        schema.add_field("description", DataType.VARCHAR, max_length=1024)
        schema.add_field("relation_json", DataType.VARCHAR, max_length=4096)

        self.client.create_collection(
            collection_name=name,
            schema=schema,
        )

    def insert_field_documents(
        self,
        documents: List[FieldDocument],
        embeddings: List[List[float]],
    ) -> None:
        """写入字段级索引文档和向量。"""
        if len(documents) != len(embeddings):
            raise ValueError("documents 和 embeddings 数量不一致。")

        rows = []

        for doc, embedding in zip(documents, embeddings):
            col = doc.column

            rows.append(
                {
                    "doc_id": doc.doc_id,
                    "database": col.database,
                    "table_name": col.table_name,
                    "column_name": col.column_name,
                    "data_type": col.data_type,
                    "semantic_role": col.semantic_role,
                    "keyword_text": doc.keyword_text,
                    "vector_text": doc.vector_text,
                    "rerank_text": doc.rerank_text,
                    "embedding": embedding,
                }
            )

        self.client.insert(
            collection_name=self.config.field_collection_name,
            data=rows,
        )

        self.client.flush(
            collection_name=self.config.field_collection_name,
        )

    def insert_relations(self, relations: List[TableRelation]) -> None:
        """
        写入表间关系。

        注意：
        关系不是字段召回的对象，而是 SchemaGraph 构建阶段的边信息。
        """
        rows = []

        for relation in relations:
            relation_payload = {
                "database": relation.database,
                "source_table": relation.source_table,
                "source_column": relation.source_column,
                "target_table": relation.target_table,
                "target_column": relation.target_column,
                "relation_type": relation.relation_type,
                "join_condition": relation.join_condition,
                "description": relation.description,
            }

            rows.append(
                {
                    "relation_key": relation.relation_key,
                    "database": relation.database,
                    "source_table": relation.source_table,
                    "source_column": relation.source_column,
                    "target_table": relation.target_table,
                    "target_column": relation.target_column,
                    "relation_type": relation.relation_type,
                    "join_condition": relation.join_condition,
                    "description": relation.description,
                    "relation_json": json.dumps(relation_payload, ensure_ascii=False),
                }
            )

        if not rows:
            return

        self.client.insert(
            collection_name=self.config.relation_collection_name,
            data=rows,
        )

        self.client.flush(
            collection_name=self.config.relation_collection_name,
        )

    def search_fields(
        self,
        query_embedding: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """简单字段向量检索验证。"""
        results = self.client.search(
            collection_name=self.config.field_collection_name,
            data=[query_embedding],
            anns_field="embedding",
            limit=top_k,
            output_fields=[
                "doc_id",
                "database",
                "table_name",
                "column_name",
                "keyword_text",
                "vector_text",
                "rerank_text",
            ],
        )

        return results[0]

    def query_relations_by_table_pairs(
        self,
        database: str,
        table_pairs: List[tuple[str, str]],
    ) -> List[TableRelation]:
        """
        根据表名组合查询表间关系。

        输入：
            [(suppliers, purchase_orders), (purchase_orders, payments)]

        查询：
            relation_key in [
                "db:purchase_orders__suppliers",
                "db:payments__purchase_orders"
            ]
        """
        if not table_pairs:
            return []

        relation_keys = [
            self.build_relation_key(database, left, right)
            for left, right in table_pairs
        ]

        quoted_keys = ", ".join([f'"{key}"' for key in relation_keys])
        filter_expr = f"relation_key in [{quoted_keys}]"

        rows = self.client.query(
            collection_name=self.config.relation_collection_name,
            filter=filter_expr,
            output_fields=[
                "relation_json",
            ],
        )

        relations: List[TableRelation] = []

        for row in rows:
            payload = json.loads(row["relation_json"])
            relations.append(
                TableRelation(
                    database=payload["database"],
                    source_table=payload["source_table"],
                    source_column=payload["source_column"],
                    target_table=payload["target_table"],
                    target_column=payload["target_column"],
                    relation_type=payload.get("relation_type", "foreign_key"),
                    description=payload.get("description", ""),
                )
            )

        return relations

    def build_relation_key(
        self,
        database: str,
        table_a: str,
        table_b: str,
    ) -> str:
        """构建无向表关系 key。"""
        left, right = sorted([table_a, table_b])
        return f"{database}:{left}__{right}"
