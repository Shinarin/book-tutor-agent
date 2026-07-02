"""Book Tutor Agent — MCP Server 入口。

启动命令: python -m book_tutor_agent
"""

import sys
import os


def main():
    """MCP Server 主入口。"""
    # 确保 src/ 在 path 中
    _src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _src not in sys.path:
        sys.path.insert(0, _src)

    from book_tutor_agent.server import mcp
    mcp.run()


if __name__ == "__main__":
    main()
