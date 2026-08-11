# 问数 · 描述性数据分析 Agent

本项目演示从自然语言问题到安全 SQL、数据结论、表格和图表的端到端流程。公开 Cloudflare Demo 定位为描述性分析，只回答现有数据中“发生了什么”，支持趋势、排名、占比、期间对比和分布，不提供异常诊断、原因归因、预测或策略建议。

## 核心模块

```text
schema_indexing/      # Schema索引构建，离线阶段
schema_retrieval/     # Schema检索与SchemaGraph构建
cot_planning/         # CoT四元组规划
sql_generation/       # SQL生成
mcp_router/           # MCP路由执行
sql_validation/       # SQL AST校验与自动修复
result_quality/       # 查询结果合理性检查
data_analysis/        # 确定性统计与洞察
chart_generation/     # 用户明确要求时执行 ECharts 图表推荐
response_generation/  # 可信回答生成
askdata_pipeline/     # 端到端流程编排
askdata_memory/       # 长短期记忆、个人知识库召回与会话编排
backend/              # FastAPI、后台任务、SSE 事件和会话 API
web/                  # 无构建依赖的响应式网页
reporting/            # CSV 与 Excel 分析报表生成
```

## 端到端运行

```bash
python -m askdata_pipeline.end_to_end_demo
```

当前 Demo 会自动创建 SQLite 测试库：

```text
runtime_data/trade_demo.db
```

测试 Query：

```text
查询总交易笔数大于50000的利率是多少
```

## 当前链路

```text
用户Query
  ↓
关键词抽取
  ↓
Schema混合检索 + RRF + Rerank
  ↓
SchemaGraph
  ↓
CoT四元组规划
  ↓
SQL生成
  ↓
SQL校验与自动修复
  ↓
MCP路由执行
  ↓
结果质量检查
  ↓
确定性数据分析
  ↓
按用户请求可选执行图表推荐
  ↓
可信回答与统一API JSON
```

动态路由会先结合长短期记忆判断用户意图：需要新业务数据时进入上述 Text2SQL 链路；结果解释、指标说明、历史结果追问和业务知识问答进入 `data_qa` 链路，不执行数据库查询。

路由实现位于 `askdata_pipeline/routing.py`，统一编排入口位于 `askdata_pipeline/dynamic_service.py`，对话问答实现位于 `askdata_pipeline/data_qa.py`。

当前已包含 SQL 校验与有限次数自动修正、结果合理性检查、确定性分析、按需图表推荐和可信回答生成。图表默认不生成，只有用户在问题中明确要求可视化，或 API 传入 `generate_chart=true` 时才运行。

## 第一阶段生产化改造

当前版本已增加：

- `AgentState`：统一保存 run、用户、会话、Schema、SQL、校验、查询结果和错误状态；
- `sql_validation/`：SQLGlot AST 校验、表/字段白名单、单语句限制、自动 LIMIT；
- SQL 校验失败后的自动修复循环，默认最多修复 2 次；
- `QueryExecutor` 接口、增强的 SQLite 只读执行器和 MySQL 执行器；
- Pipeline 依赖注入，可传入 Schema 服务、查询执行器、路由器、校验器和 SQL 生成器。

默认 Demo 为兼容旧示例，在未安装 SQLGlot 时使用保守校验，并在结果中写入降级警告。生产环境必须：

```python
PipelineConfig(
    bootstrap_demo_database=False,
    database_type="mysql",
    sql_dialect="mysql",
    require_sqlglot=True,
)
```

并安装 `sqlglot`、使用只读数据库账号。MySQL 凭证通过环境变量提供：

```text
ASKDATA_MYSQL_HOST
ASKDATA_MYSQL_PORT
ASKDATA_MYSQL_USER
ASKDATA_MYSQL_PASSWORD
ASKDATA_MYSQL_DATABASE
ASKDATA_MYSQL_MAX_EXECUTION_TIME_MS
ASKDATA_MYSQL_MAX_ROWS
```

生产模式还必须注入真实数据库对应的 `schema_retrieval_service` 和 `MySQLQueryExecutor`，不会自动创建 Demo 数据库。

## 第二阶段分析与结果输出

当前版本已增加：

- `ResultQualityValidator`：处理查询失败、空结果、截断、行数不一致、异常数值和结构错误；
- `DeterministicDataAnalyzer`：生成数值概览、总计、趋势、变化率和分类排名；
- `EChartsRecommender`：推荐折线、柱状、环形和散点图，只输出安全 JSON；
- `GroundedResponseGenerator`：仅引用查询与确定性计算结果生成结论；
- `AgentState.to_api_response()`：返回稳定的前端协议，不暴露内部 Prompt 和完整 Schema；
- 记忆链路优先保存自然语言结论，同时保留完整结构化结果用于后续追问。

获取前端响应：

```python
result = AskDataText2SQLPipeline().run("查询总交易笔数大于50000的利率")
payload = result.to_api_response()
```

响应包含：`answer`、`insights`、`table`、`charts`、`sql`、`scope`、`warnings`、`suggested_questions` 和 `error`。

## 第三阶段 Web 产品化

当前版本已增加：

- FastAPI 前后端一体服务，默认访问地址为 `http://127.0.0.1:8000`；
- 后台 `RunManager`：任务队列、状态查询、结果保留、事件回放和协作式取消；
- SSE 实时运行步骤：意图、Schema、规划、SQL 生成/校验/执行、分析与完成；
- 会话列表与历史结果接口；
- 可视化网页：结论、洞察、Canvas 图表、数据表格、SQL 复制和追问建议；
- Docker 单容器部署文件。

### 一键离线体验（无需服务器或安装依赖）

macOS 用户直接双击项目根目录中的 `打开AskData离线演示.command`，页面会在默认浏览器中打开。也可以直接双击 `web/index.html`。

离线页面会自动进入访客账号，并使用内置 Mock 数据模拟 Agent 的执行步骤、分析结论、数据表格、SQL、按需图表、下钻和 Dashboard。问题中包含“图表”“趋势图”“可视化”等要求时才会生成图表。

离线模式仅用于界面与交互演示；通过 HTTP、Cloudflare Pages/Workers 或 FastAPI 打开时，会自动关闭 Mock 并请求真实后端 API。

### 启动本地服务（可选）

如果需要用 HTTP 方式查看页面，在项目目录执行：

```bash
python3 -m backend.static_preview
```

然后打开 `http://127.0.0.1:8000`。

### 本地启动

建议使用 Python 3.11 或 3.12：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.txt
chmod +x run_web.sh
./run_web.sh
```

浏览器打开 `http://127.0.0.1:8000`。不配置 `DASHSCOPE_API_KEY` 时会使用内置 Mock 模型和演示数据库，适合验证整条网页链路。
使用真实模型前，在当前 shell 中导出 `.env.example` 里对应的模型环境变量。

开发模式默认登录账号为 `demo@askdata.local`，密码为 `askdata-demo`。正式部署不得继续使用该密码。

### API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | 邮箱密码登录并签发 HttpOnly 会话 Cookie |
| `GET` | `/api/auth/me` | 获取当前登录用户 |
| `POST` | `/api/auth/logout` | 撤销当前会话并退出 |
| `GET` | `/api/data-source/status` | 查看当前数据源和 Schema 状态 |
| `POST` | `/api/data-source/test` | 管理员检测连接和只读权限 |
| `POST` | `/api/data-source/sync` | 管理员重新同步 Schema 并热切换 Pipeline |
| `POST` | `/api/chat` | 提交分析任务，返回 `run_id` |
| `POST` | `/api/drilldown` | 基于已完成分析发起下钻或上卷查询 |
| `GET` | `/api/runs/{run_id}/events` | SSE 实时步骤及断线回放 |
| `GET` | `/api/runs/{run_id}` | 查询状态与最终结果 |
| `POST` | `/api/runs/{run_id}/cancel` | 请求取消任务 |
| `GET` | `/api/conversations` | 获取用户会话列表 |
| `GET` | `/api/conversations/{session_id}` | 获取会话消息和结果 |
| `POST` | `/api/analyses` | 保存已完成的分析结果 |
| `GET` | `/api/analyses` | 获取当前用户保存的分析 |
| `GET` | `/api/analyses/{analysis_id}` | 读取一条已保存分析 |
| `DELETE` | `/api/analyses/{analysis_id}` | 删除一条已保存分析 |
| `GET` | `/api/analyses/{analysis_id}/export.csv` | 从保存记录下载 CSV |
| `GET` | `/api/analyses/{analysis_id}/export.xlsx` | 从保存记录下载 Excel |
| `POST` | `/api/dashboards` | 创建个人仪表盘 |
| `GET` | `/api/dashboards` | 获取当前用户的仪表盘列表 |
| `GET` | `/api/dashboards/{dashboard_id}` | 获取仪表盘及完整卡片数据 |
| `POST` | `/api/dashboards/{dashboard_id}/cards` | 将已保存分析添加到仪表盘 |
| `DELETE` | `/api/dashboards/{dashboard_id}/cards/{card_id}` | 从仪表盘移除卡片 |
| `DELETE` | `/api/dashboards/{dashboard_id}` | 删除个人仪表盘 |
| `GET` | `/api/runs/{run_id}/export.csv` | 下载结果数据 CSV |
| `GET` | `/api/runs/{run_id}/export.xlsx` | 下载三工作表 Excel 报告 |
| `GET` | `/health` | 健康检查 |

`POST /api/chat` 可选传入 `generate_chart: true`。未传入时由用户问题中的明确制图意图决定；普通查询不生成图表。

除 `/health`、`/api/auth/login` 和网页静态文件外，所有业务 API 都要求有效登录会话。服务端会从会话 Cookie 确定用户身份，客户端不再提交或决定 `user_id`。

### Docker 启动

```bash
docker compose up --build
```

`runtime_data` 通过 Docker volume 持久化，用户和会话存储在其中的 `auth.db`。正式环境请在网关层配置 HTTPS、设置强初始化密码、启用安全 Cookie，并将 Demo SQLite 替换为只读业务数据源。

### 测试

```bash
python -m unittest discover -s tests -v
node --check web/app.js
```

## 长短期记忆

项目已支持滑动窗口、异步增量摘要、用户主动长期记忆存储，以及可选个人知识库召回。详细设计与调用方式见 [askdata_memory/README.md](askdata_memory/README.md)。

运行长短期记忆端到端 Demo：

```bash
python askdata_pipeline/memory_end_to_end_demo.py
```

## 第四阶段业务测试库

内置 SQLite Demo 已扩展为 6 张业务表：

- `trade_summary`、`interest_info`：保留原有交易与利率示例；
- `customers`：12 个客户与 6 个区域；
- `products`：8 个商品与品类、单价；
- `orders`：最近 180 天的 360 笔订单；
- `order_items`：360 条订单商品明细。

每次重建时数据日期会相对当天生成，因此“最近 30 天”和“本月/上月”始终可测，并覆盖不同日期、区域、商品和品类的销售表现。

重建测试库：

```bash
python -m askdata_pipeline.create_demo_db
```

可直接验证：

```text
最近30天销售额趋势如何？
各区域订单量排名是什么？
销售额最高的前5个产品是哪些？
本月与上月销售额相比变化多少？
各品类销售额占比是多少？
销售额在各区域如何分布？
请生成最近30天销售额折线图。
```

不配置模型 API Key 时，上述标准问题由可重复的离线规则链路支持；配置真实模型后则进入通用 Text2SQL 流程。

## 第五阶段保存与报表导出

当前版本已增加：

- 用户可把已完成的 Agent 结果保存到 SQLite，并在网页侧边栏再次打开或删除；
- 保存记录按 `user_id` 隔离，同一用户重复保存同一次运行时更新原记录；
- 结果页支持下载 UTF-8 BOM CSV，可直接用 Excel 打开；
- 结果页支持下载 Excel 报告，包含“分析概览”“数据明细”“SQL”三个工作表；
- Excel 数据明细带冻结表头、自动筛选、列宽和数值格式；
- 保存和导出接口会校验运行归属及完成状态，不能跨用户读取结果。

网页使用流程：先提交并完成一次分析，然后在结果上方点击“保存分析”“下载 CSV”或“下载 Excel”。保存后的结果会显示在左侧“已保存分析”区域。

第五阶段最初由网页提交 `user_id`；第六阶段已升级为服务端登录会话，网页不能再指定用户身份。

## 第六阶段登录鉴权

当前版本已增加：

- SQLite 用户与会话存储，密码使用 PBKDF2-SHA256、独立随机盐和慢哈希保存；
- 登录成功后签发随机不透明令牌，数据库只保存令牌的 SHA-256 摘要；
- 会话通过 `HttpOnly`、`SameSite=Lax` Cookie 传递，前端 JavaScript 无法读取令牌；
- 登录、当前账号、退出接口，以及登录失败频率限制；
- 任务状态、取消、SSE、会话历史、保存分析和报表下载均执行服务端归属校验；
- 网页登录页、账号展示和退出入口；
- 初始化账号通过环境变量创建或轮换，用户 ID 在密码轮换时保持不变。

生产环境至少设置：

```bash
export ASKDATA_ENV=production
export ASKDATA_BOOTSTRAP_EMAIL=admin@your-company.com
export ASKDATA_BOOTSTRAP_PASSWORD='替换为高强度密码'
export ASKDATA_BOOTSTRAP_DISPLAY_NAME='Data Admin'
export ASKDATA_COOKIE_SECURE=true
```

首次启动后会在 `runtime_data/auth.db` 创建账号。后续修改初始化密码并重启服务，可轮换该账号密码。生产部署必须使用 HTTPS；如果需要企业统一登录，下一阶段可将本地登录替换为 OIDC/OAuth 企业身份提供商。

## 第七阶段真实 MySQL 数据源

当前版本支持通过环境变量在内置 SQLite 和真实 MySQL 之间切换：

- 从 `information_schema` 读取业务表、字段类型、注释、主键和外键；
- 启动和手动同步时检测数据库连接与 `SHOW GRANTS`；
- 默认要求账号明确只有 SELECT 权限，检测到 UPDATE、INSERT、DELETE、DDL 或管理权限时拒绝启用；
- Schema 同步成功后原子切换 Agent Pipeline，不需要重启正在运行的 Web 服务；
- 连接失败时保留登录和状态页，但禁止提交分析，不会静默回退到演示数据；
- Schema 元数据快照写入 `runtime_data/mysql_schema_snapshot.json`，不包含密码；
- 默认不抽取字段样例值，减少敏感业务数据进入模型上下文的风险；
- 可通过 `business_meta.example.json` 补充中文表意、字段别名和 metric/dimension/time 等语义角色。

### 1. 创建只读 MySQL 账号

以下 SQL 需要由数据库管理员执行，并替换数据库名、来源地址和密码：

```sql
CREATE USER 'askdata_readonly'@'应用服务器地址' IDENTIFIED BY '高强度随机密码';
GRANT SELECT ON analytics.* TO 'askdata_readonly'@'应用服务器地址';
FLUSH PRIVILEGES;
```

不要授予 `ALL PRIVILEGES`、写入、DDL、FILE、SUPER 或 GRANT OPTION。

### 2. 配置服务

```bash
export ASKDATA_DATABASE_TYPE=mysql
export ASKDATA_DATABASE_ALIAS=analytics
export ASKDATA_MYSQL_HOST=mysql.internal
export ASKDATA_MYSQL_PORT=3306
export ASKDATA_MYSQL_USER=askdata_readonly
export ASKDATA_MYSQL_PASSWORD='数据库只读密码'
export ASKDATA_MYSQL_DATABASE=analytics
export ASKDATA_ENFORCE_READONLY=true
export ASKDATA_REQUIRE_SQLGLOT=true
export ASKDATA_SCHEMA_SAMPLE_SIZE=0
export ASKDATA_BUSINESS_META_PATH=/app/business_meta.json
```

`ASKDATA_DATABASE_ALIAS` 是 Agent Prompt 和路由中使用的逻辑名称；`ASKDATA_MYSQL_DATABASE` 是真实数据库名。建议两者保持一致。

### 3. 启动和同步

正常启动服务并登录管理员账号。网页左下角会展示连接状态；管理员点击同步按钮后，系统会重新测试连接、检查只读权限、读取 Schema 并切换 Pipeline。

也可调用：

```text
GET  /api/data-source/status
POST /api/data-source/test
POST /api/data-source/sync
```

生产 MySQL 模式需要同时配置真实模型 API，因为离线 Mock SQL 只覆盖内置演示库的固定问题。正式开放前应让数据库管理员再次核对只读账号权限，并在预发布环境执行代表性问题验收。

## 第八阶段个人 Dashboard

当前版本已补齐流程图中的“保存为 Dashboard”能力：

- 用户可把一次已完成的分析保存后添加到个人仪表盘；
- 首次使用时网页自动创建“我的仪表盘”，后续重复添加同一分析不会产生重复卡片；
- Dashboard 卡片集中展示分析结论、关键洞察，以及用户明确要求生成的图表；
- 可从卡片返回完整分析，也可只从仪表盘移除卡片；
- 删除已保存分析时，对应 Dashboard 卡片自动清理；
- 仪表盘、卡片和来源分析全部按登录用户隔离，不能跨账号访问或引用。

网页使用流程：完成一次分析后点击“添加到仪表盘”，再从左侧“我的仪表盘”进入集中视图。普通问题仍不会自动生成图表；只有原分析包含图表时，Dashboard 才展示该图表。

## 第九阶段钻取分析

当前版本已增加：

- 根据结果字段生成安全、确定性的下钻和上卷动作；
- 区域排名可下钻到头部区域的产品销售额；
- 产品排名可继续下钻到头部产品的区域分布；
- 日级销售趋势可上卷到最近 180 天的月度趋势；
- 钻取查询走完整的 Schema 检索、SQL 生成、只读校验和执行链路；
- 子分析结果记录 `parent_run_id` 和 `direction`，形成可追踪的分析层级；
- 父分析归属由服务端校验，不能跨用户发起钻取；
- 数据库维度值经过字符白名单处理后才允许进入钻取问题，避免将不可信内容拼接到模型输入。

网页会在可钻取的结果下展示“继续钻取”。点击后沿用当前会话执行新查询，并在结果范围中标记“下钻分析”或“上卷分析”。

## 第十阶段 Cloudflare Pages / Workers 部署

当前版本已加入适合 GitHub 自动部署的 Cloudflare Edge 版本：

- `web/` 由 Cloudflare Pages 托管静态网页；
- `functions/api/[[path]].js` 使用 Pages Functions（Workers Runtime）实现同域 `/api/*`；
- Cloudflare D1 保存 6 张演示业务表、任务、分析和 Dashboard；
- 第一次 API 请求自动初始化演示库，不需要上传本地 SQLite 文件；
- 百炼 API Key 通过 Cloudflare Secret `DASHSCOPE_API_KEY` 注入；
- 每个访客网络总共只能提交 2 次问题或钻取，清除 Cookie/无痕模式不能重置，前端会提示剩余额度；
- 项目所有者可在 Cloudflare Secret 中配置 `ASKDATA_TEST_TOKEN`，通过网页右上角“测试模式”输入测试码后不限提问次数；测试码只保存在当前浏览器标签页，公开访客仍受 2 次限制；
- 未配置 Key 时继续使用安全规则 SQL，方便先验证部署；
- `web/_routes.json` 让静态资源绕过 Function，仅 API 消耗 Workers 配额；
- `npm run test:worker` 会使用本地 SQLite 测试库执行核心查询、排名、占比、结论和图表数据校验，全程不调用模型 API；
- CSV、Excel 和图表 PNG 均由访客浏览器直接生成下载文件；Excel 包含分析概览、完整数据明细和实际执行 SQL，不依赖登录态跳转；
- 本地 Python/FastAPI 版本和双击离线 Demo 保持可用。

Cloudflare Pages 构建参数：

```text
Project name: askdata-agent-demo
Framework preset: None
Build command: npm run build
Build output directory: web
D1 binding: DB
Secret: DASHSCOPE_API_KEY
Optional tester secret: ASKDATA_TEST_TOKEN
```

完整步骤见 [Cloudflare 部署指南](docs/CLOUDFLARE部署指南.md)。模型密钥只放 Cloudflare Secret 或本机 `.dev.vars`，不要提交到代码仓库。
