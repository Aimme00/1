PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS interest_info;
DROP TABLE IF EXISTS trade_summary;

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
    rate_type TEXT NOT NULL,
    effective_status TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES trade_summary(user_id)
);

INSERT INTO trade_summary (
    user_id,
    total_trade_count,
    total_trade_amount,
    active_days,
    last_trade_time
) VALUES
    (1001, 12,     5300.50,    5,   '2024-05-01'),
    (1002, 5800,   230000.00,  180, '2024-05-06'),
    (1003, 56000,  1800000.00, 320, '2024-05-10'),
    (1004, 102430, 3700000.00, 365, '2024-05-11'),
    (1005, 0,      0.00,       0,   NULL),
    (1006, 50001,  910000.00,  250, '2024-05-13'),
    (1007, 49999,  870000.00,  245, '2024-05-13'),
    (1008, 76000,  2500000.00, 330, '2024-05-14'),
    (1009, 340,    35000.00,   40,  '2024-05-15'),
    (1010, 88888,  3200000.00, 350, '2024-05-16');

INSERT INTO interest_info (
    user_id,
    interest_rate,
    rate_type,
    effective_status
) VALUES
    (1001, 2.35, 'standard',   'active'),
    (1002, 3.12, 'vip',        'active'),
    (1003, 4.58, 'high_value', 'active'),
    (1004, 4.95, 'high_value', 'active'),
    (1005, 1.80, 'standard',   'inactive'),
    (1006, 4.20, 'high_value', 'active'),
    (1007, 3.88, 'vip',        'active'),
    (1008, 5.15, 'high_value', 'inactive'),
    (1009, 2.80, 'standard',   'active'),
    (1010, 5.05, 'high_value', 'active');

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL,
    region TEXT NOT NULL,
    customer_level TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price REAL NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    order_status TEXT NOT NULL,
    sales_amount REAL NOT NULL,
    discount_amount REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    line_amount REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

INSERT INTO customers (
    customer_id, customer_name, region, customer_level, created_at
) VALUES
    (2001, '华东零售客户A', '华东', '黄金', date('now', '-420 day')),
    (2002, '华东零售客户B', '华东', '白银', date('now', '-390 day')),
    (2003, '华南零售客户A', '华南', '黄金', date('now', '-360 day')),
    (2004, '华南零售客户B', '华南', '普通', date('now', '-330 day')),
    (2005, '华北零售客户A', '华北', '钻石', date('now', '-300 day')),
    (2006, '华北零售客户B', '华北', '白银', date('now', '-270 day')),
    (2007, '西南零售客户A', '西南', '黄金', date('now', '-240 day')),
    (2008, '西南零售客户B', '西南', '普通', date('now', '-210 day')),
    (2009, '东北零售客户A', '东北', '白银', date('now', '-180 day')),
    (2010, '东北零售客户B', '东北', '普通', date('now', '-150 day')),
    (2011, '西北零售客户A', '西北', '黄金', date('now', '-120 day')),
    (2012, '西北零售客户B', '西北', '普通', date('now', '-90 day'));

INSERT INTO products (
    product_id, product_name, category, unit_price, status
) VALUES
    (3001, '智能手机 Pro', '数码', 5999.00, 'active'),
    (3002, '轻薄笔记本', '电脑', 7999.00, 'active'),
    (3003, '无线耳机', '数码配件', 899.00, 'active'),
    (3004, '智能手表', '可穿戴', 1899.00, 'active'),
    (3005, '4K 显示器', '电脑配件', 2499.00, 'active'),
    (3006, '机械键盘', '电脑配件', 699.00, 'active'),
    (3007, '平板电脑', '数码', 4299.00, 'active'),
    (3008, '智能音箱', '智能家居', 1299.00, 'active');

-- 构造最近 180 天的 360 笔订单，每天 2 笔。
-- 距今 17 天的订单量被刻意放大，用于验证异常日期分析。
WITH RECURSIVE sequence(n) AS (
    SELECT 0
    UNION ALL
    SELECT n + 1 FROM sequence WHERE n < 359
)
INSERT INTO orders (
    order_id, customer_id, order_date, channel, order_status,
    sales_amount, discount_amount
)
SELECT
    n + 1,
    2001 + (n % 12),
    date('now', printf('-%d day', CAST(n / 2 AS INTEGER))),
    CASE n % 4
        WHEN 0 THEN '官网'
        WHEN 1 THEN '电商平台'
        WHEN 2 THEN '门店'
        ELSE '企业直销'
    END,
    CASE WHEN n % 19 = 0 THEN 'refunded' ELSE 'completed' END,
    ROUND(
        product.unit_price *
        CASE WHEN CAST(n / 2 AS INTEGER) = 17 THEN 20 ELSE (n % 4) + 1 END,
        2
    ),
    ROUND(CASE WHEN n % 7 = 0 THEN product.unit_price * 0.05 ELSE 0 END, 2)
FROM sequence
JOIN products AS product
  ON product.product_id = 3001 + (n % 8);

INSERT INTO order_items (
    order_item_id, order_id, product_id, quantity, unit_price, line_amount
)
SELECT
    orders.order_id,
    orders.order_id,
    3001 + ((orders.order_id - 1) % 8),
    CASE
        WHEN CAST((julianday('now') - julianday(orders.order_date)) AS INTEGER) = 17
        THEN 20
        ELSE ((orders.order_id - 1) % 4) + 1
    END,
    products.unit_price,
    orders.sales_amount
FROM orders
JOIN products
  ON products.product_id = 3001 + ((orders.order_id - 1) % 8);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);
