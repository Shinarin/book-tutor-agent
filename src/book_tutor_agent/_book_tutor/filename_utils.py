"""跨平台合法文件名工具。"""

import re

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """把任意字符串清成跨平台合法的文件名 stem；清洗后为空时抛错。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if not cleaned or cleaned.upper().split(".")[0] in _WIN_RESERVED:
        raise ValueError(f"无法从 {name!r} 生成合法文件名，请人工处理")
    return cleaned[:120]
