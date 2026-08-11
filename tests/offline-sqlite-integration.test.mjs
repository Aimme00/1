import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import {
  CREATE_STATEMENTS,
  SEED_STATEMENTS,
  buildAnswer,
  buildChart,
  buildInsights,
  fallbackSql,
  validateSql,
} from '../functions/api/[[path]].js';

const bootstrap = `${[...CREATE_STATEMENTS, ...SEED_STATEMENTS].join(';\n')};\n`;

function executeOffline(query) {
  const sql = validateSql(fallbackSql(query));
  const input = `${bootstrap}.mode json\n${sql};\n`;
  const output = execFileSync('sqlite3', [':memory:'], { input, encoding: 'utf8' }).trim();
  return { sql, rows: output ? JSON.parse(output) : [] };
}

test('offline regression set executes every core analysis intent without a model API', () => {
  const cases = [
    ['最近30天销售额趋势如何', 1],
    ['各区域订单量排名', 6],
    ['销售额最高的产品排名', 1],
    ['各品类销售额排名', 4],
    ['本月与上月销售额对比', 1],
    ['各品类销售额占比是多少', 4],
    ['销售额区域分布', 6],
    ['查询华南区域产品销售额排名', 1],
  ];
  for (const [query, minimumRows] of cases) {
    const result = executeOffline(query);
    assert.ok(result.rows.length >= minimumRows, `${query} 应返回至少 ${minimumRows} 行`);
    assert.match(result.sql, /SELECT/i);
  }
});

test('regional top-3 fixture has consistent totals, ranks, ordering, answer and chart labels', () => {
  const query = '请分析最近30天各区域销售额排名前3的产品并生成柱状图';
  const { rows: objects, sql } = executeOffline(query);
  assert.equal(objects.length, 18);
  assert.match(sql, /FROM ranked\s+WHERE region_rank<=3/i);

  const regions = new Map();
  for (const row of objects) {
    if (!regions.has(row.region)) regions.set(row.region, []);
    regions.get(row.region).push(row);
    assert.ok(row.sales_share_pct > 0 && row.sales_share_pct <= 100);
  }
  assert.equal(regions.size, 6);
  for (const rows of regions.values()) {
    assert.deepEqual(rows.map(row => row.region_rank), [1, 2, 3]);
    assert.equal(new Set(rows.map(row => row.region_sales)).size, 1);
    assert.ok(rows[0].product_sales >= rows[1].product_sales);
    assert.ok(rows[1].product_sales >= rows[2].product_sales);
  }
  for (let index = 1; index < objects.length; index += 1) {
    assert.ok(objects[index - 1].region_sales >= objects[index].region_sales);
  }
  assert.equal(objects[0].region, '西南');
  assert.equal(objects[0].product_name, '智能手机 Pro');
  assert.equal(objects[0].product_sales, 83986);
  assert.equal(objects[0].region_sales, 199956);

  const columns = Object.keys(objects[0]);
  const rows = objects.map(object => columns.map(column => object[column]));
  const answer = buildAnswer(columns, rows);
  const insights = buildInsights(columns, rows);
  const [chart] = buildChart(columns, rows, query, true);
  assert.match(answer, /6 个区域、18 个头部产品/);
  assert.match(answer, /华南.*扫地机器人.*47.88%/);
  assert.equal(insights.length, 3);
  assert.equal(chart.option.orientation, 'horizontal');
  assert.equal(chart.option.series[0].data.length, 18);
  assert.ok(chart.option.series[0].data.every(item => item.name.includes(' · ')));
});
