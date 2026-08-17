# 问数 Agent Demo

个人 AI 产品与 Vibe Coding 原型验证。

这是一个面向经营数据描述性分析的实验性 Demo：用户用自然语言提问，系统检索固定 SQLite 演示库的 Schema，生成并校验只读 SQL，执行查询后返回结论、数据表、可选图表和实际执行 SQL。

> 当前仅用于个人作品集、学习与面试演示，不是生产级数据分析平台。项目由个人完成产品范围设计与验收，并借助 AI Coding 完成原型实现和重构。

## 1. 解决什么问题

业务人员查看演示经营数据时，常需要手写 SQL 或反复整理表格。本 Demo 验证一条最小闭环：自然语言问题 → Schema 检索 → 查询计划 → SQL 生成与只读校验 → SQLite 执行 → 描述性结果展示。

## 2. 当前用户

- 面试官或作品集访客：无需注册，一键进入访客体验。
- AI 产品/工程学习者：了解一个 Text2SQL 原型的端到端组成。

## 3. 当前支持

- 最近 30 天销售额趋势、区域订单量排名、商品销售额 Top 5 与期间对比等已验证的描述性问题。
- 固定 SQLite 演示数据集与只读查询。
- 六段公开执行状态，不展示模型内部思维过程。
- 结论、数据表、按需图表、实际执行 SQL。
- CSV、Excel 和已生成图表 PNG 下载。
- 健康检查、基础错误提示和核心 E2E 验证。

## 4. 当前不支持

- 异常诊断、原因归因、未来预测或经营策略建议。
- 上传任意数据、切换数据源或在线管理 Schema。
- 面向访客的记忆、知识库、保存分析、仪表盘、下钻/上卷与用户管理。
- 生产级权限、审计、高并发、SLA 或多租户能力。

相关后端实验模块仍保留在仓库中，但不属于当前 Demo 主路径。重构前的完整说明已归档到 [docs/README_HISTORY.md](docs/README_HISTORY.md)。

## 5. 快速启动

```bash
cd /Users/aimme/Documents/Codex/2026-08-08/new-chat/work/askdata_agent_source/askdata_agent/askdata_agent
cp .env.example .env
```

在 `.env` 中启用本地 SQLite + Mock：

```dotenv
ASKDATA_DATABASE_TYPE=sqlite
ASKDATA_ALLOW_MOCK_MODEL=true
ASKDATA_ENV=development
```

启动：

```bash
./.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。页面会自动申请访客会话。

## 6. 示例问题

- 最近 30 天销售额趋势如何？
- 各区域订单量排名是什么？
- 销售额最高的前 5 个产品是哪些？
- 本月与上月销售额相比变化多少？
- 请生成最近 30 天销售额折线图。

## 7. 核心链路

1. 理解问题：识别是否需要访问演示数据库。
2. 检索 Schema：定位相关表、字段和关联关系。
3. 生成查询计划：形成结构化执行步骤。
4. 生成并校验 SQL：检查只读安全、表字段合法性与基础语法。
5. 执行查询：在固定 SQLite 演示库运行 SQL。
6. 生成结果：输出描述性结论、表格和按需图表。

## 8. 安全与校验边界

- SQL 校验目前覆盖只读安全、表/字段合法性和基础语法。
- 当前没有稳定、独立的结构化意图解析层，不能宣称已输出可靠的指标/维度/时间 JSON。
- 当前没有 SQL 语义一致性校验。SQL 即使可以执行，也可能与用户问题语义不一致。
- 结果用于 Demo 展示；重要判断必须核对实际 SQL 与原始数据。

## 9. 测试现状

- 已建立一组标准问题、SQL 与结果记录，用于回归和人工核对。
- 核心离线测试覆盖访客进入、提交问题、SQL 只读校验、SQLite 查询、结果返回和导出。
- 真实模型全量回归尚未完成，不能把“接口成功”或“SQL 可执行”当作“语义与数值正确”。

运行核心测试：

```bash
ASKDATA_ALLOW_MOCK_MODEL=true ./.venv/bin/python -m unittest \
  tests.test_mvp_surface \
  tests.test_phase4 \
  tests.test_api_e2e \
  tests.test_acceptance_questions \
  tests.test_security_regressions \
  tests.test_routing
```

## 10. 已知问题

- 模型生成 SQL 可能出现“可执行但语义错误”。
- 数据分析结论依赖 SQL 返回结果，错误 SQL 会进一步影响结论和图表。
- Serverless、外部数据库和真实模型调用受平台时限、网络与额度影响。

## 11. 继续开发前的优先级

1. 建立可独立验收的结构化意图表示。
2. 增加 SQL 与问题之间的语义一致性校验。
3. 使用带标准答案的真实模型回归集验证语义和数值正确性。
4. 在准确率稳定后，再评估记忆、仪表盘、多数据源和生产化能力。
