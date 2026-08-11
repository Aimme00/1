import test from 'node:test';
import assert from 'node:assert/strict';

await import('../web/downloads.js');

const { buildCsv, buildExcelDocument, safeFileName } = globalThis.AskDataDownloads;

const result = {
  answer: '华东销售额最高。',
  insights: [{ title: '比较范围', text: '共比较 2 个区域。' }],
  table: {
    columns: ['region', 'sales_amount', 'note'],
    rows: [['华东', 1200, '=1+1'], ['华南', 900, '正常']],
  },
  sql: { text: 'SELECT region, SUM(sales_amount) FROM orders GROUP BY region' },
};

test('browser Excel report contains overview, full table and SQL', () => {
  const excel = buildExcelDocument(result, '各区域销售额排名');
  assert.match(excel, /问数 · 描述性分析报告/);
  assert.match(excel, /华东销售额最高/);
  assert.match(excel, /sales_amount/);
  assert.match(excel, /SELECT region/);
  assert.match(excel, /&#39;=1\+1/);
});

test('browser CSV download keeps BOM and neutralizes formulas', () => {
  const csv = buildCsv(result);
  assert.equal(csv.charCodeAt(0), 0xFEFF);
  assert.match(csv, /"'=1\+1"/);
});

test('download file names are safe across browsers', () => {
  assert.equal(safeFileName('各区域销售额：排名 / 2026', '问数分析'), '各区域销售额_排名_2026');
});
