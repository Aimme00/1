import fs from 'node:fs/promises';
import path from 'node:path';
import { FileBlob, SpreadsheetFile } from '/Users/aimme/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const outDir = path.join(root, 'outputs', '019fe006-70b1-7963-b702-5d990ca821aa');
const xlsxPath = path.join(outDir, '问数Agent_30条标准答案真实API测试记录.xlsx');
const renderDir = path.join(outDir, 'renders');
await fs.mkdir(renderDir, { recursive: true });

const input = await FileBlob.load(xlsxPath);
const wb = await SpreadsheetFile.importXlsx(input);
const sheets = [
  ['测试总览', 'A1:F16'],
  ['测试结果', 'A1:J33'],
  ['标准答案', 'A1:G33'],
  ['测试集', 'A1:E33'],
];

const checks = [];
for (const [sheetName, range] of sheets) {
  const inspected = await wb.inspect({ kind: 'table', range: `${sheetName}!${range}`, include: 'values,formulas', table_max_rows: 40, table_max_cols: 12 });
  checks.push({ sheetName, range, inspected });
  const preview = await wb.render({ sheetName, range, autoCrop: 'all', scale: 1, format: 'png' });
  await fs.writeFile(path.join(renderDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const errors = await wb.inspect({
  kind: 'match',
  searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',
  options: { use_regex: true, max_results: 100 },
  summary: 'final formula error scan',
});
await fs.writeFile(path.join(outDir, 'verification.json'), JSON.stringify({ checks, errors }, null, 2));
console.log(JSON.stringify({ sheets: sheets.map(s => s[0]), renderDir, errorScan: errors }, null, 2));
