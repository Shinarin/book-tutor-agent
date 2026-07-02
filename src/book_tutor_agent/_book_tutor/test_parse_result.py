"""Test parse_result via SDK (same path as book_summarize.py)."""
import os
os.environ["PYTHONUTF8"] = "1"

import asyncio
from .book_summarize import parse_result

SAMPLE_OUTPUT = """已完成第 1 部分总结：

- **章节标题**：前言
- **输出文件**：`E:\\books\\the-intelligent-investor\\01-前言.md`
- **next_offset**：`138`"""


async def main():
    try:
        title, fname, offset = await parse_result(SAMPLE_OUTPUT)
        print(f"OK! chapter_title={title}, file_name={fname}, next_offset={offset}")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(main())
