const SNAPSHOT_END = '2026-08-13';
const trendValues = [10293,5999,18093,3897,10293,21997,18093,3897,10293,21997,5196,3897,55960,21997,18093,3897,10293,21997,18093,3897,2697,21997,18093,3897,10293,21997,18093,3897,10293,15998];
const dates = Array.from({length:30},(_,i)=>{const d=new Date('2026-07-15T00:00:00');d.setDate(d.getDate()+i);return d.toISOString().slice(0,10)});
const fmt = n => Number(n).toLocaleString('zh-CN',{maximumFractionDigits:2});
const scenarios = {
  trend: {
    question:'最近30天销售额趋势如何？', rows:dates.map((date,i)=>({日期:date,销售额:trendValues[i]})), chart:'line', chartTitle:'最近30天销售额趋势',
    answer:`截至 ${SNAPSHOT_END} 的最近30天，已完成订单销售额合计 <b>${fmt(trendValues.reduce((a,b)=>a+b,0))}</b>，日均 <b>${fmt(trendValues.reduce((a,b)=>a+b,0)/30)}</b>。最高点出现在 2026-07-27，为 <b>55,960</b>；期末销售额为 <b>15,998</b>。图表横轴完整覆盖30个自然日。`,
    insights:[['覆盖范围','2026-07-15 至 2026-08-13，共30天'],['峰值','2026-07-27：55,960'],['整体概览',`累计 ${fmt(trendValues.reduce((a,b)=>a+b,0))}，日均 ${fmt(trendValues.reduce((a,b)=>a+b,0)/30)}`]],
    sql:`SELECT\n  order_date::date AS sales_date,\n  ROUND(SUM(sales_amount)::numeric, 2) AS sales_amount\nFROM orders\nWHERE order_status = 'completed'\n  AND order_date::date BETWEEN DATE '2026-07-15' AND DATE '2026-08-13'\nGROUP BY order_date::date\nORDER BY sales_date;`,
    schema:'命中 orders\n字段：order_date、order_status、sales_amount', plan:'筛选固定快照最近30天的 completed 订单；按日聚合销售额；按日期升序输出。'
  },
  products: {
    question:'已完成订单中，销售额最高的前5个产品是哪些？', chart:'bar', chartTitle:'已完成订单产品销售额 Top 5',
    rows:[['扫地机器人',86376,1],['智能手机 Pro',83986,2],['轻薄笔记本',62991,3],['平板电脑',51588,4],['4K 显示器',35994,5]].map(x=>({产品名称:x[0],销售额:x[1],排名:x[2]})),
    answer:'已完成订单销售额最高的5个产品依次是：<ol><li><b>扫地机器人</b>：86,376</li><li><b>智能手机 Pro</b>：83,986</li><li><b>轻薄笔记本</b>：62,991</li><li><b>平板电脑</b>：51,588</li><li><b>4K 显示器</b>：35,994</li></ol>',
    insights:[['排名第一','扫地机器人：86,376'],['Top 5 合计','320,935'],['头部差距','第1名比第2名高 2,390']],
    sql:`SELECT\n  p.product_name,\n  ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS sales_amount\nFROM orders o\nJOIN order_items oi ON oi.order_id = o.order_id\nJOIN products p ON p.product_id = oi.product_id\nWHERE o.order_status = 'completed'\nGROUP BY p.product_name\nORDER BY sales_amount DESC\nLIMIT 5;`,
    schema:'命中 orders、order_items、products\n关联键：order_id、product_id', plan:'筛选 completed 订单；关联订单明细和产品；按产品汇总销售额；降序取前5名。'
  },
  compare: {
    question:'本月与上月已完成订单销售额相比变化多少？', chart:'compare', chartTitle:'本月与上月已完成订单销售额对比',
    rows:[{月份:'2026-07',销售额:171242},{月份:'2026-08',销售额:426516}],
    answer:'本月已完成订单销售额为 <b>426,516</b>，上月为 <b>171,242</b>；本月较上月<b>增加 255,274</b>，环比<b>增长 149.07%</b>。',
    insights:[['本月销售额','426,516'],['上月销售额','171,242'],['环比变化','+255,274（+149.07%）']],
    sql:`WITH monthly AS (\n  SELECT\n    DATE_TRUNC('month', order_date)::date AS sales_month,\n    ROUND(SUM(sales_amount)::numeric, 2) AS sales_amount\n  FROM orders\n  WHERE order_status = 'completed'\n    AND order_date >= DATE '2026-07-01'\n    AND order_date < DATE '2026-09-01'\n  GROUP BY 1\n)\nSELECT\n  sales_month,\n  sales_amount,\n  sales_amount - LAG(sales_amount) OVER (ORDER BY sales_month) AS amount_change,\n  ROUND((sales_amount / NULLIF(LAG(sales_amount) OVER (ORDER BY sales_month), 0) - 1) * 100, 2) AS change_pct\nFROM monthly\nORDER BY sales_month;`,
    schema:'命中 orders\n字段：order_date、order_status、sales_amount', plan:'分别汇总2026年7月和8月 completed 订单销售额；计算金额差和环比变化率。'
  }
};

let currentKey=null, chartState=null;
const $=s=>document.querySelector(s);
const toast=msg=>{const el=$('#toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),1800)};
function identify(q){q=q.replace(/\s/g,'');if(/30天|三十天|趋势/.test(q))return'trend';if(/产品/.test(q)&&(/前5|前五|最高|top5/i.test(q)))return'products';if(/本月/.test(q)&&/上月/.test(q)&&(/变化|相比|对比|环比/.test(q)))return'compare';return null}
function workflow(s){return [
  ['意图识别','已识别为只读数据库分析任务',`用户问题：${s.question}\n任务类型：描述性分析`],
  ['Schema 检索','已定位查询所需的数据表与字段',s.schema],
  ['分析规划','已生成 1 个结构化执行步骤',s.plan],
  ['SQL 生成','已根据分析计划生成查询语句',s.sql.split('\n').slice(0,4).join('\n')+'\n…'],
  ['SQL 校验','SQL 只读安全校验通过','仅包含 SELECT / WITH；无写入语句；PostgreSQL 方言检查通过'],
  ['执行查询',`查询完成，返回 ${s.rows.length} 行数据`,`执行耗时：演示快照 12 ms\n是否截断：否`],
  ['结果分析','已基于查询结果生成描述性结论',`洞察数量：${s.insights.length}\n图表数量：1`]
]}
function runScenario(key,question){const s=scenarios[key];currentKey=key;$('#welcome').classList.add('hidden');$('#resultView').classList.remove('hidden');$('#questionBanner').textContent=question||s.question;$('#workflowGrid').innerHTML='';$('#workflowStatus').textContent='问数正在分析';const steps=workflow(s);steps.forEach((st,i)=>{const el=document.createElement('article');el.className='flow-step pending';el.innerHTML=`<h3>○ ${st[0]}</h3><p>${st[1]}</p><div class="flow-detail">等待执行</div>`;$('#workflowGrid').appendChild(el);setTimeout(()=>{el.classList.remove('pending');el.innerHTML=`<h3>✓ ${st[0]}</h3><p>${st[1]}</p><div class="flow-detail"></div>`;el.querySelector('.flow-detail').textContent=st[2];if(i===steps.length-1){$('#workflowStatus').textContent='分析流程执行完成'}},120*i)});
  $('#answerText').innerHTML=s.answer;$('#rowCount').textContent=`共 ${s.rows.length} 行`;$('#insightGrid').innerHTML=s.insights.map((x,i)=>`<article class="insight"><small>INSIGHT 0${i+1}</small><h3>${x[0]}</h3><p>${x[1]}</p></article>`).join('');$('#chartTitle').textContent=s.chartTitle;$('#sqlText').textContent=s.sql;renderTable(s.rows);setTimeout(()=>drawChart(s),120);saveHistory(key,question||s.question);window.scrollTo({top:0,behavior:'smooth'})}
function renderTable(rows){const keys=Object.keys(rows[0]||{});$('#tableRows').textContent=`展示 ${rows.length} 行`;$('#tableHead').innerHTML='<tr>'+keys.map(k=>`<th>${k}</th>`).join('')+'</tr>';$('#tableBody').innerHTML=rows.map(r=>'<tr>'+keys.map(k=>`<td>${typeof r[k]==='number'?fmt(r[k]):r[k]}</td>`).join('')+'</tr>').join('')}
function drawChart(s){const c=$('#chartCanvas'),rect=c.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);c.width=Math.max(700,Math.round(rect.width*dpr));c.height=Math.round(380*dpr);const ctx=c.getContext('2d');ctx.scale(dpr,dpr);const W=c.width/dpr,H=c.height/dpr,p={l:64,r:24,t:34,b:s.chart==='line'?72:58},rows=s.rows,vals=rows.map(r=>Number(r.销售额)),labels=rows.map(r=>r.日期||r.产品名称||r.月份),max=Math.ceil(Math.max(...vals)*1.18/1000)*1000;ctx.clearRect(0,0,W,H);ctx.font='12px sans-serif';ctx.fillStyle='#7e899e';ctx.strokeStyle='#e4e8f0';ctx.lineWidth=1;for(let i=0;i<=4;i++){const y=p.t+(H-p.t-p.b)*i/4;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(W-p.r,y);ctx.stroke();ctx.fillText(fmt(Math.round(max*(4-i)/4)),4,y+4)}const x=i=>p.l+(W-p.l-p.r)*(rows.length===1?.5:i/(rows.length-1));const y=v=>p.t+(H-p.t-p.b)*(1-v/max);if(s.chart==='line'){ctx.strokeStyle='#4d67ed';ctx.lineWidth=3;ctx.beginPath();vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.stroke();vals.forEach((v,i)=>{ctx.fillStyle='#4d67ed';ctx.beginPath();ctx.arc(x(i),y(v),3.5,0,Math.PI*2);ctx.fill();ctx.save();ctx.translate(x(i),H-p.b+18);ctx.rotate(-Math.PI/3);ctx.fillStyle='#6f7b91';ctx.font='10px sans-serif';ctx.fillText(labels[i].slice(5),0,0);ctx.restore()})}else{const gap=(W-p.l-p.r)/rows.length,bw=Math.min(72,gap*.55);vals.forEach((v,i)=>{const xx=p.l+gap*i+(gap-bw)/2,yy=y(v);ctx.fillStyle=i===0?'#4862e9':'#7d8ff0';ctx.fillRect(xx,yy,bw,H-p.b-yy);ctx.fillStyle='#435069';ctx.textAlign='center';ctx.fillText(fmt(v),xx+bw/2,yy-8);ctx.fillStyle='#6f7b91';ctx.fillText(labels[i],xx+bw/2,H-p.b+20)});ctx.textAlign='left'}chartState={canvas:c,title:s.chartTitle}}
function csvText(rows){const keys=Object.keys(rows[0]);const safe=v=>{let s=String(v??'');if(/^[=+\-@]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"'};return '\ufeff'+[keys,...rows.map(r=>keys.map(k=>r[k]))].map(a=>a.map(safe).join(',')).join('\n')}
function download(name,blob){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},500)}
function excelXml(rows,title){const keys=Object.keys(rows[0]);const esc=s=>String(s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));const row=a=>'<Row>'+a.map(v=>`<Cell><Data ss:Type="${typeof v==='number'?'Number':'String'}">${esc(v)}</Data></Cell>`).join('')+'</Row>';return `<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="${esc(title.slice(0,25))}"><Table>${row(keys)}${rows.map(r=>row(keys.map(k=>r[k]))).join('')}</Table></Worksheet></Workbook>`}
function saveHistory(key,q){let h=JSON.parse(localStorage.getItem('askdata_static_history')||'[]');h=h.filter(x=>x.q!==q);h.unshift({key,q,t:Date.now()});localStorage.setItem('askdata_static_history',JSON.stringify(h.slice(0,8)));renderHistory()}
function renderHistory(){const h=JSON.parse(localStorage.getItem('askdata_static_history')||'[]');const box=$('#historyList');box.innerHTML=h.length?h.map((x,i)=>`<button data-history="${i}" title="${x.q}">${x.q}</button>`).join(''):'<span class="empty-side">还没有分析记录</span>';box.querySelectorAll('button').forEach((b,i)=>b.onclick=()=>runScenario(h[i].key,h[i].q))}
document.querySelectorAll('[data-scenario]').forEach(b=>b.onclick=()=>runScenario(b.dataset.scenario,scenarios[b.dataset.scenario].question));
$('#queryForm').onsubmit=e=>{e.preventDefault();const q=$('#queryInput').value.trim();if(!q)return;const key=identify(q);if(!key){toast('静态 Demo 仅支持页面中的三类演示问题');return}runScenario(key,q);$('#queryInput').value=''};
$('#newAnalysis').onclick=()=>{$('#resultView').classList.add('hidden');$('#welcome').classList.remove('hidden');window.scrollTo(0,0)};
$('#copySql').onclick=()=>navigator.clipboard.writeText($('#sqlText').textContent).then(()=>toast('SQL 已复制'));
$('#downloadCsv').onclick=()=>{const s=scenarios[currentKey];download(`问数_${currentKey}.csv`,new Blob([csvText(s.rows)],{type:'text/csv;charset=utf-8'}))};
$('#downloadExcel').onclick=()=>{const s=scenarios[currentKey];download(`问数_${currentKey}.xls`,new Blob([excelXml(s.rows,s.chartTitle)],{type:'application/vnd.ms-excel'}))};
$('#downloadPng').onclick=()=>chartState.canvas.toBlob(b=>download(`问数_${currentKey}.png`,b),'image/png');
$('#saveAnalysis').onclick=()=>toast('分析已保存在本机浏览器');
$('#addDashboard').onclick=()=>{let n=Number(localStorage.getItem('askdata_dashboard_count')||0)+1;localStorage.setItem('askdata_dashboard_count',n);$('#dashboardCount').textContent=n;toast('已添加到本机演示仪表盘')};
$('#dashboardButton').onclick=()=>toast(`本机仪表盘已保存 ${localStorage.getItem('askdata_dashboard_count')||0} 项`);
window.addEventListener('resize',()=>currentKey&&drawChart(scenarios[currentKey]));
$('#dashboardCount').textContent=localStorage.getItem('askdata_dashboard_count')||0;renderHistory();
