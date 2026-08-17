import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../web/downloads.js', import.meta.url), 'utf8');

function loadDownloads() {
  const sandbox = {};
  vm.runInNewContext(source, sandbox);
  return sandbox.AskDataDownloads;
}

test('downloads accept API table rows represented as objects', () => {
  const downloads = loadDownloads();
  const result = {
    table: {
      columns: ['region', 'sales_amount'],
      rows: [{ region: '华东', sales_amount: 1200 }, { region: '华南', sales_amount: 900 }],
    },
    answer: '华东销售额最高。',
    sql: { text: 'SELECT region, SUM(sales_amount) FROM orders GROUP BY region' },
  };

  const csv = downloads.buildCsv(result);
  const excel = downloads.buildExcelDocument(result, '各区域销售额');

  assert.match(csv, /"华东","1200"/);
  assert.match(excel, /<td>华南<\/td><td>900<\/td>/);
  assert.match(excel, /实际执行 SQL/);
});

test('CSV download neutralizes spreadsheet formulas', () => {
  const downloads = loadDownloads();
  const csv = downloads.buildCsv({ table: { columns: ['value'], rows: [{ value: '=1+1' }] } });
  assert.match(csv, /"'=1\+1"/);
});
