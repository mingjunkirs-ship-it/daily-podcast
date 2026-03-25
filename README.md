# AI Podcast Builder（Docker）

一个完整的可部署项目：

- 可在 Web 控制台配置信息源（`rss` / `arxiv` / `newsapi`）
- 内置推荐来源模板，一键导入（arXiv LLM、Agent、NewsAPI 等）
- 支持直接添加 RSS URL；也可通过 RSSHub 路由生成 RSS 后一键接入
- 内置 RSSHub（DIYgod/RSSHub）服务，便于将无 RSS 的页面转换为可订阅源
- 提供 RSSHub 路由模板，可一键套用常见站点路由
- 将来源统一转换为 RSS（每个来源一个 feed + 聚合 feed）
- 定时抓取并筛选 AI 相关内容
- 通过 LLM 进行摘要与播客脚本生成（Prompt 可在页面编辑）
- 通过 TTS 生成播客音频（默认使用 edge-tts，支持切换 custom_api）
- 通过 Telegram 富文本推送音频与参考材料
- 执行任务时显示阶段进度（抓取/过滤/总结/脚本/TTS/推送）与失败明细

> 默认定时为每天早上 `08:00`（Cron：`0 8 * * *`），可在页面中改。

---

## 1. 快速启动

```bash
docker compose up -d --build
```

访问：`http://localhost:26552`

本项目端口：

- 主应用：`http://localhost:26552`
- RSSHub：`http://localhost:26553`

首次登录默认账号：`admin` / `adminadmin`

登录后可在控制台 `账号安全` 区域直接修改管理员密码。

---

## 2. 配置流程（推荐）

1. 在 `全局设置` 中确认：
   - `language`（比如 `zh-CN`）
   - `timezone`（比如 `Asia/Shanghai`）
   - `schedule_cron`（比如每天 8 点：`0 8 * * *`）
   - `topic_keywords`（例如：`LLM,AI infra,benchmark,safety`）

2. 在 `LLM / TTS / Telegram` 中配置：
   - LLM（OpenAI 兼容接口）：`llm_api_base` + `llm_api_key` + `llm_model`
   - TTS 默认：`tts_provider=edge_tts`（免费，无需 key）
   - TTS 可选：`tts_provider=custom_api`（OpenAI 兼容接口）
   - TTS 接口模式由后端自动判断，无需手动选择
   - Telegram：`telegram_bot_token` + `telegram_chat_id`
   - 页面上可点击 `测试 LLM` / `测试 TTS` 判断连通性
   - 可在页面直接修改摘要/播客的 `System Prompt` 与 `Prompt Template`
   - 可保存/加载多套提示词版本（面板内一键切换）
   - 若 edge-tts 报 403，可在面板配置 `Edge Proxy` 与超时参数后重试（已升级到 edge-tts 7.x）
   - 小米 Mimo（`mimo-v2-tts`）推荐参数：
     - `tts_provider`: `custom_api`
     - `tts_api_base`: `https://api.xiaomimimo.com/v1`（不要填写到 `/chat/completions`）
     - `tts_model`: `mimo-v2-tts`
     - `tts_voice`: `default_zh`
     - `tts_audio_speed`: `1.0`

3. 在 `来源管理` 中新增来源：
   - 直接添加 RSS URL（推荐）
   - 或输入 RSSHub 路由（例如 `/github/repo/DIYgod/RSSHub/releases`）自动生成 RSS
   - 可先选择 RSSHub 模板自动填充路由
   - 每个来源支持单独点击 `测试来源` 验证可抓取性

4. 点击 `立即执行一次` 验证整条链路。

> 也可以在来源管理点击 `导入推荐来源`，快速导入预设的 AI 信息源。

---

## 3. 定时任务说明

- 调度器使用 Cron（5 段）：`分钟 小时 日 月 周`
- 例如：
  - 每天早上 8 点：`0 8 * * *`
  - 每天 8:00 和 20:00：`0 8,20 * * *`
  - 每个工作日 8 点：`0 8 * * 1-5`

更新 `schedule_cron` 与 `timezone` 后会立即重载任务。

---

## 4. 关键 API

- `GET /api/settings`：读取设置
- `POST /api/auth/change-password`：修改当前登录管理员密码
- `POST /api/test/llm`：测试 LLM 连接
- `POST /api/test/tts`：测试 TTS 连接
- `PUT /api/settings`：更新设置
- `GET /api/sources`：来源列表
- `POST /api/sources`：新增来源
- `POST /api/sources/rss`：通过 URL 新增 RSS 来源
- `POST /api/sources/rsshub`：通过 RSSHub 路由新增来源
- `GET /api/rsshub/templates`：获取 RSSHub 路由模板
- `POST /api/sources/{id}/test`：测试单个来源连通性
- `GET /api/source-presets`：查看内置来源模板
- `POST /api/sources/import-defaults`：导入内置来源模板
- `PUT /api/sources/{id}`：更新来源
- `DELETE /api/sources/{id}`：删除来源
- `POST /api/run-now`：手动执行采集+生成+推送
- `GET /api/episodes`：查看历史生成结果
- `POST /api/rebuild-feeds`：仅重建来源 RSS

RSS 访问：

- `GET /rss/sources/{id}.xml`
- `GET /rss/aggregated.xml`

---

## 5. 数据目录

项目使用本地 `./data` 持久化：

- `data/podcast.db`：SQLite 数据库
- `data/feeds/*.xml`：转换后的 RSS
- `data/audio/*`：生成的播客音频
- `data/notes/*`：每期参考材料（markdown）

---

## 6. 架构概览

- 后端：FastAPI + SQLAlchemy + APScheduler
- 抓取适配：RSS / arXiv API / NewsAPI
- 内容加工：LLM 摘要 + 播客脚本生成
- RSS 聚合：RSS / arXiv / NewsAPI + 内置 RSSHub（DIYgod/RSSHub）
- 音频：默认 edge-tts（微软语音），可切换 custom_api（OpenAI 兼容）
- 分发：Telegram Bot API
- 部署：Docker / Docker Compose

---

## 6.1 核心组件说明

- `RSSHub`：用于把没有原生 RSS 的网站转换成 RSS 链接，项目内已通过 Docker Compose 集成，可在 `http://localhost:26553` 访问。
- `edge-tts`：默认 TTS 引擎，无需单独 API Key；支持语言与音色选择。遇到网络限制（如 403）时，可在面板配置代理，或切换到 `custom_api`。

---

## 7. 生产建议

- 本项目已内置管理员登录；上线前务必修改默认账号密码与 `AUTH_SECRET`
- 可升级为企业级鉴权（OIDC / SSO / MFA）
- 将 API keys 放入密钥管理（不要长期明文存 DB）
- 对每个来源增加失败重试与速率限制
- 引入可观测性（结构化日志 / Prometheus / 告警）
- 使用 Postgres 替代 SQLite 以支持多实例

---

## 8. 鉴权环境变量

- `ADMIN_USERNAME`：默认管理员用户名（默认 `admin`）
- `ADMIN_PASSWORD`：默认管理员密码（默认 `adminadmin`）
- `AUTH_SECRET`：会话签名密钥（生产必须修改）
- `AUTH_SESSION_TTL_HOURS`：登录有效时长，默认 `48`
- `AUTH_COOKIE_SECURE`：是否仅 HTTPS 发送 Cookie（`true/false`）
