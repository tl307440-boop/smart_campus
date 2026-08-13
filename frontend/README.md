# 智悦校园 · 前端

Next.js 16（App Router）+ React 19 + TypeScript + Tailwind CSS v4。
只负责界面与交互，所有模型能力通过 FastAPI 接口获取，可独立构建、独立部署。

## 本地开发

后端先跑起来（在 `../backend` 目录）：

```powershell
python -m uvicorn api:app --reload --port 8001
```

然后启动前端：

```powershell
copy .env.example .env.local
npm install
npm run dev
```

打开 http://localhost:3000 。

## 环境变量

| 变量 | 说明 |
|---|---|
| `NEXT_PUBLIC_API_BASE` | FastAPI 地址，默认 `http://localhost:8001`。浏览器直连，构建期被内联，改完要重新构建。前后端同域部署时置为空字符串，改用相对路径 `/api/...`。 |

后端需要把前端地址加进 `FRONTEND_ORIGINS`（CORS 白名单），默认已包含 `http://localhost:3000`。

## 目录结构

```
app/
  layout.tsx        字体、元信息
  page.tsx          聊天页面
  globals.css       暗场 + 金色主题
components/
  Composer.tsx      输入框（Enter 发送 / Shift+Enter 换行）
  ConsolePanel.tsx  侧栏：会话、语音开关、知识库
  MessageBubble.tsx 消息气泡、来源片段、情感结果
hooks/
  useChat.ts        会话恢复、SSE 收流、中断
lib/
  api.ts            接口封装与 SSE 解析
```

## 说明

- 会话 ID 存在地址栏 `?tid=` 与 localStorage，刷新后仍是同一段对话。
- 语音播报使用浏览器 `speechSynthesis`，不经过后端。
- 构建产物为 `output: "standalone"`，容器里直接 `node server.js`。
