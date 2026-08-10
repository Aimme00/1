from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from schema_retrieval.bm25 import BM25Index
from schema_retrieval.embedding_client import (
    AliyunEmbeddingClient,
    AliyunEmbeddingConfig,
)
from schema_retrieval.graph_builder import build_schema_graph
from schema_retrieval.keyword_extractor_client import (
    AliyunKeywordExtractor,
    AliyunKeywordExtractorConfig,
)
from schema_retrieval.objects import SchemaHit
from schema_retrieval.rerank_client import (
    AliyunRerankClient,
    AliyunRerankConfig,
    RerankDocument,
    RerankResult,
)
from schema_retrieval.rrf_fusion_client import (
    RRFFusionClient,
    RRFFusionConfig,
    RRFFusionHit,
    RouteRecallResult,
)
from schema_retrieval.sqlite_loader import SQLiteSchemaLoader
from schema_retrieval.vector_index import VectorIndex


@dataclass
class HybridFieldDocument:
    """
    混合检索字段文档。

    一个字段会同时维护三类文本：
    - keyword_text：用于 BM25 关键词召回
    - vector_text：用于 Embedding 向量召回
    - rerank_text：用于 Rerank 精排
    """

    doc_id: str
    column: object
    keyword_text: str
    vector_text: str
    rerank_text: str


@dataclass
class HybridSchemaRetrievalConfig:
    """
    混合 Schema 检索服务配置。
    """

    per_keyword_top_k: int = 20
    """每个关键词在每一路召回中的 Top-K 数量。"""

    include_join_columns: bool = True
    """构建 SchemaGraph 时是否自动补充 Join 字段。"""

    rrf_config: RRFFusionConfig = field(
        default_factory=lambda: RRFFusionConfig(
            rrf_k=60,
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
    """RRF 融合配置。"""

    rerank_top_multiplier: int = 2
    """
    Rerank 输出数量倍数。

    计算方式：
        rerank_top_n = 关键词数量 * rerank_top_multiplier
    """

    rerank_min_top_n: int = 2
    """Rerank 输出数量下限。"""

    rerank_max_top_n: int = 20
    """Rerank 输出数量上限。"""


@dataclass
class HybridSchemaRetrievalResult:
    """
    混合 Schema 检索服务返回结果。
    """

    query: str
    keywords: List[str]
    route_results: List[RouteRecallResult]
    fusion_hits: List[RRFFusionHit]
    rerank_results: List[RerankResult]
    schema_hits: List[SchemaHit]
    schema_graph: object


class HybridSchemaRetrievalService:
    """
    混合 Schema 检索服务。

    完整流程：
    1. 用户 Query
    2. LLM 抽取关键词
    3. BM25 关键词召回
    4. Embedding 向量召回
    5. RRF 融合
    6. Rerank 精排
    7. SchemaHit
    8. SchemaGraph
    """

    def __init__(
        self,
        tables: dict,
        columns: list,
        relations: list,
        business_meta: Optional[dict],
        embedding_client: AliyunEmbeddingClient,
        rerank_client: AliyunRerankClient,
        keyword_extractor: Optional[AliyunKeywordExtractor] = None,
        config: Optional[HybridSchemaRetrievalConfig] = None,
    ):
        self.tables = tables
        self.columns = columns
        self.relations = relations
        self.business_meta = business_meta or {}
        self.embedding_client = embedding_client
        self.rerank_client = rerank_client
        self.keyword_extractor = keyword_extractor
        self.config = config or HybridSchemaRetrievalConfig()

        self.documents: List[HybridFieldDocument] = []
        self.keyword_index: Optional[BM25Index] = None
        self.vector_index: Optional[VectorIndex] = None
        self.rrf_client = RRFFusionClient(self.config.rrf_config)

        self._enrich_columns_with_business_meta()
        self._build_documents()
        self._build_indices()

    @classmethod
    def from_sqlite(
        cls,
        db_path,
        database_name: str,
        business_meta: Optional[dict],
        embedding_client: AliyunEmbeddingClient,
        rerank_client: AliyunRerankClient,
        keyword_extractor: Optional[AliyunKeywordExtractor] = None,
        config: Optional[HybridSchemaRetrievalConfig] = None,
        sample_size: int = 5,
    ) -> "HybridSchemaRetrievalService":
        """
        从 SQLite 数据库初始化混合 Schema 检索服务。

        Args:
            db_path: SQLite 数据库路径。
            database_name: 数据库名称。
            business_meta: 表级和字段级业务元数据。
            embedding_client: Embedding Client。
            rerank_client: Rerank Client。
            keyword_extractor: 关键词抽取 Client。不传时需要在 retrieve() 中手动传 keywords。
            config: 服务配置。
            sample_size: 字段样例值抽取数量。

        Returns:
            HybridSchemaRetrievalService: 初始化完成的服务实例。
        """
        loader = SQLiteSchemaLoader(
            db_path=db_path,
            database_name=database_name,
            business_meta=business_meta or {},
            sample_size=sample_size,
        )

        tables, columns, relations = loader.load()

        return cls(
            tables=tables,
            columns=columns,
            relations=relations,
            business_meta=business_meta,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            keyword_extractor=keyword_extractor,
            config=config,
        )

    def retrieve(
        self,
        query: str,
        keywords: Optional[Sequence[str]] = None,
    ) -> HybridSchemaRetrievalResult:
        """
        执行混合检索，并返回 SchemaGraph。

        Args:
            query: 用户自然语言 Query。
            keywords: 可选的核心关键词列表。
                如果传入，则直接使用该关键词列表。
                如果不传，则使用 keyword_extractor 自动抽取。

        Returns:
            HybridSchemaRetrievalResult: 包含关键词、RRF 结果、Rerank 结果和 SchemaGraph。
        """
        resolved_keywords = self._resolve_keywords(query, keywords)

        route_results = self._build_route_results(resolved_keywords)

        fusion_hits = self.rrf_client.fuse(
            route_results=route_results,
            keyword_count=len(resolved_keywords),
        )

        rerank_results = self._rerank(
            query=query,
            fusion_hits=fusion_hits,
            keyword_count=len(resolved_keywords),
        )

        schema_hits = self._build_schema_hits(
            rerank_results=rerank_results,
            fusion_hits=fusion_hits,
        )

        schema_graph = build_schema_graph(
            hits=schema_hits,
            tables=self.tables,
            all_columns=self.columns,
            relations=self.relations,
            include_join_columns=self.config.include_join_columns,
        )
        return HybridSchemaRetrievalResult(
            query=query,
            keywords=resolved_keywords,
            route_results=route_results,
            fusion_hits=fusion_hits,
            rerank_results=rerank_results,
            schema_hits=schema_hits,
            schema_graph=schema_graph,
        )

    def _resolve_keywords(
        self,
        query: str,
        keywords: Optional[Sequence[str]],
    ) -> List[str]:
        """
        获取最终使用的核心关键词。
        """
        if keywords:
            return [item.strip() for item in keywords if item and item.strip()]

        if self.keyword_extractor is None:
            raise ValueError(
                "未传入 keywords，且未配置 keyword_extractor，无法自动抽取关键词。"
            )

        extracted_keywords = self.keyword_extractor.extract(query)

        if not extracted_keywords:
            raise ValueError(f"关键词抽取结果为空，query={query}")

        return extracted_keywords

    def _build_route_results(
        self,
        keywords: List[str],
    ) -> List[RouteRecallResult]:
        """
        执行 BM25 关键词召回和 Embedding 向量召回。
        """
        if self.keyword_index is None:
            raise RuntimeError("keyword_index 尚未初始化。")

        if self.vector_index is None:
            raise RuntimeError("vector_index 尚未初始化。")

        route_results: List[RouteRecallResult] = []

        for keyword in keywords:
            keyword_hits = self.keyword_index.search(
                keyword,
                top_k=self.config.per_keyword_top_k,
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

            vector_hits = self.vector_index.search(
                keyword,
                top_k=self.config.per_keyword_top_k,
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

    def _rerank(
        self,
        query: str,
        fusion_hits: List[RRFFusionHit],
        keyword_count: int,
    ) -> List[RerankResult]:
        """
        对 RRF 融合结果进行 Rerank 精排。
        """
        rerank_candidates = [
            RerankDocument(
                doc_index=hit.doc_index,
                text=self.documents[hit.doc_index].rerank_text,
            )
            for hit in fusion_hits
        ]

        rerank_top_n = self._calculate_rerank_top_n(
            keyword_count=keyword_count,
            candidate_count=len(rerank_candidates),
        )

        return self.rerank_client.rerank(
            query=query,
            documents=rerank_candidates,
            top_n=rerank_top_n,
        )

    def _calculate_rerank_top_n(
        self,
        keyword_count: int,
        candidate_count: int,
    ) -> int:
        """
        根据关键词数量动态计算 Rerank 输出数量。
        """
        dynamic_top_n = keyword_count * self.config.rerank_top_multiplier

        top_n = max(
            self.config.rerank_min_top_n,
            min(dynamic_top_n, self.config.rerank_max_top_n),
        )

        return min(top_n, candidate_count)

    def _build_schema_hits(
        self,
        rerank_results: List[RerankResult],
        fusion_hits: List[RRFFusionHit],
    ) -> List[SchemaHit]:
        """
        将 Rerank 结果转换为 SchemaHit。
        """
        fusion_hit_map = {
            hit.doc_index: hit
            for hit in fusion_hits
        }

        schema_hits: List[SchemaHit] = []

        for result in rerank_results:
            doc = self.documents[result.doc_index]
            fusion_hit = fusion_hit_map.get(result.doc_index)

            best_rank_by_source = (
                fusion_hit.best_rank_by_source
                if fusion_hit is not None
                else {}
            )

            try:
                schema_hit = SchemaHit(
                    doc_id=doc.doc_id,
                    score=result.rerank_score,
                    column=doc.column,
                    keyword_rank=best_rank_by_source.get("keyword"),
                    vector_rank=best_rank_by_source.get("vector"),
                    rrf_score=fusion_hit.score if fusion_hit is not None else 0.0,
                    rerank_score=result.rerank_score,
                )
            except TypeError:
                # 兼容旧版 SchemaHit 结构。
                schema_hit = SchemaHit(
                    doc_id=doc.doc_id,
                    score=result.rerank_score,
                    column=doc.column,
                )

            schema_hits.append(schema_hit)

        return schema_hits

    def _build_indices(self) -> None:
        """
        构建 BM25 索引和向量索引。
        """
        keyword_texts = [
            doc.keyword_text
            for doc in self.documents
        ]

        self.keyword_index = BM25Index(keyword_texts)

        vector_texts = [
            doc.vector_text
            for doc in self.documents
        ]

        self.vector_index = VectorIndex(self.embedding_client)
        self.vector_index.build(vector_texts)

    def _build_documents(self) -> None:
        """
        构建混合检索字段文档。
        """
        self.documents = []

        for column in self.columns:
            table_meta = self.business_meta.get(column.table_name, {})
            column_meta = table_meta.get("columns", {}).get(column.column_name, {})

            keyword_text = (
                column_meta.get("keyword_text")
                or self._get_index_text(column, "keyword_text")
                or self._build_default_keyword_text(column)
            )

            vector_text = (
                column_meta.get("vector_text")
                or self._get_index_text(column, "vector_text")
                or self._build_default_vector_text(column)
            )

            rerank_text = (
                column_meta.get("rerank_text")
                or self._get_index_text(column, "rerank_text")
                or self._build_default_rerank_text(column)
            )

            self.documents.append(
                HybridFieldDocument(
                    doc_id=column.full_name,
                    column=column,
                    keyword_text=keyword_text,
                    vector_text=vector_text,
                    rerank_text=rerank_text,
                )
            )

    def _get_index_text(
        self,
        column: object,
        attr_name: str,
    ) -> str:
        """
        从 ColumnSchema.index_texts 中读取索引文本。
        """
        index_texts = getattr(column, "index_texts", None)

        if index_texts is None:
            return ""

        return getattr(index_texts, attr_name, "") or ""

    def _enrich_columns_with_business_meta(self) -> None:
        """
        将业务元数据补充回 ColumnSchema。
        """
        for column in self.columns:
            table_meta = self.business_meta.get(column.table_name, {})
            column_meta = table_meta.get("columns", {}).get(column.column_name, {})

            if table_meta:
                column.table_description = table_meta.get(
                    "description",
                    getattr(column, "table_description", ""),
                )
                column.table_aliases = table_meta.get(
                    "aliases",
                    getattr(column, "table_aliases", []),
                )

            if not column_meta:
                continue

            column.description = column_meta.get(
                "description",
                getattr(column, "description", ""),
            )
            column.aliases = column_meta.get(
                "aliases",
                getattr(column, "aliases", []),
            )
            column.semantic_role = column_meta.get(
                "semantic_role",
                getattr(column, "semantic_role", ""),
            )
            column.value_range = column_meta.get(
                "value_range",
                getattr(column, "value_range", ""),
            )
            column.data_distribution = column_meta.get(
                "data_distribution",
                getattr(column, "data_distribution", ""),
            )
            column.business_usage = column_meta.get(
                "business_usage",
                getattr(column, "business_usage", ""),
            )

            if "samples" in column_meta:
                column.samples = [
                    str(item)
                    for item in column_meta["samples"]
                ]

    def _build_default_keyword_text(
        self,
        column: object,
    ) -> str:
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

    def _build_default_vector_text(
        self,
        column: object,
    ) -> str:
        """
        构建默认向量索引文本。
        """
        parts = [
            f"字段名：{getattr(column, 'column_name', '')}",
            f"所属表：{getattr(column, 'table_name', '')}",
            f"所属表业务含义：{getattr(column, 'table_description', '')}",
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

    def _build_default_rerank_text(
        self,
        column: object,
    ) -> str:
        """
        构建默认 Rerank 文本。
        """
        parts = [
            f"字段：{getattr(column, 'table_name', '')}.{getattr(column, 'column_name', '')}",
            f"数据类型：{getattr(column, 'data_type', '')}",
            f"所属表业务含义：{getattr(column, 'table_description', '')}",
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


if __name__ == "__main__":
    # 这里复用完整链路 Demo 中的测试数据库和业务元数据。
    # 该 import 只用于示例，不影响服务类本身被其他模块导入。
    from schema_retrieval.hybrid_rerank_schema_demo import (
        create_demo_database,
        get_business_meta,
    )

    db_path = create_demo_database()
    business_meta = get_business_meta()

    embedding_client = AliyunEmbeddingClient(
        AliyunEmbeddingConfig(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            embedding_url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
            model="text-embedding-v4",
            dimensions=1024,
        )
    )

    # 如果你想使用大模型自动抽取关键词，就配置 keyword_extractor。
    # 如果 retrieve() 中直接传 keywords，则可以不配置 keyword_extractor。
    keyword_extractor = None

    if os.getenv("DASHSCOPE_API_KEY"):
        keyword_extractor = AliyunKeywordExtractor(
            AliyunKeywordExtractorConfig(
                api_key=os.getenv("DASHSCOPE_API_KEY", ""),
                model="qwen-plus",
            )
        )

    rerank_client = AliyunRerankClient(
        AliyunRerankConfig(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", ""),

            # 这里按你的要求默认使用 qwen-rerank。
            # 如果你的环境使用 qwen3-rerank，可以改为：
            # model="qwen3-rerank"
            model=os.getenv("DASHSCOPE_RERANK_MODEL", "qwen-rerank"),
        )
    )

    service = HybridSchemaRetrievalService.from_sqlite(
        db_path=db_path,
        database_name="demo_finance",
        business_meta=business_meta,
        embedding_client=embedding_client,
        rerank_client=rerank_client,
        keyword_extractor=keyword_extractor,
        config=HybridSchemaRetrievalConfig(
            per_keyword_top_k=20,
            include_join_columns=True,
            rrf_config=RRFFusionConfig(
                rrf_k=60,
                truncate_multiplier=6,
                min_fused_top_k=10,
                max_fused_top_k=50,
                final_top_k=20,
                route_weights={
                    "keyword": 1.0,
                    "vector": 1.0,
                },
            ),
            rerank_top_multiplier=2,
            rerank_min_top_n=2,
            rerank_max_top_n=20,
        ),
        sample_size=5,
    )

    query = "查询总交易笔数大于50000的利率是多少"

    # 示例 1：手动传关键词。
    # 如果你想强制使用大模型抽取关键词，把 keywords 参数删掉即可。
    result = service.retrieve(
        query=query,
        keywords=["总交易笔数", "利率"],
    )

    print("=" * 80)
    print("Hybrid Schema Retrieval Service Demo")
    print("=" * 80)

    print("\n[1] Query:")
    print(result.query)

    print("\n[2] Keywords:")
    print("，".join(result.keywords))

    print("\n[3] RRF fusion hits:")
    for rank, hit in enumerate(result.fusion_hits, start=1):
        doc = service.documents[hit.doc_index]
        col = doc.column

        print(
            f"{rank}. {col.table_name}.{col.column_name} "
            f"rrf_score={hit.score:.6f} "
            f"sources={hit.sources} "
            f"matched_terms={hit.matched_terms} "
            f"best_rank_by_source={hit.best_rank_by_source}"
        )

    print("\n[4] Rerank results:")
    for rank, item in enumerate(result.rerank_results, start=1):
        doc = service.documents[item.doc_index]
        col = doc.column

        print(
            f"{rank}. {col.table_name}.{col.column_name} "
            f"rerank_score={item.rerank_score:.6f}"
        )

    print("\n[5] Schema graph prompt context:")
    print(result.schema_graph.to_prompt_context())
