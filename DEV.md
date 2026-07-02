# Book Tutor Agent — 开发者文档

> 面向 LLM Agent 维护者。阅读本文档后即可快速理解项目结构、功能边界、修改指南。

---

## 一、项目定位

将任意格式文档（PDF/EPUB/DOCX/PPTX）→ Markdown → AI 驱动的学习材料（总结/教案/知识点/长文/有声书）。

通过 **MCP 协议** 暴露为 9 个 Tool，可在 VS Code Copilot / Claude Desktop / Cursor / Continue 等任意 MCP 兼容的 Agent 工具中自然语言使用。

---

## 二、目录结构

```
book-tutor-agent/
├── pyproject.toml              ← pip 包定义 + 依赖
├── README.md                   ← 用户安装&使用文档
├── run_pipeline.py             ← CLI 入口（方案一）
├── LICENSE                     ← MIT + 上游署名
│
├── src/book_tutor_agent/       ← ★ 唯一 Python 包（pip install 后即为此包）
│   ├── __init__.py             ← 版本号
│   ├── __main__.py             ← MCP 入口：python -m book_tutor_agent
│   ├── server.py               ← 9 个 @mcp.tool() — MCP Server 核心
│   ├── pipeline.py             ← 流水线编排：summarize→teach→keypoints
│   ├── pdf_detector.py         ← PDF 文字/扫描型自动检测（零 LLM）
│   │
│   ├── skills/book-tutor/
│   │   └── SKILL.md            ← Agent behavior guide
│   │
│   ├── _markitdown/            ← 继承：文档格式转换引擎 (MIT)
│   │   ├── _markitdown.py      ← MarkItDown 主类
│   │   └── converters/         ← PDF/DOCX/EPUB/HTML... 各转换器
│   │
│   ├── _markitdown_ocr/        ← 继承：视觉 LLM PDF 引擎 (MIT)
│   │   ├── _pdf_converter_llm_full_page.py  ← 全页视觉 LLM 转录
│   │   ├── _pdf_llm_prompts.py             ← LLM prompt
│   │   └── scripts/glue_pages.py           ← 页面粘合（独立脚本）
│   │
│   └── _book_tutor/            ← 继承：AI 学习材料生成引擎 (MIT)
│       ├── agent_sdk.py        ← Claude/Codex 双后端适配
│       ├── book_summarize.py   ← 逐章总结
│       ├── book_teach.py       ← 逐章教案
│       ├── book_keypoints.py   ← 知识点提炼
│       ├── book_skeleton.py    ← 目录骨架
│       ├── book_breakdown.py   ← 全书框架拆解
│       ├── book_article.py     ← 长文生成
│       ├── book_audiobook.py   ← 有声书改写
│       ├── book_restyle.py     ← 文风调整
│       ├── book_tts.py         ← Azure TTS 合成
│       ├── book.py             ← 主编排器
│       ├── filename_utils.py   ← 文件名处理
│       └── prompts/            ← 16 个 Agent prompt 模板（关键依赖）
│
├── configs/                    ← 多平台 MCP 配置模板
│   ├── vscode-mcp.json
│   ├── claude-desktop-mcp.json
│   ├── cursor-mcp.json
│   └── continue-mcp.json
│
└── scripts/
    └── install.ps1             ← Windows 一键安装
```

---

## 三、架构分层

```
┌──────────────────────────────────────┐
│          MCP Tool 层 (server.py)       │  ← 9 个 @mcp.tool()，外部调用入口
│  convert_document / run_pipeline /    │
│  teach_chapter / extract_keypoints ...│
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│        流水线层 (pipeline.py)          │  ← 编排三阶段，断点续传，进度管理
│  run_pipeline()                      │
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│       引擎层 (_book_tutor/)           │  ← AI 加工：summarize/teach/keypoints
│  + Agent SDK (Claude/Codex 适配)      │
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│      转换层 (_markitdown/ + _ocr/)    │  ← 文档→MD，含 PDF 检测 & 视觉 LLM
│  MarkItDown / PdfConverterLLMFullPage │
└──────────────────────────────────────┘
```

---

## 四、MCP Tool 清单（9 个）

| # | 函数名 | 参数 | LLM? | 说明 |
|---|--------|------|------|------|
| T1 | `convert_document` | file_path, force_ocr? | 仅OCR | 文档→MD，自动 PDF 类型检测 |
| T2 | `run_pipeline` | book_path, context_size?, seqs?, start_seq? | ✅ | 一键三阶段，核心入口 |
| T3 | `summarize_chapter` | book_path, seq | ✅ | 单章总结 |
| T4 | `teach_chapter` | book_path, seq | ✅ | 单章教案 |
| T5 | `extract_keypoints` | book_path, seq? | ✅ | 知识点提炼 |
| T6 | `generate_article` | book_path | ✅ | 长文生成 |
| T7 | `get_book_progress` | book_path | ❌ | 进度查询（纯本地） |
| T8 | `read_chapter_content` | book_path, seq, content_type? | ❌ | 内容读取（纯本地） |
| T9 | `install_skill` | target_dir? | ❌ | 安装 SKILL 文件 |

---

## 五、数据流

```
用户 PDF
  │
  ├─ T1 convert_document()
  │     ├─ pdf_detector.detect_pdf_type()  (文字/扫描/混合)
  │     ├─ 文字型 → _markitdown.PdfConverter (pdfplumber)
  │     └─ 扫描型 → _markitdown_ocr.PdfConverterLLMFullPage (视觉 LLM)
  │     └─ 输出: xxx.md
  │
  ├─ T2 run_pipeline()
  │     ├─ 阶段1: _book_tutor.book_summarize.summarize_book()
  │     │     └─ 输出: summaries/progress.json + summaries/01-*.md
  │     ├─ 阶段2: _book_tutor.book_teach.teach_book()
  │     │     └─ 输出: 书籍目录下 *.md (教案)
  │     └─ 阶段3: _book_tutor.book_keypoints.keypoints_book()
  │           └─ 输出: keypoints/00-全书核心知识点.md
  │
  └─ T7/T8 随时查询进度/内容（零 Token）
```

---

## 六、关键设计决策

### 6.1 自包含
所有源码（含上游 markitdown + book-tutor）在同一个 `book_tutor_agent` 包内。`pip install` 只安装这一个包，`pip uninstall` 全部清理。

### 6.2 API 免配置
MCP Server 是宿主 Agent 的子进程，自动继承环境变量。`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` 多名称兜底。

### 6.3 零 LLM 检测
`pdf_detector.py` 使用 pdfplumber 字符数阈值判断 PDF 类型，不消耗 Token。

### 6.4 断点续传
`progress.json` 记录每阶段状态（running/done/error），中断后自动从未完成处恢复。

### 6.5 上游集成
`_markitdown/`、`_markitdown_ocr/`、`_book_tutor/` 三个 `_` 前缀子包为继承的上游代码（MIT），核心修改仅限于 import 路径适配。原始逻辑不改。

---

## 七、修改指南

### 新增 Tool
1. 在 `server.py` 中添加 `@mcp.tool()` 函数
2. 同步更新 `skills/book-tutor/SKILL.md` 的工具表
3. 更新 `README.md` 的 Tool 列表
4. 更新本 `DEV.md` 的 Tool 清单

### 新增流水线阶段
1. 在 `pipeline.py` 的 `run_pipeline()` 中追加阶段
2. 在 `progress.json` 的 `_pipeline` 字段中写入状态

### 修改上游功能
1. 找到 `_markitdown/` 或 `_book_tutor/` 中对应文件
2. 修改时注意 import 路径均为相对路径或 `book_tutor_agent._xxx` 格式
3. 测试 `pip install -e .` 后 import 通过

### 新增 Agent 工具支持
1. 在 `configs/` 下添加该工具的配置模板
2. 更新 `install.ps1` 和 `server.py` 的 `install_skill` 中的工具检测列表
3. 更新 `README.md` 的配置表

---

## 八、环境变量

| 变量 | 用途 | 必填 |
|------|------|------|
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | API 密钥 | 是 |
| `ANTHROPIC_BASE_URL` | 自定义 API 地址 | 否 |
| `BOOK_TUTOR_AGENT_PROVIDER` | claude/codex | 否 (默认 claude) |
| `LLM_MODEL` | 模型名 | 否 |
| `AZURE_SPEECH_KEY` | TTS (可选) | 否 |

---

## 九、许可

MIT License。集成的 `_markitdown/`、`_markitdown_ocr/`、`_book_tutor/` 均来自 MIT 许可项目，原始版权声明保留在各文件头部。

上游来源：
- [forestpeas/markitdown](https://github.com/forestpeas/markitdown) (Fork of microsoft/markitdown)
- [forestpeas/book-tutor](https://github.com/forestpeas/book-tutor)
