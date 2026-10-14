# 智悦校园（Smart Campus）

面向校园场景的 AI 问答伴侣：基于校规 / 手册等文档做 RAG 检索，用 LangGraph 做意图路由与多轮对话，前后端分离部署。

仓库描述：Smart Campus (智悦校园)  —— 旧版为 Streamlit 一体界面，当前主路径为 **Next.js + FastAPI**。

## 架构

```
浏览器
  │  HTTP + SSE
  ▼
Next.js 前端 (frontend/, :3000)     ← 界面与交互
  │
  ▼
FastAPI (backend/api.py, :8001)     ← 会话 / 聊天 / 文档
  │
  ├─ LangGraph (graph_app.py)       意图路由 + 生成
  ├─ ChromaDB                       向量检索
  ├─ Redis Stack                    多轮记忆
  └─ DeepSeek / Kimi / 智谱 Embedding …
```

| 服务 | 端口 | 说明 |
|------|------|------|
| `web` | 3000 | Next.js 前端 |
| `api` | 8001 | FastAPI 接口 |
| `redis` | 6380→6379 | Redis Stack（会话 checkpoint） |
| `app`（可选） | 8502 | 旧版 Streamlit，`--profile legacy` |

## 功能概览

- 校规 / 学生手册等知识库问答（RAG + 可选重排序）
- SSE 流式聊天、会话恢复（`thread_id` + Redis）
- 文档上传与向量库管理（txt / pdf / md / docx），以及网页导入（贴官网通知链接，服务端抓正文，拦截内网地址）
- 结构化规章维基（`wiki/`，LLM 维护的制度知识沉淀）
- 回答附来源卡片：文件名、相似度与命中片段，历史会话恢复后仍可展开
- 服务端长期记忆（Redis，按学校 + 用户隔离）：发「记住：xxx」手动记，或在情感分析时顺带自动抽取；换设备不丢
- 每校工作区（管理员）：助手名称、人设提示词、只按资料回答（查不到就拒答）、相似度阈值、检索条数、学校心理咨询联系方式
- 每校 AI 服务（管理员）：在「AI 服务」页配置学校自己的对话 / 精读模型，密钥加密入库；学生可自带密钥；朗读回答的声音与语速
- 情绪陪伴：倾诉时换成陪伴式回复（借鉴 ESConv 的 8 种支持策略与 SoulChat / EmoLLM / MeChat 的多轮共情写法，只借方法、不换模型）；
  出现伤害自己的念头时（关键词判定不依赖模型，情绪分析判高风险时再兜一层）附求助卡片：全国心理援助热线 12356、110 / 120、学校心理咨询，只给学生本人看，不通知任何人
- 嵌入挂件：学校官网贴一行脚本出现右下角聊天气泡，访客免登录，按来源域名白名单签发 2 小时令牌，可设每日上限、随时停用

学校官网嵌入代码（管理员在「工作区与嵌入挂件」里创建后可一键复制）：

```html
<script src="https://你的前端域名/embed.js" data-embed-id="挂件ID" defer></script>
```

访客只能问答：不能读写记忆、看文档列表或自带模型密钥；工作区与挂件设置最多 60 秒后生效。

## 目录结构

```
├── backend/          # FastAPI + LangGraph + RAG（含旧版 Streamlit）
├── frontend/         # Next.js 16 + React 19 + Tailwind CSS v4
├── data/             # 预载知识文档
├── wiki/             # 校规知识维基（sources / concepts / topics）
├── docker-compose.yml
├── requirements.txt
└── DEPLOY.md         # 云服务器 Docker 部署详解
```

## 快速开始（Docker）

1. 配置 `backend/.env`（可参考 `backend/.env.example`），填入 DeepSeek / Kimi / 智谱等密钥。
2. 配置根目录 `.env`（可参考 `.env.example`），填入 `POSTGRES_PASSWORD` 与 `AUTH_JWT_SECRET`。
   这两个变量未设置时 compose 会直接报错退出，`AUTH_JWT_SECRET` 由 `api` 与 `web` 共用，不会出现两端对不上的情况。
3. 按需修改 `docker-compose.yml` 中 `web.build.args.NEXT_PUBLIC_API_BASE`（浏览器能访问到的 API 地址），并同步 `api` 的 `FRONTEND_ORIGINS`。
4. 启动（`migrate` 会先建表，成功后 `web` 才起）：

```bash
docker compose up -d --build
```

5. 首次部署需要创建学校与管理员账号。Postgres 已映射到本机 5433，在 `frontend/` 下执行：

```bash
npm run db:seed
```

- 前端：http://localhost:3000  
- 健康检查：http://localhost:8001/api/health  

除健康检查外，所有 API 都要求登录；上传与清空知识库还需要 STAFF 或 ADMIN 角色。
升级已有部署时，`migrate` 会自动建好 `tenant_settings` 与 `embed_configs` 两张新表。

完整上云步骤（打包、Nginx、HTTPS）见 [DEPLOY.md](./DEPLOY.md)。

## 本地开发

### 后端

```powershell
cd backend
# 配置 .env 后
python -m uvicorn api:app --reload --port 8001
```

依赖见根目录 `requirements.txt`。Redis 建议用 compose 只起缓存：

```bash
docker compose up -d redis
# 本机连 Redis 时：REDIS_URL=redis://localhost:6380
```

### 前端

```powershell
cd frontend
copy .env.example .env.local
npm install
npm run dev
```

打开 http://localhost:3000 。更多说明见 [frontend/README.md](./frontend/README.md)。

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/sessions` | 新建会话 |
| GET | `/api/sessions/{tid}/messages` | 恢复历史 |
| POST | `/api/chat` | 提问（SSE） |
| GET/POST/DELETE | `/api/documents` | 文档列表 / 上传 / 清空 |
| POST | `/api/documents/url` | 导入网页（STAFF / ADMIN） |
| GET/POST | `/api/memories` | 我的长期记忆：列出 / 新增 |
| DELETE | `/api/memories/{id}` | 删除一条记忆 |
| GET | `/api/workspace` | 本校助手名称与模式（访客可读） |

以下由 Next（前端服务）提供，读写 Postgres：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/PUT | `/api/admin/workspace` | 工作区设置（ADMIN） |
| GET/POST | `/api/admin/embeds` | 挂件列表 / 创建（ADMIN） |
| PATCH/DELETE | `/api/admin/embeds/{id}` | 修改 / 删除挂件（ADMIN） |
| POST | `/api/embed/{id}/token` | 为白名单域名的访客签发令牌（CORS） |

## 相关文档

- [DEPLOY.md](./DEPLOY.md) — Docker / 服务器部署
- [backend/启动与部署指南.md](./backend/启动与部署指南.md) — 架构与 Redis 记忆原理
- [frontend/README.md](./frontend/README.md) — 前端开发说明
- [wiki/overview.md](./wiki/overview.md) — 规章维基总览

## License

未指定开源协议时，默认保留所有权利。如需开源请补充 `LICENSE`。
