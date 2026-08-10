# AskData Cloudflare 一键体验部署指南

## 1. 已采用的部署结构

```text
访客浏览器
   │
   ├─ 静态页面、CSS、JS ── Cloudflare Pages
   │
   └─ /api/* ─────────── Pages Functions（Workers Runtime）
                              │
                              ├─ Cloudflare D1 演示数据库
                              └─ 阿里云百炼 Chat Completions API
```

Pages 和 API 使用同一个域名，因此不需要额外配置 CORS。项目保留原 Python/FastAPI 版本用于本地开发；Cloudflare 线上版本使用 JavaScript Pages Functions，实现相同的前端 API 协议。

线上 Demo 支持：访客自动登录、自然语言提问、Agent 步骤 SSE、Schema 提示、SQL 生成、只读 SQL 校验、D1 查询、确定性洞察、按需图表、下钻、保存分析、Dashboard、CSV 和 Excel 兼容格式下载。

## 2. GitHub 仓库要求

将包含以下文件的项目目录作为 GitHub 仓库根目录：

```text
web/
functions/
scripts/
tests/
package.json
wrangler.jsonc
```

不要上传 `.dev.vars`、`.env`、API Key、`node_modules` 或 `.wrangler`。项目的 `.gitignore` 已经排除这些文件。

## 3. 在 Cloudflare 连接 GitHub

1. 打开 Cloudflare Dashboard → **Workers & Pages**。
2. 选择 **Create application** → **Pages** → **Connect to Git**。
3. 选择 AskData 的 GitHub 仓库。
4. 项目名称填写 `askdata-agent-demo`，与 `wrangler.jsonc` 保持一致。
5. Framework preset 选择 `None`。
6. Build command 填写 `npm run build`。
7. Build output directory 填写 `web`。
8. Root directory：如果 GitHub 仓库根目录就是本项目，保持空白。
9. 保存并执行第一次部署。

之后每次向生产分支推送，Cloudflare 都会自动构建和发布；Pull Request/其他分支可以生成 Preview Deployment。

## 4. D1 数据库

`wrangler.jsonc` 已声明 `database_name=askdata-agent-demo`、binding `DB`。新版 Wrangler 的自动资源配置可以在首次 CLI 部署时补齐资源；为了让 GitHub → Pages 自动部署结果可重复，正式发布前仍建议先创建 D1 并把真实 ID 写入配置：

```bash
npx wrangler d1 create askdata-agent-demo
```

将命令返回的 `database_id` 添加到 `wrangler.jsonc` 的 `d1_databases[0]`。该 ID 不是密钥，可以提交到 GitHub。不要填写虚构 ID；当前仓库在尚未连接你的 Cloudflare 账号时只保留数据库名称。

如果使用 Dashboard 手动绑定：

1. Cloudflare Dashboard → **D1 SQL Database** → **Create database**。
2. 数据库名称填写 `askdata-demo`。
3. 回到 Pages 项目 → **Settings** → **Bindings** → **Add binding**。
4. 类型选择 D1 database，Variable name 必须填写 `DB`。
5. 选择刚创建的 `askdata-demo`，保存后重新部署。

不需要手工导入 SQL。线上第一次访问 API 时，Function 会幂等创建 6 张演示业务表、应用表和种子数据。

## 5. 配置模型 API Key

在 Pages 项目 → **Settings** → **Variables and Secrets** 添加：

| 名称 | 类型 | 必填 | 示例 |
| --- | --- | --- | --- |
| `DASHSCOPE_API_KEY` | Secret | 是 | `sk-...` |
| `DASHSCOPE_MODEL` | Variable | 否 | `qwen-plus` |
| `DASHSCOPE_BASE_URL` | Variable | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `ASKDATA_GUEST_QUERY_LIMIT` | Variable | 否 | `2` |

API Key 必须使用 Secret 类型，不要写进 `wrangler.jsonc` 或 GitHub。保存变量后重新部署一次。

如果你的 Key 属于专属 Workspace 或其他地域，请把 `DASHSCOPE_BASE_URL` 改成对应 OpenAI 兼容接口的 base URL，只保留到 `/v1`，不要附加 `/chat/completions`。

没有配置 Key 时网页也能运行，但 Worker 会使用内置安全规则生成 SQL，并在结果中明确提示未调用模型。

公开 Demo 默认按 Cloudflare 提供的访客网络地址限制总共 2 次新分析/钻取，可通过 `ASKDATA_GUEST_QUERY_LIMIT` 调整为 1–20。次数不会在第二天自动恢复。清除 Cookie、无痕模式或更换同一网络下的浏览器都不会重置额度。

Worker 使用 D1 内自动生成的随机盐和 HMAC-SHA256 生成网络标识，D1 不保存访客原始 IP。浏览器 Cookie 仍用于隔离会话、保存分析和 Dashboard。共享同一出口 IP 的访客会共享 2 次额度；切换网络或 VPN 仍可能获得新额度，这是不要求登录的公开 Demo 无法完全消除的边界。如果以后需要精确到自然人，应改用邮箱验证码、OAuth 或 Cloudflare Access 身份。

## 6. 验收

部署完成后依次验证：

1. 打开 Cloudflare 提供的 `*.pages.dev` 地址，应自动进入 `Demo Guest` 工作台。
2. 访问 `/api/health`，确认：

```json
{
  "ok": true,
  "runtime": "cloudflare-pages-functions",
  "database_bound": true,
  "model_configured": true
}
```

3. 提问“各区域销售额排名是什么？”，确认返回表格和只读 SQL。
4. 提问“生成最近30天销售额趋势图”，确认出现图表。
5. 提问“最近30天销售额趋势如何？”，不要勾选“生成图表”，确认只返回结论与表格。
6. 测试保存分析、添加到 Dashboard 和 CSV 下载。
7. 第二次提问后确认输入区显示“公开体验额度已用完”；清除 Cookie 后仍不能继续提问。

## 7. 域名与费用

Cloudflare 会免费提供 `*.pages.dev` 体验地址，因此面试 Demo 不买域名也能使用。需要品牌域名时，再进入 Pages 项目 → **Custom domains** 绑定即可。

当前设计面向 Free Plan：静态资源不经过 Function，只有 `/api/*` 使用 Workers 配额；演示数据量很小，D1 查询与写入也保持在免费额度的典型使用范围内。公开展示前建议在 Cloudflare 设置用量告警，并定期清理访客保存的数据。

## 8. 本地验证 Cloudflare 版本（可选）

```bash
npm install
cp .dev.vars.example .dev.vars
# 编辑 .dev.vars，填入自己的 Key
npm run dev:cloudflare
```

Wrangler 会启动 Pages、Functions 和本地 D1。直接双击 `web/index.html` 时仍然进入不依赖服务器的离线 Mock 体验，两种模式互不影响。
