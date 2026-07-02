"""根据原文为每个 chunk 抽取细分目录骨架，再统一合并成全书索引。

Usage:
    python book_skeleton.py <book_path> [--summary-dir path] [--seqs 1,3,5] [--start-seq 5]
                                         [--output-dir path]
"""

import argparse
import asyncio
import os
import sys
import time

from .agent_sdk import AgentOptions, default_model, format_event, run_agent
from .book_teach import detect_summary_dir, load_chapters_from_progress
from .filename_utils import sanitize_filename

LLM_MODEL = os.environ.get("LLM_MODEL", default_model())

# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


CHAPTER_SKELETON_PROMPT = _load_prompt("chapter_skeleton.md")


# ---------------------------------------------------------------------------
# Per-chunk agent
# ---------------------------------------------------------------------------


async def skeleton_chapter(
    seq: int,
    chapter_title: str,
    book_path: str,
    start_line: int,
    end_line: int,
    output_path: str,
) -> None:
    print(f"\n[开始] 第 {seq} chunk: {chapter_title}")
    start_time = time.time()

    prompt = (
        f"{CHAPTER_SKELETON_PROMPT}\n\n"
        f"---\n\n"
        f"book_path: {book_path}\n"
        f"start_line: {start_line + 1}\n"
        f"end_line: {end_line}\n"
        f"chapter_title: {chapter_title}\n"
        f"output_path: {output_path}"
    )

    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Write"],
            model=LLM_MODEL,
            max_turns=30,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(output_path),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"[完成] 第 {seq} chunk: {chapter_title} | 耗时: {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def skeleton_book(
    summary_dir: str,
    book_path: str,
    output_dir: str,
    seqs: list[int] | None = None,
    start_seq: int | None = None,
) -> None:
    summary_dir = os.path.abspath(summary_dir)
    book_path = os.path.abspath(book_path)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    chapters = load_chapters_from_progress(summary_dir, book_path)
    if not chapters:
        print("Error: 未能从 progress.json 加载 chunk 信息", file=sys.stderr)
        sys.exit(1)

    total = len(chapters)
    print(f"总结目录: {summary_dir}")
    print(f"原书: {book_path}")
    print(f"chunk 数: {total}")
    print(f"骨架输出目录: {output_dir}\n")

    book_start = time.time()
    count = 0

    for seq, title, start_line, end_line, _ in chapters:
        stem = sanitize_filename(title)
        output_path = os.path.join(output_dir, f"{stem}.md")

        if (start_seq is not None and seq < start_seq) or (seqs and seq not in seqs):
            print(f"[跳过] 第 {seq} chunk")
            continue

        await skeleton_chapter(
            seq=seq,
            chapter_title=title,
            book_path=book_path,
            start_line=start_line,
            end_line=end_line,
            output_path=output_path,
        )
        count += 1

    total_elapsed = time.time() - book_start

    index_path = os.path.join(output_dir, "index.md")
    lines = ["# 全书 skeleton 章节次序\n", "按原书顺序列出。决定章节先后、相邻关系时以此为准。\n"]
    for seq, title, start_line, end_line, _ in chapters:
        stem = sanitize_filename(title)
        lines.append(f"{seq}. [{title}]({stem}.md)  [L{start_line + 1}-L{end_line}]")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"已生成索引: {index_path}")

    print(f"\n{'='*60}")
    print(f"chunk 骨架完成! 共处理 {count} chunk | 耗时: {total_elapsed:.1f}s")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="为每个 chunk 抽取细分目录骨架，并合并成全书索引")
    parser.add_argument("book_path", help="原书 Markdown 文件路径")
    parser.add_argument("--summary-dir", default=None, help="总结输出目录路径 (默认: 书文件同目录下的 summaries/)")
    parser.add_argument("--output-dir", default=None, help="骨架输出目录 (默认: 书文件同目录下的 skeleton/)")
    parser.add_argument("--seqs", type=str, default=None, help="只处理指定 chunk，逗号分隔 (e.g. 1,3,5)")
    parser.add_argument("--start-seq", type=int, default=None, help="从第 N chunk 开始处理 (断点续传)")
    args = parser.parse_args()

    if not args.book_path.endswith(".md"):
        print("Error: 只支持 .md 文件", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.book_path):
        print(f"Error: 原书文件不存在: {args.book_path}", file=sys.stderr)
        sys.exit(1)

    summary_dir = args.summary_dir
    if not summary_dir:
        summary_dir = detect_summary_dir(args.book_path)
        if not summary_dir:
            print("Error: 无法自动检测总结目录，请使用 --summary-dir 指定", file=sys.stderr)
            sys.exit(1)

    if not os.path.isdir(summary_dir):
        print(f"Error: 目录不存在: {summary_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.book_path)), "skeleton"
    )

    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None

    try:
        asyncio.run(
            skeleton_book(summary_dir, args.book_path, output_dir, seqs, args.start_seq)
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
