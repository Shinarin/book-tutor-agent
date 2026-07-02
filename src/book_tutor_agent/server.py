"""
Book Tutor Agent — MCP Server
8 个 Tool，通过 MCP 协议暴露给任意 Agent 工具（VS Code / Claude Desktop / Cursor 等）。
"""

from mcp.server.fastmcp import FastMCP
import os
import sys

# pip install 后所有模块在 site-packages 中，无需额外 path。
# 保留以下作为从源码直接运行时的兜底。
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

mcp = FastMCP("book-tutor")


# ---------------------------------------------------------------------------
# API 配置辅助
# ---------------------------------------------------------------------------

def _ensure_api():
    """确保 API 环境变量已设置。同时兼容 Anthropic 和 OpenAI 协议。"""
    api_key = (os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN")
               or os.environ.get("OPENAI_API_KEY"))
    if api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
        os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", api_key)
        os.environ.setdefault("OPENAI_API_KEY", api_key)
        return True
    return False


def _require_api():
    """要求 API，未配置时返回友好提示。"""
    if not _ensure_api():
        return (
            "⚠️ 未检测到 API Key。\n\n"
            "在 MCP 配置的 env 字段中设置（支持 Anthropic 或 OpenAI 协议）：\n"
            "  Anthropic:\n"
            '    "ANTHROPIC_API_KEY": "sk-ant-xxx"\n'
            '    "ANTHROPIC_BASE_URL": "http://your-proxy:8080/v1"  // 可选\n'
            "  OpenAI / 兼容:\n"
            '    "OPENAI_API_KEY": "sk-xxx"\n'
            '    "OPENAI_BASE_URL": "http://your-proxy:8080/v1"  // 可选\n'
            '    "BOOK_TUTOR_AGENT_PROVIDER": "codex"\n'
            "\n"
            "无需 API 的查询工具（get_book_progress、read_chapter_content、install_skill）始终可用。"
        )
    return None


def _resolve_book_path(book_path: str) -> str:
    """解析并验证书籍路径。"""
    path = os.path.abspath(os.path.expanduser(book_path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    return path


# ---------------------------------------------------------------------------
# T1: convert_document — 文档 → Markdown
# ---------------------------------------------------------------------------

@mcp.tool()
async def convert_document(file_path: str, force_ocr: bool = False) -> str:
    """将 PDF/EPUB/DOCX/PPTX 等任意文档转换为 Markdown。

    自动检测 PDF 是文字型还是扫描型（图片型），选择最优转换策略。
    扫描型 PDF 会使用视觉 LLM 逐页转录（消耗较大）。

    Args:
        file_path: 文档的绝对路径
        force_ocr: 强制使用 OCR（视觉 LLM）模式，即使检测为文字型

    Returns:
        转换结果摘要
    """
    file_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(file_path):
        return f"❌ 文件不存在: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    md_path = os.path.splitext(file_path)[0] + ".md"

    # 如果已经是 .md，直接返回
    if ext == ".md":
        return f"✅ 文件已是 Markdown 格式: {file_path}"

    try:
        from book_tutor_agent._markitdown import MarkItDown

        # PDF 类型检测
        ocr_mode = force_ocr
        if ext == ".pdf" and not force_ocr:
            from book_tutor_agent.pdf_detector import detect_pdf_type
            detection = detect_pdf_type(file_path)
            ocr_mode = detection["mode"] in ("image", "mixed")
            mode_label = {
                "text": "文字型 → 标准提取",
                "image": "扫描型 → 视觉 LLM 转录",
                "mixed": "混合型 → 视觉 LLM 转录",
            }.get(detection["mode"], "未知")
        else:
            mode_label = "标准转换"

        md = MarkItDown()
        result = md.convert(file_path)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(result.text_content)

        size_kb = round(os.path.getsize(md_path) / 1024, 1)
        return (
            f"✅ 转换完成\n"
            f"   模式: {mode_label}\n"
            f"   输出: {md_path}\n"
            f"   大小: {size_kb} KB"
        )

    except ImportError:
        return "❌ markitdown 模块未正确安装，请运行 pip install -e ."
    except Exception as e:
        return f"❌ 转换失败: {e}"


# ---------------------------------------------------------------------------
# T2: run_pipeline — 一键流水线
# ---------------------------------------------------------------------------

@mcp.tool()
async def run_pipeline(
    book_path: str,
    context_size: str = "200k",
    seqs: str = "",
    start_seq: int = 1,
) -> str:
    """一键处理全书：逐章总结 + 生成教案 + 提炼知识点。

    这是最常用的入口。三阶段自动顺序执行，支持断点续传。

    Args:
        book_path: Markdown 书籍文件路径
        context_size: LLM 上下文窗口大小 (默认 "200k")
        seqs: 只处理指定章节，逗号分隔如 "1,3,5" (默认空=全部)
        start_seq: 从第几章开始，用于断点续传

    Returns:
        处理结果摘要
    """
    _ensure_api()
    api_err = _require_api()
    if api_err:
        return api_err
    book_path = _resolve_book_path(book_path)

    seqs_list = None
    if seqs.strip():
        try:
            seqs_list = [int(s.strip()) for s in seqs.split(",") if s.strip()]
        except ValueError:
            return "❌ seqs 格式错误，应为逗号分隔的数字，如: 1,3,5"

    try:
        from book_tutor_agent.pipeline import run_pipeline as _run

        result = await _run(
            book_path=book_path,
            context_size=context_size,
            seqs=seqs_list,
            start_seq=start_seq,
        )

        return (
            f"✅ 全书处理完成！\n"
            f"   章节数: {result['chapter_count']}\n"
            f"   总结目录: {result['output_dir']}\n"
            f"   知识点目录: {result['keypoints_dir']}\n"
            f"   总耗时: {result['elapsed_seconds']} 秒\n\n"
            "你可以说「讲第N章」来听教案，或问「核心知识点是什么」来查看重点。"
        )
    except FileNotFoundError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ 流水线执行失败: {e}"


# ---------------------------------------------------------------------------
# T3: summarize_chapter — 单章总结
# ---------------------------------------------------------------------------

@mcp.tool()
async def summarize_chapter(book_path: str, seq: int) -> str:
    """重跑指定章节的总结。

    Args:
        book_path: Markdown 书籍文件路径
        seq: 章节编号 (从 1 开始)

    Returns:
        总结结果
    """
    api_err = _require_api()
    if api_err:
        return api_err
    book_path = _resolve_book_path(book_path)

    try:
        from book_tutor_agent._book_tutor.book_summarize import parse_size, summarize_book

        output_dir = os.path.join(os.path.dirname(book_path), "summaries")
        await summarize_book(book_path, parse_size("200k"), output_dir, seq, [seq])
        return f"✅ 第 {seq} 章总结完成。文件在 {output_dir}/"
    except Exception as e:
        return f"❌ 总结失败: {e}"


# ---------------------------------------------------------------------------
# T4: teach_chapter — 单章教案
# ---------------------------------------------------------------------------

@mcp.tool()
async def teach_chapter(book_path: str, seq: int) -> str:
    """生成/读取指定章节的教案，用通俗易懂的方式讲解。

    如果教案已存在则直接返回内容，否则生成新的。

    Args:
        book_path: Markdown 书籍文件路径
        seq: 章节编号 (从 1 开始)

    Returns:
        教案内容（前 2000 字）或生成状态
    """
    # 先检查是否已有教案（不需要 API）
    book_path = _resolve_book_path(book_path)

    # 先检查是否已有教案
    from book_tutor_agent._book_tutor.book_teach import detect_summary_dir, load_chapters_from_progress
    summary_dir = detect_summary_dir(book_path)
    if summary_dir:
        chapters = load_chapters_from_progress(summary_dir, book_path)
        for s, title, _, _, fname in chapters:
            if s == seq:
                teach_path = os.path.join(os.path.dirname(book_path), fname)
                if os.path.isfile(teach_path):
                    with open(teach_path, encoding="utf-8") as f:
                        content = f.read()
                    preview = content[:2000]
                    suffix = "...(内容已截断)" if len(content) > 2000 else ""
                    return f"📖 第 {seq} 章: {title}\n\n{preview}{suffix}"
                break

    # 无已有教案，生成新的
    try:
        from book_tutor_agent._book_tutor.book_teach import teach_book
        await teach_book(summary_dir or os.path.join(os.path.dirname(book_path), "summaries"),
                         book_path, [seq], None)
        return f"✅ 第 {seq} 章教案已生成，在书籍同目录下。"
    except Exception as e:
        return f"❌ 教案生成失败: {e}"


# ---------------------------------------------------------------------------
# T5: extract_keypoints — 知识点提炼
# ---------------------------------------------------------------------------

@mcp.tool()
async def extract_keypoints(book_path: str, seq: int = 0) -> str:
    """提炼全书或指定章节的核心知识点。

    Args:
        book_path: Markdown 书籍文件路径
        seq: 章节编号，0 表示全书 (默认 0)

    Returns:
        知识点内容或状态
    """
    api_err = _require_api()
    if api_err:
        return api_err
    book_path = _resolve_book_path(book_path)

    book_dir = os.path.dirname(book_path)
    keypoints_dir = os.path.join(book_dir, "keypoints")

    # 检查是否已有全书知识点
    if seq == 0:
        master_path = os.path.join(keypoints_dir, "00-全书核心知识点.md")
        if os.path.isfile(master_path):
            with open(master_path, encoding="utf-8") as f:
                content = f.read()
            preview = content[:2000]
            suffix = "..." if len(content) > 2000 else ""
            return f"📚 全书核心知识点:\n\n{preview}{suffix}"

    try:
        from book_tutor_agent._book_tutor.book_keypoints import keypoints_book
        seqs = [seq] if seq > 0 else None
        await keypoints_book(
            os.path.join(book_dir, "summaries"),
            book_path, keypoints_dir, seqs, None,
            skip_unify=(seq > 0),
        )
        return f"✅ 知识点已生成在 {keypoints_dir}/"
    except Exception as e:
        return f"❌ 知识点提炼失败: {e}"


# ---------------------------------------------------------------------------
# T6: generate_article — 生成长文
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_article(book_path: str) -> str:
    """基于已有总结生成全书长文。

    依赖: 需要先完成 run_pipeline (至少 summarize 阶段)。

    Args:
        book_path: Markdown 书籍文件路径

    Returns:
        生成结果
    """
    api_err = _require_api()
    if api_err:
        return api_err
    book_path = _resolve_book_path(book_path)

    book_dir = os.path.dirname(book_path)
    article_path = os.path.join(book_dir, "article.md")

    if os.path.isfile(article_path):
        return f"📄 长文已存在: {article_path}"

    try:
        from book_tutor_agent._book_tutor.book_article import write_article
        await write_article(
            book_path,
            os.path.join(book_dir, "breakdown.md"),
            os.path.join(book_dir, "summaries"),
            os.path.join(book_dir, "skeleton"),
            article_path,
        )
        return f"✅ 长文已生成: {article_path}"
    except Exception as e:
        return f"❌ 长文生成失败: {e}"


# ---------------------------------------------------------------------------
# T7: get_book_progress — 查进度（纯本地，零 LLM）
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_book_progress(book_path: str) -> str:
    """查询书籍处理进度和章节列表。

    纯本地读取 progress.json，不消耗 LLM Token。

    Args:
        book_path: Markdown 书籍文件路径

    Returns:
        进度详情
    """
    book_path = _resolve_book_path(book_path)
    book_dir = os.path.dirname(book_path)

    progress_file = os.path.join(book_dir, "summaries", "progress.json")
    if not os.path.isfile(progress_file):
        return (
            "📭 尚未开始处理。\n"
            "使用 run_pipeline 一键处理，或告诉我「帮我读这本书」。"
        )

    try:
        import json
        with open(progress_file, encoding="utf-8") as f:
            progress = json.load(f)

        pipeline = progress.get("_pipeline", {})
        chapters = sorted(
            [(int(k), v) for k, v in progress.items() if k.isdigit()],
            key=lambda x: x[0],
        )

        lines = ["📊 书籍处理进度:\n"]

        # 流水线状态
        for stage in ["summarize", "teach", "keypoints"]:
            info = pipeline.get(stage, {})
            status = info.get("status", "未开始")
            icon = {"done": "✅", "running": "🔄", "error": "❌"}.get(status, "⬜")
            lines.append(f"  {icon} {stage}: {status}")

        # 章节列表
        lines.append(f"\n📑 章节 ({len(chapters)} 章):")
        for seq, entry in chapters[:20]:  # 最多显示 20 章
            title = entry.get("title", "未知")
            fname = entry.get("file_name", "")
            has_teach = os.path.isfile(os.path.join(book_dir, fname))
            teach_mark = "📖" if has_teach else "  "
            lines.append(f"  {seq:>3}. {teach_mark} {title}")

        if len(chapters) > 20:
            lines.append(f"  ... 共 {len(chapters)} 章")

        # 产物检查
        lines.append(f"\n📁 产物检查:")
        lines.append(f"  summaries/: {'✅' if os.path.isdir(os.path.join(book_dir, 'summaries')) else '⬜'}")
        lines.append(f"  keypoints/: {'✅' if os.path.isdir(os.path.join(book_dir, 'keypoints')) else '⬜'}")

        return "\n".join(lines)

    except Exception as e:
        return f"❌ 读取进度失败: {e}"


# ---------------------------------------------------------------------------
# T8: read_chapter_content — 读已有内容（纯本地，零 LLM）
# ---------------------------------------------------------------------------

@mcp.tool()
async def read_chapter_content(
    book_path: str,
    seq: int,
    content_type: str = "teach",
) -> str:
    """读取指定章节已生成的内容。

    纯本地文件读取，不消耗 LLM Token。

    Args:
        book_path: Markdown 书籍文件路径
        seq: 章节编号
        content_type: 内容类型 - "teach"(教案), "summary"(总结), "keypoints"(知识点)

    Returns:
        章节内容（截取前 3000 字）
    """
    book_path = _resolve_book_path(book_path)
    book_dir = os.path.dirname(book_path)

    from book_tutor_agent._book_tutor.book_teach import detect_summary_dir, load_chapters_from_progress

    # 查找章节信息
    summary_dir = detect_summary_dir(book_path)
    if not summary_dir:
        return "❌ 尚未处理，请先运行 run_pipeline。"

    chapters = load_chapters_from_progress(summary_dir, book_path)
    chapter_info = None
    for s, title, _, _, fname in chapters:
        if s == seq:
            chapter_info = (s, title, fname)
            break

    if not chapter_info:
        return f"❌ 未找到第 {seq} 章。可用章节请用 get_book_progress 查看。"

    s, title, fname = chapter_info

    # 根据类型确定文件路径
    if content_type == "summary":
        file_path = os.path.join(book_dir, "summaries", fname)
    elif content_type == "keypoints":
        file_path = os.path.join(book_dir, "keypoints", fname)
    else:  # teach
        file_path = os.path.join(book_dir, fname)

    if not os.path.isfile(file_path):
        return f"❌ {content_type} 文件不存在: {os.path.basename(file_path)}"

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    preview = content[:3000]
    suffix = "...(内容已截断)" if len(content) > 3000 else ""

    type_label = {"teach": "教案", "summary": "总结", "keypoints": "知识点"}.get(
        content_type, content_type
    )

    return f"📖 第 {seq} 章: {title} ({type_label})\n\n{preview}{suffix}"


# ---------------------------------------------------------------------------
# T9: install_skill — 安装 SKILL 文件（通用，不限 Agent 工具）
# ---------------------------------------------------------------------------

@mcp.tool()
async def install_skill() -> str:
    """返回 Book Tutor SKILL 文件的路径，供 Agent 工具的 skill 安装机制使用。

    不要自行复制文件。将返回的路径交给你的 skill 安装模块处理。
    如果 Agent 工具没有 skill 安装模块，请完整读取该文件内容并注册为 skill。

    Returns:
        SKILL.md 的绝对路径
    """
    from pathlib import Path

    skill_path = Path(__file__).parent / "skills" / "book-tutor" / "SKILL.md"
    if not skill_path.exists():
        return f"❌ SKILL 文件未找到: {skill_path}"

    return (
        f"📄 SKILL 文件路径: {skill_path}\n\n"
        "请调用你的 skill 安装机制来注册此 skill。\n"
        "如果当前 Agent 工具没有 skill 安装模块，请完整读取上述文件内容并注册。"
    )
