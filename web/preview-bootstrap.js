(() => {
  if (window.location.protocol !== 'file:') return;

  const previewUser = {
    id: 'guest_local_preview',
    email: 'preview@askdata.local',
    display_name: 'Demo Guest',
    is_active: true,
    is_admin: false,
    is_guest: true,
    created_at: Math.floor(Date.now() / 1000),
  };
  const runs = new Map();
  const analyses = new Map();
  const dashboards = new Map();
  let sequence = 0;

  function json(payload, status = 200) {
    return Promise.resolve(new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    }));
  }

  function noContent() {
    return Promise.resolve(new Response(null, { status: 204 }));
  }

  function parseRequest(input, options = {}) {
    const raw = typeof input === 'string' ? input : input.url;
    const url = new URL(raw, 'https://preview.askdata.local');
    let body = {};
    try { body = options.body ? JSON.parse(options.body) : {}; } catch {}
    return { path: url.pathname, method: (options.method || 'GET').toUpperCase(), body };
  }

  function chartFor(query, columns, rows, chartRequested = false) {
    const wantsChart = chartRequested || /生成图表|生成图片|折线图|柱状图|可视化/.test(query);
    if (!wantsChart) return [];
    return [{
      title: '最近 30 天销售额趋势',
      option: {
        xAxis: { type: 'category', data: rows.map(row => row[0]) },
        yAxis: { type: 'value' },
        series: [{ type: 'line', data: rows.map(row => row[1]) }],
      },
    }];
  }

  function buildResult(runId, query, lineage = null, chartRequested = false) {
    let columns;
    let rows;
    let answer;
    let insights;
    let sql;
    let drillActions = [];

    if (/区域/.test(query)) {
      columns = ['region', 'order_count'];
      rows = [['华东', 82], ['华南', 71], ['华北', 64], ['西南', 53], ['东北', 47], ['西北', 43]];
      answer = '华东区域订单量最高，共 82 笔；华南和华北分列第二、第三。';
      insights = [
        { title: '领先区域', text: '华东订单量排名第一。' },
        { title: '区域差距', text: '华东比西北多 39 笔订单。' },
      ];
      sql = "SELECT customers.region, COUNT(orders.order_id) AS order_count FROM orders JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_status = 'completed' GROUP BY customers.region ORDER BY order_count DESC LIMIT 5000;";
      drillActions = [{ id: 'region_to_product', direction: 'down', label: '下钻华东的产品销售额', query: '查询华东区域各产品销售额排名' }];
    } else if (/产品|商品/.test(query)) {
      columns = ['product_name', 'sales_amount'];
      rows = [['轻薄笔记本', 168420], ['智能手机 Pro', 152680], ['4K 显示器', 119900], ['平板电脑', 98200], ['智能手表', 86740]];
      answer = '轻薄笔记本销售额最高，为 168,420；智能手机 Pro 排名第二。';
      insights = [
        { title: 'Top 1 产品', text: '轻薄笔记本贡献最高销售额。' },
        { title: '头部集中', text: '前两名产品销售表现明显领先。' },
      ];
      sql = "SELECT products.product_name, ROUND(SUM(order_items.line_amount), 2) AS sales_amount FROM order_items JOIN products ON order_items.product_id = products.product_id JOIN orders ON order_items.order_id = orders.order_id WHERE orders.order_status = 'completed' GROUP BY products.product_id, products.product_name ORDER BY sales_amount DESC LIMIT 5;";
      drillActions = [{ id: 'product_to_region', direction: 'down', label: '下钻轻薄笔记本的区域分布', query: '查询产品轻薄笔记本在各区域的销售额排名' }];
    } else {
      columns = ['order_date', 'sales_amount'];
      rows = Array.from({ length: 12 }, (_, index) => {
        const day = String(index + 1).padStart(2, '0');
        return [`08-${day}`, 9200 + index * 630 + (index === 7 ? 9500 : 0)];
      });
      answer = '最近销售额整体呈上升趋势，期间出现一次明显峰值，建议继续查看当天产品构成。';
      insights = [
        { title: '总体趋势', text: '销售额较期初增长约 75%。' },
        { title: '异常峰值', text: '08-08 的销售额显著高于日常水平。' },
        { title: '数据范围', text: '当前预览展示 12 个日期样本。' },
      ];
      sql = "SELECT order_date, ROUND(SUM(sales_amount), 2) AS sales_amount FROM orders WHERE order_status = 'completed' AND order_date >= date('now', '-29 day') GROUP BY order_date ORDER BY order_date ASC LIMIT 5000;";
      drillActions = [{ id: 'day_to_month', direction: 'up', label: '上卷到月度销售额', query: '按月汇总最近180天销售额趋势' }];
    }

    return {
      schema_version: '1.0',
      run_id: runId,
      status: 'completed',
      answer,
      insights,
      table: { columns, rows, returned_rows: rows.length, truncated: false },
      charts: chartFor(query, columns, rows, chartRequested),
      sql: { text: sql, dialect: 'sqlite', duration_ms: 8, validation: { is_valid: true, parser: 'preview' } },
      scope: {
        database: 'trade_db',
        row_count: rows.length,
        truncated: false,
        chart_requested: chartRequested || /生成图表|生成图片|折线图|柱状图|可视化/.test(query),
        drill_actions: drillActions,
        ...(lineage ? { drilldown: lineage } : {}),
      },
      warnings: ['当前为双击打开的离线预览，数据和 Agent 步骤均为 Mock。'],
      suggested_questions: ['可以按时间维度查看趋势吗？', '按主要维度展示排名前10。'],
      error: null,
    };
  }

  window.fetch = async (input, options = {}) => {
    const { path, method, body } = parseRequest(input, options);

    if (path === '/api/auth/me') return json({ user: previewUser });
    if (path === '/api/auth/login') return json({ user: previewUser });
    if (path === '/api/auth/logout') return noContent();
    if (path === '/api/data-source/status') return json({ database: 'trade_db', alias: 'trade_db', database_type: 'sqlite', ready: true, table_count: 6, column_count: 32 });
    if (path === '/api/conversations') return json({ items: [] });
    if (path.startsWith('/api/conversations/')) return json({ items: [] });

    if (path === '/api/chat' && method === 'POST') {
      const runId = `run_preview_${++sequence}`;
      runs.set(runId, { query: body.query || '最近30天销售额趋势如何？', generate_chart: body.generate_chart === true, result: null });
      return json({ run_id: runId, status: 'pending', events_url: `/api/runs/${runId}/events`, result_url: `/api/runs/${runId}` }, 202);
    }

    if (path === '/api/drilldown' && method === 'POST') {
      const runId = `run_preview_${++sequence}`;
      const lineage = { parent_run_id: body.parent_run_id, direction: body.direction };
      runs.set(runId, { query: body.query || '查询产品排名', lineage, generate_chart: body.generate_chart === true, result: null });
      return json({ run_id: runId, status: 'pending', events_url: `/api/runs/${runId}/events`, result_url: `/api/runs/${runId}` }, 202);
    }

    const runMatch = path.match(/^\/api\/runs\/([^/]+)$/);
    if (runMatch && method === 'GET') {
      const runId = runMatch[1];
      const run = runs.get(runId) || { query: '最近30天销售额趋势如何？' };
      run.result ||= buildResult(runId, run.query, run.lineage, run.generate_chart);
      return json({ run_id: runId, status: 'completed', result: run.result, error: null });
    }
    if (/^\/api\/runs\/[^/]+\/cancel$/.test(path)) return json({ status: 'cancelled' });

    if (path === '/api/analyses' && method === 'GET') return json({ items: [...analyses.values()] });
    if (path === '/api/analyses' && method === 'POST') {
      const run = runs.get(body.run_id);
      const id = `analysis_preview_${analyses.size + 1}`;
      const item = { id, run_id: body.run_id, session_id: 'preview_session', title: body.title || '预览分析', query: run?.query || '预览问题', result: buildResult(body.run_id, run?.query || '', run?.lineage, run?.generate_chart) };
      analyses.set(id, item);
      return json(item, 201);
    }
    const analysisMatch = path.match(/^\/api\/analyses\/([^/]+)$/);
    if (analysisMatch && method === 'GET') return json(analyses.get(analysisMatch[1]) || {}, analyses.has(analysisMatch[1]) ? 200 : 404);
    if (analysisMatch && method === 'DELETE') { analyses.delete(analysisMatch[1]); return noContent(); }

    if (path === '/api/dashboards' && method === 'GET') return json({ items: [...dashboards.values()].map(item => ({ ...item, card_count: item.cards.length })) });
    if (path === '/api/dashboards' && method === 'POST') {
      const item = { id: `dashboard_preview_${dashboards.size + 1}`, name: body.name || '我的仪表盘', description: body.description || '', cards: [] };
      dashboards.set(item.id, item);
      return json(item, 201);
    }
    const cardAddMatch = path.match(/^\/api\/dashboards\/([^/]+)\/cards$/);
    if (cardAddMatch && method === 'POST') {
      const dashboard = dashboards.get(cardAddMatch[1]);
      const analysis = analyses.get(body.analysis_id);
      const card = { id: `card_preview_${dashboard.cards.length + 1}`, analysis_id: body.analysis_id, title: body.title || analysis?.title || '分析卡片', query: analysis?.query || '', result: analysis?.result || buildResult('run_preview_card', '') };
      dashboard.cards.push(card);
      return json(card, 201);
    }
    const cardDeleteMatch = path.match(/^\/api\/dashboards\/([^/]+)\/cards\/([^/]+)$/);
    if (cardDeleteMatch && method === 'DELETE') {
      const dashboard = dashboards.get(cardDeleteMatch[1]);
      dashboard.cards = dashboard.cards.filter(card => card.id !== cardDeleteMatch[2]);
      return noContent();
    }
    const dashboardMatch = path.match(/^\/api\/dashboards\/([^/]+)$/);
    if (dashboardMatch && method === 'GET') return json(dashboards.get(dashboardMatch[1]) || {}, dashboards.has(dashboardMatch[1]) ? 200 : 404);
    if (dashboardMatch && method === 'DELETE') { dashboards.delete(dashboardMatch[1]); return noContent(); }

    return json({ detail: '该操作在离线预览中不可用。' }, 503);
  };

  class PreviewEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = new Map();
      this.closed = false;
      setTimeout(() => this.play(), 80);
    }

    addEventListener(name, callback) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(callback);
    }

    emit(name, node, status, message) {
      if (this.closed) return;
      const event = { data: JSON.stringify({ event: name, node, status, message, sequence: ++sequence, data: {} }) };
      (this.listeners.get(name) || []).forEach(callback => callback(event));
    }

    play() {
      const steps = [
        ['progress', 'intent', 'completed', '已识别数据查询意图'],
        ['progress', 'schema', 'completed', '已检索 6 张业务表'],
        ['progress', 'plan', 'completed', '分析计划已生成'],
        ['progress', 'sql_generate', 'completed', 'SQL 已生成'],
        ['progress', 'sql_validate', 'completed', 'SQL 安全校验通过'],
        ['progress', 'sql_execute', 'completed', '只读查询执行完成'],
        ['progress', 'analysis', 'completed', '数据分析完成'],
      ];
      steps.forEach((step, index) => setTimeout(() => this.emit(...step), index * 90));
      setTimeout(() => this.emit('completed', 'run', 'completed', '任务完成'), steps.length * 90 + 40);
    }

    close() { this.closed = true; }
  }

  window.EventSource = PreviewEventSource;
  document.body.classList.add('offline-preview');
  const badge = document.createElement('div');
  badge.className = 'preview-mode-badge';
  badge.textContent = '离线界面预览 · 双击可体验';
  document.body.append(badge);
})();
