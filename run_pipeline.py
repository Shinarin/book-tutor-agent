#!/usr/bin/env python
"""Book Tutor Agent — CLI 流水线入口。

Usage:
    python run_pipeline.py <book_path> [options]

Examples:
    python run_pipeline.py book.pdf
    python run_pipeline.py book.md --stages summarize,teach,keypoints
    python run_pipeline.py book.md --seqs 1,3,5
    python run_pipeline.py book.md --start-seq 10
"""

import argparse
import asyncio
import os
import sys

# 将 src/ 加入 path
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


async def main():
    parser = argparse.ArgumentParser(
        description="Book Tutor Agent — 一键将文档转为学习材料",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_pipeline.py book.pdf                        # 自动转换 + 三阶段处理
  python run_pipeline.py book.md --stages summarize      # 只做总结
  python run_pipeline.py book.md --seqs 1,3,5            # 只处理指定章节
  python run_pipeline.py book.md --start-seq 10           # 从第10章续传
        """,
    )
    parser.add_argument("book_path", help="文档路径 (PDF/EPUB/DOCX/MD)")
    parser.add_argument("--stages", default="summarize,teach,keypoints",
                        help="要执行的阶段，逗号分隔 (默认: summarize,teach,keypoints)")
    parser.add_argument("--context-size", default="200k",
                        help="LLM 上下文大小 (默认: 200k)")
    parser.add_argument("--seqs", default=None,
                        help="只处理指定章节，逗号分隔 (如: 1,3,5)")
    parser.add_argument("--start-seq", type=int, default=1,
                        help="从第几章开始 (默认: 1)")
    parser.add_argument("--force-ocr", action="store_true",
                        help="强制使用 OCR (视觉 LLM) 模式转换 PDF")

    args = parser.parse_args()

    book_path = os.path.abspath(args.book_path)
    if not os.path.isfile(book_path):
        print(f"❌ 文件不存在: {book_path}")
        sys.exit(1)

    stages = [s.strip() for s in args.stages.split(",")]
    seqs = [int(s) for s in args.seqs.split(",")] if args.seqs else None

    # 检查 API
    api_key = (os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if not api_key and "summarize" in stages:
        print("⚠️  未设置 ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN 环境变量")
        print("   Set them in .env or system environment.")

    # --- 格式转换 ---
    ext = os.path.splitext(book_path)[1].lower()
    if ext != ".md":
        print(f"\n📄 格式转换: {ext} → .md")
        from book_tutor_agent.pdf_detector import detect_pdf_type

        if ext == ".pdf":
            detection = detect_pdf_type(book_path)
            print(f"   检测结果: {detection['mode']} "
                  f"(文字页 {len(detection['text_pages'])}/{detection['total_pages']})")

        try:
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(book_path)
            md_path = os.path.splitext(book_path)[0] + ".md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(result.text_content)
            book_path = md_path
            print(f"   ✅ 转换完成: {book_path}")
        except Exception as e:
            print(f"   ❌ 转换失败: {e}")
            sys.exit(1)

    # --- 流水线 ---
    from book_tutor_agent.pipeline import run_pipeline

    def progress(msg):
        print(f"  {msg}")

    print(f"\n🚀 开始处理: {book_path}")
    print(f"   阶段: {' → '.join(stages)}")
    print()

    try:
        result = await run_pipeline(
            book_path=book_path,
            context_size=args.context_size,
            seqs=seqs,
            start_seq=args.start_seq,
            on_progress=progress,
        )
        print(f"\n{'='*50}")
        print(f"✅ 全部完成!")
        print(f"   章节数: {result['chapter_count']}")
        print(f"   总结: {result['output_dir']}")
        print(f"   知识点: {result['keypoints_dir']}")
        print(f"   耗时: {result['elapsed_seconds']} 秒")
        print(f"{'='*50}")
    except Exception as e:
        print(f"\n❌ 处理失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
