"""把 book_teach.py 产出的教案改写为适合朗读、用耳朵听的有声书文稿。

Usage:
    python book_audiobook.py <book_path> [--output-dir path]
"""

import argparse
import asyncio
import json
import os
import sys
import time

from .agent_sdk import AgentOptions, default_model, format_event, run_agent

LLM_MODEL = os.environ.get("LLM_MODEL", default_model())

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


BOOK_AUDIOBOOK_PROMPT = _load_prompt("book_audiobook.md")
BOOK_SSML_PROMPT = _load_prompt("book_ssml.md")


def detect_summary_dir(book_path: str) -> str | None:
    """书文件同目录下的 summaries/ 里有 progress.json 就认为是有效的总结目录。"""
    summary_dir = os.path.join(os.path.dirname(os.path.abspath(book_path)), "summaries")
    if os.path.exists(os.path.join(summary_dir, "progress.json")):
        return summary_dir
    return None


def load_teach_files(
    summary_dir: str,
    book_dir: str,
    seqs: list[int] | None = None,
    start_seq: int | None = None,
) -> list[str]:
    """从 progress.json 按 seq 顺序读取 file_name，定位到 book_dir 下的教案文件。

    seqs 非空时，只返回指定 seq 对应的教案。
    start_seq 非空时，跳过 seq < start_seq 的教案 (断点续传)。
    """
    with open(os.path.join(summary_dir, "progress.json"), encoding="utf-8") as f:
        progress = json.load(f)

    files: list[str] = []
    for seq_str in sorted(progress.keys(), key=int):
        seq = int(seq_str)
        if start_seq is not None and seq < start_seq:
            continue
        if seqs and seq not in seqs:
            continue
        name = progress[seq_str]["file_name"]
        teach_path = os.path.join(book_dir, name)
        if not os.path.isfile(teach_path):
            print(f"Warning: 教案文件不存在，跳过: {teach_path}", file=sys.stderr)
            continue
        files.append(teach_path)
    return files


async def audiobook_teach(teach_path: str, output_path: str, idx: int, total: int) -> None:
    print(f"\n[{idx}/{total}] 开始: {os.path.basename(teach_path)}")
    print(f"  输入: {teach_path}")
    print(f"  输出: {output_path}")

    prompt = (
        BOOK_AUDIOBOOK_PROMPT
        .replace("{teach_path}", teach_path)
        .replace("{output_path}", output_path)
    )

    start_time = time.time()
    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Write"],
            model=LLM_MODEL,
            max_turns=20,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(os.path.abspath(output_path)),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"[{idx}/{total}] 改写完成: {os.path.basename(teach_path)} | 耗时: {elapsed:.1f}s")

    #await ssml_annotate(output_path, idx, total)


async def ssml_annotate(audiobook_path: str, idx: int, total: int) -> None:
    """给听感稿原地加 SSML 标注 (多音字、易错分词等)，覆盖写回 audiobook_path。"""
    print(f"[{idx}/{total}] 开始 SSML 标注: {os.path.basename(audiobook_path)}")

    prompt = BOOK_SSML_PROMPT.replace("{audiobook_path}", audiobook_path)

    start_time = time.time()
    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Write"],
            model=LLM_MODEL,
            max_turns=20,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(os.path.abspath(audiobook_path)),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"[{idx}/{total}] SSML 标注完成: {os.path.basename(audiobook_path)} | 耗时: {elapsed:.1f}s")


async def audiobook_book(
    book_path: str,
    output_dir: str,
    summary_dir: str | None,
    seqs: list[int] | None = None,
    start_seq: int | None = None,
) -> None:
    book_path = os.path.abspath(book_path)
    book_dir = os.path.dirname(book_path)
    output_dir = os.path.abspath(output_dir)

    summary_dir = summary_dir or detect_summary_dir(book_path)
    if not summary_dir:
        print("Error: 无法自动检测总结目录 (期望 <book_dir>/summaries/progress.json)，请用 --summary-dir 指定", file=sys.stderr)
        sys.exit(1)
    summary_dir = os.path.abspath(summary_dir)
    if not os.path.isfile(os.path.join(summary_dir, "progress.json")):
        print(f"Error: progress.json 不存在: {summary_dir}", file=sys.stderr)
        sys.exit(1)

    teach_files = load_teach_files(summary_dir, book_dir, seqs, start_seq)
    if not teach_files:
        parts = []
        if seqs:
            parts.append(f"seqs={seqs}")
        if start_seq is not None:
            parts.append(f"start_seq={start_seq}")
        hint = f" (筛选 {', '.join(parts)})" if parts else ""
        print(f"Error: progress.json 中没有可用的教案文件{hint} (检查 {book_dir})", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    total = len(teach_files)
    print(f"原书: {book_path}")
    print(f"教案目录: {book_dir}")
    print(f"progress.json: {os.path.join(summary_dir, 'progress.json')}")
    print(f"听感稿输出目录: {output_dir}")
    print(f"找到 {total} 份教案")

    book_start = time.time()
    for idx, teach_path in enumerate(teach_files, 1):
        name = os.path.basename(teach_path)
        output_path = os.path.join(output_dir, name)
        await audiobook_teach(teach_path, output_path, idx, total)

    total_elapsed = time.time() - book_start
    print(f"\n{'='*60}")
    print(f"全部完成! 共 {total} 份听感稿 | 总耗时: {total_elapsed:.1f}s | 输出: {output_dir}")
    print(f"{'='*60}")


def _default_output_dir(book_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(book_path)), "audiobook")


def main() -> None:
    parser = argparse.ArgumentParser(description="把同目录下所有教案改写为适合朗读的有声书文稿")
    parser.add_argument("book_path", help="原书 Markdown 文件路径 (用来定位教案所在目录)")
    parser.add_argument("--summary-dir", default=None,
                        help="总结目录路径 (默认: <book_dir>/summaries/)")
    parser.add_argument("--output-dir", default=None,
                        help="听感稿输出目录 (默认: <book_dir>/audiobook/)")
    parser.add_argument("--seqs", type=str, default=None,
                        help="只处理指定 seq 的教案，逗号分隔 (e.g. 1 或 1,3,5)，方便先测一章")
    parser.add_argument("--start-seq", type=int, default=None,
                        help="从第 N chunk 开始处理 (断点续传)")
    args = parser.parse_args()

    if not args.book_path.endswith(".md"):
        print("Error: 只支持 .md 文件", file=sys.stderr)
        sys.exit(1)

    book_path = os.path.abspath(args.book_path)
    if not os.path.isfile(book_path):
        print(f"Error: 原书文件不存在: {book_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = os.path.abspath(args.output_dir or _default_output_dir(book_path))
    if output_dir == os.path.dirname(book_path):
        print("Error: 输出目录不能与教案目录相同", file=sys.stderr)
        sys.exit(1)

    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None

    try:
        asyncio.run(audiobook_book(book_path, output_dir, args.summary_dir, seqs, args.start_seq))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
