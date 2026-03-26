# AI Podcast Builder（Docker）

一个可直接部署的 AI 播客自动化系统：

- 从多来源采集 AI 信息（RSS / arXiv / NewsAPI / RSSHub）
- 自动筛选、总结、生成播客脚本
- 默认使用 `edge-tts` 合成音频（可切换 `custom_api`）
- 通过 Telegram 推送文本材料与音频
- 提供 Web 管理面板（定时、提示词、来源、历史、测试）

---

## 功能概览

### 1) 全局设置

- 语言（下拉选择：主流语言）
- 时区
- 定时任务开关（启用/停用）
- Cron 定时表达式
- 自然语言转 Cron（如“每天早上8点”一键转换）
- 播客名称
- 一键测试 Cron 配置是否有效（显示未来触发时间）

### 2) LLM 配置

- OpenAI 兼容接口配置（`base_url / api_key / model / temperature`）
- 摘要 Prompt 与播客脚本 Prompt 可视化编辑
- 提示词版本保存 / 加载 / 删除
- LLM 连通性测试

### 3) TTS 配置

- 默认 `edge_tts`（无需 API Key）
- 可切换 `custom_api`（OpenAI 兼容）
- Edge 音色按语言分组选择
- “试听当前音色”按钮（固定播放“我是{音色名}”，如“我是雲龍”）
- edge-tts 版本检查（与 GitHub 最新版本对比并提示更新）
- TTS 连通性测试

### 4) 来源管理

- 默认来源为空（不自动导入）
- 支持直接添加 RSS URL
- 支持批量导入 RSS 配置（JSON，可让 AI 生成后粘贴导入）
- 支持 RSSHub 路由转换为 RSS
- 支持来源级关键词与条目上限
- 单来源测试 / 修改 / 启停 / 删除

### 5) 播客历史

- 查看每次生成记录、材料笔记、音频
- 单条删除
- 一键清空（数据库与文件同步删除）

### 6) 登录 / 注册 / 管理员

- 登录页支持注册（用户名 + 密码 + 确认密码）
- 注册后自动切回登录页，自动填充用户名（密码留空）
- 支持“开放注册”与“注册需管理员审核”开关
- 管理员支持用户列表、用户禁用/启用、删除用户、重置密码
- 管理员可审核待注册用户（通过 / 拒绝）

---

## 技术栈

- 后端：FastAPI + SQLAlchemy + APScheduler
- 前端：原生 HTML/CSS/JS
- 数据库：SQLite（`data/podcast.db`）
- RSS 聚合：RSSHub（Docker 内置）
- TTS：`edge-tts`（默认）/ `custom_api`
- 部署：Docker Compose

---

## 快速启动

```bash
docker compose up -d --build
```

访问：

- 主应用：`http://localhost:26552`
- RSSHub：`http://localhost:26553`

默认账号：

- 用户名：`admin`
- 密码：`adminadmin`

---

## 推荐使用流程

1. 登录后台，先配置 `LLM`、`TTS`、`Telegram`
2. 在“全局设置”里设置 `language / timezone / schedule_cron`
3. 需要自动运行时，确认“定时任务开关=启用”
4. 可先用自然语言转换 Cron，再点击“测试 Cron”确认
5. 在“来源管理”添加 RSS 或 RSSHub 路由
6. 点击“执行”触发一次，检查生成结果
7. 在“播客历史”查看音频与材料

---

## 数据目录

项目使用本地 `./data` 持久化：

- `data/podcast.db`：数据库
- `data/audio/`：播客音频
- `data/notes/`：材料笔记
- `data/feeds/`：转换后的 RSS

---

## 常用 API

- `GET /api/settings`
- `PUT /api/settings`
- `POST /api/test/cron`
- `POST /api/cron/from-natural`
- `POST /api/test/llm`
- `POST /api/test/tts`
- `GET /api/tts/edge-voices`
- `GET /api/tts/edge-version`
- `POST /api/test/edge-voice`
- `GET /api/sources`
- `POST /api/sources`
- `POST /api/sources/rss`
- `POST /api/sources/rsshub`
- `POST /api/sources/import-rss`
- `POST /api/sources/{id}/test`
- `PUT /api/sources/{id}`
- `DELETE /api/sources/{id}`
- `POST /api/run-now`
- `GET /api/episodes`
- `DELETE /api/episodes/{episode_id}`
- `DELETE /api/episodes`
- `GET /api/auth/register-options`
- `POST /api/auth/register`
- `GET /api/auth/users`
- `POST /api/auth/users/reset-password`
- `POST /api/auth/users/set-disabled`
- `DELETE /api/auth/users/{username}`
- `GET /api/auth/registrations/pending`
- `POST /api/auth/registrations/{id}/approve`
- `POST /api/auth/registrations/{id}/reject`

---

## 鉴权相关环境变量

- `ADMIN_USERNAME`（默认：`admin`）
- `ADMIN_PASSWORD`（默认：`adminadmin`）
- `AUTH_SECRET`（生产环境必须修改）
- `AUTH_SESSION_TTL_HOURS`（默认：`48`）
- `AUTH_COOKIE_SECURE`（`true/false`）
- `AUTH_ALLOW_REGISTER`（默认：`true`）
- `AUTH_REGISTER_REQUIRE_ADMIN_APPROVAL`（默认：`false`）

---

## Contributing

欢迎提交 Issue / PR。

在贡献代码前，请特别注意：

1. 本项目默认采用 **`edge-tts`** 作为 TTS 引擎，请确保改动不破坏：
   - `edge-tts` 音色列表加载
   - 音色试听接口（`/api/test/edge-voice`）
   - 版本检查接口（`/api/tts/edge-version`）
2. `edge-tts` 上游更新频繁，提交涉及 TTS 的变更时，请在 PR 中说明：
   - 本地测试的 `edge-tts` 版本
   - 是否验证过多语言音色映射
   - 是否验证过 Docker 重建后的行为
3. 如修改默认提示词，请同步考虑：
   - 首次初始化默认值（`DEFAULT_SETTINGS`）
   - 面板提示词变量说明与实际一致

---

## License

仅供学习与内部项目改造使用，请按你的组织规范补充正式许可证。
