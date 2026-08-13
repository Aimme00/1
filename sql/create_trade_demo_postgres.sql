CREATE TABLE IF NOT EXISTS trade_summary (
    user_id BIGINT PRIMARY KEY,
    total_trade_count BIGINT NOT NULL,
    total_trade_amount DOUBLE PRECISION NOT NULL,
    active_days INTEGER NOT NULL,
    last_trade_time DATE
);

CREATE TABLE IF NOT EXISTS interest_info (
    user_id BIGINT PRIMARY KEY REFERENCES trade_summary(user_id),
    interest_rate DOUBLE PRECISION NOT NULL,
    rate_type TEXT NOT NULL,
    effective_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id BIGINT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    region TEXT NOT NULL,
    customer_level TEXT NOT NULL,
    created_at DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id BIGINT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    order_date DATE NOT NULL,
    channel TEXT NOT NULL,
    order_status TEXT NOT NULL,
    sales_amount DOUBLE PRECISION NOT NULL,
    discount_amount DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(order_id),
    product_id BIGINT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    unit_price DOUBLE PRECISION NOT NULL,
    line_amount DOUBLE PRECISION NOT NULL
);

INSERT INTO trade_summary VALUES
    (1001, 12, 5300.50, 5, DATE '2024-05-01'),
    (1002, 5800, 230000.00, 180, DATE '2024-05-06'),
    (1003, 56000, 1800000.00, 320, DATE '2024-05-10'),
    (1004, 102430, 3700000.00, 365, DATE '2024-05-11'),
    (1005, 0, 0.00, 0, NULL),
    (1006, 50001, 910000.00, 250, DATE '2024-05-13'),
    (1007, 49999, 870000.00, 245, DATE '2024-05-13'),
    (1008, 76000, 2500000.00, 330, DATE '2024-05-14'),
    (1009, 340, 35000.00, 40, DATE '2024-05-15'),
    (1010, 88888, 3200000.00, 350, DATE '2024-05-16')
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO interest_info VALUES
    (1001, 2.35, 'standard', 'active'),
    (1002, 3.12, 'vip', 'active'),
    (1003, 4.58, 'high_value', 'active'),
    (1004, 4.95, 'high_value', 'active'),
    (1005, 1.80, 'standard', 'inactive'),
    (1006, 4.20, 'high_value', 'active'),
    (1007, 3.88, 'vip', 'active'),
    (1008, 5.15, 'high_value', 'inactive'),
    (1009, 2.80, 'standard', 'active'),
    (1010, 5.05, 'high_value', 'active')
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO customers VALUES
    (2001, '华东零售客户A', '华东', '黄金', CURRENT_DATE - 420),
    (2002, '华东零售客户B', '华东', '白银', CURRENT_DATE - 390),
    (2003, '华南零售客户A', '华南', '黄金', CURRENT_DATE - 360),
    (2004, '华南零售客户B', '华南', '普通', CURRENT_DATE - 330),
    (2005, '华北零售客户A', '华北', '钻石', CURRENT_DATE - 300),
    (2006, '华北零售客户B', '华北', '白银', CURRENT_DATE - 270),
    (2007, '西南零售客户A', '西南', '黄金', CURRENT_DATE - 240),
    (2008, '西南零售客户B', '西南', '普通', CURRENT_DATE - 210),
    (2009, '东北零售客户A', '东北', '白银', CURRENT_DATE - 180),
    (2010, '东北零售客户B', '东北', '普通', CURRENT_DATE - 150),
    (2011, '西北零售客户A', '西北', '黄金', CURRENT_DATE - 120),
    (2012, '西北零售客户B', '西北', '普通', CURRENT_DATE - 90)
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO products VALUES
    (3001, '智能手机 Pro', '数码', 5999.00, 'active'),
    (3002, '轻薄笔记本', '电脑', 7999.00, 'active'),
    (3003, '无线耳机', '数码配件', 899.00, 'active'),
    (3004, '智能手表', '可穿戴', 1899.00, 'active'),
    (3005, '4K 显示器', '电脑配件', 2499.00, 'active'),
    (3006, '机械键盘', '电脑配件', 699.00, 'active'),
    (3007, '平板电脑', '数码', 4299.00, 'active'),
    (3008, '智能音箱', '智能家居', 1299.00, 'active')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO orders (
    order_id, customer_id, order_date, channel, order_status,
    sales_amount, discount_amount
)
SELECT
    n + 1,
    2001 + (n % 12),
    CURRENT_DATE - ((n / 2)::INTEGER),
    CASE n % 4 WHEN 0 THEN '官网' WHEN 1 THEN '电商平台' WHEN 2 THEN '门店' ELSE '企业直销' END,
    CASE WHEN n % 19 = 0 THEN 'refunded' ELSE 'completed' END,
    ROUND((p.unit_price * CASE WHEN (n / 2)::INTEGER = 17 THEN 20 ELSE (n % 4) + 1 END)::NUMERIC, 2),
    ROUND((CASE WHEN n % 7 = 0 THEN p.unit_price * 0.05 ELSE 0 END)::NUMERIC, 2)
FROM generate_series(0, 359) AS sequence(n)
JOIN products p ON p.product_id = 3001 + (n % 8)
ON CONFLICT (order_id) DO NOTHING;

INSERT INTO order_items (
    order_item_id, order_id, product_id, quantity, unit_price, line_amount
)
SELECT
    o.order_id,
    o.order_id,
    3001 + ((o.order_id - 1) % 8),
    CASE WHEN CURRENT_DATE - o.order_date = 17 THEN 20 ELSE ((o.order_id - 1) % 4) + 1 END,
    p.unit_price,
    o.sales_amount
FROM orders o
JOIN products p ON p.product_id = 3001 + ((o.order_id - 1) % 8)
ON CONFLICT (order_item_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);
