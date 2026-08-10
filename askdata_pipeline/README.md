# askdata_pipeline

端到端 Text2SQL Demo。

## 目录结构

```text
askdata_pipeline/
├── __init__.py
├── demo_data.py             # 测试数据库创建与业务Schema元数据
├── create_demo_db.py        # 单独创建SQLite测试库
├── sql_execution_demo.py    # 最小MCP SQL执行Demo
├── local_clients.py         # 本地Mock关键词抽取和Embedding
├── objects.py               # Pipeline数据结构
├── serialization.py         # API JSON安全序列化
├── text2sql_pipeline.py     # 端到端流程编排
└── end_to_end_demo.py       # 完整Text2SQL Demo
```

## 1. 创建测试数据库

```bash
python -m askdata_pipeline.create_demo_db
```

会生成：

```text
runtime_data/trade_demo.db
```

测试数据 SQL 在：

```text
sql/create_trade_demo.sql
```

## 2. 只测试SQL执行环境

```bash
python -m askdata_pipeline.sql_execution_demo
```

这个 Demo 不走大模型，只验证 MCP 路由和 SQLite 执行。

## 3. 运行端到端流程

```bash
python -m askdata_pipeline.end_to_end_demo
```

程序化调用时使用统一 API 协议：

```python
pipeline = AskDataText2SQLPipeline()
result = pipeline.run("查询总交易笔数大于50000的利率")
payload = result.to_api_response()
```

完整链路为：Schema 检索 → CoT → SQL 生成 → SQL 校验/修复 → 查询执行 → 结果质量检查 → 确定性分析 → 图表推荐 → 可信回答。

## 4. 运行动态路由 + 长短期记忆 Demo

```bash
python askdata_pipeline/memory_end_to_end_demo.py
```

统一入口为 `DynamicAskDataService`：

- `database_query`：进入 Schema 检索、CoT、SQL 生成和数据库执行；
- `data_qa`：用于结果解释、指标说明、历史结果追问、数据分析和业务知识问答，不执行 SQL；
- 路由前先读取短期上下文，并按用户开关决定是否召回长期记忆；
- 配置模型 API Key 时优先使用模型分类，无模型或模型输出异常时使用规则兜底；
- 路由结论会随助手消息写入记忆元信息。

示例：

```python
from askdata_memory import ConversationMemoryService
from askdata_pipeline import AskDataText2SQLPipeline, DynamicAskDataService

memory = ConversationMemoryService()
service = DynamicAskDataService(AskDataText2SQLPipeline(), memory)

response = service.run(
    user_id="user-001",
    session_id="session-001",
    query="查询总交易笔数大于50000的利率",
)

print(response.decision.route.value)
print(response.decision.reason)
print(response.queried_database)
print(response.answer)
```

单独运行路由测试：

```bash
python tests/test_routing.py
```

## 4. 运行带长短期记忆的端到端流程
bash run_memory.sh
