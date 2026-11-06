# 启动 DeepSeek Harness Web UI（默认预设 = 排水智能体）。
# 前提：排水智能体 HTTP 服务在跑（python -m swbrain.serve --port 8766）；模型密钥在环境变量（OPENAI_API_KEY 或 DEEPSEEK_API_KEY）。
# 用法：.\scripts\dsh_web.ps1 [-Port 3080] [-Open]
param([int]$Port = 3080, [switch]$Open)
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$env:PYTHONIOENCODING = 'utf-8'
# setx 写的用户级密钥只有新开的进程才带；从旧终端跑这个脚本时补上（代码里 swbrain/envkeys.py 也有同样的兜底）
foreach ($k in 'DEEPSEEK_API_KEY','OPENAI_API_KEY','GOOGLE_MAPS_API_KEY') { if (-not (Get-Item -Path "Env:$k" -ErrorAction SilentlyContinue)) { $v = [Environment]::GetEnvironmentVariable($k, 'User'); if ($v) { Set-Item -Path "Env:$k" -Value $v } } }
$args = @('-m', 'swbrain.dsh_bridge', '--web', '--port', "$Port")
if ($Open) { $args += '--open' }
python @args
