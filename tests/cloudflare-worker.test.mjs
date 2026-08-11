import test from 'node:test';
import assert from 'node:assert/strict';
import { assertSupportedQuery, buildAnswer, buildChart, buildInsights, buildModelRequest, fallbackSql, isDeterministicDescriptiveQuery, isRegionalTopProductsQuery, planSqlQuery, testerAuthorized, toCsv, validateSql } from '../functions/api/[[path]].js';

test('fallback SQL covers the main demo intents', () => {
  assert.match(fallbackSql('各区域订单排名'), /regions/i);
  assert.match(fallbackSql('销售额最高的产品'), /products/i);
  assert.match(fallbackSql('最近30天趋势'), /order_date/i);
  assert.match(fallbackSql('各品类销售额占比'), /sales_share_pct/i);
  assert.match(fallbackSql('本月与上月销售额对比'), /month_over_month_pct/i);
});

test('demo is explicitly limited to descriptive analysis', () => {
  for (const query of ['找出销售额异常的日期', '为什么本月销售额下降', '预测下月销售额', '给出经营策略建议']) {
    assert.throws(() => assertSupportedQuery(query), /描述性分析/);
  }
  for (const query of ['最近30天销售额趋势', '各区域订单量排名', '各品类销售额占比', '本月与上月销售额对比', '销售额区域分布']) {
    assert.equal(assertSupportedQuery(query), true);
    assert.equal(isDeterministicDescriptiveQuery(query), true);
  }
});

test('regional top products use an outer query to filter window rankings', () => {
  const sql = fallbackSql('最近30天各区域销售额排名前3的产品');
  assert.match(sql, /ROW_NUMBER\(\) OVER/i);
  assert.match(sql, /FROM ranked\s+WHERE region_rank<=3/i);
  assert.doesNotMatch(sql, /WHERE[^)]*ROW_NUMBER/i);
  assert.equal(validateSql(sql).endsWith('LIMIT 500'), true);
  assert.equal(isRegionalTopProductsQuery('最近30天各区域销售额排名前3的产品'), true);
});

test('regional product result uses readable labels and consistent conclusions', () => {
  const columns = ['region', 'product_name', 'product_sales', 'sales_quantity', 'order_count', 'region_sales', 'sales_share_pct', 'region_rank'];
  const rows = [
    ['华南', '扫地机器人', 86376, 24, 8, 124768, 69.23, 1],
    ['华南', '轻薄笔记本', 27996, 4, 4, 124768, 22.44, 2],
    ['华南', '无线耳机', 10396, 8, 3, 124768, 8.33, 3],
    ['华东', '4K 显示器', 32990, 10, 4, 72984, 45.2, 1],
  ];
  const [chart] = buildChart(columns, rows, '各区域销售额排名前3的产品并生成柱状图', true);
  assert.equal(chart.option.orientation, 'horizontal');
  assert.deepEqual(chart.option.series[0].data[0], { name: '华南 · 扫地机器人', value: 86376 });
  assert.match(buildAnswer(columns, rows), /华南.*扫地机器人.*69.23%/);
  assert.match(buildInsights(columns, rows)[2].text, /华南.*69.23%/);
});

test('regional top-3 planning does not call the configured model API', async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => { fetchCalls += 1; throw new Error('offline test must not call the network'); };
  try {
    const plan = await planSqlQuery({ MODEL_API_KEY: 'must-not-be-used' }, '最近30天各区域销售额排名前3的产品');
    assert.equal(fetchCalls, 0);
    assert.equal(plan.modelUsed, false);
    assert.match(plan.warning, /不消耗模型 API/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('standard descriptive analysis does not call the configured model API', async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => { fetchCalls += 1; throw new Error('offline test must not call the network'); };
  try {
    for (const query of ['最近30天销售额趋势', '各区域订单量排名', '各品类销售额占比', '本月与上月销售额对比', '销售额区域分布']) {
      const plan = await planSqlQuery({ MODEL_API_KEY: 'must-not-be-used' }, query);
      assert.equal(plan.modelUsed, false);
      assert.match(plan.warning, /不消耗模型 API/);
    }
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('time series and share results use descriptive conclusions instead of ranking templates', () => {
  const trendColumns = ['order_date', 'sales_amount', 'order_count'];
  const trendRows = [['2026-08-01', 100, 2], ['2026-08-02', 150, 3], ['2026-08-03', 120, 2]];
  const trendAnswer = buildAnswer(trendColumns, trendRows, '最近30天销售额趋势');
  assert.match(trendAnswer, /销售额合计 370/);
  assert.match(trendAnswer, /最高日期为 2026-08-02/);
  assert.doesNotMatch(trendAnswer, /排名第一/);

  const shareColumns = ['category_name', 'sales_amount', 'quantity', 'sales_share_pct'];
  const shareRows = [['电脑办公', 600, 10, 60], ['手机数码', 400, 8, 40]];
  assert.match(buildAnswer(shareColumns, shareRows, '各品类销售额占比'), /电脑办公占比最高.*60%/);
  assert.match(buildInsights(shareColumns, shareRows, '各品类销售额占比')[2].text, /100%/);
});

test('validator accepts read-only business SQL and enforces limit', () => {
  const sql = validateSql('SELECT region_name FROM regions');
  assert.match(sql, /LIMIT 500$/);
  const cte = validateSql('WITH daily AS (SELECT order_date, SUM(sales_amount) total FROM orders GROUP BY order_date) SELECT * FROM daily');
  assert.match(cte, /FROM daily LIMIT 500$/);
});

test('validator rejects writes and unknown tables', () => {
  assert.throws(() => validateSql('DELETE FROM orders'), /只允许 SELECT|非只读/);
  assert.throws(() => validateSql('SELECT * FROM auth_users'), /不允许访问表/);
  assert.throws(() => validateSql('SELECT * FROM orders; SELECT * FROM products'), /一条/);
});

test('unsupported prompts fail closed instead of using an unrelated trend query', () => {
  assert.throws(() => fallbackSql('退款率最高的渠道是什么'), /缺少|支持范围/);
  assert.throws(() => fallbackSql('查询客户手机号'), /缺少|支持范围/);
  assert.throws(() => fallbackSql('删除全部订单'), /只读/);
  assert.equal(assertSupportedQuery('本月与上月销售额对比'), true);
  assert.match(fallbackSql('本月与上月销售额对比'), /sales_month/i);
});

test('validator rejects dangerous functions, locks and cartesian joins', () => {
  for (const sql of [
    "SELECT load_file('/etc/passwd') FROM orders",
    'SELECT sleep(30) FROM orders',
    'SELECT order_id FROM orders FOR UPDATE',
    'SELECT a.order_id FROM orders a CROSS JOIN orders b',
    'SELECT hex(zeroblob(100000000)) FROM orders',
  ]) {
    assert.throws(() => validateSql(sql));
  }
});

test('CSV export neutralizes spreadsheet formulas without changing numbers', () => {
  const csv = toCsv({ table: { columns: ['name', 'value'], rows: [['=1+1', '+2'], ['@SUM(1,1)', '-3+4'], ['normal', -2]] } });
  assert.match(csv, /"'=1\+1"/);
  assert.match(csv, /"'\+2"/);
  assert.match(csv, /"'@SUM/);
  assert.match(csv, /"'-3\+4"/);
  assert.match(csv, /"-2"/);
});

test('DeepSeek uses the current OpenAI-compatible endpoint in non-thinking mode', () => {
  const request = buildModelRequest({
    MODEL_API_KEY: 'test-key',
    MODEL_BASE_URL: 'https://api.deepseek.com',
    MODEL_NAME: 'deepseek-v4-flash',
  }, '各区域销售额排名');
  assert.equal(request.apiKey, 'test-key');
  assert.equal(request.url, 'https://api.deepseek.com/chat/completions');
  assert.equal(request.body.model, 'deepseek-v4-flash');
  assert.deepEqual(request.body.thinking, { type: 'disabled' });
  assert.match(request.body.messages[0].content, /order_status='completed'/);
  assert.match(request.body.messages[0].content, /CTE 或子查询/);
});

test('legacy secret name remains compatible during provider migration', () => {
  const request = buildModelRequest({
    DASHSCOPE_API_KEY: 'legacy-key',
    MODEL_BASE_URL: 'https://api.deepseek.com/',
    MODEL_NAME: 'deepseek-v4-flash',
  }, '销售趋势');
  assert.equal(request.apiKey, 'legacy-key');
  assert.equal(request.url, 'https://api.deepseek.com/chat/completions');
});

test('chart keeps each category bundled with its own numeric value', () => {
  const [chart] = buildChart(
    ['region', 'sales_amount'],
    [['华南', 165359], ['华北', 170158]],
    '区域销售额柱状图',
    true,
  );
  assert.deepEqual(chart.option.series[0].data, [
    { name: '华南', value: 165359 },
    { name: '华北', value: 170158 },
  ]);
});

test('tester mode only accepts the configured secret token', async () => {
  const env = { ASKDATA_TEST_TOKEN: 'private-test-code' };
  const authorized = new Request('https://example.com/api/health', { headers: { 'X-AskData-Test-Token': 'private-test-code' } });
  const rejected = new Request('https://example.com/api/health', { headers: { 'X-AskData-Test-Token': 'wrong-code' } });
  assert.equal(await testerAuthorized(env, authorized), true);
  assert.equal(await testerAuthorized(env, rejected), false);
  assert.equal(await testerAuthorized({}, authorized), false);
});
