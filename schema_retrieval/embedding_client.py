from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class AliyunEmbeddingConfig:
    """
    阿里云百炼 Embedding 接口配置。

    默认使用 OpenAI 兼容 Embedding 接口。
    如果使用业务空间专属域名，可以通过 embedding_url 传入完整的 embeddings 地址。
    """

    api_key: str = ""
    """阿里云百炼 API Key。为空时会读取环境变量 DASHSCOPE_API_KEY。"""

    embedding_url: str = ""
    """
    Embedding HTTP 接口地址。

    示例：
    https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings

    或业务空间专属域名：
    https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings
    """

    model: str = "text-embedding-v4"
    """Embedding 模型名称。"""

    dimensions: int = 1024
    """向量维度。text-embedding-v4 默认 1024，也可以设置为 2048、1536、768、512、256、128、64。"""

    timeout: int = 60
    """HTTP 请求超时时间，单位为秒。"""


class AliyunEmbeddingClient:
    """
    阿里云百炼 Embedding Client。

    该 Client 负责调用阿里云 Embedding API，将文本转换为向量。
    当前实现使用 HTTP 方式调用 OpenAI 兼容接口，不依赖 openai SDK。
    """

    def __init__(self, config: AliyunEmbeddingConfig | None = None):
        self.config = config or AliyunEmbeddingConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

        if not self.config.embedding_url:
            self.config.embedding_url = os.getenv(
                "DASHSCOPE_EMBEDDING_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
            )

        if not self.config.api_key:
            raise ValueError(
                "缺少阿里云百炼 API Key。请设置环境变量 DASHSCOPE_API_KEY，"
                "或在 AliyunEmbeddingConfig(api_key='...') 中传入。"
            )

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        批量生成文本向量。

        Args:
            texts: 待向量化的文本列表。

        Returns:
            np.ndarray: 归一化后的向量矩阵，形状为 [文本数量, 向量维度]。
        """
        if not texts:
            return np.empty((0, self.config.dimensions), dtype=np.float32)

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
            embeddings = [
                item["embedding"]
                for item in sorted(result["data"], key=lambda x: x.get("index", 0))
            ]
        except Exception as exc:
            raise RuntimeError(f"解析 Embedding API 返回结果失败，原始返回: {result}") from exc

        vectors = np.array(embeddings, dtype=np.float32)

        # 归一化，后续可以直接用点积计算 cosine similarity。
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-8)

        return vectors