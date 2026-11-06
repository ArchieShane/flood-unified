#!/usr/bin/env bash
# 启动推演前检查环境：这台机器现在能不能满足这次推演。有 FAIL 返回非 0（start.sh 会先调它，不通过不启动）。
#   scripts/check.sh <区域ID> [--scenarios S33D1,S61D24] [--state L1]
. "$(dirname "$0")/_lib.sh"
[ $# -ge 1 ] || { sed -n '2,3p' "$0"; exit 1; }
REGION="$1"; shift
SCN=""; STATE="L1"; ALLOW=()
while [ $# -gt 0 ]; do case "$1" in --scenarios) SCN="$2"; shift 2;; --state) STATE="$2"; shift 2;; --allow-unmodeled) ALLOW=(--allow-unmodeled); shift;; *) die "不认识的参数：$1";; esac; done
echo "==== 宿主机检查 ===="
ok() { printf '[PASS] %s\n' "$*"; }; warn() { printf '[WARN] %s\n' "$*"; }; fail() { printf '[FAIL] %s\n' "$*"; FAILED=1; }
FAILED=0
command -v docker >/dev/null 2>&1 && ok "docker：$(docker --version | cut -d, -f1)" || { fail "没有 docker"; exit 1; }
docker info >/dev/null 2>&1 && ok "docker 守护进程可用" || fail "docker 守护进程不可用（权限 / 没启动）"
docker image inspect "$IMAGE" >/dev/null 2>&1 && ok "镜像 $IMAGE（运行环境）" || fail "没有镜像 $IMAGE（docker build 或 docker load）"
[ -d "$ENGINE_DIR/tools" ] && [ -d "$ENGINE_DIR/data/regions" ] && ok "engine 目录 $ENGINE_DIR（脚本 / 规则 / 数据都在这里，映射进容器）" || fail "engine 目录不对：$ENGINE_DIR"
[ -d "$NAS_DIR" ] && ok "NAS 目录 $NAS_DIR" || fail "NAS 目录不存在：$NAS_DIR（没挂上？）"
if [ -n "${DTM_PATH:-}" ]; then [ -e "$DTM_PATH" ] && ok "高精度 DTM：$DTM_PATH（推演改用它，不用包里的 5 m）" || fail "DTM_PATH 不存在：$DTM_PATH"; else ok "DTM：包里的 5 m（要换 50 cm：docker/.env 里设 DTM_PATH）"; fi
mkdir -p "$WORK_DIR" 2>/dev/null && ok "工作目录 $WORK_DIR" || fail "工作目录建不了：$WORK_DIR"
echo "$WORK_DIR" | grep -qP '[\x{4e00}-\x{9fff}]' 2>/dev/null && fail "工作目录路径含中文：$WORK_DIR" || true
if docker image inspect "$IMAGE" >/dev/null 2>&1 && [ -d "$NAS_DIR" ] && [ -d "$WORK_DIR" ]; then
  echo; echo "==== 容器内检查（规则 / 区域数据 / 雨型 / 空间 / CPU / 内存）===="
  ARGS=(--region "$REGION" --state "$STATE" --work /work --nas /nas); [ -z "$SCN" ] || ARGS+=(--scenarios "$SCN"); ARGS+=(${ALLOW[@]+"${ALLOW[@]}"})
  dock tools/preflight.py "${ARGS[@]}" || FAILED=1
fi
echo
[ "$FAILED" = 0 ] && echo "✔ 环境检查通过" || { echo "✘ 环境检查没通过，不能启动推演"; exit 1; }
