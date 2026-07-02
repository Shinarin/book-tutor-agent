"""根据 prompts/book_breakdown.md 拆解，产出全书框架文档。

Usage:
    python book_breakdown.py <book_path> [--keypoints-file path]
        [--skeleton-path path] [--output-path path]
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


BOOK_BREAKDOWN_PROMPT = _load_prompt("book_breakdown_lite.md")


async def breakdown_book(
    book_path: str,
    keypoints_file: str,
    skeleton_path: str,
    output_path: str,
) -> None:
    book_path = os.path.abspath(book_path)
    keypoints_file = os.path.abspath(keypoints_file)
    skeleton_path = os.path.abspath(skeleton_path)
    output_path = os.path.abspath(output_path)

    print(f"原书:      {book_path}")
    print(f"keypoints: {keypoints_file}")
    print(f"skeleton:  {skeleton_path}")
    print(f"输出:      {output_path}\n")

    prompt = (
        BOOK_BREAKDOWN_PROMPT
        .replace("{book_path}", book_path)
        .replace("{keypoints_file}", keypoints_file)
        .replace("{skeleton_path}", skeleton_path)
        .replace("{output_path}", output_path)
    )

    start_time = time.time()
    async for event in run_agent(
        prompt,
        AgentOptions(
            allowed_tools=["Read", "Write", "Glob", "Grep"],
            model=LLM_MODEL,
            max_turns=50,
            permission_mode="acceptEdits",
            cwd=os.path.dirname(book_path),
        ),
    ):
        line = format_event(event, prefix="  ")
        if line:
            print(line)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"拆解完成! 耗时: {elapsed:.1f}s")
    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="根据 book_breakdown.md 拆解全书，产出全书框架")
    parser.add_argument("book_path", help="原书 Markdown 文件路径")
    parser.add_argument("--keypoints-file", default=None, help="全书核心知识点文件路径 (默认: 书文件同目录下的 00-全书核心知识点.md)")
    parser.add_argument("--skeleton-path", default=None,
                        help="skeleton 目录路径 (默认: <book_dir>/skeleton/)")
    parser.add_argument("--output-path", default=None,
                        help="拆解输出文件路径 (默认: <book_dir>/00-全书拆解.md)")
    args = parser.parse_args()

    if not os.path.isfile(args.book_path):
        print(f"Error: 原书文件不存在: {args.book_path}", file=sys.stderr)
        sys.exit(1)

    book_dir = os.path.dirname(os.path.abspath(args.book_path))

    keypoints_file = args.keypoints_file or os.path.join(
        book_dir, "00-全书核心知识点.md"
    )

    if not os.path.isfile(keypoints_file):
        print(f"Error: keypoints 文件不存在: {keypoints_file}", file=sys.stderr)
        sys.exit(1)

    skeleton_path = os.path.abspath(args.skeleton_path or os.path.join(book_dir, "skeleton"))
    if not os.path.exists(skeleton_path):
        print(f"Error: skeleton 路径不存在: {skeleton_path}", file=sys.stderr)
        sys.exit(1)

    output_path = os.path.abspath(args.output_path or os.path.join(book_dir, f"{os.path.splitext(os.path.basename(args.book_path))[0]}_breakdown.md"))

    try:
        asyncio.run(breakdown_book(args.book_path, keypoints_file, skeleton_path, output_path))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
