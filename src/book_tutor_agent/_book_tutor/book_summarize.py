"""逐章总结一本 Markdown 格式的书，生成结构化的章节总结文件。

Usage:
    python book_summarize.py <book_path> [--context-size 1M] [--output-dir path]
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time

from pydantic import BaseModel

from .agent_sdk import AgentOptions, default_model, format_event, run_agent, run_agent_text
from .book_teach import load_chapters_from_progress
from .filename_utils import sanitize_filename

LLM_MODEL = os.environ.get("LLM_MODEL", default_model())

# ---------------------------------------------------------------------------
# Token / word helpers (ported from .claude/skills/book-summarize/tools/count-words.py)
# ---------------------------------------------------------------------------

TOKENS_PER_CJK = 1.5
TOKENS_PER_WORD = 1.3


def _count_words(text: str) -> tuple[int, int]:
    cjk = len(re.findall(r"[一-鿿㐀-䶿]", text))
    no_cjk = re.sub(r"[一-鿿㐀-䶿]", " ", text)
    eng = len(no_cjk.split())
    return cjk, eng


def estimate_tokens(text: str) -> int:
    cjk, eng = _count_words(text)
    return int(cjk * TOKENS_PER_CJK + eng * TOKENS_PER_WORD)


def count_file_tokens(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> int:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    if start_line is not None and end_line is not None:
        lines = lines[start_line:end_line]
    elif start_line is not None:
        lines = lines[start_line:]
    return estimate_tokens("".join(lines))


def tokens_to_words(token_budget: float) -> int:
    return int(token_budget / 1.4)


# ---------------------------------------------------------------------------
# Result parser
# ---------------------------------------------------------------------------


class ChapterResult(BaseModel):
    chapter_title: str
    next_offset: str


async def parse_result(text: str) -> tuple[str, str]:
    """Call the configured agent SDK to extract chapter_title and next_offset."""
    schema = ChapterResult.model_json_schema()
    schema["additionalProperties"] = False

    prompt = (
        "从 <agent_output> 中提取两个字段，严格以 JSON 格式返回，不要输出任何其他内容。\n\n"
        "返回格式（严格遵守，不要添加 markdown 代码块或额外文字）：\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "字段说明：\n"
        "- chapter_title: 章节标题\n"
        "- next_offset: 下一章起始行号（整数字符串）或 \"END\"\n\n"
        f"<agent_output>\n{text}\n</agent_output>"
    )

    result_text = await run_agent_text(
        prompt,
        AgentOptions(
            model=LLM_MODEL,
            max_turns=10,
            permission_mode="acceptEdits",
        ),
    )

    print(f"[parse_result] LLM response: {result_text}")
    try:
        result = ChapterResult.model_validate_json(result_text)
    except Exception as e:
        print(f"[parse_result] Invalid JSON: {e}")
        raise
    return result.chapter_title, result.next_offset


# ---------------------------------------------------------------------------
# Size parser
# ---------------------------------------------------------------------------


def parse_size(s: str) -> int:
    s = s.strip().upper()
    if s.endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    if s.endswith("K"):
        return int(float(s[:-1]) * 1_000)
    return int(s)


# ---------------------------------------------------------------------------
# Agent prompts (loaded from prompts/ directory)
# ---------------------------------------------------------------------------

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


CHAPTER_SUMMARIZER_PROMPT = _load_prompt("chapter_summarizer.md")
CHAPTER_COMPRESSOR_PROMPT = _load_prompt("chapter_compressor.md")

# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def summarize_book(
    book_path: str,
    context_size: int,
    output_dir: str,
    start_seq: int = 1,
    seqs: list[int] | None = None,
) -> None:
    book_path = os.path.abspath(book_path)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    total_tokens = count_file_tokens(book_path)
    budget = int(context_size * 0.6)
    ratio = min(1.0, budget / total_tokens) if total_tokens > 0 else 1.0

    print(f"Book: {book_path}")
    print(f"Total tokens (est.): {total_tokens}")
    print(f"Budget: {budget} tokens (context {context_size} × 60%)")
    print(f"Ratio: {ratio:.2f}")
    print(f"Output: {output_dir}\n")

    current_offset: int | str = 0
    seq = 1
    cwd = os.path.dirname(book_path)
    progress_file = os.path.join(output_dir, "progress.json")

    progress: dict[str, dict] = {}
    if os.path.exists(progress_file):
        with open(progress_file, encoding="utf-8") as f:
            progress = json.load(f)

    if start_seq > 1:
        key = str(start_seq - 1)
        if key in progress:
            val = progress[key]["next_offset"]
            current_offset = val if val == "END" else int(val)
            seq = start_seq
            print(f"Resuming from chunk {start_seq} (offset {current_offset})\n")
        else:
            print(f"Warning: no progress for chunk {start_seq - 1}, starting from beginning\n")

    book_start_time = time.time()

    while current_offset != "END":
        if seqs and seq not in seqs:
            key = str(seq)
            if key in progress:
                val = progress[key]["next_offset"]
                current_offset = val if val == "END" else int(val)
                print(f"[跳过] 第 {seq} chunk (不在指定列表中)")
                seq += 1
                continue
            break

        print(f"\n{'='*60}")
        print(f"[开始] 第 {seq} chunk | 起始偏移: {current_offset}")
        print(f"{'='*60}")
        chapter_start_time = time.time()

        prompt = (
            f"{CHAPTER_SUMMARIZER_PROMPT}\n\n"
            f"---\n\n"
            f"请处理以下参数：\n"
            f"book_file_path: {book_path}\n"
            f"start_offset: {current_offset}\n"
            f"seq: {seq}\n"
            f"output_dir: {output_dir}"
        )

        result_text = ""
        async for event in run_agent(
            prompt,
            AgentOptions(
                allowed_tools=["Read", "Write", "Glob"],
                model=LLM_MODEL,
                max_turns=30,
                permission_mode="acceptEdits",
                cwd=cwd,
            ),
        ):
            line = format_event(event)
            if line:
                print(line)
            if event.kind == "result":
                result_text = event.text

        summarize_elapsed = time.time() - chapter_start_time
        print(f"[总结完成] 第 {seq} chunk | 耗时: {summarize_elapsed:.1f}s")

        chapter_title, next_offset = await parse_result(result_text)
        file_name = f"{sanitize_filename(chapter_title)}.md"
        output_file = os.path.join(output_dir, file_name)

        tmp_file = os.path.join(output_dir, f"_chunk_{seq}.md")
        if os.path.exists(output_file):
            os.remove(output_file)
        os.rename(tmp_file, output_file)

        # Budget check
        end_line = None if next_offset == "END" else int(next_offset)
        chapter_tokens = count_file_tokens(
            book_path,
            int(current_offset) if isinstance(current_offset, int) else 0,
            end_line,
        )
        chapter_budget = chapter_tokens * ratio

        if os.path.exists(output_file):
            summary_tokens = count_file_tokens(output_file)
            print(f"[预算检查] 总结 {summary_tokens} tokens / 预算 {int(chapter_budget)} tokens (上限 {int(chapter_budget * 1.2)})")
            if summary_tokens > chapter_budget * 1.2:
                target_words = tokens_to_words(chapter_budget)
                print(f"[压缩] 第 {seq} chunk 超出预算，压缩目标: {target_words} 词")
                compress_start = time.time()
                compress_prompt = (
                    f"{CHAPTER_COMPRESSOR_PROMPT}\n\n"
                    f"---\n\n"
                    f"请处理以下参数：\n"
                    f"file_path: {output_file}\n"
                    f"target_words: {target_words}"
                )
                async for event in run_agent(
                    compress_prompt,
                    AgentOptions(
                        allowed_tools=["Read", "Write"],
                        model=LLM_MODEL,
                        max_turns=10,
                        permission_mode="acceptEdits",
                        cwd=cwd,
                    ),
                ):
                    line = format_event(event)
                    if line:
                        print(line)
                print(f"[压缩完成] 耗时: {time.time() - compress_start:.1f}s")
                compressed_tokens = count_file_tokens(output_file)
                print(f"[压缩后] tokens: {compressed_tokens} (预算: {chapter_budget:.0f})")

        chapter_elapsed = time.time() - chapter_start_time
        print(f"[完成] 第 {seq} chunk: {chapter_title} -> {file_name} | 总耗时: {chapter_elapsed:.1f}s")

        current_offset = next_offset if next_offset == "END" else int(next_offset)
        progress[str(seq)] = {
            "title": chapter_title,
            "file_name": file_name,
            "next_offset": current_offset,
        }
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        seq += 1

    total_elapsed = time.time() - book_start_time
    total_parts = seq - 1

    index_path = os.path.join(output_dir, "index.md")
    index_lines = ["# 全书 summaries 章节次序\n", "按原书顺序列出。决定章节先后、相邻关系时以此为准。\n"]
    for s, title, start_line, end_line, fname in load_chapters_from_progress(output_dir, book_path):
        stem = os.path.splitext(fname)[0]
        index_lines.append(f"{s}. [{title}]({stem}.md)  [L{start_line + 1}-L{end_line}]")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines) + "\n")
    print(f"已生成索引: {index_path}")

    print(f"\n{'='*60}")
    print(f"全部完成! 共 {total_parts} chunk | 总耗时: {total_elapsed:.1f}s | 输出: {output_dir}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="逐章总结一本 Markdown 格式的书")
    parser.add_argument("book_path", help="Markdown 书文件路径")
    parser.add_argument("--context-size", default="200k", help="Context 窗口大小 (e.g. 200k, 1M)")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("--start-seq", type=int, default=1, help="从第chunk开始 (断点续传)")
    parser.add_argument("--seqs", type=str, default=None, help="只处理指定chunk，逗号分隔 (e.g. 1,3,5)")
    args = parser.parse_args()

    if not args.book_path.endswith(".md"):
        print("Error: 只支持 .md 文件", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.book_path):
        print(f"Error: file not found: {args.book_path}", file=sys.stderr)
        sys.exit(1)

    context_size = parse_size(args.context_size)
    output_dir = args.output_dir or os.path.join(os.path.dirname(os.path.abspath(args.book_path)), "summaries")
    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None

    try:
        asyncio.run(summarize_book(args.book_path, context_size, output_dir, args.start_seq, seqs))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
