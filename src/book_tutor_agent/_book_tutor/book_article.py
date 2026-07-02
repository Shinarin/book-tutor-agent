"""根据 breakdown / summaries / skeleton / 原书，生成一篇全书长文。

Usage:
    python book_article.py <book_path> \\
        [--breakdown-path path] [--summaries-path path] [--skeleton-path path] [--output path]
"""

import argparse
import asyncio
import os
import sys
import time

from .agent_sdk import AgentOptions, default_model, format_event, run_agent

LLM_MODEL = os.environ.get("LLM_MODEL", default_model())

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


BOOK_ARTICLE_PROMPT = _load_prompt("book_article_lite.md")
BOOK_TRANSLATE_PROMPT = _load_prompt("book_translate.md")
BOOK_REFLECT_PROMPT = _load_prompt("book_reflect.md")


async def write_article(
    book_path: str,
    breakdown_path: str,
    summaries_path: str,
    skeleton_path: str,
    output_path: str,
) -> None:
    print(f"原书:      {book_path}")
    print(f"breakdown: {breakdown_path}")
    print(f"summaries: {summaries_path}")
    print(f"skeleton:  {skeleton_path}")
    print(f"输出:      {output_path}\n")

    prompt = (
        BOOK_ARTICLE_PROMPT
        .replace("{book_path}", book_path)
        .replace("{breakdown_path}", breakdown_path)
        .replace("{summaries_path}", summaries_path)
        .replace("{skeleton_path}", skeleton_path)
        .replace("{output_path}", output_path)
    )

    import subprocess
    subprocess.run("clip", input=prompt.encode("utf-16-le"), check=True)
    print("prompt 已复制到剪切板")
    return

    start_time = time.time()
    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep"],
            model=LLM_MODEL,
            max_turns=80,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(os.path.abspath(book_path)),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"长文生成完成! 耗时: {elapsed:.1f}s | 输出: {output_path}")
    print(f"{'='*60}")

    #await reflect_article(output_path)
    #await translate_article(output_path)


async def reflect_article(article_path: str) -> None:
    print(f"\n[独立思考] 开始追加独立思考与延伸思考: {article_path}\n")

    prompt = BOOK_REFLECT_PROMPT.replace("{article_path}", article_path)

    start_time = time.time()
    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Edit", "Write"],
            model=LLM_MODEL,
            max_turns=20,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(os.path.abspath(article_path)),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"独立思考完成! 耗时: {elapsed:.1f}s | 输出: {article_path}")
    print(f"{'='*60}")


async def translate_article(article_path: str) -> None:
    print(f"\n[翻译] 开始审校并中文化: {article_path}\n")

    prompt = BOOK_TRANSLATE_PROMPT.replace("{article_path}", article_path)

    start_time = time.time()
    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Edit", "Write"],
            model=LLM_MODEL,
            max_turns=20,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(os.path.abspath(article_path)),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"翻译完成! 耗时: {elapsed:.1f}s | 输出: {article_path}")
    print(f"{'='*60}")


def _default_breakdown(book_path: str) -> str:
    book_dir = os.path.dirname(os.path.abspath(book_path))
    stem = os.path.splitext(os.path.basename(book_path))[0]
    return os.path.join(book_dir, f"{stem}_breakdown.md")


def _default_summaries(book_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(book_path)), "summaries")


def _default_skeleton(book_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(book_path)), "skeleton")


def _default_output(book_path: str) -> str:
    book_dir = os.path.dirname(os.path.abspath(book_path))
    stem = os.path.splitext(os.path.basename(book_path))[0]
    return os.path.join(book_dir, f"{stem}_article.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="根据 breakdown / summaries / skeleton 生成一篇 ≤10000 字全书长文")
    parser.add_argument("book_path", help="原书 Markdown 文件路径")
    parser.add_argument("--breakdown-path", default=None,
                        help="breakdown 文件路径 (默认: <book_dir>/<book_stem>_breakdown.md)")
    parser.add_argument("--summaries-path", default=None,
                        help="summaries 目录路径，包含每章 summary 文件 (默认: <book_dir>/summaries/)")
    parser.add_argument("--skeleton-path", default=None,
                        help="skeleton 目录路径，包含每章 skeleton 文件 (默认: <book_dir>/skeleton/)")
    parser.add_argument("--output", default=None,
                        help="文章输出路径 (默认: <book_dir>/<book_stem>_article.md)")
    args = parser.parse_args()

    if not args.book_path.endswith(".md"):
        print("Error: 只支持 .md 文件", file=sys.stderr)
        sys.exit(1)

    book_path = os.path.abspath(args.book_path)
    if not os.path.isfile(book_path):
        print(f"Error: 原书文件不存在: {book_path}", file=sys.stderr)
        sys.exit(1)

    breakdown_path = os.path.abspath(args.breakdown_path or _default_breakdown(book_path))
    if not os.path.isfile(breakdown_path):
        print(f"Error: breakdown 文件不存在: {breakdown_path}", file=sys.stderr)
        sys.exit(1)

    summaries_path = os.path.abspath(args.summaries_path or _default_summaries(book_path))
    if not os.path.isdir(summaries_path):
        print(f"Error: summaries 目录不存在: {summaries_path}", file=sys.stderr)
        sys.exit(1)

    skeleton_path = os.path.abspath(args.skeleton_path or _default_skeleton(book_path))
    if not os.path.isdir(skeleton_path):
        print(f"Error: skeleton 目录不存在: {skeleton_path}", file=sys.stderr)
        sys.exit(1)

    output_path = os.path.abspath(args.output or _default_output(book_path))

    try:
        asyncio.run(write_article(book_path, breakdown_path, summaries_path, skeleton_path, output_path))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
