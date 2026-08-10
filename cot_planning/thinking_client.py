from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Iterator


@dataclass
class ThinkingModelConfig:
    """
    思考模型配置。

    这里使用 OpenAI compatible 格式调用模型。
    没有 API Key 时默认走 Mock，方便先跑通流程。
    """

    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    model: str = "qwen-plus"
    temperature: float = 0.0
    timeout: int = 60
    use_mock_when_no_api_key: bool = True
    mock_stream_delay: float = 0.02


class ThinkingModelClient:
    """
    思考模型 Client。

    用于将用户 Query 和 Schema 图转换为结构化 CoT 规划结果。
    支持普通输出和流式输出。
    """

    def __init__(self, config: ThinkingModelConfig | None = None):
        self.config = config or ThinkingModelConfig()

        if not self.config.api_key:
            self.config.api_key = os.getenv("DASHSCOPE_API_KEY", "")

    def generate(self, prompt: str) -> str:
        """
        非流式生成规划结果。
        """
        if self.config.api_key:
            return self._call_model(prompt)

        if self.config.use_mock_when_no_api_key:
            return self._mock_generate(prompt)

        raise ValueError("缺少 DASHSCOPE_API_KEY，无法调用思考模型。")

    def generate_stream(self, prompt: str) -> Iterator[str]:
        """
        流式生成规划结果。

        返回一个迭代器，每次 yield 一小段文本。
        Demo 中可以边生成边 print。
        """
        if self.config.api_key:
            yield from self._call_model_stream(prompt)
            return

        if self.config.use_mock_when_no_api_key:
            yield from self._mock_generate_stream(prompt)
            return

        raise ValueError("缺少 DASHSCOPE_API_KEY，无法调用思考模型。")

    def _call_model(self, prompt: str) -> str:
        """
        调用大模型，非流式。
        """
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": self.config.temperature,
            "stream": False,
        }

        result = self._post_json(payload)

        try:
            return result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise RuntimeError(f"解析思考模型返回失败，原始返回：{result}") from exc

    def _call_model_stream(self, prompt: str) -> Iterator[str]:
        """
        调用大模型，流式。

        兼容 OpenAI SSE 格式：
            data: {"choices":[{"delta":{"content":"..."}}]}
            data: [DONE]
        """
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": self.config.temperature,
            "stream": True,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "text/event-stream",
        }

        request = urllib.request.Request(
            self.config.base_url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()

                    if not line:
                        continue

                    if not line.startswith("data:"):
                        continue

                    data_text = line[len("data:"):].strip()

                    if data_text == "[DONE]":
                        break

                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue

                    delta = event.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")

                    if content:
                        yield content

        except Exception as exc:
            raise RuntimeError(f"流式调用思考模型失败: {exc}") from exc

    def _post_json(self, payload: dict) -> dict:
        """
        发送 JSON 请求。
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        request = urllib.request.Request(
            self.config.base_url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except Exception as exc:
            raise RuntimeError(f"调用思考模型失败: {exc}") from exc

    def _mock_generate(self, prompt: str) -> str:
        """
        Mock 规划结果。
        """
        user_query = self._extract_user_query(prompt)
        normalized = "".join(user_query.lower().split())

        if "退款" in normalized and "渠道" in normalized:
            return """步骤1：
(
  数据库: trade_db,
  处理对象: orders.order_id，orders.channel，orders.order_status,
  操作指令: 按orders.channel分组统计全部订单数和order_status为refunded的退款订单数；计算退款订单数除以全部订单数得到退款率；最后按退款率降序返回各渠道,
  输出目标: orders.channel，order_count，refund_count，refund_rate
)"""

        if any(term in normalized for term in ("异常日期", "异常的日期", "销售异常")):
            return """步骤1：
(
  数据库: trade_db,
  处理对象: orders.order_id，orders.order_date，orders.sales_amount，orders.order_status，order_items.order_id，order_items.quantity，orders.order_id ↔ order_items.order_id,
  操作指令: 先在orders表中筛选最近60天且状态为completed的订单；再通过order_id关联order_items；然后按order_date汇总sales_amount、订单数和quantity；再计算每日销售额相对日均值的异常倍数；最后按异常倍数降序返回前5个日期及候选原因指标,
  输出目标: orders.order_date对应的sales_date，sales_amount，order_count，total_quantity，anomaly_ratio
)"""

        if "按月" in normalized and "销售额" in normalized:
            return """步骤1：
(
  数据库: trade_db,
  处理对象: orders.order_date，orders.sales_amount，orders.order_status,
  操作指令: 先在orders表中筛选最近180天且状态为completed的订单；再将order_date按月格式化为sales_month；然后按月汇总sales_amount；最后按月份升序返回,
  输出目标: sales_month，sales_amount
)"""

        if any(term in normalized for term in ("本月与上月", "上月对比", "月度对比", "环比")):
            return """步骤1：
(
  数据库: trade_db,
  处理对象: orders.order_date，orders.sales_amount，orders.order_status,
  操作指令: 先在orders表中筛选上月月初以来且状态为completed的订单；再将order_date按月格式化为sales_month；然后按月汇总sales_amount；最后按月份升序返回,
  输出目标: sales_month，sales_amount
)"""

        matched_region = next(
            (region for region in ("华东", "华南", "华北", "西南", "东北", "西北") if region in normalized),
            "",
        )
        if matched_region and any(term in normalized for term in ("产品", "商品")):
            return f"""步骤1：
(
  数据库: trade_db,
  处理对象: customers.customer_id，customers.region，orders.customer_id，orders.order_id，orders.order_status，order_items.order_id，order_items.product_id，order_items.line_amount，products.product_id，products.product_name，customers.customer_id ↔ orders.customer_id，orders.order_id ↔ order_items.order_id，order_items.product_id ↔ products.product_id,
  操作指令: 先筛选customers.region等于'{matched_region}'且orders.order_status为completed；再关联orders、order_items和products；然后按商品汇总line_amount；最后按销售额降序返回,
  输出目标: products.product_name，line_amount合计为sales_amount
)"""

        matched_product = next(
            (
                product
                for product in (
                    "智能手机pro",
                    "轻薄笔记本",
                    "无线耳机",
                    "智能手表",
                    "4k显示器",
                    "机械键盘",
                    "平板电脑",
                    "智能音箱",
                )
                if product in normalized
            ),
            "",
        )
        if matched_product and "区域" in normalized:
            product_label = {
                "智能手机pro": "智能手机 Pro",
                "4k显示器": "4K 显示器",
            }.get(matched_product, matched_product)
            return f"""步骤1：
(
  数据库: trade_db,
  处理对象: products.product_id，products.product_name，order_items.product_id，order_items.order_id，order_items.line_amount，orders.order_id，orders.customer_id，orders.order_status，customers.customer_id，customers.region，products.product_id ↔ order_items.product_id，order_items.order_id ↔ orders.order_id，orders.customer_id ↔ customers.customer_id,
  操作指令: 先筛选products.product_name等于'{product_label}'且orders.order_status为completed；再关联order_items、orders和customers；然后按region汇总line_amount；最后按销售额降序返回,
  输出目标: customers.region，line_amount合计为sales_amount
)"""

        if (
            any(term in normalized for term in ("产品", "商品"))
            and any(term in normalized for term in ("前5", "top5", "最高", "排名"))
        ):
            return """步骤1：
(
  数据库: trade_db,
  处理对象: products.product_id，products.product_name，order_items.product_id，order_items.order_id，order_items.line_amount，orders.order_id，orders.order_status，products.product_id ↔ order_items.product_id，order_items.order_id ↔ orders.order_id,
  操作指令: 先在orders表中筛选状态为completed的订单；再通过order_id关联order_items；然后通过product_id关联products并按product_name汇总line_amount；最后按商品销售额降序返回前5名,
  输出目标: products.product_name，line_amount合计为sales_amount
)"""

        if "区域" in normalized and any(term in normalized for term in ("订单量", "排名", "排行")):
            return """步骤1：
(
  数据库: trade_db,
  处理对象: customers.customer_id，customers.region，orders.customer_id，orders.order_id，orders.order_status，customers.customer_id ↔ orders.customer_id,
  操作指令: 先在orders表中筛选状态为completed的订单；再通过customer_id关联customers；然后按region分组计算订单数量；最后按订单量降序排名,
  输出目标: customers.region，orders.order_id计数为order_count
)"""

        if (
            "销售额" in normalized
            and any(term in normalized for term in ("最近30天", "30天"))
        ):
            return """步骤1：
(
  数据库: trade_db,
  处理对象: orders.order_date，orders.sales_amount，orders.order_status,
  操作指令: 先在orders表中筛选最近30天且状态为completed的订单；再按order_date分组；然后汇总sales_amount；最后按日期升序返回每日销售额,
  输出目标: orders.order_date，sales_amount合计
)"""

        if (
            "total_trade_count" in prompt
            and "interest_rate" in prompt
            and "trade_summary.user_id" in prompt
            and "interest_info.user_id" in prompt
        ):
            return """步骤1：
(
  数据库: trade_db,
  处理对象: trade_summary.total_trade_count，interest_info.interest_rate，trade_summary.user_id，interest_info.user_id，trade_summary.user_id ↔ interest_info.user_id,
  操作指令: 先在trade_summary表中筛选total_trade_count大于50000的记录，并获取对应user_id；再基于user_id关联interest_info表；最后获取对应的interest_rate,
  输出目标: interest_info.interest_rate
)"""

        return """步骤1：
(
  数据库: 缺失,
  处理对象: Schema中未找到足够的表、字段或表关联关系,
  操作指令: 先检查用户Query涉及的筛选字段、输出字段和表关联关系；再发现当前Schema无法完整支撑该查询；最后返回缺失信息说明,
  输出目标: 缺失，无法生成明确输出目标
)"""

    @staticmethod
    def _extract_user_query(prompt: str) -> str:
        match = re.search(r"# 用户Query\s*(.*?)\s*# Schema", prompt, flags=re.S)
        query = match.group(1).strip() if match else prompt
        if "当前用户问题：" in query:
            query = query.rsplit("当前用户问题：", 1)[-1].strip()
        return query

    def _mock_generate_stream(self, prompt: str) -> Iterator[str]:
        """
        Mock 流式输出。

        为了让 Demo 看起来像真实流式输出，这里按字符逐步 yield。
        """
        text = self._mock_generate(prompt)

        for char in text:
            yield char
            time.sleep(self.config.mock_stream_delay)
