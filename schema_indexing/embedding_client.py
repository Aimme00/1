from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class AliyunEmbeddingConfig:
    """阿里云百炼 Embedding 配置。"""

    api_key: str = ""
    embedding_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    model: str = "text-embedding-v4"
    dimensions: int = 1024
    timeout: int = 60
    use_mock_when_no_api_key: bool = True


class AliyunEmbeddingClient:
    """
    Embedding Client。

    有 API Key 时调用阿里云百炼 Embedding。
    无 API Key 时默认使用本地 Hash Embedding，用于跑通索引构建流程。
    """

    def __init__(self, config: AliyunEmbeddingConfig | None = None):
        self.config = config or AliyunEmbeddingConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """批量生成文本向量。"""
        if not texts:
            return np.empty((0, self.config.dimensions), dtype=np.float32)

        if self.config.api_key:
            return self._call_aliyun_embedding(texts)

        if self.config.use_mock_when_no_api_key:
            return self._mock_embedding(texts)

        raise ValueError("缺少 DASHSCOPE_API_KEY，无法调用真实 Embedding API。")

    def _call_aliyun_embedding(self, texts: List[str]) -> np.ndarray:
        """调用阿里云百炼 Embedding API。"""
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
            raise RuntimeError(f"调用 Embedding API 失败: {exc}") from exc

        try:
            data_items = sorted(result["data"], key=lambda item: item.get("index", 0))
            embeddings = [item["embedding"] for item in data_items]
        except Exception as exc:
            raise RuntimeError(f"解析 Embedding 返回失败，原始返回：{result}") from exc

        vectors = np.array(embeddings, dtype=np.float32)
        return self._normalize(vectors)

    def _mock_embedding(self, texts: List[str]) -> np.ndarray:
        """本地 Hash Embedding。"""
        vectors = np.zeros((len(texts), self.config.dimensions), dtype=np.float32)

        for row_index, text in enumerate(texts):
            for token in self._simple_tokenize(text):
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                col_index = int(digest, 16) % self.config.dimensions
                vectors[row_index, col_index] += 1.0

        return self._normalize(vectors)

    def _simple_tokenize(self, text: str) -> List[str]:
        """Mock Embedding 使用的简单中英文切分。"""
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

    def _normalize(self, vectors: np.ndarray) -> np.ndarray:
        """L2 归一化。"""
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-8)
