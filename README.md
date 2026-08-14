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
- 文档上传与向量库管理（txt / pdf）
- 结构化规章维基（`wiki/`，LLM 维护的制度知识沉淀）

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
2. 按需修改 `docker-compose.yml` 中 `web.build.args.NEXT_PUBLIC_API_BASE`（浏览器能访问到的 API 地址），并同步 `api` 的 `FRONTEND_ORIGINS`。
3. 启动：

```bash
docker compose up -d --build
```

- 前端：http://localhost:3000  
- 健康检查：http://localhost:8001/api/health  

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

## 相关文档

- [DEPLOY.md](./DEPLOY.md) — Docker / 服务器部署
- [backend/启动与部署指南.md](./backend/启动与部署指南.md) — 架构与 Redis 记忆原理
- [frontend/README.md](./frontend/README.md) — 前端开发说明
- [wiki/overview.md](./wiki/overview.md) — 规章维基总览

## License

未指定开源协议时，默认保留所有权利。如需开源请补充 `LICENSE`。
