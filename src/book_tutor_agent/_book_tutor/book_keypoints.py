"""根据全书总结和原文，为每一章顺序提炼核心知识点，再统一合并成一份总览。

Usage:
    python book_keypoints.py <book_path> [--summary-dir path] [--seqs 1,3,5] [--start-seq 5]
                                          [--output-dir path] [--skip-unify] [--only-unify]
"""

import argparse
import asyncio
import json
import os
import sys
import time

from .agent_sdk import AgentOptions, default_model, format_event, run_agent
from .book_teach import detect_summary_dir, load_chapters_from_progress

LLM_MODEL = os.environ.get("LLM_MODEL", default_model())

# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


CHAPTER_KEYPOINTS_PROMPT = _load_prompt("chapter_keypoints.md")
KEYPOINTS_UNIFIER_PROMPT = _load_prompt("keypoints_unifier.md")


# ---------------------------------------------------------------------------
# Per-chapter agent
# ---------------------------------------------------------------------------


async def keypoints_chapter(
    seq: int,
    chapter_title: str,
    book_path: str,
    start_line: int,
    end_line: int,
    summaries_path: str,
    output_path: str,
) -> None:
    print(f"\n[开始] 第 {seq} chunk: {chapter_title}")
    start_time = time.time()

    prompt = (
        f"{CHAPTER_KEYPOINTS_PROMPT}\n\n"
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
            max_turns=20,
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
# Unify agent
# ---------------------------------------------------------------------------


def unify_keypoints_code(
    keypoints_paths: list[str],
    book_title: str,
    output_path: str,
) -> None:
    print(f"\n[开始统一-代码模式] 共 {len(keypoints_paths)} 个章节知识点文件")
    start_time = time.time()

    parts: list[str] = [f"# {book_title} · 全书核心知识点\n"]
    for p in keypoints_paths:
        with open(p, encoding="utf-8") as f:
            parts.append(f.read().rstrip())
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")

    elapsed = time.time() - start_time
    print(f"[统一完成-代码模式] 耗时: {elapsed:.1f}s | 输出: {output_path}")


async def unify_keypoints(
    keypoints_paths: list[str],
    book_title: str,
    output_path: str,
) -> None:
    print(f"\n[开始统一] 共 {len(keypoints_paths)} 个章节知识点文件")
    start_time = time.time()

    prompt = (
        f"{KEYPOINTS_UNIFIER_PROMPT}\n\n"
        f"---\n\n"
        f"keypoints_paths: {','.join(keypoints_paths)}\n"
        f"book_title: {book_title}\n"
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
    print(f"[统一完成] 耗时: {elapsed:.1f}s | 输出: {output_path}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def keypoints_book(
    summary_dir: str,
    book_path: str,
    output_dir: str,
    seqs: list[int] | None = None,
    start_seq: int | None = None,
    skip_unify: bool = False,
    only_unify: bool = False,
    unify_mode: str = "code",
) -> None:
    summary_dir = os.path.abspath(summary_dir)
    book_path = os.path.abspath(book_path)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

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

    total = len(chapters)
    print(f"总结目录: {summary_dir}")
    print(f"原书: {book_path}")
    print(f"chunk数: {total}")
    print(f"知识点输出目录: {output_dir}\n")

    book_start = time.time()
    count = 0
    all_keypoints_paths: list[str] = []

    for seq, title, start_line, end_line, file_name in chapters:
        stem = os.path.splitext(file_name)[0]
        output_path = os.path.join(output_dir, f"{stem}.md")

        if only_unify or (start_seq is not None and seq < start_seq) or (seqs and seq not in seqs):
            if os.path.exists(output_path):
                all_keypoints_paths.append(output_path)
            print(f"[跳过] 第 {seq} chunk")
            continue

        await keypoints_chapter(
            seq=seq,
            chapter_title=title,
            book_path=book_path,
            start_line=start_line,
            end_line=end_line,
            summaries_path=summaries_path,
            output_path=output_path,
        )
        if os.path.exists(output_path):
            all_keypoints_paths.append(output_path)
        count += 1

    total_elapsed = time.time() - book_start
    print(f"\n{'='*60}")
    print(f"章节知识点完成! 共处理 {count} chunk | 耗时: {total_elapsed:.1f}s")
    print(f"{'='*60}")

    if skip_unify:
        print("已跳过统一合并步骤 (--skip-unify)")
        return

    existing_paths = [p for p in all_keypoints_paths if os.path.exists(p)]
    if not existing_paths:
        print("Error: 未找到任何知识点文件，跳过统一", file=sys.stderr)
        return

    book_title = os.path.splitext(os.path.basename(book_path))[0]
    unified_path = os.path.join(os.path.dirname(book_path), "00-全书核心知识点.md")
    if unify_mode == "llm":
        await unify_keypoints(existing_paths, book_title, unified_path)
    else:
        unify_keypoints_code(existing_paths, book_title, unified_path)

    print(f"\n{'='*60}")
    print(f"全部完成! 章节知识点 + 统一总览 | 输出: {output_dir}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="根据全书总结和原文，顺序提炼核心知识点并统一合并")
    parser.add_argument("book_path", help="原书 Markdown 文件路径")
    parser.add_argument("--summary-dir", default=None, help="总结输出目录路径 (默认: 书文件同目录下的 summaries/)")
    parser.add_argument("--output-dir", default=None, help="知识点输出目录 (默认: 书文件同目录下的 keypoints/)")
    parser.add_argument("--seqs", type=str, default=None, help="只处理指定chunk，逗号分隔 (e.g. 1,3,5)")
    parser.add_argument("--start-seq", type=int, default=None, help="从第 N chunk 开始处理 (断点续传)")
    parser.add_argument("--skip-unify", action="store_true", help="跳过最后统一合并步骤")
    parser.add_argument("--only-unify", action="store_true", help="只跑统一合并步骤，跳过所有章节处理")
    parser.add_argument("--unify-mode", choices=["code", "llm"], default="code", help="统一合并方式: code=直接代码拼接(默认), llm=调用LLM合并")
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
        os.path.dirname(os.path.abspath(args.book_path)), "keypoints"
    )

    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None

    if args.skip_unify and args.only_unify:
        print("Error: --skip-unify 与 --only-unify 不能同时使用", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(
            keypoints_book(
                summary_dir, args.book_path, output_dir, seqs, args.start_seq,
                args.skip_unify, args.only_unify, args.unify_mode,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
