# Book Tutor Agent

> 📚 PDF/EPUB/DOCX → Markdown → AI 总结 + 教案 + 知识点  
> 一键将任意文档转为学习材料，通过 MCP 协议在任意 Agent 工具中使用

---

## 功能

将任意格式的书籍/文档自动处理为：

| 产物 | 说明 |
|------|------|
| 📝 **Markdown 原文** | PDF/EPUB/DOCX → 结构化 Markdown |
| 📊 **逐章总结** | AI 自动切分章节并撰写摘要 |
| 🎓 **深度教案** | 通俗讲解 + 批判性审视 + 跨学科拓展 |
| 💡 **核心知识点** | 提炼关键概念 + 全书知识点总览 |
| 📄 **全书长文** | 基于总结生成的完整文章 |

## 快速开始

### 方式一：自然语言安装（推荐）

在你的 Agent 工具中直接说：

> "帮我安装 book-tutor-agent：pip install git+https://github.com/Shinarin/book-tutor-agent.git ，然后把 MCP 配置加到全局设置，再帮我注册 book-tutor skill"

Agent 自动完成 pip 安装 + MCP 配置 + SKILL 注册。重启即可用。

### 方式二：手动安装（分步操作）

**第 1 步 — pip 安装**

```powershell
pip install git+https://github.com/Shinarin/book-tutor-agent.git
```

安装后文件位于 `site-packages/book_tutor_agent/`，由 pip 统一管理。

**第 2 步 — 配置 MCP**

在 Agent 工具的 **全局** MCP 配置文件中，加入以下内容：

```json
{
  "book-tutor": {
    "command": "python",
    "args": ["-m", "book_tutor_agent"]
  }
}
```

具体操作（按工具选一个）：

| 工具 | 操作 |
|------|------|
| **VS Code** | 打开 `%APPDATA%\Code\User\mcp.json`（没有则新建），把上面 JSON 放入 `servers` 对象中 |
| **Claude Desktop** | 打开 `%APPDATA%\Claude\claude_desktop_config.json`，放入 `mcpServers` 对象中 |
| **Cursor** | 打开 `%APPDATA%\Cursor\User\mcp.json`，放入 `mcpServers` 对象中 |
| **Continue.dev** | 打开 `%USERPROFILE%\.continue\config.json`，放入 `experimental.mcpServers` 对象中 |
| **其他工具** | 搜 `工具名 mcp server config` 找到配置文件位置和键名，放入相同内容 |

> GitHub `configs/` 目录下有各工具的完整配置模板，可直接复制使用。

**第 3 步 — 注册 SKILL**

重启 Agent 工具后，在对话中说：

```
帮我注册 book tutor skill
```

Agent 会调用 `install_skill` 工具获取 SKILL 文件路径，然后用自己的 skill 模块完成注册。

> 也可自行安装：SKILL 文件位于 pip 安装目录下的 `book_tutor_agent/skills/book-tutor/SKILL.md`。
> 将该文件按 Agent 工具的要求放到对应 skills 目录即可。

**第 4 步 — 验证**

在对话中说：

```
book-tutor 有哪些可用的工具？
```

如果返回 9 个工具列表（convert_document、run_pipeline 等），说明安装成功。

### 使用

重启 Agent 工具后，对话中说：

```
"帮我处理这本 d:/books/国富论.pdf"
"第三章讲了什么？"
```

### 卸载

```powershell
pip uninstall book-tutor-agent -y
# 删除 MCP 配置中的 book-tutor 段即可
```

---

## 使用方式

| 方式 | 怎么用 | 适合 |
|------|--------|------|
| Agent 对话 | 在 VS Code / Claude / Cursor 中自然语言 | 日常主力 |
| 命令行 | `python run_pipeline.py book.pdf --stages summarize,teach,keypoints` | 批量、CI |

---

## MCP 工具列表（9 个）

| # | 工具 | 作用 | Token |
|---|------|------|-------|
| T1 | `convert_document` | 文档 → Markdown（自动检测 PDF 类型） | 仅 OCR |
| T2 | `run_pipeline` | **一键** 总结 + 教案 + 知识点 | ✅ |
| T3 | `summarize_chapter` | 重跑单章总结 | ✅ |
| T4 | `teach_chapter` | 讲解指定章节 | ✅ |
| T5 | `extract_keypoints` | 提炼知识点（seq=0 全书） | ✅ |
| T6 | `generate_article` | 生成全书长文 | ✅ |
| T7 | `get_book_progress` | 查进度/章节列表 | ❌ |
| T8 | `read_chapter_content` | 读已有内容 | ❌ |
| T9 | `install_skill` | 返回 SKILL 路径供注册 | ❌ |

---

## 项目结构

```
book-tutor-agent/
├── src/book_tutor_agent/    ← 唯一包（含所有子模块）
│   ├── server.py            ← 9 个 MCP Tool
│   ├── pipeline.py          ← 流水线核心
│   ├── pdf_detector.py      ← PDF 类型检测
│   ├── skills/book-tutor/   ← SKILL.md
│   ├── _markitdown/         ← 文档格式转换引擎
│   ├── _markitdown_ocr/     ← 视觉 LLM PDF 引擎
│   └── _book_tutor/         ← AI 加工引擎 + prompts/
├── configs/                  ← 多平台 MCP 配置模板
├── run_pipeline.py           ← CLI 入口
├── DEV.md                    ← 开发者文档
├── README.md
└── scripts/install.ps1       ← 一键安装
```

---

## 依赖

所有依赖在 `pip install` 时自动安装：

- Python 3.10+
- `claude-agent-sdk` — Claude Agent SDK（也支持 Codex）
- `mcp` — MCP Python SDK
- `pdfplumber` `PyMuPDF` `pillow` — PDF 处理
- `httpx` `python-dotenv` — 辅助

---

## 许可

MIT License © Book Tutor Agent

本项目集成了以下 MIT 许可的开源项目：
- [forestpeas/markitdown](https://github.com/forestpeas/markitdown) — 文档格式转换
- [forestpeas/book-tutor](https://github.com/forestpeas/book-tutor) — 书本学习材料生成
