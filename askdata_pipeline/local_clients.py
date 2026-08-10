from __future__ import annotations

import hashlib
import re
from typing import List

import numpy as np


class LocalHashEmbeddingClient:
    """
    本地 Hash Embedding。

    用于无 API Key 时跑通端到端流程。
    该实现只用于 Demo，不代表真实语义召回效果。
    """

    def __init__(self, dimensions: int = 1024):
        self.dimensions = dimensions

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        批量生成文本向量。
        """
        vectors = np.zeros((len(texts), self.dimensions), dtype=np.float32)

        for row_index, text in enumerate(texts):
            for token in self._tokenize(text):
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                col_index = int(digest, 16) % self.dimensions
                vectors[row_index, col_index] += 1.0

        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-8)

    def _tokenize(self, text: str) -> List[str]:
        """
        简单中英文混合切分。
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


class SimpleKeywordExtractor:
    """
    简单关键词抽取器。

    真实流程中可以替换为大模型关键词抽取。
    当前 Demo 为了离线可运行，使用规则抽取核心业务词。
    """

    def extract(self, query: str) -> List[str]:
        """
        从用户 Query 中抽取关键词。
        """
        keywords: List[str] = []

        phrase_rules = [
            "总交易笔数",
            "累计交易笔数",
            "交易笔数",
            "交易次数",
            "利率",
            "年化利率",
            "交易金额",
            "活跃天数",
            "生效状态",
            "销售额",
            "订单量",
            "订单金额",
            "订单日期",
            "销售日期",
            "区域",
            "产品",
            "商品",
            "品类",
            "渠道",
            "退款率",
            "退款",
            "手机号",
            "最近30天",
            "趋势",
            "排名",
            "同比",
            "环比",
            "异常",
        ]

        for phrase in phrase_rules:
            if phrase in query and phrase not in keywords:
                keywords.append(phrase)

        if not keywords:
            keywords.append(query)

        return keywords
