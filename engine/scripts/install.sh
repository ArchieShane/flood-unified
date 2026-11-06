#!/usr/bin/env bash
# 排水系统一键安装（Linux / macOS / Windows Git Bash）。在仓库根目录运行：
#   bash scripts/install.sh [--work ~/swbrain_work/swmm] [--no-venv] [--skip-selftest] [--rebuild-image]
# 做的事同 scripts/install.ps1：建 .venv → 装依赖（锁定版本，失败退回 requirements.txt）→ 构建计算镜像 → 生成 config.toml
#        → 提示设 SWBRAIN_SWMM_WORK → 安装自检 → 快速验证（合成算例，约 2 分钟）。只在 Windows 上验证过；Linux / macOS 按道理可用、没实测。
set -euo pipefail
cd "$(dirname "$0")/.."
WORK="${HOME}/swbrain_work/swmm"; VENV=1; SELF=1; REBUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --work) WORK="$2"; shift 2 ;;
    --no-venv) VENV=0; shift ;;
    --skip-selftest) SELF=0; shift ;;
    --rebuild-image) REBUILD=1; shift ;;
    *) echo "不认识的参数 $1"; exit 2 ;;
  esac
done
step() { printf '\n== %s\n' "$1"; }

step "Python"
PYBIN=$(command -v python3 || command -v python || true)
[ -n "$PYBIN" ] || { echo "没找到 python3：先装 Python 3.11 以上"; exit 1; }
"$PYBIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 太旧：要 3.11 以上"; exit 1; }
if [ "$VENV" = 1 ]; then
  [ -d .venv ] || "$PYBIN" -m venv .venv
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi
else PY="$PYBIN"; fi
"$PY" -m pip install --upgrade pip >/dev/null

step "Python 依赖（锁定版本）"
if ! "$PY" -m pip install -r requirements.lock.txt; then
  echo "锁定版本装不上，改用 requirements.txt 的下限版本"
  "$PY" -m pip install -r requirements.txt
fi

step "计算镜像 anuga-swmm:1.0"
command -v docker >/dev/null || { echo "没找到 docker：先装并启动 docker"; exit 1; }
if [ "$REBUILD" = 1 ] || ! docker image inspect anuga-swmm:1.0 >/dev/null 2>&1; then
  docker build -t anuga-swmm:1.0 docker/anuga-swmm
else echo "已有，跳过（--rebuild-image 重建）"; fi

step "配置"
if [ ! -f config.toml ]; then cp config.example.toml config.toml; echo "已生成 config.toml：按 docs/部署与交付.md 填数据路径、数据库、口令"; else echo "config.toml 已有，不动"; fi

step "一二维工作目录 SWBRAIN_SWMM_WORK"
case "$WORK" in *[![:print:]]*|*" "*) echo "工作目录不能有中文或空格"; exit 1 ;; esac
if printf '%s' "$WORK" | LC_ALL=C grep -q '[^ -~]'; then echo "工作目录不能有中文（SWMM 不认）"; exit 1; fi
mkdir -p "$WORK"; export SWBRAIN_SWMM_WORK="$WORK"
echo "SWBRAIN_SWMM_WORK=$WORK（本次已生效；要长期生效，把 export SWBRAIN_SWMM_WORK=$WORK 写进 ~/.bashrc）"

step "安装自检"
"$PY" -m swbrain.doctor --no-db || true

if [ "$SELF" = 1 ]; then
  step "快速验证（合成算例，约 2 分钟）"
  "$PY" tools/c1_pipeline.py selftest
fi
printf '\n安装完成。下一步：按 docs/部署与交付.md 放好数据、填 config.toml，再跑 %s -m swbrain.doctor；启动控制台 %s -m swbrain.serve\n' "$PY" "$PY"
