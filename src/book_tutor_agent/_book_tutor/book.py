"""先逐章总结，再生成教案，最后提炼知识点。

Usage:
    python book.py <book_path> [--context-size 200k] [--output-dir path]
                               [--start-seq N] [--seqs 1,3,5]
                               [--skip-summarize] [--skip-teach]
                               [--keypoints-dir path] [--skip-keypoints] [--skip-unify]
"""

import argparse
import asyncio
import os
import sys

from .book_keypoints import keypoints_book
from .book_summarize import parse_size, summarize_book
from .book_teach import detect_summary_dir, teach_book


def convert_to_md(src_path: str) -> str:
    """使用内建 markitdown 将任意文档转为 Markdown。"""
    md_path = os.path.splitext(src_path)[0] + ".md"
    if os.path.isfile(md_path):
        print(f"[markitdown] reuse existing {md_path}", file=sys.stderr)
        return md_path
    print(f"[markitdown] converting {src_path} -> {md_path}", file=sys.stderr)
    try:
        from book_tutor_agent._markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(src_path)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(result.text_content)
    except Exception as e:
        print(f"Error: markitdown conversion failed: {e}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(md_path):
        print(f"Error: markitdown did not produce {md_path}", file=sys.stderr)
        sys.exit(1)
    return md_path


async def run(
    book_path: str,
    context_size: int,
    output_dir: str,
    start_seq: int,
    seqs: list[int] | None,
    skip_summarize: bool,
    skip_teach: bool,
    keypoints_dir: str,
    skip_keypoints: bool,
    skip_unify: bool,
) -> None:
    if not skip_summarize:
        await summarize_book(book_path, context_size, output_dir, start_seq, seqs)

    summary_dir = detect_summary_dir(book_path) or output_dir

    if not skip_teach:
        await teach_book(summary_dir, book_path, seqs, start_seq if start_seq > 1 else None)

    if not skip_keypoints:
        await keypoints_book(
            summary_dir,
            book_path,
            keypoints_dir,
            seqs,
            start_seq if start_seq > 1 else None,
            skip_unify,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="先总结后教学再提炼知识点：一条命令跑完")
    parser.add_argument("book_path", help="书文件路径（任意格式，会经 markitdown 转为 .md；也可直接传 .md）")
    parser.add_argument("--context-size", default="200k", help="Context 窗口大小 (e.g. 200k, 1M)")
    parser.add_argument("--output-dir", default=None, help="总结输出目录")
    parser.add_argument("--start-seq", type=int, default=1, help="从第 N chunk 开始 (各阶段共用)")
    parser.add_argument("--seqs", type=str, default=None, help="只处理指定chunk，逗号分隔")
    parser.add_argument("--skip-summarize", action="store_true", help="跳过章节总结步骤")
    parser.add_argument("--skip-teach", action="store_true", help="跳过教案生成步骤")
    parser.add_argument("--keypoints-dir", default=None, help="知识点输出目录")
    parser.add_argument("--skip-keypoints", action="store_true", help="跳过知识点提炼步骤")
    parser.add_argument("--skip-unify", action="store_true", help="跳过知识点统一合并步骤")
    args = parser.parse_args()

    if not os.path.isfile(args.book_path):
        print(f"Error: file not found: {args.book_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(args.book_path)[1].lower()
    book_path = args.book_path if ext == ".md" else convert_to_md(args.book_path)

    context_size = parse_size(args.context_size)
    book_dir = os.path.dirname(os.path.abspath(book_path))
    output_dir = args.output_dir or os.path.join(book_dir, "summaries")
    keypoints_dir = args.keypoints_dir or os.path.join(book_dir, "keypoints")
    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None

    try:
        asyncio.run(
            run(
                book_path,
                context_size,
                output_dir,
                args.start_seq,
                seqs,
                args.skip_summarize,
                args.skip_teach,
                keypoints_dir,
                args.skip_keypoints,
                args.skip_unify,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
