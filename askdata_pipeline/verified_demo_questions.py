from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Optional, Tuple

from cot_planning.objects import CotPlanningResult, CotPlanningStep


def _normalize_question(value: str) -> str:
    return re.sub(r"[\s。！？!?]+", "", value).lower()


@dataclass(frozen=True)
class VerifiedDemoQuestion:
    """A versioned demo question whose SQL is reviewed and regression-tested."""

    question: str
    processing_objects: str
    operation_instruction: str
    output_target: str
    sql_by_dialect: Dict[str, str]
    expected_columns: Tuple[str, ...]
    expected_rows: int

    def build_plan(self, database: str) -> CotPlanningResult:
        raw_output = (
            f"数据库：{database}\n"
            f"处理对象：{self.processing_objects}\n"
            f"操作指令：{self.operation_instruction}\n"
            f"输出目标：{self.output_target}"
        )
        return CotPlanningResult(
            user_query=self.question,
            prompt="verified-demo-plan",
            raw_output=raw_output,
            steps=[
                CotPlanningStep(
                    step_no=1,
                    database=database,
                    processing_objects=self.processing_objects,
                    operation_instruction=self.operation_instruction,
                    output_target=self.output_target,
                )
            ],
        )

    def sql_for(self, dialect: str) -> str:
        normalized = (dialect or "sqlite").lower()
        if normalized in {"postgresql", "postgres"}:
            normalized = "postgres"
        return self.sql_by_dialect[normalized]

    def result_contract_error(self, columns: object, row_count: object) -> str:
        actual_columns = tuple(str(item) for item in (columns or []))
        actual_rows = int(row_count or 0)
        if actual_columns != self.expected_columns:
            return (
                "Demo 结果列不符合已验证契约："
                f"期望 {list(self.expected_columns)}，实际 {list(actual_columns)}"
            )
        if actual_rows != self.expected_rows:
            return (
                "Demo 结果行数不符合已验证契约："
                f"期望 {self.expected_rows}，实际 {actual_rows}"
            )
        return ""


VERIFIED_DEMO_QUESTIONS: Tuple[VerifiedDemoQuestion, ...] = (
    VerifiedDemoQuestion(
        question="最近30天销售额趋势如何？",
        processing_objects="orders.order_date、orders.sales_amount、orders.order_status",
        operation_instruction="筛选最近30天已完成订单，按订单日期汇总销售额并按日期升序排列",
        output_target="orders.order_date、销售额 sales_amount",
        sql_by_dialect={
            "sqlite": """WITH bounds AS (
    SELECT DATE(MAX(order_date)) AS max_date FROM orders
), dates AS (
    SELECT DATE((SELECT max_date FROM bounds), '-29 days') AS order_date
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-28 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-27 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-26 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-25 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-24 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-23 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-22 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-21 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-20 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-19 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-18 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-17 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-16 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-15 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-14 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-13 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-12 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-11 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-10 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-9 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-8 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-7 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-6 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-5 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-4 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-3 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-2 days')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds), '-1 day')
    UNION ALL SELECT DATE((SELECT max_date FROM bounds))
), daily AS (
    SELECT DATE(orders.order_date) AS order_date,
           ROUND(SUM(orders.sales_amount), 2) AS sales_amount
    FROM orders
    WHERE orders.order_status = 'completed'
      AND DATE(orders.order_date) BETWEEN
          DATE((SELECT max_date FROM bounds), '-29 days')
          AND (SELECT max_date FROM bounds)
    GROUP BY DATE(orders.order_date)
)
SELECT dates.order_date, COALESCE(daily.sales_amount, 0) AS sales_amount
FROM dates
LEFT JOIN daily ON daily.order_date = dates.order_date
ORDER BY dates.order_date ASC""",
            "postgres": """WITH bounds AS (
    SELECT MAX(order_date)::DATE AS max_date FROM orders
), dates AS (
    SELECT GENERATE_SERIES(
        bounds.max_date - INTERVAL '29 days',
        bounds.max_date,
        INTERVAL '1 day'
    )::DATE AS order_date
    FROM bounds
), daily AS (
    SELECT orders.order_date::DATE AS order_date,
           ROUND(SUM(orders.sales_amount)::NUMERIC, 2) AS sales_amount
    FROM orders
    WHERE orders.order_status = 'completed'
      AND orders.order_date::DATE BETWEEN
          (SELECT max_date FROM bounds) - INTERVAL '29 days'
          AND (SELECT max_date FROM bounds)
    GROUP BY orders.order_date::DATE
)
SELECT dates.order_date, COALESCE(daily.sales_amount, 0::NUMERIC) AS sales_amount
FROM dates
LEFT JOIN daily ON daily.order_date = dates.order_date
ORDER BY dates.order_date ASC""",
        },
        expected_columns=("order_date", "sales_amount"),
        expected_rows=30,
    ),
    VerifiedDemoQuestion(
        question="已完成订单中，销售额最高的前5个产品是哪些？",
        processing_objects="products.product_name、products.product_id、order_items.product_id、order_items.order_id、order_items.line_amount、orders.order_id、orders.order_status",
        operation_instruction="关联订单、订单明细与产品，筛选已完成订单，按产品汇总销售额并取前5名",
        output_target="products.product_name、销售额 sales_amount",
        sql_by_dialect={
            "sqlite": """SELECT p.product_name AS product_name, ROUND(SUM(oi.line_amount), 2) AS sales_amount
FROM products AS p
JOIN order_items AS oi ON oi.product_id = p.product_id
JOIN orders AS o ON o.order_id = oi.order_id
WHERE o.order_status = 'completed'
GROUP BY p.product_id, p.product_name
ORDER BY sales_amount DESC, product_name ASC
LIMIT 5""",
            "postgres": """SELECT p.product_name AS product_name, ROUND(SUM(oi.line_amount)::NUMERIC, 2) AS sales_amount
FROM products AS p
JOIN order_items AS oi ON oi.product_id = p.product_id
JOIN orders AS o ON o.order_id = oi.order_id
WHERE o.order_status = 'completed'
GROUP BY p.product_id, p.product_name
ORDER BY sales_amount DESC, product_name ASC
LIMIT 5""",
        },
        expected_columns=("product_name", "sales_amount"),
        expected_rows=5,
    ),
    VerifiedDemoQuestion(
        question="本月与上月已完成订单销售额相比变化多少？",
        processing_objects="orders.order_date、orders.sales_amount、orders.order_status",
        operation_instruction="筛选本月和上月的已完成订单，按月份汇总销售额并按月份升序排列",
        output_target="月份 sales_month、销售额 sales_amount",
        sql_by_dialect={
            "sqlite": """SELECT STRFTIME('%Y-%m', order_date) AS sales_month,
       ROUND(SUM(sales_amount), 2) AS sales_amount
FROM orders
WHERE order_status = 'completed'
  AND order_date >= DATE((SELECT MAX(order_date) FROM orders), 'start of month', '-1 month')
  AND order_date < DATE((SELECT MAX(order_date) FROM orders), 'start of month', '+1 month')
GROUP BY STRFTIME('%Y-%m', order_date)
ORDER BY sales_month ASC""",
            "postgres": """SELECT TO_CHAR(DATE_TRUNC('month', order_date), 'YYYY-MM') AS sales_month,
       ROUND(SUM(sales_amount)::NUMERIC, 2) AS sales_amount
FROM orders
WHERE order_status = 'completed'
  AND order_date >= DATE_TRUNC('month', (SELECT MAX(order_date) FROM orders)) - INTERVAL '1 month'
  AND order_date < DATE_TRUNC('month', (SELECT MAX(order_date) FROM orders)) + INTERVAL '1 month'
GROUP BY DATE_TRUNC('month', order_date)
ORDER BY DATE_TRUNC('month', order_date) ASC""",
        },
        expected_columns=("sales_month", "sales_amount"),
        expected_rows=2,
    ),
)


def find_verified_demo_question(query: str) -> Optional[VerifiedDemoQuestion]:
    normalized = _normalize_question(query)
    for item in VERIFIED_DEMO_QUESTIONS:
        if _normalize_question(item.question) == normalized:
            return item
    return None


def verified_demo_question_texts() -> Tuple[str, ...]:
    return tuple(item.question for item in VERIFIED_DEMO_QUESTIONS)
