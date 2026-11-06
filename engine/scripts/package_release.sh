#!/usr/bin/env bash
# 打部署包（在有 docker 和镜像的机器上跑，如 109）：推演 worker 镜像 + engine 目录（脚本 / 规则 / 数据）+ 校验和 + 安装脚本。
#   scripts/package_release.sh [--out <目录>] [--with-app]
#     --with-app  再带上 flood-new 镜像（management + flood-viewer 页面，装在放页面的那台机器上）
# 产物（--out，缺省 ./release）：
#   flood-worker_<TAG>.tar.gz   镜像（运行环境，不含脚本和数据）
#   engine_<日期>.tar.gz        engine 目录（脚本、规则、公共数据、各区域数据）
#   [flood-new_<标签>.tar.gz]   --with-app 时
#   SHA256SUMS、install_server.sh、INSTALL.md
. "$(dirname "$0")/_lib.sh"
OUT="$PWD/release"; APP=0
while [ $# -gt 0 ]; do case "$1" in --out) OUT="$2"; shift 2;; --with-app) APP=1; shift;; *) die "不认识的参数：$1";; esac; done
need_docker
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "没有镜像 $IMAGE"
mkdir -p "$OUT"
D=$(date +%Y%m%d)
echo "▶ 导出镜像 $IMAGE …"; docker save "$IMAGE" | gzip > "$OUT/flood-worker_${TAG}.tar.gz"
echo "▶ 打 engine 目录 …"
tar czf "$OUT/engine_${D}.tar.gz" -C "$(dirname "$ENGINE_DIR")" --exclude='__pycache__' --exclude='.git' --exclude='out' --exclude='docker/.env' --exclude='*.log' --exclude='._*' "$(basename "$ENGINE_DIR")"
if [ "$APP" = 1 ]; then
  APPIMG="${APP_IMAGE:-$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^flood-new:' | head -1)}"
  [ -n "$APPIMG" ] || die "没有 flood-new 镜像"
  echo "▶ 导出页面镜像 $APPIMG …"; docker save "$APPIMG" | gzip > "$OUT/flood-new_${APPIMG#*:}.tar.gz"
fi
cp "$ENGINE_DIR/scripts/install_server.sh" "$OUT/"; cp "$ENGINE_DIR/docs/INSTALL.md" "$OUT/INSTALL.md" 2>/dev/null || true
( cd "$OUT" && (sha256sum *.tar.gz > SHA256SUMS 2>/dev/null || shasum -a 256 *.tar.gz > SHA256SUMS) )
echo; echo "✓ 部署包：$OUT"; ls -lh "$OUT"
