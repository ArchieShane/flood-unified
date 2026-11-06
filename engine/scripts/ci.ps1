# 本地最小 CI：测试 + 前端语法 + 本体自检 + 配置漂移。任何一步失败退出码非 0。
# 用法：.\scripts\ci.ps1            （加 -Integration 会跑真机集成测试，需要 out/state 与 config.toml）
param([switch]$Integration)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$env:PYTHONIOENCODING = 'utf-8'
$fail = 0
function Step($name, $block) {
  Write-Host "== $name" -ForegroundColor Cyan
  & $block
  if ($LASTEXITCODE -ne 0) { Write-Host "   失败" -ForegroundColor Red; $script:fail = 1 } else { Write-Host "   通过" -ForegroundColor Green }
}
Step '单元测试' { python -m pytest tests -q }
Step '控制台脚本语法' { node --check console/i18n.js; if ($LASTEXITCODE -eq 0) { node --check console/app.js } }
Step '控制台交互逻辑' { node tests/console_ui.test.js }
Step '本体自检' { python -m swbrain.ontology --check }
Step '配置漂移' { python -m swbrain.configcheck }
Step '行尾' { python scripts/check_line_endings.py }
if ($Integration) { $env:SWBRAIN_INTEGRATION = '1'; Step '真机集成测试' { python -m pytest tests/test_integration_serve.py -q } }
if ($fail) { Write-Host 'CI 失败' -ForegroundColor Red; exit 1 } else { Write-Host 'CI 通过' -ForegroundColor Green; exit 0 }
