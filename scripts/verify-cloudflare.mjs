import { access, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';

const required = [
  'web/index.html',
  'web/styles.css',
  'web/dashboard.css',
  'web/app.js',
  'web/preview-bootstrap.js',
  'web/_routes.json',
  'functions/api/[[path]].js',
  'wrangler.jsonc',
];

await Promise.all(required.map(file => access(file, constants.R_OK)));
const html = await readFile('web/index.html', 'utf8');
for (const asset of ['./styles.css', './dashboard.css', './preview-bootstrap.js', './app.js']) {
  if (!html.includes(asset)) throw new Error(`index.html 缺少资源：${asset}`);
}
const routes = JSON.parse(await readFile('web/_routes.json', 'utf8'));
if (!routes.include?.includes('/api/*')) throw new Error('_routes.json 必须只让 /api/* 调用 Function');
const wrangler = JSON.parse(await readFile('wrangler.jsonc', 'utf8'));
const database = wrangler.d1_databases?.find(item => item.binding === 'DB');
if (!database?.database_name) throw new Error('wrangler.jsonc 必须声明 DB 的 database_name');
console.log('AskData Cloudflare Pages 构建检查通过。');
