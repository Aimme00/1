import { access, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';

const html = await readFile('web/index.html', 'utf8');
const app = await readFile('web/app.js', 'utf8');
const isStaticDemo =
  html.includes('问数｜静态体验 Demo') ||
  (html.includes('id="downloadCsv"') &&
    html.includes('id="downloadExcel"') &&
    html.includes('id="downloadPng"'));

if (isStaticDemo) {
  await verifyStaticDemo(html, app);
  console.log('问数静态 Demo Cloudflare Pages 构建检查通过。');
} else {
  await verifyFullAgent(html, app);
  console.log('AskData Cloudflare Pages 构建检查通过。');
}

async function verifyStaticDemo(page, source) {
  const required = ['web/index.html', 'web/styles.css', 'web/app.js'];
  await Promise.all(required.map(file => access(file, constants.R_OK)));

  if (!/href=["'](?:\.\/)?styles\.css(?:\?[^"']*)?["']/.test(page)) {
    throw new Error('静态 Demo 的 index.html 缺少 styles.css');
  }
  if (!/src=["'](?:\.\/)?app\.js(?:\?[^"']*)?["']/.test(page)) {
    throw new Error('静态 Demo 的 index.html 缺少 app.js');
  }
  for (const id of ['downloadCsv', 'downloadExcel', 'downloadPng']) {
    if (!page.includes(`id="${id}"`)) throw new Error(`静态 Demo 缺少下载按钮：${id}`);
  }
  if (!source.includes('new Blob') || !source.includes('canvas.toBlob')) {
    throw new Error('静态 Demo 必须支持浏览器下载表格和图表 PNG');
  }
  for (const scenario of ['trend:', 'products:', 'compare:']) {
    if (!source.includes(scenario)) throw new Error(`静态 Demo 缺少演示场景：${scenario}`);
  }
}

async function verifyFullAgent(page, source) {
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
  for (const asset of ['./styles.css', './dashboard.css', './preview-bootstrap.js', './app.js', './downloads.js']) {
    if (!page.includes(asset)) throw new Error(`index.html 缺少资源：${asset}`);
  }
  if (!page.includes('downloadChartButton') || !source.includes('function downloadChart') || !source.includes("toDataURL('image/png')")) {
    throw new Error('前端必须支持下载图表 PNG');
  }
  const downloads = await readFile('web/downloads.js', 'utf8');
  if (!downloads.includes('new Blob([content]') || !downloads.includes('canvas.toBlob') || !downloads.includes('triggerDownload')) {
    throw new Error('前端必须使用浏览器文件 Blob 下载 Excel 和 PNG');
  }
  if (!page.includes('app.js?v=') || !page.includes('styles.css?v=')) {
    throw new Error('前端静态资源必须带版本标识，避免浏览器继续使用旧图表脚本');
  }
  if (!source.includes('function niceAxisMax') || !source.includes('formatChartNumber(v)')) {
    throw new Error('图表必须使用独立纵轴上限并在柱顶显示真实值');
  }
  const worker = await readFile('functions/api/[[path]].js', 'utf8');
  if (!source.includes("option.orientation==='horizontal'") || !workerChartSource(worker)) {
    throw new Error('区域产品图表必须使用包含地区与产品名称的横向柱状图');
  }
  if (!page.includes('testerModeButton') || !source.includes('askdata_test_token') || !source.includes('X-AskData-Test-Token')) {
    throw new Error('前端必须提供受测试码保护的不限次数测试模式');
  }
  if (!worker.includes('ASKDATA_TEST_TOKEN') || !worker.includes('testerAuthorized')) {
    throw new Error('后端必须验证测试码，不能直接取消公开访客限额');
  }
  const routes = JSON.parse(await readFile('web/_routes.json', 'utf8'));
  if (!routes.include?.includes('/api/*')) throw new Error('_routes.json 必须只让 /api/* 调用 Function');
  const wrangler = JSON.parse(await readFile('wrangler.jsonc', 'utf8'));
  const database = wrangler.d1_databases?.find(item => item.binding === 'DB');
  if (!database?.database_name) throw new Error('wrangler.jsonc 必须声明 DB 的 database_name');
}

function workerChartSource(source) {
  return source.includes("orientation: 'horizontal'") && source.includes('各区域 Top 3 产品销售额');
}
