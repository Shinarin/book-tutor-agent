这些是常见 MCP 兼容工具的配置模板。

核心配置在所有工具中都一样：
```json
{
  "book-tutor": {
    "command": "python",
    "args": ["-m", "book_tutor_agent"]
  }
}
```

唯一不同的是：
- 配置文件的位置
- 外层 JSON 的键名（servers / mcpServers / experimental.mcpServers）

如果你的工具不在下面列表中：
1. 搜 `你的工具名 mcp server config`
2. 找到配置文件位置和键名
3. 把上面那段核心配置放入即可
