"""把 book_article.py 产出的长文调整风格 / 文风 / 姿态。

Usage:
    python book_restyle.py <article_path> [--output path]
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


BOOK_RESTYLE_PROMPT = _load_prompt("book_restyle.md")


async def restyle_article(article_path: str, output_path: str) -> None:
    print(f"\n[改风格] 输入: {article_path}")
    print(f"[改风格] 输出: {output_path}\n")

    prompt = (
        BOOK_RESTYLE_PROMPT
        .replace("{article_path}", article_path)
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
    print(f"\n{'='*60}")
    print(f"改风格完成! 耗时: {elapsed:.1f}s | 输出: {output_path}")
    print(f"{'='*60}")


def _default_output(article_path: str) -> str:
    article_dir = os.path.dirname(os.path.abspath(article_path))
    stem = os.path.splitext(os.path.basename(article_path))[0]
    return os.path.join(article_dir, f"{stem}_restyled.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="调整 book_article 产出长文的风格 / 文风 / 姿态")
    parser.add_argument("article_path", help="长文 Markdown 文件路径 (book_article.py 的产物)")
    parser.add_argument("--output", default=None,
                        help="输出路径 (默认: <article_dir>/<article_stem>_restyled.md)")
    args = parser.parse_args()

    if not args.article_path.endswith(".md"):
        print("Error: 只支持 .md 文件", file=sys.stderr)
        sys.exit(1)

    article_path = os.path.abspath(args.article_path)
    if not os.path.isfile(article_path):
        print(f"Error: 文章文件不存在: {article_path}", file=sys.stderr)
        sys.exit(1)

    output_path = os.path.abspath(args.output or _default_output(article_path))
    if os.path.abspath(output_path) == article_path:
        print("Error: 输出路径不能与原文件相同", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(restyle_article(article_path, output_path))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
