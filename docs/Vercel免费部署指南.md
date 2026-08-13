# 问数：Vercel 免费部署指南

这套部署运行的是完整 Python Agent，不是 Cloudflare 的简化演示版本。

## 一、先创建免费云数据库

1. 打开 https://neon.tech 并用 GitHub 登录。
2. 新建一个免费项目，区域选择离使用者较近的位置。
3. 在 Neon 的 Connect 页面复制以 `postgresql://` 开头的连接地址。
4. 不需要手动建表；问数首次启动时会自动初始化 6 张业务表和内部持久化表。

## 二、导入 Vercel

1. 打开 https://vercel.com/new 并用 GitHub 登录。
2. 选择仓库 `Aimme00/1`，点击 Import。
3. Framework Preset 保持 Other，Root Directory 保持仓库根目录。
4. 不填写 Build Command 和 Output Directory。

## 三、添加环境变量

在 Vercel 项目的 Environment Variables 中加入：

| 变量名 | 填写内容 |
|---|---|
| `ASKDATA_ENV` | `production` |
| `ASKDATA_DATABASE_TYPE` | `postgres` |
| `ASKDATA_DATABASE_ALIAS` | `trade_db` |
| `ASKDATA_POSTGRES_URL` | Neon 复制的 PostgreSQL 连接地址 |
| `ASKDATA_COOKIE_SECURE` | `true` |
| `ASKDATA_SESSION_SECRET` | 自己生成的至少 32 位随机字符串 |
| `ASKDATA_BOOTSTRAP_EMAIL` | 面试体验登录邮箱 |
| `ASKDATA_BOOTSTRAP_PASSWORD` | 至少 8 位的登录密码 |
| `ASKDATA_BOOTSTRAP_DISPLAY_NAME` | `Interview Demo` |
| `ASKDATA_LLM_PROVIDER` | `deepseek` |
| `DEEPSEEK_API_KEY` | 你的 DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/chat/completions` |
| `DEEPSEEK_COT_MODEL` | 你已开通的 DeepSeek 模型名 |
| `DEEPSEEK_CODER_MODEL` | 你已开通的 DeepSeek 模型名 |
| `ASKDATA_ALLOW_MOCK_MODEL` | `false` |
| `ASKDATA_REQUIRE_SQLGLOT` | `true` |
| `ASKDATA_GUEST_QUERY_LIMIT` | `2` |
| `ASKDATA_QUOTA_SALT` | 自己生成的随机字符串 |
| `ASKDATA_TEST_TOKEN` | 仅自己知道的测试码 |

不要将数据库地址、API Key、密码和测试码写进 GitHub 文件。

## 四、部署与测试

1. 点击 Deploy，等待构建完成。
2. 打开 Vercel 提供的 `*.vercel.app` 地址。
3. 使用设置的登录邮箱和密码登录。
4. 先测试：“查询总交易笔数大于 50000 的利率，并展示实际执行的 SQL”。
5. 再测试带图表的问题以及 CSV、Excel、PNG 下载。

访客按网络指纹限制为两次提问；在页面输入 `ASKDATA_TEST_TOKEN` 后，你自己可以不限次数测试。
公开访客可以共用体验邮箱和密码，但每次登录会获得独立会话空间，不会看到其他体验者保存的分析和仪表盘。

## 五、免费版注意事项

- 不购买域名也可以直接分享 `*.vercel.app` 地址。
- Vercel 和 Neon 的免费额度适合面试演示，但不是生产 SLA。
- DeepSeek API 调用仍按你的模型账户计费，两次提问限制用于控制成本。
- 如果 Vercel 显示 Function Timeout，说明一次 Agent 调用超过免费套餐允许的运行时间，应缩小问题范围或升级方案。
