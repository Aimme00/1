from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from schema_retrieval.sqlite_loader import SQLiteSchemaLoader


# =============================================================================
# 1. 向量召回文档对象
# =============================================================================


@dataclass
class VectorFieldDocument:
    """
    字段级向量检索文档。

    该对象只用于当前向量召回 Demo，不依赖 document_builder.py。
    这样可以避免 objects.py 和 document_builder.py 还在逐步改造时影响 Demo 运行。
    """

    doc_id: str
    """字段文档唯一标识。"""

    column: object
    """字段 Schema 对象。"""

    vector_text: str
    """向量索引文本。"""


# =============================================================================
# 2. 关键词抽取
# =============================================================================


@dataclass
class KeywordExtractorConfig:
    """
    关键词抽取模型配置。

    当 api_key 为空时，使用本地规则抽取，便于无 API Key 环境下跑通 Demo。
    当 api_key 不为空时，调用阿里云百炼 OpenAI 兼容 Chat Completions 接口。
    """

    api_key: str = ""
    """阿里云百炼 API Key。为空时读取环境变量 DASHSCOPE_API_KEY。"""

    chat_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    """阿里云百炼 OpenAI 兼容 Chat Completions 接口地址。"""

    model: str = "qwen-plus"
    """用于关键词抽取的大模型名称。"""

    timeout: int = 60
    """HTTP 请求超时时间，单位为秒。"""


class KeywordExtractor:
    """
    基于大模型的核心检索词抽取器。

    该抽取器用于从用户 Query 中提取字段、指标、表级概念和关键业务语义信息。
    向量召回阶段不直接对完整 Query 做 Embedding，而是对抽取后的关键词做 Embedding。
    """

    SYSTEM_PROMPT = """你是一个字段解析助手。

任务：
从用户输入的查询中提取核心字段、业务指标、表级概念以及关键业务语义信息。

要求：
1. 提取字段、指标、表格或业务概念等核心关键词
2. 尽量保留用户原始表达，不进行语义改写
3. 不输出解释、分析过程或额外内容
4. 多个关键词之间使用全角逗号分隔
5. 若存在聚合语义，如数量、总金额、平均值等，需完整保留
6. 若存在业务限定词，如年化利率、总交易笔数等，禁止拆分

输出格式：
解析结果：<关键词1>，<关键词2>

示例1：
用户Query：查询用户年龄大于30的用户数量
解析结果：用户年龄，用户数量

示例2：
用户Query：统计每个城市的订单总金额
解析结果：城市，订单总金额

示例3：
用户Query：查询总交易笔数大于50000的利率是多少
解析结果：总交易笔数，利率
"""

    def __init__(self, config: KeywordExtractorConfig | None = None):
        self.config = config or KeywordExtractorConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

    def extract(self, query: str) -> List[str]:
        """
        从用户 Query 中抽取核心检索词。

        Args:
            query: 用户自然语言查询。

        Returns:
            List[str]: 核心检索词列表。
        """
        if not self.config.api_key:
            return self._extract_by_rule(query)

        content = self._call_llm(query)
        return self._parse_keywords(content)

    def _call_llm(self, query: str) -> str:
        """
        调用阿里云百炼大模型完成关键词抽取。

        Args:
            query: 用户自然语言查询。

        Returns:
            str: 大模型返回的原始文本。
        """
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": self.SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"用户Query：{query}",
                },
            ],
            "temperature": 0,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        request = urllib.request.Request(
            self.config.chat_url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8")
                result = json.loads(body)

        except Exception as exc:
            raise RuntimeError(f"调用关键词抽取模型失败: {exc}") from exc

        try:
            return result["choices"][0]["message"]["content"].strip()

        except Exception as exc:
            raise RuntimeError(f"解析关键词抽取模型返回失败，原始返回: {result}") from exc

    def _parse_keywords(self, text: str) -> List[str]:
        """
        从模型输出中解析关键词。

        Args:
            text: 模型输出文本。

        Returns:
            List[str]: 去重后的关键词列表。
        """
        match = re.search(r"解析结果[:：]\s*(.+)", text)

        if match:
            keyword_text = match.group(1).strip()
        else:
            keyword_text = text.strip()

        raw_keywords = re.split(r"[，,\n；;]+", keyword_text)

        keywords: List[str] = []
        seen = set()

        for item in raw_keywords:
            keyword = item.strip()

            if not keyword:
                continue

            if keyword not in seen:
                keywords.append(keyword)
                seen.add(keyword)

        if not keywords:
            raise ValueError(f"未能从模型输出中解析到关键词，模型输出为: {text}")

        return keywords

    def _extract_by_rule(self, query: str) -> List[str]:
        """
        本地规则抽取。

        仅用于无 API Key 环境下跑通 Demo。生产环境建议使用大模型抽取。
        """
        known_phrases = [
            "订单总金额",
            "总交易笔数",
            "累计交易笔数",
            "交易笔数",
            "交易次数",
            "年化利率",
            "利率",
            "城市",
            "销售额",
            "订单金额",
            "用户数量",
            "用户年龄",
        ]

        keywords: List[str] = []
        seen = set()

        for phrase in known_phrases:
            if phrase in query and phrase not in seen:
                keywords.append(phrase)
                seen.add(phrase)

        if not keywords:
            normalized = re.sub(r"[？?。.\s]+", "", query)
            if normalized:
                keywords.append(normalized)

        return keywords


# =============================================================================
# 3. Embedding Client
# =============================================================================


@dataclass
class AliyunEmbeddingConfig:
    """
    阿里云百炼 Embedding 配置。

    当 api_key 为空时，使用本地 Hash Embedding，便于无 API Key 环境下跑通 Demo。
    当 api_key 不为空时，调用阿里云百炼 OpenAI 兼容 Embedding 接口。
    """

    api_key: str = ""
    """阿里云百炼 API Key。为空时读取环境变量 DASHSCOPE_API_KEY。"""

    embedding_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    """阿里云百炼 OpenAI 兼容 Embedding 接口地址。"""

    model: str = "text-embedding-v4"
    """Embedding 模型名称。"""

    dimensions: int = 1024
    """Embedding 向量维度。"""

    timeout: int = 60
    """HTTP 请求超时时间，单位为秒。"""


class AliyunEmbeddingClient:
    """
    阿里云百炼 Embedding Client。

    该 Client 用于将字段索引文本和抽取后的核心检索词转换为向量。
    """

    def __init__(self, config: AliyunEmbeddingConfig | None = None):
        self.config = config or AliyunEmbeddingConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        批量生成文本向量。

        Args:
            texts: 待向量化的文本列表。

        Returns:
            np.ndarray: 归一化后的向量矩阵。
        """
        if not texts:
            return np.empty((0, self.config.dimensions), dtype=np.float32)

        if not self.config.api_key:
            return self._local_hash_embedding(texts)

        return self._call_aliyun_embedding(texts)

    def _call_aliyun_embedding(self, texts: List[str]) -> np.ndarray:
        """
        调用阿里云百炼 Embedding API。

        Args:
            texts: 待向量化的文本列表。

        Returns:
            np.ndarray: 归一化后的向量矩阵。
        """
        payload = {
            "model": self.config.model,
            "input": texts,
            "dimensions": self.config.dimensions,
            "encoding_format": "float",
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        request = urllib.request.Request(
            self.config.embedding_url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8")
                result = json.loads(body)

        except Exception as exc:
            raise RuntimeError(f"调用阿里云 Embedding API 失败: {exc}") from exc

        try:
            data_items = sorted(result["data"], key=lambda item: item.get("index", 0))
            embeddings = [item["embedding"] for item in data_items]

        except Exception as exc:
            raise RuntimeError(f"解析 Embedding API 返回结果失败，原始返回: {result}") from exc

        vectors = np.array(embeddings, dtype=np.float32)
        return self._normalize(vectors)

    def _local_hash_embedding(self, texts: List[str]) -> np.ndarray:
        """
        本地 Hash Embedding。

        仅用于无 API Key 环境下验证流程，不代表真实语义向量效果。
        """
        vectors = np.zeros((len(texts), self.config.dimensions), dtype=np.float32)

        for row_index, text in enumerate(texts):
            for token in self._tokenize_for_hash(text):
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                col_index = int(digest, 16) % self.config.dimensions
                vectors[row_index, col_index] += 1.0

        return self._normalize(vectors)

    def _normalize(self, vectors: np.ndarray) -> np.ndarray:
        """
        对向量进行 L2 归一化。

        Args:
            vectors: 原始向量矩阵。

        Returns:
            np.ndarray: 归一化后的向量矩阵。
        """
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-8)

    def _tokenize_for_hash(self, text: str) -> List[str]:
        """
        本地 Hash Embedding 使用的简单分词方法。
        """
        text = text.lower()
        tokens: List[str] = []

        tokens.extend(re.findall(r"[a-zA-Z0-9_]+", text))

        chinese_spans = re.findall(r"[\u4e00-\u9fff]+", text)
        for span in chinese_spans:
            tokens.append(span)
            tokens.extend(list(span))

            for i in range(len(span) - 1):
                tokens.append(span[i:i + 2])

            for i in range(len(span) - 2):
                tokens.append(span[i:i + 3])

        return [token for token in tokens if token.strip()]


# =============================================================================
# 4. 内存向量索引
# =============================================================================


class VectorIndex:
    """
    内存向量索引。

    当前 Demo 使用 numpy 矩阵保存字段向量，并通过余弦相似度完成向量召回。
    生产环境可替换为 FAISS、Milvus、PGVector 或 Elasticsearch dense_vector。
    """

    def __init__(self, embedding_client: AliyunEmbeddingClient):
        self.embedding_client = embedding_client
        self.documents: List[str] = []
        self.embeddings: np.ndarray | None = None

    def build(self, documents: List[str]) -> None:
        """
        构建向量索引。

        Args:
            documents: 字段级向量索引文本列表。
        """
        self.documents = documents
        self.embeddings = self.embedding_client.embed_texts(documents)

    def search(self, query_text: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        使用单个检索词进行向量召回。

        Args:
            query_text: 单个核心检索词。
            top_k: 返回候选数量。

        Returns:
            List[Tuple[int, float]]: 文档下标与相似度得分。
        """
        if self.embeddings is None:
            raise RuntimeError("VectorIndex 尚未构建，请先调用 build()。")

        query_vector = self.embedding_client.embed_texts([query_text])[0]
        scores = self.embeddings @ query_vector

        top_indices = np.argsort(-scores)[:top_k]

        return [
            (int(index), float(scores[index]))
            for index in top_indices
        ]


# =============================================================================
# 5. Demo 数据库与业务元数据
# =============================================================================


def create_demo_database() -> Path:
    """
    创建向量召回 Demo 使用的测试数据库。

    测试数据库包含两张表：
    - trade_summary: 用户交易汇总表
    - interest_info: 用户利率信息表
    """
    db_path = Path(tempfile.gettempdir()) / "text2sql_vector_recall_demo.db"

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
    返回 Demo 使用的表级和字段级业务元数据。

    向量召回主要依赖字段语义描述，因此该元数据重点维护字段描述、
    字段别名、业务用途和 vector_text。
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
                },
                "total_trade_count": {
                    "description": "用户累计交易总笔数，表示用户在统计周期内完成的交易次数。",
                    "aliases": ["总交易笔数", "累计交易笔数", "交易笔数", "交易次数"],
                    "semantic_role": "metric_filter",
                    "value_range": "整数，>= 0",
                    "data_distribution": "多数用户集中在0-50区间，高频交易用户可能超过50000。",
                    "business_usage": "用于衡量用户交易活跃程度，筛选高频交易用户。",
                    "samples": ["0", "12", "5800", "56000", "102430"],
                    "index_texts": {
                        "vector_text": (
                            "字段名：total_trade_count。所属表：trade_summary，"
                            "该表记录用户交易汇总信息。该字段表示用户在统计周期内的累计交易总笔数，"
                            "用于衡量用户交易活跃程度，可用于筛选高频交易用户。"
                        )
                    },
                },
                "total_trade_amount": {
                    "description": "用户累计交易金额，表示用户在统计周期内完成交易的总金额。",
                    "aliases": ["交易金额", "累计交易金额", "成交金额", "交易总额"],
                    "semantic_role": "metric",
                    "value_range": ">= 0",
                    "business_usage": "用于衡量用户交易价值、统计交易金额或识别高价值用户。",
                    "samples": ["0.00", "5300.50", "230000.00", "1800000.00"],
                    "index_texts": {
                        "vector_text": (
                            "字段名：total_trade_amount。所属表：trade_summary。"
                            "该字段表示用户在统计周期内完成交易的累计金额，"
                            "用于衡量用户交易价值或交易规模。"
                        )
                    },
                },
                "active_days": {
                    "description": "用户在统计周期内发生交易的活跃天数。",
                    "aliases": ["活跃天数", "交易活跃天数"],
                    "semantic_role": "metric",
                    "business_usage": "用于衡量用户交易频率和持续活跃程度。",
                },
                "last_trade_time": {
                    "description": "用户最近一次交易发生时间。",
                    "aliases": ["最近交易时间", "最后交易时间"],
                    "semantic_role": "time",
                    "business_usage": "用于判断用户最近活跃情况。",
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
                },
                "interest_rate": {
                    "description": "用户对应的年化利率数值。",
                    "aliases": ["利率", "年化利率", "用户利率", "利率数值"],
                    "semantic_role": "output_metric",
                    "value_range": "0-100",
                    "business_usage": "用于展示或分析用户当前适用的年化利率。",
                    "samples": ["2.35", "3.12", "4.58", "4.95"],
                    "index_texts": {
                        "vector_text": (
                            "字段名：interest_rate。所属表：interest_info，"
                            "该表记录用户利率信息。该字段表示用户当前对应的年化利率数值，"
                            "通常作为查询结果输出。"
                        )
                    },
                },
                "rate_type": {
                    "description": "用户适用的利率类型，例如standard、vip、high_value。",
                    "aliases": ["利率类型", "费率类型"],
                    "semantic_role": "dimension",
                    "business_usage": "用于区分不同用户分层下的利率类型。",
                },
                "effective_status": {
                    "description": "利率生效状态，例如active、inactive。",
                    "aliases": ["生效状态", "利率状态"],
                    "semantic_role": "filter",
                    "business_usage": "用于筛选当前有效或无效的利率记录。",
                },
            },
        },
    }


def enrich_columns_with_business_meta(columns: list, business_meta: dict) -> None:
    """
    将业务元数据补充回 ColumnSchema。

    该函数用于兼容当前 Demo：即使 sqlite_loader.py 还没有支持 value_range、
    business_usage、index_texts 等字段，也可以在 Demo 中把这些信息补充到字段对象上。
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
        column.data_distribution = column_meta.get("data_distribution", getattr(column, "data_distribution", ""))
        column.business_usage = column_meta.get("business_usage", getattr(column, "business_usage", ""))

        if "samples" in column_meta:
            column.samples = [str(item) for item in column_meta["samples"]]

        index_texts = column_meta.get("index_texts", {})
        column.vector_text = index_texts.get("vector_text", "")


def build_vector_documents(columns: list) -> List[VectorFieldDocument]:
    """
    构建字段级向量检索文档。

    Args:
        columns: 字段 Schema 列表。

    Returns:
        List[VectorFieldDocument]: 字段级向量检索文档列表。
    """
    documents: List[VectorFieldDocument] = []

    for column in columns:
        doc_id = getattr(column, "full_name", None)
        if not doc_id:
            doc_id = f"{column.database}.{column.table_name}.{column.column_name}"

        vector_text = getattr(column, "vector_text", "")

        if not vector_text:
            vector_text = "\n".join(
                part for part in [
                    f"字段名：{column.column_name}",
                    f"所属表：{column.table_name}",
                    f"所属表业务含义：{getattr(column, 'table_description', '')}",
                    f"字段含义：{getattr(column, 'description', '')}",
                    f"字段别名：{', '.join(getattr(column, 'aliases', []))}" if getattr(column, "aliases", []) else "",
                    f"业务用途：{getattr(column, 'business_usage', '')}" if getattr(column, "business_usage", "") else "",
                    f"字段角色：{getattr(column, 'semantic_role', '')}" if getattr(column, "semantic_role", "") else "",
                ]
                if part
            )

        documents.append(
            VectorFieldDocument(
                doc_id=doc_id,
                column=column,
                vector_text=vector_text,
            )
        )

    return documents


# =============================================================================
# 6. 多关键词向量召回
# =============================================================================


def search_by_extracted_keywords(
    vector_index: VectorIndex,
    documents: List[VectorFieldDocument],
    keywords: List[str],
    per_keyword_top_k: int = 5,
    final_top_k: int = 5,
) -> List[Tuple[int, float, List[str]]]:
    """
    使用抽取后的核心关键词进行多路向量召回。

    每个关键词会单独进行向量检索。多个关键词的召回结果会按照字段文档去重，
    并保留每个字段的最高相似度得分。

    Args:
        vector_index: 已构建完成的向量索引。
        documents: 字段级检索文档列表。
        keywords: 核心检索词列表。
        per_keyword_top_k: 每个关键词单独召回的候选数量。
        final_top_k: 最终返回的字段数量。

    Returns:
        List[Tuple[int, float, List[str]]]: 文档下标、最终得分、命中的关键词列表。
    """
    merged: Dict[int, Dict[str, object]] = {}

    for keyword in keywords:
        keyword_hits = vector_index.search(keyword, top_k=per_keyword_top_k)

        for doc_index, score in keyword_hits:
            if doc_index not in merged:
                merged[doc_index] = {
                    "score": score,
                    "matched_keywords": [keyword],
                }
            else:
                merged[doc_index]["score"] = max(
                    float(merged[doc_index]["score"]),
                    score,
                )

                matched_keywords = merged[doc_index]["matched_keywords"]
                assert isinstance(matched_keywords, list)

                if keyword not in matched_keywords:
                    matched_keywords.append(keyword)

    results = [
        (
            doc_index,
            float(payload["score"]),
            payload["matched_keywords"],
        )
        for doc_index, payload in merged.items()
    ]

    results.sort(key=lambda item: item[1], reverse=True)

    return results[:final_top_k]


# =============================================================================
# 7. 主流程
# =============================================================================


def main() -> None:
    db_path = create_demo_database()
    business_meta = get_business_meta()

    print("=" * 80)
    print("向量召回 Demo")
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

    documents = build_vector_documents(columns)

    print("\n[1] Vector documents:")
    for index, doc in enumerate(documents):
        print(f"{index}. {doc.doc_id}")
        print(f"   {doc.vector_text}")

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

    query = "查询总交易笔数大于50000的年化利率是多少"

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

    hits = search_by_extracted_keywords(
        vector_index=vector_index,
        documents=documents,
        keywords=keywords,
        per_keyword_top_k=5,
        final_top_k=5,
    )

    print("\n[5] Vector recall results by extracted keywords:")
    for rank, (doc_index, score, matched_keywords) in enumerate(hits, start=1):
        doc = documents[doc_index]
        col = doc.column

        print(
            f"{rank}. {col.table_name}.{col.column_name} "
            f"score={score:.6f} "
            f"matched_keywords={matched_keywords}"
        )
        print(f"   字段含义：{getattr(col, 'description', '')}")
        print(f"   向量文本：{doc.vector_text}")


if __name__ == "__main__":
    main()
