# 部署包一键启动（Windows PowerShell，在部署包目录里运行）：
#   powershell -ExecutionPolicy Bypass -File start.ps1
# 第一次：没有 .env 时生成一份并退出（先改路径再运行）；有了就：导入镜像（没有时）→ 建目录 → 放配置 → docker compose up → 容器内自检
# 不用全局 Stop：Windows PowerShell 5.1 下 docker / pip 往 stderr 写进度就会被当成致命错误（2026-09-30 实测）。
# 每条原生命令后显式查 $LASTEXITCODE，出错 throw（throw 照样终止脚本）
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
function Native([scriptblock]$b) {
  # Windows PowerShell 5.1：$ErrorActionPreference = Stop 时，原生命令（docker）往 stderr 写一句就被当成致命错误。
  # 这里临时放开、只看退出码（2026-09-30 部署包实测踩过：「镜像不存在」这一句就让脚本退出）
  $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $b 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) } finally { $ErrorActionPreference = $p }
}
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "没找到 docker：先装 Docker Desktop 并启动" }
if (-not (Test-Path ".env")) {
  Copy-Item .env.example .env
  Write-Host "已生成 .env：先改里面的 DATA_DIR / WORK_DIR / RUNTIME_DIR（和可选的密钥），再运行一次本脚本" -ForegroundColor Yellow
  exit 0
}
# .env 若被存成带 BOM 的 UTF-8（PowerShell 5.1 的 Set-Content -Encoding UTF8、部分编辑器会这样），docker compose 读到的第一个变量名会带 BOM → 先去掉
$raw = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot ".env"))
if ($raw.Length -ge 3 -and $raw[0] -eq 0xEF -and $raw[1] -eq 0xBB -and $raw[2] -eq 0xBF) { [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot ".env"), $raw[3..($raw.Length - 1)]); Write-Host ".env 去掉了 BOM" }
$E = @{}
Get-Content .env -Encoding UTF8 | Where-Object { $_ -match '^\s*[A-Z_]+\s*=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $E[$k.Trim()] = $v.Trim() }
$V = $E["VERSION"]
if ($E["WORK_DIR"] -match '[^\x00-\x7F]' -or $E["WORK_DIR"] -match ' ') { throw "WORK_DIR 不能有中文或空格（SWMM 不认）" }

Step "镜像"
foreach ($img in @("swbrain-app:$V", "anuga-swmm:1.0")) {
  if (-not (Native { docker image inspect $img })) {
    $tar = "images\" + ($img -replace ':', '-') + ".tar"
    Write-Host "导入 $tar（几 GB，要一两分钟）"
    docker load -i $tar
    if ($LASTEXITCODE -ne 0) { throw "导入 $tar 失败" }
  } else { Write-Host "$img 已有" }
}

Step "目录与配置"
foreach ($d in @($E["DATA_DIR"], $E["WORK_DIR"], "$($E['RUNTIME_DIR'])\out", "$($E['RUNTIME_DIR'])\console_data", "$($E['RUNTIME_DIR'])\exchange")) { New-Item -ItemType Directory -Force $d | Out-Null }
$cfg = Join-Path $E["RUNTIME_DIR"] "config.toml"
if (-not (Test-Path $cfg)) {
  Copy-Item config.package.toml $cfg
  Write-Host "已放配置 $cfg（数据路径已按「部署说明」的目录结构写好；数据库、口令按需填）" -ForegroundColor Yellow
} else { Write-Host "配置 $cfg 已有，不动" }

Step "启动"
docker compose up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up 失败" }
$port = if ($E["PORT"]) { $E["PORT"] } else { "8766" }
# 等容器里的服务真正在监听（宿主机端口由 docker 代理先接住，不能只测它）
for ($i = 0; $i -lt 120; $i++) {
  docker compose exec -T swbrain python -c "import socket; socket.create_connection(('127.0.0.1', 8766), 2)" *> $null
  if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2
}
Step "自检（容器内）"
docker compose exec swbrain python -m swbrain.doctor --no-db
Write-Host "`n控制台 http://127.0.0.1:$port/   管理端 http://127.0.0.1:$port/admin.html" -ForegroundColor Green
Write-Host "快速验证（合成算例，约 2 分钟）：docker compose exec swbrain python tools/c1_pipeline.py selftest"
Write-Host "日志：docker compose logs -f swbrain    停止：docker compose down"
