import fs from 'node:fs';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '/Users/aimme/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const input = JSON.parse(fs.readFileSync(path.join(root, 'runtime_data', 'oracle-30-results.json'), 'utf8'));
const outDir = path.join(root, 'outputs', '019fe006-70b1-7963-b702-5d990ca821aa');
fs.mkdirSync(outDir, { recursive: true });
const output = path.join(outDir, '问数Agent_30条标准答案真实API测试记录.xlsx');

const wb = Workbook.create();
const navy = '#17233D';
const blue = '#4F6BED';
const paleBlue = '#EAF0FF';
const paleRed = '#FDECEC';
const paleGreen = '#E8F7F1';
const grey = '#667085';

function styleTitle(sheet, range, title) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(':')[0]).values = [[title]];
  sheet.getRange(range).format = {
    fill: navy,
    font: { bold: true, color: '#FFFFFF', size: 18 },
    verticalAlignment: 'center',
    horizontalAlignment: 'left',
  };
  sheet.getRange(range).format.rowHeight = 32;
}

const summary = wb.worksheets.add('测试总览');
styleTitle(summary, 'A1:F1', '问数 Agent｜30 条带标准答案组合测试');
summary.getRange('A3:B10').values = [
  ['指标', '结果'],
  ['测试用例数', input.test_count],
  ['已完成真实 API 调用', input.real_api_calls],
  ['标准答案已计算', input.test_count],
  ['真实 API 通过', 0],
  ['真实 API 失败', 0],
  ['真实 API 阻塞', input.test_count],
  ['生成时间', input.generated_at],
];
summary.getRange('A3:B3').format = { fill: blue, font: { bold: true, color: '#FFFFFF' } };
summary.getRange('A4:A10').format.font = { bold: true, color: navy };
summary.getRange('A12:F12').merge();
summary.getRange('A12').values = [['结论：30 条标准 SQL 与标准答案已独立计算；真实 API 未执行，不计为通过。']];
summary.getRange('A12:F12').format = { fill: paleRed, font: { bold: true, color: '#B42318' }, wrapText: true };
summary.getRange('A14:F14').merge();
summary.getRange('A14').values = [[`阻塞原因：${input.blocked_reason}`]];
summary.getRange('A14:F14').format = { fill: '#FFF7E6', font: { color: '#8A4B08' }, wrapText: true };
summary.getRange('A16:F16').merge();
summary.getRange('A16').values = [[`目标部署：${input.deployment_url}`]];
summary.getRange('A16:F16').format = { fill: paleBlue, font: { color: navy } };
summary.getRange('A3:B10').format.borders = { color: '#D0D5DD', style: 'continuous' };
summary.getRange('A:B').format.columnWidth = 30;
summary.getRange('C:F').format.columnWidth = 18;
summary.freezePanes.freezeRows(1);

const results = wb.worksheets.add('测试结果');
styleTitle(results, 'A1:J1', '真实 API 测试结果（未测不得写成通过）');
const headers = ['ID', '类别', '测试问题', 'API状态', '语义通过', '标准行数', '实际行数', '标准字段', '实际字段', '说明'];
const resultRows = input.cases.map(c => [
  c.id, c.category, c.question, c.api_status, c.semantic_pass === null ? '未执行' : (c.semantic_pass ? '通过' : '失败'),
  c.expected_row_count, c.actual_rows.length, c.expected_columns.join(', '), c.actual_columns.join(', '), c.api_detail,
]);
results.getRange(`A3:J${3 + resultRows.length}`).values = [headers, ...resultRows];
results.getRange('A3:J3').format = { fill: blue, font: { bold: true, color: '#FFFFFF' }, wrapText: true };
results.getRange(`A4:J${3 + resultRows.length}`).format = { verticalAlignment: 'top', wrapText: true };
results.getRange(`D4:D${3 + resultRows.length}`).conditionalFormats.addCustom('=D4="PASS"', { fill: paleGreen, font: { color: '#067647', bold: true } });
results.getRange(`D4:D${3 + resultRows.length}`).conditionalFormats.addCustom('=D4="BLOCKED"', { fill: '#FFF7E6', font: { color: '#8A4B08', bold: true } });
results.getRange(`D4:D${3 + resultRows.length}`).conditionalFormats.addCustom('=D4="FAIL"', { fill: paleRed, font: { color: '#B42318', bold: true } });
results.getRange('A:A').format.columnWidth = 7;
results.getRange('B:B').format.columnWidth = 15;
results.getRange('C:C').format.columnWidth = 55;
results.getRange('D:G').format.columnWidth = 13;
results.getRange('H:I').format.columnWidth = 28;
results.getRange('J:J').format.columnWidth = 62;
results.getRange(`A3:J${3 + resultRows.length}`).format.borders = { color: '#E4E7EC', style: 'continuous' };
results.freezePanes.freezeRows(3);

const oracle = wb.worksheets.add('标准答案');
styleTitle(oracle, 'A1:G1', '独立标准 SQL 与标准答案');
const oracleHeaders = ['ID', '类别', '标准字段', '标准行数', '标准答案（JSON）', '标准SQL', '核验说明'];
const oracleRows = input.cases.map(c => [
  c.id, c.category, c.expected_columns.join(', '), c.expected_row_count,
  JSON.stringify(c.expected_rows), c.oracle_sql,
  '标准答案直接在项目 SQLite 测试库执行标准 SQL 得出，与 Agent 生成 SQL 相互独立。',
]);
oracle.getRange(`A3:G${3 + oracleRows.length}`).values = [oracleHeaders, ...oracleRows];
oracle.getRange('A3:G3').format = { fill: blue, font: { bold: true, color: '#FFFFFF' }, wrapText: true };
oracle.getRange(`A4:G${3 + oracleRows.length}`).format = { verticalAlignment: 'top', wrapText: true };
oracle.getRange('A:A').format.columnWidth = 7;
oracle.getRange('B:B').format.columnWidth = 15;
oracle.getRange('C:C').format.columnWidth = 30;
oracle.getRange('D:D').format.columnWidth = 12;
oracle.getRange('E:E').format.columnWidth = 70;
oracle.getRange('F:F').format.columnWidth = 90;
oracle.getRange('G:G').format.columnWidth = 45;
oracle.getRange(`A3:G${3 + oracleRows.length}`).format.borders = { color: '#E4E7EC', style: 'continuous' };
oracle.freezePanes.freezeRows(3);

const cases = wb.worksheets.add('测试集');
styleTitle(cases, 'A1:E1', '30 条组合测试题');
const caseRows = input.cases.map(c => [c.id, c.category, c.question, c.expected_columns.join(', '), c.expected_row_count]);
cases.getRange(`A3:E${3 + caseRows.length}`).values = [['ID', '类别', '问题', '预期字段', '预期行数'], ...caseRows];
cases.getRange('A3:E3').format = { fill: blue, font: { bold: true, color: '#FFFFFF' }, wrapText: true };
cases.getRange(`A4:E${3 + caseRows.length}`).format = { wrapText: true, verticalAlignment: 'top' };
cases.getRange('A:A').format.columnWidth = 7;
cases.getRange('B:B').format.columnWidth = 16;
cases.getRange('C:C').format.columnWidth = 75;
cases.getRange('D:D').format.columnWidth = 35;
cases.getRange('E:E').format.columnWidth = 12;
cases.getRange(`A3:E${3 + caseRows.length}`).format.borders = { color: '#E4E7EC', style: 'continuous' };
cases.freezePanes.freezeRows(3);

const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(output);
console.log(output);
