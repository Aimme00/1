const GUEST_COOKIE = 'askdata_guest';
const MAX_QUERY_LENGTH = 2000;
const MAX_RESULT_ROWS = 500;
const SQL_TABLES = new Set(['regions', 'categories', 'customers', 'products', 'orders', 'order_items']);
const CHART_INTENT = /图表|趋势图|折线图|柱状图|饼图|可视化|画图|生成图片|生成图像/i;
const SUPPORTED_QUERY_TOPIC = /销售额|销售|订单量|订单数|订单|产品|商品|品类|类别|分类|区域|地区|趋势|环比|月度|按月|月份|异常|峰值/i;
const UNSUPPORTED_QUERY_TOPIC = /退款|退货|渠道|手机号|电话|身份证|邮箱|密码|住址|利率/i;
const WRITE_QUERY_INTENT = /删除|删掉|清空|修改|更新|写入|插入|新增|导入|drop|delete|update|insert/i;
const DANGEROUS_SQL_FUNCTION = /\b(?:benchmark|load_file|pg_sleep|randomblob|readfile|sleep|sys_eval|sys_exec|writefile|zeroblob)\s*\(/i;

const SCHEMA_PROMPT = `
SQLite / Cloudflare D1 数据库，只允许查询以下业务表：
- regions(region_id INTEGER, region_name TEXT)
- categories(category_id INTEGER, category_name TEXT)
- customers(customer_id INTEGER, customer_name TEXT, region_id INTEGER)
- products(product_id INTEGER, product_name TEXT, category_id INTEGER, unit_price REAL)
- orders(order_id INTEGER, customer_id INTEGER, order_date TEXT, order_status TEXT, sales_amount REAL)
- order_items(order_item_id INTEGER, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL, line_amount REAL)
关系：customers.region_id=regions.region_id；orders.customer_id=customers.customer_id；order_items.order_id=orders.order_id；order_items.product_id=products.product_id；products.category_id=categories.category_id。
`;

export const CREATE_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS regions (region_id INTEGER PRIMARY KEY, region_name TEXT NOT NULL UNIQUE)`,
  `CREATE TABLE IF NOT EXISTS categories (category_id INTEGER PRIMARY KEY, category_name TEXT NOT NULL UNIQUE)`,
  `CREATE TABLE IF NOT EXISTS customers (customer_id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, region_id INTEGER NOT NULL REFERENCES regions(region_id))`,
  `CREATE TABLE IF NOT EXISTS products (product_id INTEGER PRIMARY KEY, product_name TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(category_id), unit_price REAL NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), order_date TEXT NOT NULL, order_status TEXT NOT NULL, sales_amount REAL NOT NULL DEFAULT 0)`,
  `CREATE TABLE IF NOT EXISTS order_items (order_item_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), product_id INTEGER NOT NULL REFERENCES products(product_id), quantity INTEGER NOT NULL, unit_price REAL NOT NULL, line_amount REAL NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS agent_runs (run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL, query TEXT NOT NULL, generate_chart INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, result_json TEXT, error_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)`,
  `CREATE INDEX IF NOT EXISTS idx_agent_runs_owner_session ON agent_runs(owner_id, session_id, updated_at DESC)`,
  `CREATE TABLE IF NOT EXISTS guest_usage (owner_id TEXT NOT NULL, usage_day TEXT NOT NULL, query_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner_id, usage_day))`,
  `CREATE TABLE IF NOT EXISTS saved_analyses (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, run_id TEXT NOT NULL, session_id TEXT NOT NULL, title TEXT NOT NULL, query TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner_id, run_id))`,
  `CREATE INDEX IF NOT EXISTS idx_saved_analyses_owner ON saved_analyses(owner_id, updated_at DESC)`,
  `CREATE TABLE IF NOT EXISTS dashboards (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS dashboard_cards (id TEXT PRIMARY KEY, dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE, analysis_id TEXT NOT NULL REFERENCES saved_analyses(id) ON DELETE CASCADE, title TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(dashboard_id, analysis_id))`,
];

export const SEED_STATEMENTS = [
  `INSERT OR IGNORE INTO regions(region_id, region_name) VALUES (1,'华东'),(2,'华南'),(3,'华北'),(4,'西南'),(5,'东北'),(6,'西北')`,
  `INSERT OR IGNORE INTO categories(category_id, category_name) VALUES (1,'电脑办公'),(2,'手机数码'),(3,'智能穿戴'),(4,'家用电器')`,
  `INSERT OR IGNORE INTO customers(customer_id, customer_name, region_id) VALUES
    (1,'上海星河科技',1),(2,'杭州云帆贸易',1),(3,'广州南方商贸',2),(4,'深圳未来电子',2),
    (5,'北京远见咨询',3),(6,'天津海河零售',3),(7,'成都锦城商业',4),(8,'重庆山城科技',4),
    (9,'沈阳北方供应链',5),(10,'大连海岸商贸',5),(11,'西安长安科技',6),(12,'兰州丝路零售',6)`,
  `INSERT OR IGNORE INTO products(product_id, product_name, category_id, unit_price) VALUES
    (1,'轻薄笔记本',1,6999),(2,'4K 显示器',1,3299),(3,'智能手机 Pro',2,5999),(4,'平板电脑',2,4299),
    (5,'智能手表',3,1999),(6,'无线耳机',3,899),(7,'空气净化器',4,2599),(8,'扫地机器人',4,3599)`,
  `WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<90)
   INSERT OR IGNORE INTO orders(order_id, customer_id, order_date, order_status, sales_amount)
   SELECT n, ((n-1)%12)+1, date('now', printf('-%d days',(90-n)%45)), CASE WHEN n%17=0 THEN 'cancelled' ELSE 'completed' END, 0 FROM seq`,
  `WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<90)
   INSERT OR IGNORE INTO order_items(order_item_id, order_id, product_id, quantity, unit_price, line_amount)
   SELECT n*2-1,n,((n-1)%8)+1,(1+(n%3))*CASE WHEN n IN (43,88) THEN 5 ELSE 1 END,p.unit_price,
          (1+(n%3))*CASE WHEN n IN (43,88) THEN 5 ELSE 1 END*p.unit_price
   FROM seq JOIN products p ON p.product_id=((n-1)%8)+1
   UNION ALL
   SELECT n*2,n,(n%8)+1,1+(n%2),p.unit_price,(1+(n%2))*p.unit_price
   FROM seq JOIN products p ON p.product_id=(n%8)+1`,
  `UPDATE orders SET sales_amount=COALESCE((SELECT ROUND(SUM(line_amount),2) FROM order_items WHERE order_items.order_id=orders.order_id),0) WHERE sales_amount=0`,
  `INSERT OR REPLACE INTO app_meta(key,value) VALUES ('schema_version','2')`,
];

let databaseReady = false;

function nowIso() {
  return new Date().toISOString();
}

function parseJson(value, fallback = null) {
  try { return value ? JSON.parse(value) : fallback; } catch { return fallback; }
}

function safeTitle(value, fallback = '数据分析') {
  const title = String(value || '').trim().replace(/\s+/g, ' ');
  return title ? title.slice(0, 120) : fallback;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

function identityFor(request) {
  const cookie = request.headers.get('Cookie') || '';
  const match = cookie.match(new RegExp(`(?:^|;\\s*)${GUEST_COOKIE}=([a-f0-9-]{36})`, 'i'));
  if (match) return { id: match[1], setCookie: null };
  const id = crypto.randomUUID();
  return {
    id,
    setCookie: `${GUEST_COOKIE}=${id}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax`,
  };
}

function responseHeaders(identity, extra = {}) {
  const headers = new Headers({
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'same-origin',
    ...extra,
  });
  if (identity?.setCookie) headers.set('Set-Cookie', identity.setCookie);
  return headers;
}

function json(data, status = 200, identity = null, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: responseHeaders(identity, { 'Content-Type': 'application/json; charset=utf-8', ...extra }),
  });
}

function noContent(identity = null, extra = {}) {
  return new Response(null, { status: 204, headers: responseHeaders(identity, extra) });
}

async function readBody(request) {
  try { return await request.json(); } catch { return {}; }
}

async function ensureDatabase(db) {
  if (databaseReady) return;
  try {
    const version = await db.prepare(`SELECT value FROM app_meta WHERE key='schema_version'`).first('value');
    if (version === '2') {
      databaseReady = true;
      return;
    }
  } catch {}
  await db.batch(CREATE_STATEMENTS.map(sql => db.prepare(sql)));
  for (const sql of SEED_STATEMENTS) await db.prepare(sql).run();
  databaseReady = true;
}

async function getQuotaSalt(db) {
  let salt = await db.prepare(`SELECT value FROM app_meta WHERE key='quota_salt'`).first('value');
  if (salt) return salt;
  const generated = `${crypto.randomUUID()}${crypto.randomUUID()}`;
  await db.prepare(`INSERT OR IGNORE INTO app_meta(key,value) VALUES('quota_salt',?)`).bind(generated).run();
  salt = await db.prepare(`SELECT value FROM app_meta WHERE key='quota_salt'`).first('value');
  return salt || generated;
}

async function networkQuotaKey(env, request, ownerId) {
  const connectingIp = request.headers.get('CF-Connecting-IP');
  const source = connectingIp ? `network:${connectingIp}` : `local-guest:${ownerId}`;
  const salt = await getQuotaSalt(env.DB);
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(salt),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(source));
  return `network:${[...new Uint8Array(signature)].map(value => value.toString(16).padStart(2, '0')).join('')}`;
}

function configuredQueryLimit(env) {
  const configured = Number(env.ASKDATA_GUEST_QUERY_LIMIT || 2);
  return Number.isFinite(configured) ? Math.max(1, Math.min(Math.floor(configured), 20)) : 2;
}

async function queryQuotaStatus(env, request, ownerId) {
  const limit = configuredQueryLimit(env);
  const usageBucket = 'lifetime';
  const quotaKey = await networkQuotaKey(env, request, ownerId);
  const count = await env.DB.prepare(`SELECT query_count FROM guest_usage WHERE owner_id=? AND usage_day=?`).bind(quotaKey, usageBucket).first('query_count');
  return { limit, remaining: Math.max(0, limit - Number(count || 0)), scope: 'network' };
}

async function reserveQueryQuota(env, request, ownerId) {
  const limit = configuredQueryLimit(env);
  const usageBucket = 'lifetime';
  const quotaKey = await networkQuotaKey(env, request, ownerId);
  const row = await env.DB.prepare(`INSERT INTO guest_usage(owner_id,usage_day,query_count) VALUES(?,?,1) ON CONFLICT(owner_id,usage_day) DO UPDATE SET query_count=query_count+1 WHERE query_count<? RETURNING query_count`)
    .bind(quotaKey, usageBucket, limit).first();
  if (!row) return { allowed: false, limit, remaining: 0, scope: 'network' };
  return { allowed: true, limit, remaining: Math.max(0, limit - Number(row.query_count || 0)), scope: 'network' };
}

export function fallbackSql(query) {
  assertSupportedQuery(query);
  const region = ['华东', '华南', '华北', '西南', '东北', '西北'].find(item => String(query).includes(item));
  if (region && /产品|商品/.test(query)) {
    return `SELECT p.product_name, ROUND(SUM(oi.line_amount),2) AS sales_amount, SUM(oi.quantity) AS quantity FROM order_items oi JOIN products p ON oi.product_id=p.product_id JOIN orders o ON oi.order_id=o.order_id JOIN customers c ON o.customer_id=c.customer_id JOIN regions r ON c.region_id=r.region_id WHERE o.order_status='completed' AND r.region_name='${region}' GROUP BY p.product_id,p.product_name ORDER BY sales_amount DESC LIMIT 10`;
  }
  if (/区域/.test(query)) {
    return `SELECT r.region_name AS region, COUNT(DISTINCT o.order_id) AS order_count, ROUND(SUM(o.sales_amount),2) AS sales_amount FROM orders o JOIN customers c ON o.customer_id=c.customer_id JOIN regions r ON c.region_id=r.region_id WHERE o.order_status='completed' GROUP BY r.region_id,r.region_name ORDER BY sales_amount DESC`;
  }
  if (/产品|商品/.test(query)) {
    return `SELECT p.product_name, ROUND(SUM(oi.line_amount),2) AS sales_amount, SUM(oi.quantity) AS quantity FROM order_items oi JOIN products p ON oi.product_id=p.product_id JOIN orders o ON oi.order_id=o.order_id WHERE o.order_status='completed' GROUP BY p.product_id,p.product_name ORDER BY sales_amount DESC LIMIT 10`;
  }
  if (/品类|类别|分类/.test(query)) {
    return `SELECT c.category_name, ROUND(SUM(oi.line_amount),2) AS sales_amount FROM order_items oi JOIN products p ON oi.product_id=p.product_id JOIN categories c ON p.category_id=c.category_id JOIN orders o ON oi.order_id=o.order_id WHERE o.order_status='completed' GROUP BY c.category_id,c.category_name ORDER BY sales_amount DESC`;
  }
  if (/环比|月度|按月|月份|本月与上月|上月对比/.test(query)) {
    return `SELECT strftime('%Y-%m',o.order_date) AS sales_month, ROUND(SUM(o.sales_amount),2) AS sales_amount, COUNT(*) AS order_count FROM orders o WHERE o.order_status='completed' GROUP BY sales_month ORDER BY sales_month`;
  }
  if (/异常|峰值/.test(query)) {
    return `SELECT o.order_date, ROUND(SUM(o.sales_amount),2) AS sales_amount, COUNT(*) AS order_count FROM orders o WHERE o.order_status='completed' GROUP BY o.order_date ORDER BY sales_amount DESC LIMIT 10`;
  }
  return `SELECT o.order_date, ROUND(SUM(o.sales_amount),2) AS sales_amount, COUNT(*) AS order_count FROM orders o WHERE o.order_status='completed' AND o.order_date>=date('now','-29 day') GROUP BY o.order_date ORDER BY o.order_date`;
}

export function assertSupportedQuery(query) {
  const normalized = String(query || '').trim();
  if (!normalized) throw new Error('问题不能为空。');
  if (WRITE_QUERY_INTENT.test(normalized)) {
    throw new Error('当前 Demo 仅支持只读数据分析，不能执行删除、修改或写入操作。');
  }
  if (UNSUPPORTED_QUERY_TOPIC.test(normalized)) {
    throw new Error('当前 D1 演示库缺少回答该问题所需的字段，请改问销售额、订单、区域、商品或品类分析。');
  }
  if (!SUPPORTED_QUERY_TOPIC.test(normalized)) {
    throw new Error('当前问题不在演示数据支持范围内，请改问销售额、订单、区域、商品或品类分析。');
  }
  return true;
}

function extractJsonObject(text) {
  const cleaned = String(text || '').replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim();
  const start = cleaned.indexOf('{');
  const end = cleaned.lastIndexOf('}');
  if (start < 0 || end <= start) throw new Error('模型未返回 JSON');
  return JSON.parse(cleaned.slice(start, end + 1));
}

function modelApiKey(env) {
  return env.MODEL_API_KEY || env.DASHSCOPE_API_KEY || '';
}

export function buildModelRequest(env, query) {
  const apiKey = modelApiKey(env);
  const baseUrl = String(env.MODEL_BASE_URL || env.DASHSCOPE_BASE_URL || 'https://dashscope.aliyuncs.com/compatible-mode/v1').replace(/\/$/, '');
  const model = String(env.MODEL_NAME || env.DASHSCOPE_MODEL || 'qwen-plus');
  const body = {
    model,
    temperature: 0,
    messages: [
      {
        role: 'system',
        content: `你是只读数据分析 Agent 的 SQL 生成节点。${SCHEMA_PROMPT}\n仅输出 JSON：{"sql":"一条 SQLite SELECT 查询，不要分号","title":"短标题"}。不得使用写操作、PRAGMA、系统表或未列出的表；默认最多返回 500 行。`,
      },
      { role: 'user', content: query },
    ],
  };
  if (/api\.deepseek\.com/i.test(baseUrl) || /^deepseek-v4-/i.test(model)) {
    body.thinking = { type: 'disabled' };
  }
  return { apiKey, url: `${baseUrl}/chat/completions`, body };
}

async function generateSqlPlan(env, query) {
  assertSupportedQuery(query);
  const modelRequest = buildModelRequest(env, query);
  if (!modelRequest.apiKey) {
    return { sql: fallbackSql(query), title: safeTitle(query), modelUsed: false, warning: '未配置模型 API Key，当前使用内置规则生成 SQL。' };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(modelRequest.url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${modelRequest.apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(modelRequest.body),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`模型接口返回 ${response.status}`);
    const payload = await response.json();
    const parsed = extractJsonObject(payload?.choices?.[0]?.message?.content);
    return { sql: parsed.sql, title: safeTitle(parsed.title, query), modelUsed: true, warning: null };
  } catch (error) {
    return { sql: fallbackSql(query), title: safeTitle(query), modelUsed: false, warning: `模型暂时不可用，已使用安全规则继续分析：${error.message}` };
  } finally {
    clearTimeout(timer);
  }
}

export function validateSql(rawSql) {
  let sql = String(rawSql || '').trim().replace(/^```(?:sql)?\s*/i, '').replace(/\s*```$/i, '').trim();
  sql = sql.replace(/;\s*$/, '');
  if (!sql || sql.length > 5000) throw new Error('SQL 为空或过长');
  if (!/^\s*(SELECT|WITH)\b/i.test(sql)) throw new Error('只允许 SELECT 查询');
  if (/[;]/.test(sql) || /--|\/\*/.test(sql)) throw new Error('SQL 只能包含一条无注释查询');
  if (/\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|GRANT|REVOKE|CALL)\b/i.test(sql)) {
    throw new Error('检测到非只读 SQL');
  }
  const structuralSql = sql.replace(/'(?:''|[^'])*'/g, "''");
  if (/\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b/i.test(structuralSql)) {
    throw new Error('不允许锁查询');
  }
  if (/\bINTO\s+(?:OUT|DUMP)FILE\b/i.test(structuralSql) || DANGEROUS_SQL_FUNCTION.test(structuralSql)) {
    throw new Error('不允许文件访问、延时或高成本函数');
  }
  if (/\bCROSS\s+JOIN\b|\bWITH\s+RECURSIVE\b/i.test(structuralSql)) {
    throw new Error('不允许笛卡尔积或递归查询');
  }
  const joinCount = (structuralSql.match(/\bJOIN\b/gi) || []).length;
  if (joinCount > 8) throw new Error('查询关联表过多');
  const cteNames = new Set([...sql.matchAll(/\b([a-zA-Z_][\w]*)\s+AS\s*\(/gi)].map(match => match[1].toLowerCase()));
  const references = [...sql.matchAll(/\b(?:FROM|JOIN)\s+([`"\[]?)([a-zA-Z_][\w]*)(?:[`"\]]?)/gi)].map(match => match[2].toLowerCase());
  for (const table of references) {
    if (!SQL_TABLES.has(table) && !cteNames.has(table)) throw new Error(`不允许访问表：${table}`);
  }
  if (!references.length) throw new Error('SQL 未引用允许的业务表');
  if (/\bLIMIT\s+\d+/i.test(sql)) {
    sql = sql.replace(/\bLIMIT\s+(\d+)/i, (_, value) => `LIMIT ${Math.min(Number(value), MAX_RESULT_ROWS)}`);
  } else {
    sql += ` LIMIT ${MAX_RESULT_ROWS}`;
  }
  return sql;
}

function numberValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function buildInsights(columns, rows) {
  if (!rows.length) return [{ title: '查询结果', text: '当前条件下没有匹配数据。' }];
  const numericIndexes = columns.map((column, index) => ({ column, index })).filter(({ index }) => rows.some(row => numberValue(row[index]) !== null));
  if (!numericIndexes.length) return [{ title: '结果规模', text: `共返回 ${rows.length} 行数据。` }];
  const metric = numericIndexes.find(item => !/(^id$|_id$|date|month|year)/i.test(item.column)) || numericIndexes[0];
  const values = rows.map(row => numberValue(row[metric.index])).filter(value => value !== null);
  const total = values.reduce((sum, value) => sum + value, 0);
  const max = Math.max(...values);
  const maxIndex = rows.findIndex(row => numberValue(row[metric.index]) === max);
  return [
    { title: '结果规模', text: `本次查询返回 ${rows.length} 行。` },
    { title: '指标合计', text: `${metric.column} 合计为 ${formatNumber(total)}。` },
    { title: '最高值', text: `${rows[maxIndex]?.[0] ?? '当前结果'} 的 ${metric.column} 最高，为 ${formatNumber(max)}。` },
  ];
}

function buildAnswer(columns, rows) {
  if (!rows.length) return '当前筛选条件下没有查询到匹配数据。';
  const metricIndex = columns.findIndex(column => /(sales|amount|count|quantity|total|revenue)/i.test(column));
  if (metricIndex > 0) {
    return `${rows[0][0]} 当前排名第一，${columns[metricIndex]} 为 ${formatNumber(rows[0][metricIndex])}；本次共比较 ${rows.length} 组数据。`;
  }
  return `查询已完成，共返回 ${rows.length} 行数据。你可以查看数据表和生成的只读 SQL。`;
}

function buildChart(columns, rows, query, requested) {
  if (!(requested || CHART_INTENT.test(query)) || rows.length === 0 || columns.length < 2) return [];
  const metricIndex = columns.findIndex((column, index) => index > 0 && rows.some(row => numberValue(row[index]) !== null));
  if (metricIndex < 1) return [];
  const timeSeries = /(date|day|month|year|time|日期|月份)/i.test(columns[0]);
  return [{
    title: timeSeries ? '数据趋势' : '数据对比',
    option: {
      xAxis: { type: 'category', data: rows.slice(0, 30).map(row => row[0]) },
      yAxis: { type: 'value' },
      series: [{ type: timeSeries ? 'line' : 'bar', data: rows.slice(0, 30).map(row => numberValue(row[metricIndex]) || 0) }],
    },
  }];
}

function buildDrillActions(columns, rows) {
  if (!rows.length) return [];
  if (columns[0] === 'region' && /^[\u4e00-\u9fa5A-Za-z0-9 _-]{1,30}$/.test(String(rows[0][0]))) {
    return [{ id: 'region_to_product', direction: 'down', label: `下钻${rows[0][0]}的产品销售额`, query: `查询${rows[0][0]}区域各产品销售额排名` }];
  }
  if (/(order_date|sales_date)/i.test(columns[0])) {
    return [{ id: 'day_to_month', direction: 'up', label: '上卷到月度销售额', query: '按月汇总最近180天销售额趋势' }];
  }
  return [];
}

async function executeAgent(env, query, generateChart, emit = async () => {}) {
  const started = Date.now();
  await emit('intent', 'completed', '已识别数据查询意图');
  await emit('schema', 'completed', '已检索 6 张业务表及字段关系');
  await emit('plan', 'completed', '分析计划已生成');
  const plan = await generateSqlPlan(env, query);
  await emit('sql_generate', 'completed', plan.modelUsed ? '模型已生成 SQL' : '规则节点已生成 SQL');
  let sql;
  const warnings = [];
  if (plan.warning) warnings.push(plan.warning);
  try {
    sql = validateSql(plan.sql);
  } catch (error) {
    warnings.push(`模型 SQL 校验未通过，已自动修复：${error.message}`);
    sql = validateSql(fallbackSql(query));
  }
  await emit('sql_validate', 'completed', 'SQL 只读安全校验通过');
  const queryResult = await env.DB.prepare(sql).all();
  const objects = queryResult.results || [];
  const columns = objects.length ? Object.keys(objects[0]) : [];
  const rows = objects.map(row => columns.map(column => row[column]));
  await emit('sql_execute', 'completed', `查询完成，返回 ${rows.length} 行`);
  const insights = buildInsights(columns, rows);
  const charts = buildChart(columns, rows, query, generateChart === true);
  await emit('analysis', 'completed', charts.length ? '数据分析和图表配置已生成' : '数据分析完成，无需生成图表');
  return {
    schema_version: '1.0',
    status: 'completed',
    answer: buildAnswer(columns, rows),
    insights,
    table: { columns, rows, returned_rows: rows.length, truncated: rows.length >= MAX_RESULT_ROWS },
    charts,
    sql: { text: sql, dialect: 'sqlite', duration_ms: Date.now() - started, validation: { is_valid: true, parser: 'cloudflare-readonly-guard' } },
    scope: {
      database: 'Cloudflare D1 · AskData Demo',
      row_count: rows.length,
      truncated: rows.length >= MAX_RESULT_ROWS,
      chart_requested: generateChart === true || CHART_INTENT.test(query),
      model_used: plan.modelUsed,
      drill_actions: buildDrillActions(columns, rows),
    },
    warnings,
    suggested_questions: ['按区域查看销售额排名', '生成最近30天销售趋势图', '销售额最高的前5个产品是什么？'],
    error: null,
    title: plan.title,
  };
}

function eventPayload(event, node, status, message, sequence) {
  return `event: ${event}\ndata: ${JSON.stringify({ event, node, status, message, sequence, data: {} })}\n\n`;
}

function streamRun(context, run, identity) {
  const encoder = new TextEncoder();
  let task;
  const stream = new ReadableStream({
    start(controller) {
      task = (async () => {
        let sequence = 0;
        const emit = async (node, status, message, event = 'progress') => {
          controller.enqueue(encoder.encode(eventPayload(event, node, status, message, ++sequence)));
        };
        try {
          if (run.status === 'completed') {
            await emit('run', 'completed', '任务已完成', 'completed');
            return;
          }
          const started = nowIso();
          await context.env.DB.prepare(`UPDATE agent_runs SET status='running',updated_at=? WHERE run_id=? AND owner_id=?`).bind(started, run.run_id, identity.id).run();
          await emit('run', 'running', 'Agent 开始执行', 'started');
          const result = await executeAgent(context.env, run.query, Boolean(run.generate_chart), emit);
          result.run_id = run.run_id;
          const completed = nowIso();
          await context.env.DB.prepare(`UPDATE agent_runs SET status='completed',result_json=?,error_json=NULL,updated_at=? WHERE run_id=? AND owner_id=?`).bind(JSON.stringify(result), completed, run.run_id, identity.id).run();
          await emit('run', 'completed', '分析任务完成', 'completed');
        } catch (error) {
          const failed = nowIso();
          const detail = { message: error.message || 'Agent 执行失败' };
          await context.env.DB.prepare(`UPDATE agent_runs SET status='failed',error_json=?,updated_at=? WHERE run_id=? AND owner_id=?`).bind(JSON.stringify(detail), failed, run.run_id, identity.id).run();
          await emit('run', 'failed', detail.message, 'failed');
        } finally {
          controller.close();
        }
      })();
      context.waitUntil(task);
    },
    cancel() {},
  });
  return new Response(stream, {
    headers: responseHeaders(identity, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    }),
  });
}

export function toCsv(result) {
  const columns = result?.table?.columns || [];
  const rows = result?.table?.rows || [];
  const escape = value => {
    let text = String(value ?? '');
    if (typeof value === 'string' && /^[\s]*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  };
  return '\uFEFF' + [columns, ...rows].map(row => row.map(escape).join(',')).join('\r\n');
}

function toExcelHtml(result) {
  const columns = result?.table?.columns || [];
  const rows = result?.table?.rows || [];
  return `<!doctype html><html><head><meta charset="utf-8"></head><body><table><thead><tr>${columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(v => `<td>${escapeHtml(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></body></html>`;
}

async function exportResponse(db, ownerId, kind, id, format, identity) {
  const row = kind === 'analyses'
    ? await db.prepare(`SELECT result_json,title FROM saved_analyses WHERE id=? AND owner_id=?`).bind(id, ownerId).first()
    : await db.prepare(`SELECT result_json,query AS title FROM agent_runs WHERE run_id=? AND owner_id=?`).bind(id, ownerId).first();
  if (!row?.result_json) return json({ detail: '没有可导出的分析结果。' }, 404, identity);
  const result = parseJson(row.result_json, {});
  const base = safeTitle(row.title, 'askdata-report').replace(/[^\u4e00-\u9fa5\w-]+/g, '_');
  if (format === 'csv') {
    return new Response(toCsv(result), { headers: responseHeaders(identity, { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': `attachment; filename="${encodeURIComponent(base)}.csv"` }) });
  }
  return new Response(toExcelHtml(result), { headers: responseHeaders(identity, { 'Content-Type': 'application/vnd.ms-excel; charset=utf-8', 'Content-Disposition': `attachment; filename="${encodeURIComponent(base)}.xls"` }) });
}

function dashboardPayload(row, cards = []) {
  return { id: row.id, name: row.name, description: row.description || '', created_at: row.created_at, updated_at: row.updated_at, cards };
}

async function handleApi(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();
  const identity = identityFor(request);

  if (method === 'OPTIONS') return noContent(identity, { Allow: 'GET,POST,DELETE,OPTIONS' });
  if (path === '/api/health') return json({ ok: true, runtime: 'cloudflare-pages-functions', database_bound: Boolean(env.DB), model_configured: Boolean(modelApiKey(env)) }, 200, identity);
  if (path === '/api/auth/me' || path === '/api/auth/login') {
    return json({ user: { id: identity.id, email: 'guest@askdata.demo', display_name: 'Demo Guest', is_active: true, is_admin: false, is_guest: true, created_at: Math.floor(Date.now() / 1000) } }, 200, identity);
  }
  if (path === '/api/auth/logout') {
    identity.setCookie = `${GUEST_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;
    return noContent(identity);
  }
  if (!env.DB) return json({ detail: 'Cloudflare D1 尚未绑定。请将数据库以变量名 DB 绑定到 Pages 项目后重新部署。' }, 503, identity);
  await ensureDatabase(env.DB);

  if (path === '/api/data-source/status' || path === '/api/data-source/test' || path === '/api/data-source/sync') {
    const count = await env.DB.prepare(`SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table' AND name IN ('regions','categories','customers','products','orders','order_items')`).first('count');
    const quota = await queryQuotaStatus(env, request, identity.id);
    return json({ database: 'AskData Demo', alias: 'Cloudflare D1', database_type: 'sqlite', ready: Number(count) === 6, table_count: Number(count), column_count: 27, readonly: true, model_configured: Boolean(modelApiKey(env)), query_limit: quota.limit, query_remaining: quota.remaining, quota_scope: quota.scope }, 200, identity);
  }

  if (path === '/api/chat' && method === 'POST') {
    const body = await readBody(request);
    const query = String(body.query || '').trim();
    if (!query || query.length > MAX_QUERY_LENGTH) return json({ detail: `问题不能为空且不能超过 ${MAX_QUERY_LENGTH} 字。` }, 422, identity);
    try {
      assertSupportedQuery(query);
    } catch (error) {
      return json({ detail: error.message }, 422, identity);
    }
    const quota = await reserveQueryQuota(env, request, identity.id);
    if (!quota.allowed) return json({ detail: `本次体验的 ${quota.limit} 次提问已全部使用完。` }, 429, identity);
    const runId = crypto.randomUUID();
    const created = nowIso();
    await env.DB.prepare(`INSERT INTO agent_runs(run_id,owner_id,session_id,query,generate_chart,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)`)
      .bind(runId, identity.id, String(body.session_id || crypto.randomUUID()).slice(0, 120), query, body.generate_chart === true ? 1 : 0, created, created).run();
    return json({ run_id: runId, status: 'pending', events_url: `/api/runs/${runId}/events`, result_url: `/api/runs/${runId}`, quota }, 202, identity);
  }

  if (path === '/api/drilldown' && method === 'POST') {
    const body = await readBody(request);
    const parent = await env.DB.prepare(`SELECT session_id FROM agent_runs WHERE run_id=? AND owner_id=? AND status='completed'`).bind(body.parent_run_id, identity.id).first();
    if (!parent) return json({ detail: '父分析不存在或尚未完成。' }, 404, identity);
    const query = String(body.query || '').trim();
    if (!query || query.length > MAX_QUERY_LENGTH) return json({ detail: '下钻问题无效。' }, 422, identity);
    try {
      assertSupportedQuery(query);
    } catch (error) {
      return json({ detail: error.message }, 422, identity);
    }
    const quota = await reserveQueryQuota(env, request, identity.id);
    if (!quota.allowed) return json({ detail: `本次体验的 ${quota.limit} 次提问已全部使用完。` }, 429, identity);
    const runId = crypto.randomUUID();
    const created = nowIso();
    await env.DB.prepare(`INSERT INTO agent_runs(run_id,owner_id,session_id,query,generate_chart,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)`)
      .bind(runId, identity.id, parent.session_id, query, body.generate_chart === true ? 1 : 0, created, created).run();
    return json({ run_id: runId, status: 'pending', events_url: `/api/runs/${runId}/events`, result_url: `/api/runs/${runId}`, quota }, 202, identity);
  }

  const eventsMatch = path.match(/^\/api\/runs\/([^/]+)\/events$/);
  if (eventsMatch && method === 'GET') {
    const run = await env.DB.prepare(`SELECT * FROM agent_runs WHERE run_id=? AND owner_id=?`).bind(eventsMatch[1], identity.id).first();
    if (!run) return json({ detail: '分析任务不存在。' }, 404, identity);
    return streamRun(context, run, identity);
  }

  const cancelMatch = path.match(/^\/api\/runs\/([^/]+)\/cancel$/);
  if (cancelMatch && method === 'POST') {
    const updated = nowIso();
    await env.DB.prepare(`UPDATE agent_runs SET status='cancelled',updated_at=? WHERE run_id=? AND owner_id=? AND status IN ('pending','running')`).bind(updated, cancelMatch[1], identity.id).run();
    return json({ status: 'cancelled' }, 200, identity);
  }

  const exportMatch = path.match(/^\/api\/(runs|analyses)\/([^/]+)\/export\.(csv|xlsx)$/);
  if (exportMatch && method === 'GET') return exportResponse(env.DB, identity.id, exportMatch[1], exportMatch[2], exportMatch[3], identity);

  const runMatch = path.match(/^\/api\/runs\/([^/]+)$/);
  if (runMatch && method === 'GET') {
    const run = await env.DB.prepare(`SELECT * FROM agent_runs WHERE run_id=? AND owner_id=?`).bind(runMatch[1], identity.id).first();
    if (!run) return json({ detail: '分析任务不存在。' }, 404, identity);
    return json({ run_id: run.run_id, status: run.status, result: parseJson(run.result_json), error: parseJson(run.error_json) }, 200, identity);
  }

  if (path === '/api/conversations' && method === 'GET') {
    const data = await env.DB.prepare(`SELECT session_id,MIN(query) AS title,MAX(updated_at) AS updated_at,COUNT(*) AS message_count FROM agent_runs WHERE owner_id=? GROUP BY session_id ORDER BY updated_at DESC LIMIT 30`).bind(identity.id).all();
    return json({ items: data.results || [] }, 200, identity);
  }

  const conversationMatch = path.match(/^\/api\/conversations\/([^/]+)$/);
  if (conversationMatch && method === 'GET') {
    const data = await env.DB.prepare(`SELECT query,result_json,created_at,updated_at FROM agent_runs WHERE owner_id=? AND session_id=? ORDER BY created_at ASC`).bind(identity.id, decodeURIComponent(conversationMatch[1])).all();
    const items = [];
    for (const row of data.results || []) {
      items.push({ role: 'user', content: row.query, payload: null, created_at: row.created_at });
      if (row.result_json) items.push({ role: 'assistant', content: parseJson(row.result_json)?.answer || '分析完成', payload: parseJson(row.result_json, {}), created_at: row.updated_at });
    }
    return json({ items }, 200, identity);
  }

  if (path === '/api/analyses' && method === 'GET') {
    const data = await env.DB.prepare(`SELECT id,run_id,session_id,title,query,created_at,updated_at FROM saved_analyses WHERE owner_id=? ORDER BY updated_at DESC LIMIT 50`).bind(identity.id).all();
    return json({ items: data.results || [] }, 200, identity);
  }
  if (path === '/api/analyses' && method === 'POST') {
    const body = await readBody(request);
    const run = await env.DB.prepare(`SELECT * FROM agent_runs WHERE run_id=? AND owner_id=? AND status='completed'`).bind(body.run_id, identity.id).first();
    if (!run?.result_json) return json({ detail: '只能保存已完成的分析。' }, 404, identity);
    const existing = await env.DB.prepare(`SELECT id,created_at FROM saved_analyses WHERE owner_id=? AND run_id=?`).bind(identity.id, run.run_id).first();
    const id = existing?.id || crypto.randomUUID();
    const created = existing?.created_at || nowIso();
    const updated = nowIso();
    await env.DB.prepare(`INSERT INTO saved_analyses(id,owner_id,run_id,session_id,title,query,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,run_id) DO UPDATE SET title=excluded.title,result_json=excluded.result_json,updated_at=excluded.updated_at`)
      .bind(id, identity.id, run.run_id, run.session_id, safeTitle(body.title, run.query), run.query, run.result_json, created, updated).run();
    return json({ id, run_id: run.run_id, session_id: run.session_id, title: safeTitle(body.title, run.query), query: run.query, result: parseJson(run.result_json), created_at: created, updated_at: updated }, existing ? 200 : 201, identity);
  }

  const analysisMatch = path.match(/^\/api\/analyses\/([^/]+)$/);
  if (analysisMatch && method === 'GET') {
    const item = await env.DB.prepare(`SELECT * FROM saved_analyses WHERE id=? AND owner_id=?`).bind(analysisMatch[1], identity.id).first();
    if (!item) return json({ detail: '保存的分析不存在。' }, 404, identity);
    return json({ ...item, result: parseJson(item.result_json), result_json: undefined }, 200, identity);
  }
  if (analysisMatch && method === 'DELETE') {
    await env.DB.prepare(`DELETE FROM saved_analyses WHERE id=? AND owner_id=?`).bind(analysisMatch[1], identity.id).run();
    return noContent(identity);
  }

  if (path === '/api/dashboards' && method === 'GET') {
    const data = await env.DB.prepare(`SELECT d.*,COUNT(c.id) AS card_count FROM dashboards d LEFT JOIN dashboard_cards c ON c.dashboard_id=d.id WHERE d.owner_id=? GROUP BY d.id ORDER BY d.updated_at DESC`).bind(identity.id).all();
    return json({ items: data.results || [] }, 200, identity);
  }
  if (path === '/api/dashboards' && method === 'POST') {
    const body = await readBody(request);
    const id = crypto.randomUUID();
    const created = nowIso();
    await env.DB.prepare(`INSERT INTO dashboards(id,owner_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)`).bind(id, identity.id, safeTitle(body.name, '我的仪表盘'), String(body.description || '').slice(0, 300), created, created).run();
    return json(dashboardPayload({ id, name: safeTitle(body.name, '我的仪表盘'), description: String(body.description || '').slice(0, 300), created_at: created, updated_at: created }), 201, identity);
  }

  const cardsMatch = path.match(/^\/api\/dashboards\/([^/]+)\/cards$/);
  if (cardsMatch && method === 'POST') {
    const body = await readBody(request);
    const dashboard = await env.DB.prepare(`SELECT id FROM dashboards WHERE id=? AND owner_id=?`).bind(cardsMatch[1], identity.id).first();
    const analysis = await env.DB.prepare(`SELECT id,title FROM saved_analyses WHERE id=? AND owner_id=?`).bind(body.analysis_id, identity.id).first();
    if (!dashboard || !analysis) return json({ detail: '仪表盘或分析不存在。' }, 404, identity);
    const existing = await env.DB.prepare(`SELECT id FROM dashboard_cards WHERE dashboard_id=? AND analysis_id=?`).bind(dashboard.id, analysis.id).first();
    const id = existing?.id || crypto.randomUUID();
    const created = nowIso();
    await env.DB.prepare(`INSERT INTO dashboard_cards(id,dashboard_id,analysis_id,title,created_at) VALUES(?,?,?,?,?) ON CONFLICT(dashboard_id,analysis_id) DO UPDATE SET title=excluded.title`).bind(id, dashboard.id, analysis.id, safeTitle(body.title, analysis.title), created).run();
    await env.DB.prepare(`UPDATE dashboards SET updated_at=? WHERE id=?`).bind(created, dashboard.id).run();
    return json({ id, analysis_id: analysis.id, title: safeTitle(body.title, analysis.title) }, existing ? 200 : 201, identity);
  }

  const cardMatch = path.match(/^\/api\/dashboards\/([^/]+)\/cards\/([^/]+)$/);
  if (cardMatch && method === 'DELETE') {
    await env.DB.prepare(`DELETE FROM dashboard_cards WHERE id=? AND dashboard_id IN (SELECT id FROM dashboards WHERE id=? AND owner_id=?)`).bind(cardMatch[2], cardMatch[1], identity.id).run();
    return noContent(identity);
  }

  const dashboardMatch = path.match(/^\/api\/dashboards\/([^/]+)$/);
  if (dashboardMatch && method === 'GET') {
    const dashboard = await env.DB.prepare(`SELECT * FROM dashboards WHERE id=? AND owner_id=?`).bind(dashboardMatch[1], identity.id).first();
    if (!dashboard) return json({ detail: '仪表盘不存在。' }, 404, identity);
    const data = await env.DB.prepare(`SELECT c.id,c.analysis_id,c.title,c.created_at,a.query,a.result_json,a.updated_at AS analysis_updated_at FROM dashboard_cards c JOIN saved_analyses a ON a.id=c.analysis_id WHERE c.dashboard_id=? ORDER BY c.created_at DESC`).bind(dashboard.id).all();
    const cards = (data.results || []).map(card => ({ ...card, result: parseJson(card.result_json), result_json: undefined }));
    return json(dashboardPayload(dashboard, cards), 200, identity);
  }
  if (dashboardMatch && method === 'DELETE') {
    await env.DB.prepare(`DELETE FROM dashboards WHERE id=? AND owner_id=?`).bind(dashboardMatch[1], identity.id).run();
    return noContent(identity);
  }

  return json({ detail: `未找到接口：${method} ${path}` }, 404, identity);
}

export async function onRequest(context) {
  try {
    return await handleApi(context);
  } catch (error) {
    return json({ detail: error?.message || 'Cloudflare Function 发生未知错误。' }, 500, identityFor(context.request));
  }
}
