from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "runtime_data" / "trade_demo.db"
OUT = ROOT / "runtime_data" / "oracle-30-results.json"

CASES = [
    (1, "区域聚合", "最近30天各区域已完成订单销售额分别是多少？输出 region、sales_amount，按销售额降序。", "SELECT c.region, ROUND(SUM(o.sales_amount),2) AS sales_amount FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region ORDER BY sales_amount DESC"),
    (2, "区域聚合", "最近30天各区域已完成订单数分别是多少？输出 region、order_count，降序。", "SELECT c.region, COUNT(*) AS order_count FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region ORDER BY order_count DESC, c.region"),
    (3, "渠道聚合", "最近30天各渠道已完成订单销售额、订单数、客单价分别是多少？输出 channel、sales_amount、order_count、avg_order_value。", "SELECT o.channel, ROUND(SUM(o.sales_amount),2) AS sales_amount, COUNT(*) AS order_count, ROUND(AVG(o.sales_amount),2) AS avg_order_value FROM orders o WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY o.channel ORDER BY sales_amount DESC"),
    (4, "时间趋势", "最近7天每天已完成订单销售额是多少？输出 sales_date、sales_amount，按日期升序。", "SELECT o.order_date AS sales_date, ROUND(SUM(o.sales_amount),2) AS sales_amount FROM orders o WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-6 days') GROUP BY o.order_date ORDER BY sales_date"),
    (5, "时间对比", "数据最新月份和上一个自然月的已完成订单销售额分别是多少？输出 sales_month、sales_amount。", "WITH mx AS (SELECT date(MAX(order_date),'start of month') m FROM orders) SELECT strftime('%Y-%m',o.order_date) AS sales_month, ROUND(SUM(o.sales_amount),2) AS sales_amount FROM orders o,mx WHERE o.order_status='completed' AND o.order_date>=date(mx.m,'-1 month') GROUP BY sales_month ORDER BY sales_month"),
    (6, "商品TopN", "最近30天已完成订单销售额最高的5个商品是什么？输出 product_name、sales_amount、total_quantity。", "SELECT p.product_name, ROUND(SUM(oi.line_amount),2) AS sales_amount, SUM(oi.quantity) AS total_quantity FROM orders o JOIN order_items oi ON oi.order_id=o.order_id JOIN products p ON p.product_id=oi.product_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY p.product_id,p.product_name ORDER BY sales_amount DESC LIMIT 5"),
    (7, "区域商品", "最近30天每个区域销售额最高的商品是什么？输出 region、product_name、sales_amount、rank_in_region。", "WITH x AS (SELECT c.region,p.product_name,ROUND(SUM(oi.line_amount),2) sales_amount FROM orders o JOIN customers c ON c.customer_id=o.customer_id JOIN order_items oi ON oi.order_id=o.order_id JOIN products p ON p.product_id=oi.product_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region,p.product_id,p.product_name), r AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY region ORDER BY sales_amount DESC,product_name) rank_in_region FROM x) SELECT region,product_name,sales_amount,rank_in_region FROM r WHERE rank_in_region=1 ORDER BY region"),
    (8, "区域渠道", "最近30天各区域各渠道的已完成订单销售额是多少？输出 region、channel、sales_amount。", "SELECT c.region,o.channel,ROUND(SUM(o.sales_amount),2) sales_amount FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region,o.channel ORDER BY c.region,sales_amount DESC"),
    (9, "客户分层", "最近30天不同客户等级的已完成订单销售额和订单数是多少？输出 customer_level、sales_amount、order_count。", "SELECT c.customer_level,ROUND(SUM(o.sales_amount),2) sales_amount,COUNT(*) order_count FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.customer_level ORDER BY sales_amount DESC"),
    (10, "折扣分析", "最近30天各区域折扣金额及折扣率是多少？折扣率=折扣金额/销售额。输出 region、discount_amount、sales_amount、discount_rate。", "SELECT c.region,ROUND(SUM(o.discount_amount),2) discount_amount,ROUND(SUM(o.sales_amount),2) sales_amount,ROUND(SUM(o.discount_amount)*1.0/NULLIF(SUM(o.sales_amount),0),4) discount_rate FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region ORDER BY discount_rate DESC"),
    (11, "退款分析", "最近30天各渠道总订单数、退款订单数和退款率是多少？输出 channel、order_count、refund_count、refund_rate。", "SELECT channel,COUNT(*) order_count,SUM(CASE WHEN order_status='refunded' THEN 1 ELSE 0 END) refund_count,ROUND(SUM(CASE WHEN order_status='refunded' THEN 1 ELSE 0 END)*1.0/COUNT(*),4) refund_rate FROM orders WHERE order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY channel ORDER BY refund_rate DESC,channel"),
    (12, "净销售额", "最近30天各区域已完成订单净销售额是多少？净销售额=销售额-折扣额。输出 region、net_sales。", "SELECT c.region,ROUND(SUM(o.sales_amount-o.discount_amount),2) net_sales FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region ORDER BY net_sales DESC"),
    (13, "异常描述", "最近60天销售额最高的5个日期，展示 sales_date、sales_amount、order_count、total_quantity。", "SELECT o.order_date sales_date,ROUND(SUM(o.sales_amount),2) sales_amount,COUNT(DISTINCT o.order_id) order_count,SUM(oi.quantity) total_quantity FROM orders o JOIN order_items oi ON oi.order_id=o.order_id WHERE o.order_date>=date((SELECT MAX(order_date) FROM orders),'-59 days') GROUP BY o.order_date ORDER BY sales_amount DESC LIMIT 5"),
    (14, "月度趋势", "最近180天每月已完成订单销售额是多少？输出 sales_month、sales_amount，升序。", "SELECT strftime('%Y-%m',order_date) sales_month,ROUND(SUM(sales_amount),2) sales_amount FROM orders WHERE order_status='completed' AND order_date>=date((SELECT MAX(order_date) FROM orders),'-179 days') GROUP BY sales_month ORDER BY sales_month"),
    (15, "日期明细", "数据最新月份每天已完成订单销售额是多少？输出 sales_date、sales_amount。", "SELECT order_date sales_date,ROUND(SUM(sales_amount),2) sales_amount FROM orders WHERE order_status='completed' AND order_date>=date((SELECT MAX(order_date) FROM orders),'start of month') GROUP BY order_date ORDER BY order_date"),
    (16, "品类分析", "最近30天各品类已完成订单销售额、销量、订单数是多少？输出 category、sales_amount、total_quantity、order_count。", "SELECT p.category,ROUND(SUM(oi.line_amount),2) sales_amount,SUM(oi.quantity) total_quantity,COUNT(DISTINCT o.order_id) order_count FROM orders o JOIN order_items oi ON oi.order_id=o.order_id JOIN products p ON p.product_id=oi.product_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY p.category ORDER BY sales_amount DESC"),
    (17, "商品结构", "各品类当前在售商品数和平均单价是多少？输出 category、active_product_count、avg_unit_price。", "SELECT category,COUNT(*) active_product_count,ROUND(AVG(unit_price),2) avg_unit_price FROM products WHERE status='active' GROUP BY category ORDER BY active_product_count DESC,category"),
    (18, "贡献率", "最近30天各商品在所属品类中的已完成销售额占比是多少？输出 category、product_name、sales_amount、category_ratio。", "WITH x AS (SELECT p.category,p.product_name,SUM(oi.line_amount) sales_amount FROM orders o JOIN order_items oi ON oi.order_id=o.order_id JOIN products p ON p.product_id=oi.product_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY p.category,p.product_name) SELECT category,product_name,ROUND(sales_amount,2) sales_amount,ROUND(sales_amount*1.0/SUM(sales_amount) OVER(PARTITION BY category),4) category_ratio FROM x ORDER BY category,sales_amount DESC"),
    (19, "客户TopN", "最近30天已完成订单销售额最高的5位客户是谁？输出 customer_name、region、sales_amount、order_count。", "SELECT c.customer_name,c.region,ROUND(SUM(o.sales_amount),2) sales_amount,COUNT(*) order_count FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.customer_id,c.customer_name,c.region ORDER BY sales_amount DESC LIMIT 5"),
    (20, "区域客户", "最近30天每个区域销售额最高的客户是谁？输出 region、customer_name、sales_amount。", "WITH x AS (SELECT c.region,c.customer_name,SUM(o.sales_amount) sales_amount FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY c.region,c.customer_id,c.customer_name),r AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY region ORDER BY sales_amount DESC,customer_name) rn FROM x) SELECT region,customer_name,ROUND(sales_amount,2) sales_amount FROM r WHERE rn=1 ORDER BY region"),
    (21, "复购分析", "最近90天各区域至少有3笔已完成订单的客户数是多少？输出 region、repeat_customer_count。", "WITH x AS (SELECT c.region,c.customer_id,COUNT(*) n FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-89 days') GROUP BY c.region,c.customer_id HAVING COUNT(*)>=3) SELECT region,COUNT(*) repeat_customer_count FROM x GROUP BY region ORDER BY repeat_customer_count DESC,region"),
    (22, "渠道商品", "最近30天各渠道售出的不同商品数是多少？只看已完成订单。输出 channel、product_variety_count。", "SELECT o.channel,COUNT(DISTINCT oi.product_id) product_variety_count FROM orders o JOIN order_items oi ON oi.order_id=o.order_id WHERE o.order_status='completed' AND o.order_date>=date((SELECT MAX(order_date) FROM orders),'-29 days') GROUP BY o.channel ORDER BY product_variety_count DESC,o.channel"),
    (23, "交易利率", "累计交易笔数大于50000的用户对应有效利率是多少？输出 user_id、total_trade_count、interest_rate。", "SELECT t.user_id,t.total_trade_count,i.interest_rate FROM trade_summary t JOIN interest_info i ON i.user_id=t.user_id WHERE t.total_trade_count>50000 AND i.effective_status='active' ORDER BY t.total_trade_count DESC"),
    (24, "交易利率", "高价值且利率有效的用户数、平均利率和累计交易额是多少？输出 user_count、avg_interest_rate、total_trade_amount。", "SELECT COUNT(*) user_count,ROUND(AVG(i.interest_rate),4) avg_interest_rate,ROUND(SUM(t.total_trade_amount),2) total_trade_amount FROM trade_summary t JOIN interest_info i ON i.user_id=t.user_id WHERE t.total_trade_count>50000 AND i.effective_status='active'"),
    (25, "利率状态", "有效和失效利率的用户数与平均利率分别是多少？输出 effective_status、user_count、avg_interest_rate。", "SELECT effective_status,COUNT(*) user_count,ROUND(AVG(interest_rate),4) avg_interest_rate FROM interest_info GROUP BY effective_status ORDER BY effective_status"),
    (26, "交易效率", "平均每笔交易金额最高的5个用户是谁？输出 user_id、avg_amount_per_trade。", "SELECT user_id,ROUND(total_trade_amount*1.0/total_trade_count,2) avg_amount_per_trade FROM trade_summary WHERE total_trade_count>0 ORDER BY avg_amount_per_trade DESC LIMIT 5"),
    (27, "区间筛选", "累计交易笔数在50000到90000之间且利率有效的用户有哪些？输出 user_id、total_trade_count、interest_rate。", "SELECT t.user_id,t.total_trade_count,i.interest_rate FROM trade_summary t JOIN interest_info i ON i.user_id=t.user_id WHERE t.total_trade_count BETWEEN 50000 AND 90000 AND i.effective_status='active' ORDER BY t.total_trade_count DESC"),
    (28, "利率类型", "不同利率类型的用户数、平均利率和累计交易额是多少？输出 rate_type、user_count、avg_interest_rate、total_trade_amount。", "SELECT i.rate_type,COUNT(*) user_count,ROUND(AVG(i.interest_rate),4) avg_interest_rate,ROUND(SUM(t.total_trade_amount),2) total_trade_amount FROM interest_info i JOIN trade_summary t ON t.user_id=i.user_id GROUP BY i.rate_type ORDER BY user_count DESC,i.rate_type"),
    (29, "活跃度", "利率有效用户的平均活跃天数和平均交易笔数是多少？输出 avg_active_days、avg_trade_count。", "SELECT ROUND(AVG(t.active_days),2) avg_active_days,ROUND(AVG(t.total_trade_count),2) avg_trade_count FROM trade_summary t JOIN interest_info i ON i.user_id=t.user_id WHERE i.effective_status='active'"),
    (30, "联合排名", "利率有效用户按累计交易额降序排名前5，输出 user_id、total_trade_amount、interest_rate、amount_rank。", "SELECT t.user_id,t.total_trade_amount,i.interest_rate,ROW_NUMBER() OVER(ORDER BY t.total_trade_amount DESC) amount_rank FROM trade_summary t JOIN interest_info i ON i.user_id=t.user_id WHERE i.effective_status='active' ORDER BY amount_rank LIMIT 5"),
]


def main() -> None:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    payload = []
    for case_id, category, question, sql in CASES:
        rows = [dict(row) for row in connection.execute(sql).fetchall()]
        payload.append({
            "id": case_id,
            "category": category,
            "question": question,
            "oracle_sql": sql,
            "expected_columns": list(rows[0].keys()) if rows else [],
            "expected_row_count": len(rows),
            "expected_rows": rows,
            "api_status": "BLOCKED",
            "api_detail": "当前执行环境无法解析 Vercel 部署域名；本地真实模型 Key 为空，未发送模型请求",
            "actual_sql": "",
            "actual_columns": [],
            "actual_rows": [],
            "semantic_pass": None,
        })
    connection.close()
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(DB),
        "deployment_url": "https://1-iota-gilt-74.vercel.app/",
        "test_count": len(payload),
        "real_api_calls": 0,
        "blocked_reason": "DNS resolution for deployed Vercel URL is unavailable in the current execution sandbox; local DeepSeek/DashScope keys are empty.",
        "cases": payload,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
