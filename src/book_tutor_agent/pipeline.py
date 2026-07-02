"""
流水线核心：串联 Book-Tutor 的 summarize → teach → keypoints 三阶段。
支持断点续传、进度回调和错误恢复。
"""

import asyncio
import json
import os
import sys
import time
from typing import Callable, Optional


# pip install 后所有模块在 site-packages 中，无需额外 path。
# 保留以下作为从源码直接运行时的兜底。
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def _ensure_api_env():
    """确保 API 环境变量已设置（多名称兜底）。"""
    api_key = (os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if api_key and "ANTHROPIC_API_KEY" not in os.environ:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if api_key and "ANTHROPIC_AUTH_TOKEN" not in os.environ:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = api_key


class PipelineProgress:
    """流水线进度追踪器。"""

    def __init__(self, book_path: str):
        self.book_path = book_path
        self.book_dir = os.path.dirname(os.path.abspath(book_path))
        self.progress_file = os.path.join(
            self.book_dir, "summaries", "progress.json"
        )
        self._status: dict = {}

    def load(self) -> dict:
        """读取现有进度。"""
        if os.path.exists(self.progress_file):
            with open(self.progress_file, encoding="utf-8") as f:
                self._status = json.load(f)
        return self._status

    def save_status(self, stage: str, status: str, info: dict = None):
        """更新流水线状态。"""
        self._status.setdefault("_pipeline", {})
        self._status["_pipeline"][stage] = {
            "status": status,  # "running" | "done" | "error"
            "timestamp": time.time(),
        }
        if info:
            self._status["_pipeline"][stage].update(info)
        os.makedirs(os.path.dirname(self.progress_file), exist_ok=True)
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(self._status, f, ensure_ascii=False, indent=2)

    def get_chapter_count(self) -> int:
        """从 progress.json 获取已完成章节数。"""
        self.load()
        return len([k for k in self._status if k.isdigit()])

    def get_stage_status(self, stage: str) -> Optional[str]:
        """获取某个阶段的运行状态。"""
        self.load()
        pipeline = self._status.get("_pipeline", {})
        stage_info = pipeline.get(stage, {})
        return stage_info.get("status")


async def run_pipeline(
    book_path: str,
    context_size: str = "200k",
    seqs: Optional[list] = None,
    start_seq: int = 1,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    一键流水线: summarize → teach → keypoints。

    Args:
        book_path: Markdown 格式的书文件路径
        context_size: Token 上下文窗口大小 (如 "200k", "1M")
        seqs: 只处理指定章节编号列表，None=全部
        start_seq: 从第几章开始（断点续传）
        on_progress: 进度回调函数，接收状态描述字符串

    Returns:
        {"summarize": "done", "teach": "done", "keypoints": "done",
         "chapter_count": int, "output_dir": str, "elapsed": float}
    """
    _ensure_api_env()

    book_path = os.path.abspath(book_path)
    if not os.path.isfile(book_path):
        raise FileNotFoundError(f"Book not found: {book_path}")

    progress = PipelineProgress(book_path)
    book_dir = os.path.dirname(book_path)
    output_dir = os.path.join(book_dir, "summaries")
    keypoints_dir = os.path.join(book_dir, "keypoints")

    result = {}
    overall_start = time.time()

    # --- 阶段 1: Summarize ---
    stage = "summarize"
    if progress.get_stage_status(stage) == "done":
        if on_progress:
            on_progress(f"[跳过] {stage} 已完成，复用已有结果")
    else:
        if on_progress:
            on_progress(f"[开始] 阶段 1/3: 逐章总结...")
        progress.save_status(stage, "running")

        try:
            from book_tutor_agent._book_tutor.book_summarize import parse_size, summarize_book

            ctx = parse_size(context_size)
            await summarize_book(book_path, ctx, output_dir, start_seq, seqs)
            progress.save_status(stage, "done",
                                 {"chapters": progress.get_chapter_count()})
        except Exception as e:
            progress.save_status(stage, "error", {"error": str(e)})
            raise

        if on_progress:
            on_progress(f"[完成] 阶段 1/3: 逐章总结 ✅")

    # --- 阶段 2: Teach ---
    stage = "teach"
    if progress.get_stage_status(stage) == "done":
        if on_progress:
            on_progress(f"[跳过] {stage} 已完成，复用已有结果")
    else:
        if on_progress:
            on_progress(f"[开始] 阶段 2/3: 生成教案...")
        progress.save_status(stage, "running")

        try:
            from book_tutor_agent._book_tutor.book_teach import detect_summary_dir, teach_book

            summary_dir = detect_summary_dir(book_path) or output_dir
            await teach_book(summary_dir, book_path, seqs,
                             start_seq if start_seq > 1 else None)
            progress.save_status(stage, "done")
        except Exception as e:
            progress.save_status(stage, "error", {"error": str(e)})
            raise

        if on_progress:
            on_progress(f"[完成] 阶段 2/3: 生成教案 ✅")

    # --- 阶段 3: Keypoints ---
    stage = "keypoints"
    if progress.get_stage_status(stage) == "done":
        if on_progress:
            on_progress(f"[跳过] {stage} 已完成，复用已有结果")
    else:
        if on_progress:
            on_progress(f"[开始] 阶段 3/3: 提炼知识点...")
        progress.save_status(stage, "running")

        try:
            from book_tutor_agent._book_tutor.book_keypoints import keypoints_book

            summary_dir = os.path.join(book_dir, "summaries")
            if not os.path.isdir(summary_dir):
                summary_dir = output_dir
            await keypoints_book(summary_dir, book_path, keypoints_dir,
                                 seqs, start_seq if start_seq > 1 else None,
                                 skip_unify=False)
            progress.save_status(stage, "done")
        except Exception as e:
            progress.save_status(stage, "error", {"error": str(e)})
            raise

        if on_progress:
            on_progress(f"[完成] 阶段 3/3: 提炼知识点 ✅")

    elapsed = time.time() - overall_start
    chapter_count = progress.get_chapter_count()

    return {
        "summarize": "done",
        "teach": "done",
        "keypoints": "done",
        "chapter_count": chapter_count,
        "output_dir": output_dir,
        "keypoints_dir": keypoints_dir,
        "elapsed_seconds": round(elapsed, 1),
    }
