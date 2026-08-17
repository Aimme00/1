const MVP_CONFIG=Object.freeze({
  examples:["最近30天销售额趋势如何？","各区域已完成订单量排名是什么？","已完成订单中，销售额最高的前5个产品是哪些？","本月与上月已完成订单销售额相比变化多少？"],
  stages:[
    {id:"understand",label:"理解问题",nodes:["intent"]},
    {id:"schema",label:"检索 Schema",nodes:["schema"]},
    {id:"plan",label:"生成查询计划",nodes:["plan"]},
    {id:"sql",label:"生成并校验 SQL",nodes:["sql_generate","sql_validate"]},
    {id:"execute",label:"执行查询",nodes:["sql_execute"]},
    {id:"result",label:"生成结果",nodes:["analysis","run"]},
  ],
});

const state={
  currentUser:null,
  sessionId:localStorage.getItem("askdata_mvp_session")||`mvp_${Date.now()}_${Math.random().toString(36).slice(2,8)}`,
  runId:null,eventSource:null,result:null,currentQuery:"",
};
const $=id=>document.getElementById(id);

function toast(message){
  const el=$("toast");el.textContent=message;el.classList.add("visible");
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove("visible"),2200);
}

async function api(path,options={}){
  const response=await fetch(path,{credentials:"same-origin",...options,headers:{"Content-Type":"application/json",...(options.headers||{})}});
  if(!response.ok){
    let detail=`请求失败（${response.status}）`;
    try{const body=await response.json();detail=typeof body.detail==="string"?body.detail:body.detail?.message||detail}catch{}
    throw new Error(detail);
  }
  return response.status===204?null:response.json();
}

function buildStaticUi(){
  MVP_CONFIG.examples.forEach(query=>{
    const button=document.createElement("button");button.type="button";button.className="example-button";button.textContent=query;
    button.onclick=()=>{$("queryInput").value=query;$("generateChart").checked=/图|折线|柱状|饼图/.test(query);$("queryInput").focus()};
    $("exampleGrid").append(button);
  });
  MVP_CONFIG.stages.forEach((stage,index)=>{
    const card=document.createElement("article");card.className="stage-card pending";card.dataset.stage=stage.id;
    card.innerHTML=`<span class="stage-number">${index+1}</span><div><strong>${stage.label}</strong><small>等待执行</small></div>`;
    $("stageGrid").append(card);
  });
}

function setStage(id,status,message){
  const card=document.querySelector(`[data-stage="${id}"]`);if(!card)return;
  card.className=`stage-card ${status}`;
  card.querySelector("small").textContent=message||({pending:"等待执行",running:"正在执行",completed:"已完成",failed:"未完成"}[status]);
}
function resetStages(){MVP_CONFIG.stages.forEach(stage=>setStage(stage.id,"pending","等待执行"))}
function stageForNode(node){return MVP_CONFIG.stages.find(stage=>stage.nodes.includes(node))}

async function initialize(){
  buildStaticUi();localStorage.setItem("askdata_mvp_session",state.sessionId);
  try{
    await api("/health");
    try{state.currentUser=(await api("/api/auth/me")).user}catch{state.currentUser=(await api("/api/auth/guest",{method:"POST"})).user}
    const source=await api("/api/data-source/status");if(!source.ready)throw new Error(source.error||"演示数据尚未就绪");
    $("dataSourceStatus").textContent=`${source.alias||source.database||"SQLite 演示数据"} · 已连接`;
    $("bootScreen").classList.add("hidden");
  }catch(error){$("bootMessage").textContent=`Demo 初始化失败：${error.message}`;$("bootScreen").classList.add("failed")}
}

function beginRun(query){
  state.currentQuery=query;state.result=null;$("userMessage").textContent=query;
  $("runPanel").classList.remove("hidden");$("resultView").classList.add("hidden");$("errorPanel").classList.add("hidden");
  $("cancelButton").classList.remove("hidden");$("sendButton").disabled=true;$("sendButton").textContent="分析中…";
  resetStages();setStage("understand","running","正在识别描述性分析需求");
  $("runPanel").scrollIntoView({behavior:"smooth",block:"start"});
}

async function submitQuery(event){
  event.preventDefault();const query=$("queryInput").value.trim();if(!query||state.runId)return;beginRun(query);
  try{
    const created=await api("/api/chat",{method:"POST",body:JSON.stringify({query,session_id:state.sessionId,enable_long_term:false,generate_chart:$("generateChart").checked?true:null})});
    state.runId=created.run_id;connectEvents(created.events_url);
  }catch(error){finishWithError(error.message)}
}

function connectEvents(url){
  state.eventSource?.close();const source=new EventSource(url);state.eventSource=source;
  const receive=event=>{
    let payload;try{payload=JSON.parse(event.data)}catch{return}updateProgress(payload);
    if(["completed","failed","cancelled"].includes(payload.event)){source.close();loadResult()}
  };
  ["queued","started","progress","cancel_requested","completed","failed","cancelled"].forEach(name=>source.addEventListener(name,receive));
  source.onerror=()=>{source.close();if(state.runId)setTimeout(pollRun,700)};
}

function updateProgress(event){
  const stage=stageForNode(event.node);if(!stage)return;
  const current=MVP_CONFIG.stages.indexOf(stage);
  MVP_CONFIG.stages.slice(0,current).forEach(item=>setStage(item.id,"completed","已完成"));
  const status=event.status==="failed"?"failed":event.status==="completed"?"completed":"running";
  setStage(stage.id,status,event.message);
}

async function pollRun(){
  if(!state.runId)return;
  try{const run=await api(`/api/runs/${state.runId}`);if(["completed","failed","cancelled"].includes(run.status))renderRun(run);else setTimeout(pollRun,900)}
  catch(error){finishWithError(error.message)}
}
async function loadResult(){if(!state.runId)return;try{renderRun(await api(`/api/runs/${state.runId}`))}catch(error){finishWithError(error.message)}}

function finishControls(){state.runId=null;state.eventSource?.close();$("cancelButton").classList.add("hidden");$("sendButton").disabled=false;$("sendButton").textContent="开始分析"}
function renderRun(run){
  if(run.status==="completed"&&run.result){MVP_CONFIG.stages.forEach(stage=>setStage(stage.id,"completed","已完成"));finishControls();renderResult(run.result);return}
  finishWithError(run.error?.message||(run.status==="cancelled"?"任务已取消":"任务未完成"));
}

function renderResult(result){
  state.result=result;$("answerText").textContent=result.answer||"查询完成，请结合明细与实际 SQL 核对结果。";
  const scope=result.scope||{};$("scopeLine").textContent=[scope.database,scope.row_count!=null?`共 ${scope.row_count} 行`:null,scope.truncated?"结果已截断":null].filter(Boolean).join(" · ");
  renderTable(result.table||{});renderSql(result.sql||{});renderChart((result.charts||[])[0]);renderWarnings(result.warnings||[]);
  $("resultView").classList.remove("hidden");$("resultView").scrollIntoView({behavior:"smooth",block:"start"});
}

function renderTable(table){
  const columns=table.columns||[],rows=table.rows||[];
  const labels=Object.fromEntries((table.column_meta||[]).map(item=>[item.name,item.label||item.name]));
  $("rowCount").textContent=`${rows.length} 行`;
  const thead=document.createElement("thead"),head=document.createElement("tr");
  columns.forEach(column=>{const th=document.createElement("th");th.textContent=labels[column]||column;head.append(th)});thead.append(head);
  const tbody=document.createElement("tbody");
  rows.slice(0,100).forEach(row=>{const tr=document.createElement("tr");columns.forEach((column,index)=>{const td=document.createElement("td");const value=Array.isArray(row)?row[index]:row[column];td.textContent=value==null?"—":String(value);tr.append(td)});tbody.append(tr)});
  $("resultTable").replaceChildren(thead,tbody);
}
function renderSql(sql){$("sqlCode").textContent=sql.text||"-- 本次任务未执行数据库查询";$("sqlMeta").textContent=[sql.dialect,sql.duration_ms!=null?`${sql.duration_ms} ms`:null].filter(Boolean).join(" · ")}
function renderWarnings(items){
  const list=$("warningList");list.replaceChildren();items.slice(0,5).forEach(warning=>{const li=document.createElement("li");li.textContent=typeof warning==="string"?warning:warning.message||JSON.stringify(warning);list.append(li)});
}

function renderChart(chart){
  if(!chart){$("chartPanel").classList.add("hidden");$("downloadChartButton").disabled=true;return}
  $("chartPanel").classList.remove("hidden");$("downloadChartButton").disabled=false;$("chartTitle").textContent=chart.title||"数据图表";
  const option=chart.option||chart.echarts_option||chart;requestAnimationFrame(()=>drawCanvasChart(option,$("chartCanvas")));
}

function formatChartNumber(value){return Number(value||0).toLocaleString("zh-CN",{maximumFractionDigits:2})}
function niceAxisMax(value){const rough=Math.max(Number(value)||1,1)*1.15,magnitude=10**Math.floor(Math.log10(rough)),normalized=rough/magnitude;return (normalized<=1?1:normalized<=2?2:normalized<=2.5?2.5:normalized<=5?5:10)*magnitude}
function drawCanvasChart(option,canvas){
  const series=(option.series||[])[0]||{},raw=series.data||[],values=raw.map(item=>Number(typeof item==="object"?(item.value??0):item)||0);
  const bundled=raw.every(item=>item&&typeof item==="object"&&item.name!=null),horizontal=option.orientation==="horizontal";
  const categories=bundled?raw.map(item=>item.name):((horizontal?option.yAxis?.data:option.xAxis?.data)||values.map((_,index)=>index+1));
  const rect=canvas.getBoundingClientRect(),width=Math.max(rect.width,600),height=horizontal?Math.max(320,values.length*28+58):360,ratio=window.devicePixelRatio||1;
  canvas.width=width*ratio;canvas.height=height*ratio;canvas.style.height=`${height}px`;canvas.style.maxHeight=`${height}px`;
  const ctx=canvas.getContext("2d");ctx.scale(ratio,ratio);ctx.clearRect(0,0,width,height);if(!values.length)return;
  const axisMax=niceAxisMax(Math.max(...values,1));
  if(horizontal){
    const pad={l:170,r:72,t:22,b:36},plotW=width-pad.l-pad.r,plotH=height-pad.t-pad.b,bandH=plotH/values.length;
    ctx.strokeStyle="#e1e6ef";ctx.fillStyle="#778199";ctx.font="10px system-ui";ctx.textAlign="center";
    for(let i=0;i<=4;i++){const x=pad.l+plotW*i/4;ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,height-pad.b);ctx.stroke();ctx.fillText(formatChartNumber(axisMax*i/4),x,height-14)}
    values.forEach((value,index)=>{const y=pad.t+bandH*(index+.5),barH=Math.max(8,Math.min(18,bandH*.62)),barW=value/axisMax*plotW;ctx.fillStyle="#5269e8";ctx.fillRect(pad.l,y-barH/2,barW,barH);ctx.fillStyle="#43506a";ctx.font="600 10px system-ui";ctx.textAlign="right";ctx.fillText(String(categories[index]).slice(0,22),pad.l-10,y+3);ctx.textAlign="left";ctx.fillText(formatChartNumber(value),Math.min(width-pad.r+5,pad.l+barW+7),y+3)});
    return;
  }
  const pad={l:68,r:28,t:36,b:62},plotW=width-pad.l-pad.r,plotH=height-pad.t-pad.b,band=plotW/values.length;
  ctx.font="12px system-ui";ctx.textAlign="right";ctx.fillStyle="#778199";ctx.strokeStyle="#e1e6ef";
  for(let i=0;i<=4;i++){const y=pad.t+plotH*i/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(width-pad.r,y);ctx.stroke();ctx.fillText(formatChartNumber(axisMax*(4-i)/4),pad.l-10,y+4)}
  const point=(value,index)=>({x:pad.l+band*(index+.5),y:pad.t+plotH-(value/axisMax)*plotH});
  if(series.type==="bar")ctx.fillStyle="#5269e8",values.forEach((value,index)=>{const {x,y}=point(value,index),barW=Math.max(8,Math.min(46,band*.55));ctx.fillRect(x-barW/2,y,barW,pad.t+plotH-y);drawLabels(ctx,x,y,value,categories[index],height,index,values.length)});
  else{ctx.strokeStyle="#5269e8";ctx.lineWidth=3;ctx.beginPath();values.forEach((value,index)=>{const {x,y}=point(value,index);index?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();values.forEach((value,index)=>{const {x,y}=point(value,index);ctx.fillStyle="#5269e8";ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();drawLabels(ctx,x,y,value,categories[index],height,index,values.length)})}
}
function drawLabels(ctx,x,y,value,category,height,index,total){
  const interval=Math.max(1,Math.ceil(total/8));ctx.textAlign="center";ctx.font="600 11px system-ui";ctx.fillStyle="#43506a";ctx.fillText(formatChartNumber(value),x,Math.max(16,y-9));
  if(index%interval===0||index===total-1){ctx.font="11px system-ui";ctx.fillStyle="#778199";ctx.fillText(String(category).slice(0,12),x,height-26)}
}
function showError(message){finishWithError(message)}

function finishWithError(message){
  const active=MVP_CONFIG.stages.find(stage=>document.querySelector(`[data-stage="${stage.id}"]`)?.classList.contains("running"));if(active)setStage(active.id,"failed","执行失败");
  finishControls();$("errorText").textContent=message||"请修改问题后重试。";$("errorPanel").classList.remove("hidden");$("errorPanel").scrollIntoView({behavior:"smooth",block:"center"});
}
async function cancelRun(){if(!state.runId)return;try{await api(`/api/runs/${state.runId}/cancel`,{method:"POST"});toast("正在停止任务")}catch(error){toast(error.message)}}

$("chatForm").addEventListener("submit",submitQuery);
$("cancelButton").addEventListener("click",cancelRun);
$("retryButton").addEventListener("click",()=>{$("errorPanel").classList.add("hidden");$("queryInput").focus()});
$("copySql").addEventListener("click",async()=>{try{await navigator.clipboard.writeText($("sqlCode").textContent);toast("SQL 已复制")}catch{toast("浏览器未允许复制，请手动选择 SQL")}});
$("queryInput").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();$("chatForm").requestSubmit()}});
window.addEventListener("resize",()=>{const chart=state.result?.charts?.[0];if(chart)renderChart(chart)});
initialize();
