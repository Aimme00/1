import { access, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';

const required = [
  'web/index.html',
  'web/styles.css',
  'web/dashboard.css',
  'web/app.js',
  'web/downloads.js',
  'web/preview-bootstrap.js',
  'web/_routes.json',
  'functions/api/[[path]].js',
  'tests/offline-sqlite-integration.test.mjs',
  'tests/chart-renderer.test.mjs',
  'tests/downloads.test.mjs',
  'wrangler.jsonc',
];

await Promise.all(required.map(file => access(file, constants.R_OK)));
const html = await readFile('web/index.html', 'utf8');
const app = await readFile('web/app.js', 'utf8');
for (const asset of ['./styles.css', './dashboard.css', './preview-bootstrap.js', './app.js', './downloads.js']) {
  if (!html.includes(asset)) throw new Error(`index.html 缺少资源：${asset}`);
}
if (!html.includes('downloadChartButton') || !app.includes('function downloadChart') || !app.includes("toDataURL('image/png')")) {
  throw new Error('前端必须支持下载图表 PNG');
}
const downloads = await readFile('web/downloads.js', 'utf8');
if (!downloads.includes("new Blob([content]") || !downloads.includes("canvas.toBlob") || !downloads.includes('triggerDownload')) {
  throw new Error('前端必须使用浏览器文件 Blob 下载 Excel 和 PNG');
}
if (!html.includes('app.js?v=') || !html.includes('styles.css?v=')) {
  throw new Error('前端静态资源必须带版本标识，避免浏览器继续使用旧图表脚本');
}
if (!app.includes('function niceAxisMax') || !app.includes('formatChartNumber(v)')) {
  throw new Error('图表必须使用独立纵轴上限并在柱顶显示真实值');
}
if (!app.includes("option.orientation==='horizontal'") || !workerChartSource(await readFile('functions/api/[[path]].js', 'utf8'))) {
  throw new Error('区域产品图表必须使用包含地区与产品名称的横向柱状图');
}
if (!html.includes('testerModeButton') || !app.includes('askdata_test_token') || !app.includes('X-AskData-Test-Token')) {
  throw new Error('前端必须提供受测试码保护的不限次数测试模式');
}
const worker = await readFile('functions/api/[[path]].js', 'utf8');
if (!worker.includes('ASKDATA_TEST_TOKEN') || !worker.includes('testerAuthorized')) {
  throw new Error('后端必须验证测试码，不能直接取消公开访客限额');
}
const routes = JSON.parse(await readFile('web/_routes.json', 'utf8'));
if (!routes.include?.includes('/api/*')) throw new Error('_routes.json 必须只让 /api/* 调用 Function');
const wrangler = JSON.parse(await readFile('wrangler.jsonc', 'utf8'));
const database = wrangler.d1_databases?.find(item => item.binding === 'DB');
if (!database?.database_name) throw new Error('wrangler.jsonc 必须声明 DB 的 database_name');
console.log('AskData Cloudflare Pages 构建检查通过。');

function workerChartSource(source) {
  return source.includes("orientation: 'horizontal'") && source.includes('各区域 Top 3 产品销售额');
}
