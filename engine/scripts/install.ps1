# 排水系统一键安装（Windows PowerShell）。在仓库根目录运行：
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   可选参数：-Work D:\swbrain_work\swmm（一二维工作目录，纯英文）-NoVenv（装到当前 Python）-SkipSelftest -RebuildImage
# 做的事：建 .venv → 按锁定版本装依赖（失败退回 requirements.txt）→ 构建计算镜像 → 生成 config.toml（没有时）
#        → 设用户环境变量 SWBRAIN_SWMM_WORK → 安装自检（python -m swbrain.doctor）→ 快速验证（合成算例，约 2 分钟）
# 不碰数据：客户数据与运行产物按 docs\部署与交付.md 放好、把路径填进 config.toml 即可。
param(
  [string]$Work = "D:\swbrain_work\swmm",
  [switch]$NoVenv,
  [switch]$SkipSelftest,
  [switch]$RebuildImage
)
# 不用全局 Stop：Windows PowerShell 5.1 下 docker / pip 往 stderr 写进度就会被当成致命错误（2026-09-30 实测）。
# 每条原生命令后显式查 $LASTEXITCODE，出错 throw（throw 照样终止脚本）
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

Step "Python"
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "没找到 python：先装 Python 3.11 以上（python.org），勾选 Add to PATH" }
$ver = & python -c "import sys;print('%d.%d'%sys.version_info[:2])"
if ([version]$ver -lt [version]"3.11") { throw "Python $ver 太旧：要 3.11 以上" }
Write-Host "Python $ver"
if (-not $NoVenv) {
  if (-not (Test-Path ".venv")) { & python -m venv .venv }
  $PY = Join-Path $Root ".venv\Scripts\python.exe"
} else { $PY = "python" }
& $PY -m pip install --upgrade pip | Out-Null

Step "Python 依赖（锁定版本）"
& $PY -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) {
  Write-Host "锁定版本装不上（多半是这台机器的 Python 版本没有对应的包），改用 requirements.txt 的下限版本" -ForegroundColor Yellow
  & $PY -m pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}

Step "计算镜像 anuga-swmm:1.0"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "没找到 docker：先装 Docker Desktop 并启动" }
docker image inspect anuga-swmm:1.0 *> $null
if ($RebuildImage -or $LASTEXITCODE -ne 0) {
  docker build -t anuga-swmm:1.0 docker/anuga-swmm
  if ($LASTEXITCODE -ne 0) { throw "镜像构建失败（要联网下载 python 基础镜像与 pip 包）" }
} else { Write-Host "已有，跳过（-RebuildImage 重建）" }

Step "配置"
if (-not (Test-Path "config.toml")) {
  Copy-Item config.example.toml config.toml
  Write-Host "已生成 config.toml：按 docs\部署与交付.md 填数据路径、数据库、口令" -ForegroundColor Yellow
} else { Write-Host "config.toml 已有，不动" }

Step "一二维工作目录 SWBRAIN_SWMM_WORK"
if ($Work -match '[^\x00-\x7F]' -or $Work -match ' ') { throw "工作目录 $Work 不能有中文或空格（SWMM 不认）" }
New-Item -ItemType Directory -Force $Work | Out-Null
[Environment]::SetEnvironmentVariable("SWBRAIN_SWMM_WORK", $Work, "User")
$env:SWBRAIN_SWMM_WORK = $Work
Write-Host "SWBRAIN_SWMM_WORK = $Work（已写进用户环境变量，新开的终端生效）"

Step "安装自检"
& $PY -m swbrain.doctor --no-db

if (-not $SkipSelftest) {
  Step "快速验证（合成算例，约 2 分钟）"
  & $PY tools\c1_pipeline.py selftest
  if ($LASTEXITCODE -ne 0) { throw "快速验证没通过：看上面的 ✗ 项与 out\demo\verify.json" }
}

Write-Host "`n安装完成。下一步：" -ForegroundColor Green
Write-Host "  1. 按 docs\部署与交付.md 放好数据、填 config.toml；再跑 $PY -m swbrain.doctor"
Write-Host "  2. 启动控制台：$PY -m swbrain.serve   →  http://127.0.0.1:8766/"
if (-not $NoVenv) { Write-Host "  （以后在新终端先 .venv\Scripts\Activate.ps1，或直接用 .venv\Scripts\python.exe）" }
