# 问数完整 Python Agent：Render 免费部署

这套方案部署的是完整 FastAPI/Python Agent，而不是 Cloudflare 的简化 JavaScript 演示后端。

## 免费方案边界

- 使用 Render Free Web Service 和免费 `onrender.com` HTTPS 地址，无需购买域名。
- 闲置约 15 分钟后服务会休眠，首次唤醒通常需要等待约 1 分钟。
- 免费实例没有持久磁盘；重新部署或重启后，登录会话、历史分析和仪表盘会重置，内置演示数据库会自动重建。
- 每个访客网络默认只能提问或钻取 2 次；持有测试码的项目所有者不限次数。免费实例重启或重新部署后计数会重置。

## 部署

1. 将整个项目（包括仓库根目录的 `render.yaml`）推送到 GitHub。
2. 登录 Render，选择 **New + → Blueprint**，连接包含本项目的 GitHub 仓库。
3. Render 识别 `render.yaml` 后，填写四个不会写进 GitHub 的值：
   - `DEEPSEEK_API_KEY`：你的 DeepSeek API Key。
   - `ASKDATA_BOOTSTRAP_EMAIL`：面试体验登录邮箱，不要使用 `demo@askdata.local`。
   - `ASKDATA_BOOTSTRAP_PASSWORD`：至少 8 位的面试体验密码，不要使用 `askdata-demo`。
   - `ASKDATA_TEST_TOKEN`：仅自己测试时使用的复杂测试码。
4. 确认实例类型为 **Free**，点击 **Apply / Deploy Blueprint**。
5. 等待部署状态变为 Live，打开 Render 提供的 `https://...onrender.com` 地址。
6. 使用第 3 步设置的邮箱和密码登录并测试。

## 面试分享

把 `onrender.com` 链接、体验邮箱和体验密码一起发给面试官。不要分享 DeepSeek Key 或测试码。面试前先访问一次链接并登录，可提前唤醒免费实例。

## 更新

之后每次推送到 GitHub 主分支，Render 会自动重新构建并部署。部署后的 `/health` 返回 `{"status":"ok"}` 即表示服务可用。
