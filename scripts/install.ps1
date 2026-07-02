# Book Tutor Agent 安装脚本
# 用法: .\scripts\install.ps1
#
# pip install 项目到 Python 环境，文件由 pip 管理（在 site-packages 中），不散落系统各处。

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Book Tutor Agent — pip 安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 步骤 1: pip install
Write-Host "[1/2] pip install ..." -ForegroundColor Yellow
pip install git+https://github.com/Shinarin/book-tutor-agent.git
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ pip install 失败，请检查 Python 环境和网络连接" -ForegroundColor Red
    exit 1
}
Write-Host "  ✅ 安装完成 (项目文件由 pip 管理)" -ForegroundColor Green
Write-Host ""

# 步骤 2: MCP 配置指引
Write-Host "[2/2] Agent 工具 MCP 配置" -ForegroundColor Yellow
Write-Host ""
Write-Host "  在所有 Agent 工具中添加以下配置（格式略有差异，见 configs/ 模板）:" -ForegroundColor White
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────┐" -ForegroundColor Gray
Write-Host "  │  \"book-tutor\": {                           │" -ForegroundColor White
Write-Host "  │    \"command\": \"python\",                     │" -ForegroundColor White
Write-Host "  │    \"args\": [\"-m\", \"book_tutor_agent\"]      │" -ForegroundColor White
Write-Host "  │  }                                         │" -ForegroundColor White
Write-Host "  └──────────────────────────────────────────┘" -ForegroundColor Gray
Write-Host ""

Write-Host "  适配你的工具：" -ForegroundColor Yellow
Write-Host "    1. 找到你 Agent 工具的 MCP 配置文件（搜 '工具名 mcp config location'）" -ForegroundColor White
Write-Host "    2. 按工具要求的键名放入上面那段配置（常见: servers / mcpServers）" -ForegroundColor White
Write-Host "    3. GitHub configs/ 目录下有常见工具的配置模板可参考" -ForegroundColor White
Write-Host ""

# 步骤 3: 提示 SKILL 文件路径（由 Agent 自身的 skill 安装机制处理）
Write-Host "[3/3] SKILL 文件" -ForegroundColor Yellow

$skillSrc = (python -c "from pathlib import Path; from book_tutor_agent import __file__ as f; p = Path(f).parent / 'skills' / 'book-tutor' / 'SKILL.md'; print(p if p.exists() else '')")
if (-not $skillSrc -or -not (Test-Path $skillSrc)) {
    Write-Host "  ⚠️  SKILL 源文件未找到" -ForegroundColor Yellow
} else {
    Write-Host "  📄 SKILL 文件位于: $skillSrc" -ForegroundColor White
    Write-Host "  在 Agent 对话中说「帮我安装 book tutor skill」即可自动注册" -ForegroundColor White
    Write-Host "  或手动将上述路径交给你的 Agent 工具的 skill 安装模块" -ForegroundColor Gray
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ✅ 完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "🚀 重启 Agent 工具，在对话中说: 帮我读这本书" -ForegroundColor Yellow
Write-Host ""
Write-Host "📖 或不使用 Agent，直接用 CLI:" -ForegroundColor Gray
Write-Host "  python -c \"from book_tutor_agent.pipeline import run_pipeline; import asyncio; asyncio.run(run_pipeline('book.md'))\"" -ForegroundColor Gray
Write-Host ""
Write-Host "🗑️  卸载: pip uninstall book-tutor-agent -y" -ForegroundColor Gray
Write-Host ""
