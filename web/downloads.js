(() => {
  const FORMULA_PREFIX = /^[\s]*[=+\-@]/;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]));
  }

  function safeCell(value) {
    const text = String(value ?? '');
    return typeof value === 'string' && FORMULA_PREFIX.test(text) ? `'${text}` : text;
  }

  function safeFileName(value, fallback) {
    const cleaned = String(value || '').trim().replace(/[^\u4e00-\u9fa5\w-]+/g, '_').replace(/^_+|_+$/g, '');
    return (cleaned || fallback).slice(0, 80);
  }

  function resultRows(result) {
    const columns = result?.table?.columns || [];
    const rows = result?.table?.rows || [];
    return {
      columns,
      rows: rows.map(row => Array.isArray(row) ? row : columns.map(column => row?.[column])),
    };
  }

  function buildCsv(result) {
    const { columns, rows } = resultRows(result);
    const quote = value => `"${safeCell(value).replace(/"/g, '""')}"`;
    return '\uFEFF' + [columns, ...rows].map(row => row.map(quote).join(',')).join('\r\n');
  }

  function buildExcelDocument(result, question = '') {
    const { columns, rows } = resultRows(result);
    const insights = result?.insights || [];
    const answer = result?.answer || '分析完成';
    const sql = result?.sql?.text || '';
    const overview = [
      ['分析问题', question || '问数数据分析'],
      ['分析结论', answer],
      ...insights.map(item => [item.title || '数据洞察', item.text || item.description || '']),
    ];
    const overviewRows = overview.map(row => `<tr><th>${escapeHtml(row[0])}</th><td>${escapeHtml(safeCell(row[1]))}</td></tr>`).join('');
    const header = columns.map(column => `<th>${escapeHtml(column)}</th>`).join('');
    const body = rows.map(row => `<tr>${row.map(value => `<td>${escapeHtml(safeCell(value))}</td>`).join('')}</tr>`).join('');
    return `\uFEFF<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,"Microsoft YaHei",sans-serif;color:#172033}h1{font-size:22px}h2{margin-top:28px;font-size:17px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #dfe3ea;padding:8px;text-align:left}th{background:#eef1ff}pre{white-space:pre-wrap;background:#f6f7f9;padding:12px}</style></head><body><h1>问数 · 描述性分析报告</h1><h2>分析概览</h2><table>${overviewRows}</table><h2>数据明细</h2><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table><h2>实际执行 SQL</h2><pre>${escapeHtml(sql)}</pre></body></html>`;
  }

  function triggerDownload(blob, fileName) {
    if (navigator.msSaveOrOpenBlob) {
      navigator.msSaveOrOpenBlob(blob, fileName);
      return;
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.rel = 'noopener';
    link.style.display = 'none';
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function currentResult() {
    return typeof state !== 'undefined' ? state.result : null;
  }

  function notify(message) {
    if (typeof toast === 'function') toast(message);
  }

  function downloadTable(format) {
    const result = currentResult();
    if (!result?.table) return notify('当前结果没有可下载的数据');
    const question = document.getElementById('userMessage')?.textContent?.trim() || '问数分析';
    const base = safeFileName(question, '问数分析');
    if (format === 'csv') {
      triggerDownload(new Blob([buildCsv(result)], { type: 'text/csv;charset=utf-8' }), `${base}.csv`);
      notify('CSV 已开始下载');
      return;
    }
    const content = buildExcelDocument(result, question);
    triggerDownload(new Blob([content], { type: 'application/vnd.ms-excel;charset=utf-8' }), `${base}.xls`);
    notify('Excel 报告已开始下载');
  }

  function canvasWithTitle(source) {
    const ratio = window.devicePixelRatio || 1;
    const titleHeight = Math.round(54 * ratio);
    const output = document.createElement('canvas');
    output.width = source.width;
    output.height = source.height + titleHeight;
    const context = output.getContext('2d');
    context.fillStyle = '#fff';
    context.fillRect(0, 0, output.width, output.height);
    context.fillStyle = '#172033';
    context.font = `600 ${18 * ratio}px sans-serif`;
    context.fillText((document.getElementById('chartTitle')?.textContent || '问数分析图表').slice(0, 80), Math.round(22 * ratio), Math.round(34 * ratio));
    context.drawImage(source, 0, titleHeight);
    return output;
  }

  function canvasBlob(canvas) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('无法生成图片文件')), 'image/png');
    });
  }

  async function downloadChartFile() {
    const chart = currentResult()?.charts?.[0];
    const source = document.getElementById('chartCanvas');
    if (!chart || !source?.width || document.getElementById('chartPanel')?.classList.contains('hidden')) {
      notify('本次没有生成图表。请勾选输入框下方的“生成图表”后重新提问');
      return;
    }
    try {
      const output = canvasWithTitle(source);
      const blob = await canvasBlob(output);
      const title = safeFileName(document.getElementById('chartTitle')?.textContent, '问数分析图表');
      triggerDownload(blob, `${title}.png`);
      notify('图表 PNG 已开始下载');
    } catch {
      notify('图表下载失败，请更换系统浏览器后重试');
    }
  }

  function bindDownloadButton(id, handler) {
    const button = document.getElementById(id);
    if (!button) return;
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      handler();
    }, true);
  }

  function initialize() {
    bindDownloadButton('downloadCsvButton', () => downloadTable('csv'));
    bindDownloadButton('downloadExcelButton', () => downloadTable('excel'));
    bindDownloadButton('downloadChartButton', downloadChartFile);
  }

  globalThis.AskDataDownloads = { buildCsv, buildExcelDocument, safeFileName };
  if (typeof document !== 'undefined') initialize();
})();
