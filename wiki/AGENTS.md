# 智悦校园 · LLM Wiki Schema

本目录是校园规章的**持久维基层**：介于 `data/` 原始 PDF 与线上 RAG 之间。
Agent 负责写入与维护；人类负责策展来源与提问。线上服务只读，不在运行期改维基。

## 三层结构

| 层 | 路径 | 规则 |
|---|---|---|
| Raw sources | `../data/` | 不可变。只读，禁止改写原始 PDF。 |
| Wiki | `wiki/`（本目录） | Agent 全权维护的 markdown。 |
| Schema | 本文件 `AGENTS.md` | 约定结构与工作流；随实践共进化。 |

## 目录约定

```
wiki/
  AGENTS.md          # 本 schema
  overview.md        # 总览与导航
  index.md           # 内容目录（查询时先读）
  log.md             # 时间线（append-only）
  sources/           # 每份原始文档一份摘要页
  entities/          # 学校、部门、角色等实体
  concepts/          # 可复用概念（奖学金、休学、绩点…）
  topics/            # 综合主题 / 对照 / FAQ 沉淀
  _raw_extract/      # 从 PDF 抽出的纯文本（衍生缓存，可重建）
```

## 页面格式

每页顶部 YAML frontmatter：

```yaml
---
title: 页面标题
type: source | entity | concept | topic | overview
tags: []
sources: []          # 关联的原始文件名
updated: YYYY-MM-DD
status: current | superseded | pending-ocr | stub
---
```

正文约定：

- 用 `[[页面名]]` 做维基内链（文件名不含路径与扩展名时，按同名页面解析）。
- 关键数字、金额、比例、时限必须带来源标注，例如 `(来源: [[奖助学金管理办法-2025]])`。
- 新旧冲突写在「矛盾 / 待核实」小节，并链到 `topics/新旧规定对照.md`。
- 不要把 watermark / 页眉页脚噪声写进正文。

## 工作流

### Ingest（摄入）

1. 用户把文件放进 `data/`，并告知要 ingest。
2. 抽出文本（已有则用 `_raw_extract/`）；扫描件无文字层则标 `pending-ocr`，先建 stub。
3. 与用户确认要点（可选）。
4. 写/更新 `sources/<简称>.md`。
5. 更新相关 `entities/`、`concepts/`、`topics/`（一份源常触及 5–15 页）。
6. 更新 `index.md`；在 `log.md` **末尾追加**一条：
   `## [YYYY-MM-DD] ingest | 标题`
7. 若新规废止旧规，把旧页 `status` 改为 `superseded`，并在对照页记录。

### Query（查询）

1. 先读 `index.md`，再钻入相关页。
2. 回答带引用；有价值的综合答案可回填为新 `topics/` 页。
3. 在 `log.md` 追加 `query` 条目（可选，重要问答建议记）。

### Lint（体检）

定期检查：矛盾、过期主张、孤儿页、提到但无专页的概念、缺失交叉引用、可用网页补全的缺口。

## 权威优先级

回答学生办事问题时，按以下顺序取数：

1. **现行校发文件**（有明确文号与施行日期，如武文理〔2025〕14 号）
2. **上级文件**（教育部 / 省财政厅等）用于解释国家标准与名额逻辑
3. **学生手册汇编**（多为截至某年的合集；其中已被修订版废止的章节不得当现行依据）

手册里出现「江汉大学文理学院」旧称时，视为历史沿革文本，现行校名是「武汉文理学院」。

## 与线上系统的边界

- 维基维护发生在开发侧（Cursor / git）。
- 线上 RAG：`knowledge.py` 启动时 autoload `wiki/`（排除 `AGENTS.md`、`log.md`、`_raw_extract/`），并剥离 YAML frontmatter 后再索引；文档名形如 `wiki/concepts/国家奖学金.md`。
- 禁止让线上聊天模型直接改写本目录。
