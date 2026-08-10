from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import List


@dataclass
class AliyunKeywordExtractorConfig:
    """
    阿里云百炼关键词抽取模型配置。

    该配置用于调用大模型，从用户 Query 中抽取核心检索词。
    """

    api_key: str = ""
    """阿里云百炼 API Key。为空时读取环境变量 DASHSCOPE_API_KEY。"""

    chat_url: str = ""
    """
    Chat Completions 接口地址。

    默认使用阿里云百炼 OpenAI 兼容接口：
    https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
    """

    model: str = "qwen-plus"
    """用于关键词抽取的大模型名称。"""

    timeout: int = 60
    """HTTP 请求超时时间，单位为秒。"""


class AliyunKeywordExtractor:
    """
    基于大模型的核心检索词抽取器。

    该类负责：
    1. 构造关键词抽取 Prompt。
    2. 调用阿里云百炼 Chat Completions 接口。
    3. 使用正则从模型输出中解析关键词。
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

    def __init__(self, config: AliyunKeywordExtractorConfig | None = None):
        self.config = config or AliyunKeywordExtractorConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

        if not self.config.chat_url:
            self.config.chat_url = os.getenv(
                "DASHSCOPE_CHAT_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            )

        if not self.config.api_key:
            raise ValueError(
                "缺少阿里云百炼 API Key。请设置环境变量 DASHSCOPE_API_KEY，"
                "或在 AliyunKeywordExtractorConfig(api_key='...') 中传入。"
            )

    def extract(self, query: str) -> List[str]:
        """
        从用户 Query 中抽取核心检索词。

        Args:
            query: 用户自然语言查询。

        Returns:
            List[str]: 核心检索词列表。
        """
        content = self._call_llm(query)
        return self._parse_keywords(content)

    def _call_llm(self, query: str) -> str:
        """
        调用大模型完成关键词抽取。

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