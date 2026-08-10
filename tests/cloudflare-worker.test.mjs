import test from 'node:test';
import assert from 'node:assert/strict';
import { assertSupportedQuery, buildChart, buildModelRequest, fallbackSql, testerAuthorized, toCsv, validateSql } from '../functions/api/[[path]].js';

test('fallback SQL covers the main demo intents', () => {
  assert.match(fallbackSql('各区域订单排名'), /regions/i);
  assert.match(fallbackSql('销售额最高的产品'), /products/i);
  assert.match(fallbackSql('最近30天趋势'), /order_date/i);
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
