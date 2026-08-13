# 问数：Vercel + Neon 部署指南

这套部署运行完整 Python Agent。公开访客无需登录，每个浏览器会获得独立会话，并按网络指纹限制 2 次提问。

## 必填的 3 个环境变量

| 变量 | 值 |
|---|---|
| `ASKDATA_POSTGRES_URL` | Neon 的 pooled connection string |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `ASKDATA_SESSION_SECRET` | 至少 32 位的随机字符串 |

不要在 Vercel 批量导入 `.env.example`；其他变量都有安全默认值。已导入的空变量可以保留，新版会将它们视为“未设置”。

## 可选配置

- `ASKDATA_BOOTSTRAP_EMAIL` 和 `ASKDATA_BOOTSTRAP_PASSWORD`：需要管理员登录时同时设置，密码至少 8 位。
- `ASKDATA_TEST_TOKEN`：仅自己知道的测试码，用于解除 2 次限制。
- `ASKDATA_GUEST_QUERY_LIMIT`：默认 `2`。

## 部署

1. Vercel 导入 `Aimme00/1`，Root Directory 保持 `./`。
2. Application Preset 选 FastAPI，不要填 Build Command 和 Output Directory。
3. 添加上面 3 个必填变量，点击 Deploy / Redeploy。
4. 打开 `*.vercel.app/health`，出现 `{"status":"ok"}` 后再打开首页。

首次冷启动会自动在 Neon 建立 6 张业务表和 Agent 持久化表。不需要自己执行 SQL。

## 上线前验收

1. 无痕窗口直接打开首页，不需要输入账号密码。
2. 查询“总交易笔数大于 50000 的利率，展示 SQL”。
3. 测试 CSV、Excel 和有图表时的 PNG 下载。
4. 第 3 次提问应明确提示额度用完。

Vercel 和 Neon 免费额度适合面试演示；DeepSeek API 仍按你的模型账户计费。
