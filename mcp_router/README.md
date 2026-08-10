# mcp_router

MCP 路由执行模块。

## 目录结构

```text
mcp_router/
├── __init__.py
├── objects.py          # MCP执行请求和执行结果
├── sqlite_executor.py  # SQLite MCP执行器
└── router.py           # MCP路由器
```

## 执行请求格式

```json
{
  "database": "trade_db",
  "sql": "SELECT ..."
}
```
