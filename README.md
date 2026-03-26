# Daily Podcast

自动化 AI 信息采集与播客生成平台（Docker 一键部署）。

> 从 RSS / arXiv / NewsAPI 抓取内容 → LLM 归纳总结 → 生成播客文稿与音频（Edge TTS / 自定义 TTS）→ 推送到 Telegram。

---

## 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [使用流程](#使用流程)
- [多用户隔离说明](#多用户隔离说明)
- [API 概览](#api-概览)
- [开发指南](#开发指南)
- [故障排查](#故障排查)
- [贡献指南](#贡献指南)
- [致谢](#致谢)
- [许可证](#许可证)

---

## 功能特性

- 多来源采集：支持 `RSS`、`arXiv`、`NewsAPI`。
- 内容处理链路：抓取、去重、关键词过滤、LLM 摘要、播客脚本生成。
- TTS 合成：默认 `edge-tts`（免费），可切换 `custom_api`（OpenAI 兼容接口）。
- Telegram 推送：优先推送文本摘要和材料，再推送音频。
- Web 控制台：配置来源、提示词、LLM/TTS、Cron、历史记录、管理员功能。
- 定时任务：支持 Cron 表达式与自然语言转 Cron。
- 账号体系：注册/登录、管理员审核注册、用户禁用、重置密码。
- 历史管理：单条删除与清空（同时删除数据库记录和相关文件）。
- 提示词版本：保存/加载/删除不同提示词版本。
- Edge TTS 增强：音色分组、音色试听、edge-tts 版本检查提示。

---

## 技术栈

- Backend: `FastAPI`, `SQLAlchemy`, `APScheduler`
- Frontend: 原生 `HTML/CSS/JavaScript`
- Database: `SQLite`（默认）
- TTS: `edge-tts`（默认） / `custom_api`
- Runtime: `Docker`, `Docker Compose`

---

## 系统架构

1. **采集层**：从来源抓取内容并标准化。
2. **处理层**：去重、关键词筛选、生成摘要与播客脚本。
3. **生产层**：生成材料笔记与音频文件。
4. **分发层**：推送 Telegram（文本/材料/音频）。
5. **调度层**：按用户独立 Cron 触发任务。

---

## 项目结构

```text
.
├── app/
│   ├── main.py                 # FastAPI 入口与 API 路由
│   ├── models.py               # 数据模型（用户、来源、任务、设置）
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── services/
│   │   ├── pipeline.py         # 任务主流程
│   │   ├── scheduler.py        # 定时调度
│   │   ├── settings.py         # 系统/用户设置与迁移
│   │   ├── tts_client.py       # TTS 客户端
│   │   ├── llm_client.py       # LLM 客户端
│   │   ├── telegram_client.py  # Telegram 推送
│   │   └── source_adapters.py  # 来源抓取适配
│   └── static/
│       ├── index.html          # 主控制台
│       ├── app.js              # 前端逻辑
│       └── login.html          # 登录/注册页
├── data/                       # 持久化目录
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## 快速开始

### 1) 环境要求

- Docker 24+
- Docker Compose v2+

### 2) 克隆与启动

```bash
git clone https://github.com/mingjunkirs-ship-it/daily-podcast.git
cd daily-podcast
cp .env.example .env
docker compose up -d --build
```

访问地址：

- 应用：`http://localhost:26552`

默认管理员账号（首次启动自动创建）：

- 用户名：`admin`
- 密码：`adminadmin`

> 生产环境请务必修改 `AUTH_SECRET` 与管理员密码。

---

## 配置说明

### 1) 环境变量（`.env`）

- `DATABASE_URL`：数据库连接串（默认 SQLite）
- `AUTH_SECRET`：会话签名密钥
- `AUTH_SESSION_TTL_HOURS`：登录会话过期时间（小时）
- `AUTH_COOKIE_SECURE`：是否仅 HTTPS Cookie
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`：默认管理员
- `AUTH_ALLOW_REGISTER`：是否开放注册
- `AUTH_REGISTER_REQUIRE_ADMIN_APPROVAL`：注册是否需管理员审核

### 2) 面板配置（每个用户独立）

- LLM：`base_url` / `api_key` / `model` / `temperature`
- TTS：provider、voice、speed、超时参数
- Telegram：bot token、chat id、是否发送音频
- Prompt：摘要提示词、脚本提示词、版本管理
- Cron：启停、表达式、自然语言转换

---

## 使用流程

1. 登录后先配置 LLM 与 TTS。
2. 配置 Telegram（可选）。
3. 在来源管理中添加 RSS 或批量导入 JSON。
4. 测试来源连通性。
5. 点击“执行”触发一次任务。
6. 在播客历史查看总结、材料、音频。
7. 配置 Cron 并启用自动任务。

---

## 多用户隔离说明

- `sources`（来源）按用户隔离。
- `episodes`（历史）按用户隔离。
- 任务调度按用户独立创建 Job。
- 各用户的 LLM/TTS/Telegram/Cron/Prompt 配置互不影响。
- 新注册用户默认读取系统占位默认值（例如 OpenAI 默认占位），不会继承 admin 已填写的私有配置。
- 管理员删除用户时，会同步清理该用户设置、来源、历史与关联文件。

---

## API 概览

常用接口（完整定义见 `app/main.py`）：

- `GET /api/settings` / `PUT /api/settings`
- `POST /api/test/llm` / `POST /api/test/tts`
- `POST /api/test/cron` / `POST /api/cron/from-natural`
- `GET /api/sources` / `POST /api/sources/rss` / `POST /api/sources/import-rss`
- `POST /api/sources/{id}/test` / `PUT /api/sources/{id}` / `DELETE /api/sources/{id}`
- `POST /api/run-now`
- `GET /api/episodes` / `DELETE /api/episodes/{id}` / `DELETE /api/episodes`
- `POST /api/auth/login` / `POST /api/auth/register` / `POST /api/auth/logout`
- `GET /api/auth/users` / `POST /api/auth/users/reset-password` / `POST /api/auth/users/set-disabled`

---

## 开发指南

### 本地开发运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 代码质量自检

```bash
python -m compileall app
```

---

## 故障排查

- **启动后无法登录**：检查 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 与 `AUTH_SECRET`。
- **TTS 失败**：先在面板点击“测试 TTS”，Edge TTS 可先切换音色再试。
- **无内容生成**：检查来源连通性、关键词过滤是否过严。
- **定时不触发**：确认 `schedule_enabled=true`、Cron 与时区设置正确。

---

## 贡献指南

欢迎提交 Issue / PR。

建议流程：

1. Fork 并创建功能分支。
2. 提交最小化、可验证的改动。
3. 提交前执行 `python -m compileall app`。
4. 在 PR 描述中说明变更点、验证方式与影响范围。

### Edge TTS 相关贡献要求

本项目默认依赖 `edge-tts`，涉及 TTS 的改动请在 PR 里明确：

- 测试使用的 `edge-tts` 版本
- 是否验证音色拉取与试听接口
- 是否验证 Docker 重建后的行为

---

## 致谢

- [edge-tts](https://github.com/rany2/edge-tts)
- FastAPI / SQLAlchemy / APScheduler / Uvicorn

---

## 许可证

当前仓库暂未附带 `LICENSE` 文件。开源发布前建议补充明确许可证（如 MIT / Apache-2.0）。

