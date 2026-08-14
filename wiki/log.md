# Wiki Log

Append-only. 格式：`## [YYYY-MM-DD] <op> | <title>`

## [2026-08-14] bootstrap | 初始化 wiki 结构

创建 `AGENTS.md`、`overview.md`、`index.md` 与目录 `sources/` `entities/` `concepts/` `topics/`。从 `data/` 抽取 PDF 文本到 `_raw_extract/`。

- 可抽取文字：学生手册（187434 字 / 266 页）、学籍管理实施办法（15401 字）、奖助学金管理办法 2025（6717 字，含水印噪声）。
- 无文字层（pending-ocr）：本专科生国家奖学金评审办法；湖北省学生资助资金管理办法。

## [2026-08-14] ingest | 武汉文理学院奖（助）学金管理办法（2025年修订版）

文号武文理〔2025〕14 号，2025-06-13 施行；废止 2022-09 修订版。写入 source、概念页与 [[奖助学金体系]]。

## [2026-08-14] ingest | 武汉文理学院学籍管理实施办法（2025）

文号武文理教〔2025〕20 号，2025-03-25 施行。写入 source、学籍相关概念与 [[学籍管理要点]]。发现与 2022 手册最长学制等冲突，记入 [[新旧规定对照]]。

## [2026-08-14] ingest | 学生手册（2022 制度汇编）

建立合集 source 页与学校实体页；标记其中学籍/奖助学金章节为历史参考，不以之为现行依据。

## [2026-08-14] ingest | 扫描件 stub

为两份无文字层 PDF 建立 pending-ocr source stub，待 OCR 后补全。

## [2026-08-14] integrate | wiki → RAG

`file_service.py` 支持 `.md`；`knowledge.py` autoload `wiki/`（跳过 AGENTS.md、log.md、`_raw_extract/`，剥离 frontmatter）。单元测试：`TestWikiHelpers`。

## [2026-08-14] ocr | 两份上级扫描件

RapidOCR + pypdfium2 抽出国家奖学金评审办法（6 页）与鄂财教发〔2023〕58 号（85 页）。回填 source/concept；发现省办法金额 8000/5000 与校 2025 的 10000/6000 冲突，记入对照页。
