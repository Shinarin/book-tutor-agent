"""根据全书总结和原文，为每一章顺序生成深入浅出的教案。

Usage:
    python book_teach.py <book_path> [--summary-dir path] [--seqs 1,3,5] [--start-seq 5]
"""

import argparse
import asyncio
import json
import os
import sys
import time

from .agent_sdk import AgentOptions, default_model, format_event, run_agent

LLM_MODEL = os.environ.get("LLM_MODEL", default_model())

# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


CHAPTER_TEACHER_PROMPT = _load_prompt("chapter_teacher.md") # chapter_teacher_update_extension

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_chapters_from_progress(
    summary_dir: str, book_path: str
) -> list[tuple[int, str, int, int, str]]:
    """Load chapter boundaries from progress.json.

    Returns sorted list of (seq, title, start_line, end_line, summary_file_name).
    Line numbers are 0-based offsets into the book file.
    """
    progress_file = os.path.join(summary_dir, "progress.json")
    with open(progress_file, encoding="utf-8") as f:
        progress = json.load(f)

    with open(book_path, encoding="utf-8") as f:
        total_lines = sum(1 for _ in f)

    sorted_seqs = sorted(progress.keys(), key=int)
    results: list[tuple[int, str, int, int, str]] = []

    for seq_str in sorted_seqs:
        entry = progress[seq_str]
        seq = int(seq_str)
        title = entry["title"]
        file_name = entry["file_name"]
        next_offset = entry["next_offset"]

        prev_seq_str = str(seq - 1)
        if prev_seq_str in progress:
            prev_offset = progress[prev_seq_str]["next_offset"]
            start = 0 if prev_offset == "END" else int(prev_offset)
        else:
            start = 0

        end = total_lines if next_offset == "END" else int(next_offset)
        results.append((seq, title, start, end, file_name))

    return results


def detect_summary_dir(book_path: str) -> str | None:
    """Find summary dir: <book_dir>/summaries/ if it contains progress.json and summary files."""
    book_dir = os.path.dirname(os.path.abspath(book_path))
    summary_dir = os.path.join(book_dir, "summaries")
    progress_file = os.path.join(summary_dir, "progress.json")
    if not os.path.exists(progress_file):
        return None
    with open(progress_file, encoding="utf-8") as f:
        progress = json.load(f)
    for entry in progress.values():
        if os.path.exists(os.path.join(summary_dir, entry["file_name"])):
            return summary_dir
    return None


# ---------------------------------------------------------------------------
# Per-chapter agent
# ---------------------------------------------------------------------------


async def teach_chapter(
    seq: int,
    chapter_title: str,
    book_path: str,
    start_line: int,
    end_line: int,
    summaries_path: str,
    total: int,
    output_path: str,
) -> None:
    print(f"\n[开始] 第 {seq} chunk: {chapter_title}")
    start_time = time.time()

    prompt = (
        f"{CHAPTER_TEACHER_PROMPT}\n\n"
        f"---\n\n"
        f"summaries_path: {summaries_path}\n"
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


async def teach_book(
    summary_dir: str,
    book_path: str,
    seqs: list[int] | None = None,
    start_seq: int | None = None,
) -> None:
    summary_dir = os.path.abspath(summary_dir)
    book_path = os.path.abspath(book_path)

    chapters = load_chapters_from_progress(summary_dir, book_path)
    if not chapters:
        print("Error: 未能从 progress.json 加载chunk信息", file=sys.stderr)
        sys.exit(1)

    summaries_path = os.path.join(summary_dir, "all_summaries.md")
    if not os.path.exists(summaries_path):
        parts = []
        for _, _, _, _, file_name in chapters:
            sp = os.path.join(summary_dir, file_name)
            if os.path.exists(sp):
                with open(sp, encoding="utf-8") as f:
                    parts.append(f.read())
        with open(summaries_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(parts))
        print(f"已生成合并总结文件: {summaries_path}")

    teach_dir = os.path.dirname(book_path)
    os.makedirs(teach_dir, exist_ok=True)

    total = len(chapters)
    print(f"总结目录: {summary_dir}")
    print(f"原书: {book_path}")
    print(f"chunk数: {total}")
    print(f"输出目录: {teach_dir}\n")

    book_start = time.time()
    count = 0

    for seq, title, start_line, end_line, file_name in chapters:
        if start_seq is not None and seq < start_seq:
            print(f"[跳过] 第 {seq} chunk (小于 --start-seq {start_seq})")
            continue
        if seqs and seq not in seqs:
            print(f"[跳过] 第 {seq} chunk (不在指定列表中)")
            continue

        stem = os.path.splitext(file_name)[0]
        output_path = os.path.join(teach_dir, f"{seq:02d}-{stem}.md")
        await teach_chapter(
            seq=seq,
            chapter_title=title,
            book_path=book_path,
            start_line=start_line,
            end_line=end_line,
            summaries_path=summaries_path,
            total=total,
            output_path=output_path,
        )
        count += 1

    total_elapsed = time.time() - book_start

    print(f"\n{'='*60}")
    print(f"全部完成! 共 {count} chunk教案 | 总耗时: {total_elapsed:.1f}s | 输出: {teach_dir}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="根据全书总结和原文，顺序生成教案")
    parser.add_argument("book_path", help="原书 Markdown 文件路径")
    parser.add_argument("--summary-dir", default=None, help="总结输出目录路径 (默认: 书文件同目录下的 summaries/)")
    parser.add_argument("--seqs", type=str, default=None, help="只处理指定chunk，逗号分隔 (e.g. 1,3,5)")
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

    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None

    try:
        asyncio.run(teach_book(summary_dir, args.book_path, seqs, args.start_seq))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
