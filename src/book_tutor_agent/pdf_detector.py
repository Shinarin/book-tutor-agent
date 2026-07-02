"""
PDF 类型自动检测：文字型 / 扫描型（图片型）/ 混合型。
纯程序化检测，不消耗 LLM Token。

原理: pdfplumber 逐页 extract_text() → 字符数阈值判断
"""

import os
from typing import Dict, List


def detect_pdf_type(pdf_path: str, sample_pages: int = 5,
                    char_threshold: int = 100) -> Dict:
    """
    检测 PDF 类型。

    Args:
        pdf_path: PDF 文件路径
        sample_pages: 抽样检测前 N 页
        char_threshold: 每页最少字符数，低于此值判为图片/扫描页

    Returns:
        {
            "mode": "text" | "image" | "mixed",
            "text_pages": [1, 2, 4, ...],
            "image_pages": [3, 5, ...],
            "total_pages": int,
            "text_page_ratio": float,  # 文字页占比
        }
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import pdfplumber
    except ImportError:
        # pdfplumber 不可用时，默认返回 text 模式（走标准 pdfminer）
        return {
            "mode": "text",
            "text_pages": [],
            "image_pages": [],
            "total_pages": 0,
            "text_page_ratio": 1.0,
            "note": "pdfplumber not available, defaulting to text mode",
        }

    text_pages: List[int] = []
    image_pages: List[int] = []
    total_pages = 0

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        pages_to_check = min(sample_pages, total_pages)

        for i, page in enumerate(pdf.pages, 1):
            if i > pages_to_check:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if len(text.strip()) >= char_threshold:
                text_pages.append(i)
            else:
                image_pages.append(i)

    checked = len(text_pages) + len(image_pages)
    if checked == 0:
        mode = "image"
        ratio = 0.0
    elif len(image_pages) == 0:
        mode = "text"
        ratio = 1.0
    elif len(text_pages) == 0:
        mode = "image"
        ratio = 0.0
    else:
        mode = "mixed"
        ratio = len(text_pages) / checked

    return {
        "mode": mode,
        "text_pages": text_pages,
        "image_pages": image_pages,
        "total_pages": total_pages,
        "text_page_ratio": ratio,
    }


def should_use_ocr(pdf_path: str, sample_pages: int = 5) -> bool:
    """快速判断是否应该使用 OCR（视觉 LLM）模式。"""
    result = detect_pdf_type(pdf_path, sample_pages)
    return result["mode"] in ("image", "mixed")
