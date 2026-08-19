from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from model_call_budget import consume_model_call
from model_provider import resolve_model_settings


@dataclass
class CoderModelConfig:
    """
    Coder 模型配置。

    使用 OpenAI compatible 格式调用模型。
    没有 API Key 时默认走 Mock，方便先跑通流程。
    """

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    provider: str = ""
    temperature: float = 0.0
    timeout: int = 60
    use_mock_when_no_api_key: bool = True


class CoderModelClient:
    """
    Coder 模型 Client。
    """

    def __init__(self, config: CoderModelConfig | None = None):
        self.config = config or CoderModelConfig()
        settings = resolve_model_settings(
            role="coder",
            provider=self.config.provider,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
        )
        self.config.provider = settings.provider
        self.config.api_key = settings.api_key
        self.config.base_url = settings.base_url
        self.config.model = settings.model

    def generate_sql(self, prompt: str) -> str:
        """
        生成 SQL。
        """
        if self.config.api_key:
            return self._call_model(prompt)

        if self.config.use_mock_when_no_api_key:
            return self._mock_generate(prompt)

        raise ValueError(f"缺少 {self.config.provider} API Key，无法调用 Coder 模型。")

    def _call_model(self, prompt: str) -> str:
        """
        调用 Coder 模型。
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

        consume_model_call(
            role="coder",
            provider=self.config.provider,
            model=self.config.model,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8")
                result = json.loads(body)
        except Exception as exc:
            raise RuntimeError(f"调用 Coder 模型失败: {exc}") from exc

        try:
            return result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise RuntimeError(f"解析 Coder 模型返回失败，原始返回：{result}") from exc

    def _mock_generate(self, prompt: str) -> str:
        """
        Mock SQL 生成。

        只用于本地跑通流程。
        真实效果以 Coder 模型输出为准。
        """
        operation = self._extract_operation(prompt)
        is_postgres = "符合postgres语法" in "".join(prompt.lower().split())

        if "退款率" in operation and "orders.channel" in prompt:
            return """SELECT
    orders.channel,
    COUNT(orders.order_id) AS order_count,
    SUM(CASE WHEN orders.order_status = 'refunded' THEN 1 ELSE 0 END) AS refund_count,
    ROUND(
        100.0 * SUM(CASE WHEN orders.order_status = 'refunded' THEN 1 ELSE 0 END)
        / NULLIF(COUNT(orders.order_id), 0),
        2
    ) AS refund_rate
FROM orders
GROUP BY orders.channel
ORDER BY refund_rate DESC;"""

        if "异常倍数" in operation and "orders" in prompt:
            date_filter = (
                "CURRENT_DATE - INTERVAL '59 days'"
                if is_postgres else "date('now', '-59 day')"
            )
            return f"""SELECT
    daily.sales_date,
    daily.sales_amount,
    daily.order_count,
    daily.total_quantity,
    ROUND(daily.sales_amount / AVG(daily.sales_amount) OVER (), 2) AS anomaly_ratio
FROM (
    SELECT
        orders.order_date AS sales_date,
        ROUND(SUM(orders.sales_amount), 2) AS sales_amount,
        COUNT(orders.order_id) AS order_count,
        SUM(order_items.quantity) AS total_quantity
    FROM orders
    JOIN order_items
      ON orders.order_id = order_items.order_id
    WHERE orders.order_status = 'completed'
      AND orders.order_date >= {date_filter}
    GROUP BY orders.order_date
) AS daily
ORDER BY anomaly_ratio DESC
LIMIT 5;"""

        if "最近180天" in operation and "按月格式化" in operation and "orders" in prompt:
            month_expr = "TO_CHAR(order_date, 'YYYY-MM')" if is_postgres else "strftime('%Y-%m', order_date)"
            date_filter = "CURRENT_DATE - INTERVAL '179 days'" if is_postgres else "date('now', '-179 day')"
            return f"""SELECT
    {month_expr} AS sales_month,
    ROUND(SUM(sales_amount), 2) AS sales_amount
FROM orders
WHERE order_status = 'completed'
  AND order_date >= {date_filter}
GROUP BY {month_expr}
ORDER BY sales_month ASC;"""

        if "按月格式化" in operation and "orders" in prompt:
            month_expr = "TO_CHAR(order_date, 'YYYY-MM')" if is_postgres else "strftime('%Y-%m', order_date)"
            date_filter = (
                "DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'"
                if is_postgres else "date('now', 'start of month', '-1 month')"
            )
            return f"""SELECT
    {month_expr} AS sales_month,
    ROUND(SUM(sales_amount), 2) AS sales_amount
FROM orders
WHERE order_status = 'completed'
  AND order_date >= {date_filter}
GROUP BY {month_expr}
ORDER BY sales_month ASC;"""

        if "筛选customers.region等于" in operation and "按商品汇总" in operation:
            match = re.search(r"customers\.region等于'([^']+)'", operation)
            region = match.group(1) if match else ""
            if region not in {"华东", "华南", "华北", "西南", "东北", "西北"}:
                return "SELECT 1;"
            return f"""SELECT
    products.product_name,
    ROUND(SUM(order_items.line_amount), 2) AS sales_amount
FROM customers
JOIN orders
  ON customers.customer_id = orders.customer_id
JOIN order_items
  ON orders.order_id = order_items.order_id
JOIN products
  ON order_items.product_id = products.product_id
WHERE orders.order_status = 'completed'
  AND customers.region = '{region}'
GROUP BY products.product_id, products.product_name
ORDER BY sales_amount DESC;"""

        if "筛选products.product_name等于" in operation and "按region汇总" in operation:
            match = re.search(r"products\.product_name等于'([^']+)'", operation)
            product = match.group(1) if match else ""
            allowed_products = {
                "智能手机 Pro",
                "轻薄笔记本",
                "无线耳机",
                "智能手表",
                "4K 显示器",
                "机械键盘",
                "平板电脑",
                "智能音箱",
            }
            if product not in allowed_products:
                return "SELECT 1;"
            return f"""SELECT
    customers.region,
    ROUND(SUM(order_items.line_amount), 2) AS sales_amount
FROM products
JOIN order_items
  ON products.product_id = order_items.product_id
JOIN orders
  ON order_items.order_id = orders.order_id
JOIN customers
  ON orders.customer_id = customers.customer_id
WHERE orders.order_status = 'completed'
  AND products.product_name = '{product}'
GROUP BY customers.region
ORDER BY sales_amount DESC;"""

        if "商品销售额" in operation and "order_items" in prompt:
            direction = "ASC" if "升序" in operation else "DESC"
            limit_match = re.search(r"(\d+)名", operation)
            limit = max(1, min(int(limit_match.group(1)), 50)) if limit_match else 5
            return f"""SELECT
    products.product_name,
    ROUND(SUM(order_items.line_amount), 2) AS sales_amount
FROM order_items
JOIN products
  ON order_items.product_id = products.product_id
JOIN orders
  ON order_items.order_id = orders.order_id
WHERE orders.order_status = 'completed'
GROUP BY products.product_id, products.product_name
ORDER BY sales_amount {direction}
LIMIT {limit};"""

        if "按region分组" in operation and "customers" in prompt:
            return """SELECT
    customers.region,
    COUNT(orders.order_id) AS order_count
FROM orders
JOIN customers
  ON orders.customer_id = customers.customer_id
WHERE orders.order_status = 'completed'
GROUP BY customers.region
ORDER BY order_count DESC;"""

        if "最近30天" in operation and "orders.order_date" in prompt:
            date_filter = "CURRENT_DATE - INTERVAL '29 days'" if is_postgres else "date('now', '-29 day')"
            return f"""SELECT
    order_date,
    ROUND(SUM(sales_amount), 2) AS sales_amount
FROM orders
WHERE order_status = 'completed'
  AND order_date >= {date_filter}
GROUP BY order_date
ORDER BY order_date ASC;"""

        if (
            "trade_summary" in operation
            and "interest_info" in operation
            and "total_trade_count" in operation
            and "interest_rate" in operation
        ):
            return """SELECT interest_info.interest_rate
FROM trade_summary
JOIN interest_info
  ON trade_summary.user_id = interest_info.user_id
WHERE trade_summary.total_trade_count > 50000;"""

        return "SELECT 1;"

    @staticmethod
    def _extract_operation(prompt: str) -> str:
        match = re.search(r"操作指令：(.*?)\n输出目标：", prompt, flags=re.S)
        return match.group(1).strip() if match else prompt

    def clean_sql(self, text: str) -> str:
        """
        清理模型输出，只保留 SQL。

        处理：
        - 去掉 Markdown 代码块
        - 去掉“输出SQL语句：”之类的前缀
        - 提取 SELECT/WITH 开头的 SQL
        """
        text = text.strip()

        text = re.sub(
            r"^```(?:sql)?\s*",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"\s*```$",
            "",
            text,
            flags=re.I,
        )

        text = re.sub(
            r"^(输出SQL语句|SQL语句|SQL)\s*[:：]\s*",
            "",
            text,
            flags=re.I,
        ).strip()

        match = re.search(
            r"((?:SELECT|WITH)\b[\s\S]*?;?)\s*$",
            text,
            flags=re.I,
        )

        if match:
            sql = match.group(1).strip()
        else:
            sql = text

        if sql and not sql.endswith(";"):
            sql += ";"

        return sql
