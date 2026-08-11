import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const appSource = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const chartSource = appSource.slice(
  appSource.indexOf('function formatChartNumber'),
  appSource.indexOf('function showError'),
);

function loadRenderer() {
  const sandbox = { window: { devicePixelRatio: 1 }, Intl, Math, Number };
  vm.runInNewContext(`${chartSource};globalThis.renderOfflineChart=drawCanvasChart;`, sandbox);
  return sandbox.renderOfflineChart;
}

test('horizontal regional-product chart renders every label, value and bar offline', () => {
  const labels = [
    '西南 · 智能手机 Pro', '西南 · 平板电脑', '西南 · 扫地机器人',
    '华南 · 扫地机器人', '华南 · 平板电脑', '华南 · 轻薄笔记本',
    '华北 · 轻薄笔记本', '华北 · 4K 显示器', '华北 · 智能手机 Pro',
    '西北 · 平板电脑', '西北 · 智能手表', '西北 · 无线耳机',
    '华东 · 4K 显示器', '华东 · 平板电脑', '华东 · 智能手机 Pro',
    '东北 · 轻薄笔记本', '东北 · 智能手表', '东北 · 无线耳机',
  ];
  const values = [83986, 51588, 35990, 79178, 34392, 27996, 62991, 32990, 19794, 35994, 23996, 13998, 32990, 27996, 11998, 30093, 21594, 8990];
  const fillRects = [];
  const texts = [];
  const context = {
    scale() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, arc() {}, fill() {},
    fillRect(...args) { fillRects.push(args); },
    fillText(text) { texts.push(String(text)); },
  };
  const canvas = {
    style: {}, width: 0, height: 0,
    getBoundingClientRect: () => ({ width: 1100 }),
    getContext: () => context,
  };
  const option = {
    orientation: 'horizontal',
    yAxis: { data: labels },
    series: [{ type: 'bar', data: labels.map((name, index) => ({ name, value: values[index] })) }],
  };

  loadRenderer()(option, canvas);

  assert.equal(fillRects.length, 18);
  assert.equal(canvas.height, 562);
  assert.equal(canvas.style.height, '562px');
  for (const label of labels) assert.ok(texts.includes(label), `图表缺少标签：${label}`);
  for (const value of values) assert.ok(texts.includes(value.toLocaleString('zh-CN')), `图表缺少数值：${value}`);
});
