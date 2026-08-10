from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RerankDocument:
    """
    待重排序的候选文档。
    """

    doc_index: int
    """候选文档在 documents 列表中的下标。"""

    text: str
    """候选文档文本。"""


@dataclass
class RerankResult:
    """
    重排序结果。
    """

    doc_index: int
    """原始候选文档下标。"""

    rerank_score: float
    """Rerank 模型输出的相关性分数。"""

    text: str
    """候选文档文本。"""


@dataclass
class AliyunRerankConfig:
    """
    阿里云百炼 Rerank 配置。

    qwen3-rerank 使用业务空间专属 endpoint。
    """

    api_key: str = ""
    """阿里云百炼 API Key。为空时读取环境变量 DASHSCOPE_API_KEY。"""

    workspace_id: str = ""
    """阿里云百炼业务空间 ID。为空时读取环境变量 DASHSCOPE_WORKSPACE_ID。"""

    rerank_url: str = ""
    """
    Rerank HTTP 接口地址。

    如果不传，则根据 workspace_id 自动拼接：
    https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-api/v1/reranks
    """

    model: str = "qwen3-rerank"
    """Rerank 模型名称。官方当前推荐 qwen3-rerank。"""

    timeout: int = 60
    """HTTP 请求超时时间，单位为秒。"""

    instruct: str = (
        "Given a database schema field retrieval query, "
        "rank candidate schema fields by relevance to the query."
    )
    """Rerank 任务说明。"""


class AliyunRerankClient:
    """
    阿里云百炼 Rerank Client。

    该 Client 负责对 RRF 融合后的候选字段进行二次排序。
    如果没有配置 API Key 或 Workspace ID，则使用本地 Mock 排序，方便先跑通 Demo。
    """

    def __init__(self, config: Optional[AliyunRerankConfig] = None):
        self.config = config or AliyunRerankConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

        if not self.config.workspace_id:
            self.config.workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID", "")

        if not self.config.rerank_url and self.config.workspace_id:
            self.config.rerank_url = (
                f"https://{self.config.workspace_id}.cn-beijing.maas.aliyuncs.com"
                f"/compatible-api/v1/reranks"
            )

    def rerank(
        self,
        query: str,
        documents: List[RerankDocument],
        top_n: int,
    ) -> List[RerankResult]:
        """
        对候选文档进行重排序。

        Args:
            query: 用户原始查询。
            documents: 待重排序候选文档。
            top_n: 返回的候选数量。

        Returns:
            List[RerankResult]: 重排序后的候选结果。
        """
        if not documents:
            return []

        top_n = min(top_n, len(documents))

        if not self.config.api_key or not self.config.rerank_url:
            return self._mock_rerank(
                query=query,
                documents=documents,
                top_n=top_n,
            )

        return self._call_aliyun_rerank(
            query=query,
            documents=documents,
            top_n=top_n,
        )

    def _call_aliyun_rerank(
        self,
        query: str,
        documents: List[RerankDocument],
        top_n: int,
    ) -> List[RerankResult]:
        """
        调用阿里云百炼 qwen3-rerank。
        """
        doc_texts = [
            doc.text
            for doc in documents
        ]

        payload = {
            "model": self.config.model,
            "query": query,
            "documents": doc_texts,
            "top_n": top_n,
            "instruct": self.config.instruct,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        request = urllib.request.Request(
            self.config.rerank_url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8")
                result = json.loads(body)

        except Exception as exc:
            raise RuntimeError(f"调用阿里云 Rerank API 失败: {exc}") from exc

        try:
            result_items = result["results"]
        except Exception as exc:
            raise RuntimeError(f"解析 Rerank API 返回失败，原始返回: {result}") from exc

        output: List[RerankResult] = []

        for item in result_items:
            local_index = int(item["index"])
            source_doc = documents[local_index]

            output.append(
                RerankResult(
                    doc_index=source_doc.doc_index,
                    rerank_score=float(item["relevance_score"]),
                    text=source_doc.text,
                )
            )

        output.sort(key=lambda item: item.rerank_score, reverse=True)

        return output

    def _mock_rerank(
        self,
        query: str,
        documents: List[RerankDocument],
        top_n: int,
    ) -> List[RerankResult]:
        """
        本地 Mock Rerank。

        仅用于无 API Key 或无 Workspace ID 时跑通流程。
        """
        query_terms = [
            term
            for term in ["总交易笔数", "交易笔数", "利率", "年化利率", "用户利率"]
            if term in query
        ]

        scored_results: List[RerankResult] = []

        for doc in documents:
            score = 0.0

            for term in query_terms:
                if term in doc.text:
                    score += 1.0

            # 简单增强：字段名或语义里包含核心业务词时加分。
            if "total_trade_count" in doc.text:
                score += 0.8

            if "interest_rate" in doc.text:
                score += 0.8

            scored_results.append(
                RerankResult(
                    doc_index=doc.doc_index,
                    rerank_score=score,
                    text=doc.text,
                )
            )

        scored_results.sort(key=lambda item: item.rerank_score, reverse=True)

        return scored_results[:top_n]