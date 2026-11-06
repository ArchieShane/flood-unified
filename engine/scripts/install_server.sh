#!/usr/bin/env bash
# 在目标服务器上安装推演程序（和部署包放在同一个目录里运行；服务器不需要外网）：
#   ./install_server.sh [--engine-dir /opt/flood/engine] [--nas /media/pop-fs/flood_data/result-v2] [--work /data/swbrain_work] [--threads 5] [--dtm <50cm DTM 的 tif 或分块目录>]
# 做的事：校验和 → docker load 镜像 → 解压 engine（旧的备份成 engine.bak-<时间>）→ 写 docker/.env → 建工作目录 → 跑环境检查。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE_DIR=/opt/flood/engine; NAS=/media/pop-fs/flood_data/result-v2; WORK=/data/swbrain_work; THREADS=5; DTM=""
while [ $# -gt 0 ]; do case "$1" in --engine-dir) ENGINE_DIR="$2"; shift 2;; --nas) NAS="$2"; shift 2;; --work) WORK="$2"; shift 2;; --threads) THREADS="$2"; shift 2;; --dtm) DTM="$2"; shift 2;; *) echo "不认识的参数：$1"; exit 1;; esac; done
die() { echo "✗ $*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || die "这台机器没有 docker"
docker info >/dev/null 2>&1 || die "docker 守护进程不可用（权限 / 没启动）"
[[ "$WORK" =~ [^[:ascii:]] ]] && die "工作目录路径不能含中文：$WORK"
echo "▶ 校验文件 …"; ( cd "$HERE" && (sha256sum -c SHA256SUMS 2>/dev/null || shasum -a 256 -c SHA256SUMS) ) || die "校验失败，部署包不完整"
IMGTAR="$(ls "$HERE"/flood-worker_*.tar.gz | head -1)"; TAG="$(basename "$IMGTAR" .tar.gz | sed 's/flood-worker_//')"
echo "▶ 加载镜像 $IMGTAR …"; gunzip -c "$IMGTAR" | docker load
ENG="$(ls "$HERE"/engine_*.tar.gz | head -1)"
if [ -d "$ENGINE_DIR" ]; then mv "$ENGINE_DIR" "$ENGINE_DIR.bak-$(date +%Y%m%d-%H%M%S)"; echo "  旧的 engine 已备份"; fi
mkdir -p "$(dirname "$ENGINE_DIR")"
echo "▶ 解压 engine → $ENGINE_DIR"
TMP="$(mktemp -d "$(dirname "$ENGINE_DIR")/.engine-unpack.XXXXXX")"
tar xzf "$ENG" -C "$TMP"; TOP="$(ls "$TMP" | head -1)"          # 包里顶层目录叫什么都行（engine / engine-host …）
mv "$TMP/$TOP" "$ENGINE_DIR"; rmdir "$TMP"
chmod +x "$ENGINE_DIR"/scripts/*.sh "$ENGINE_DIR"/data/regions/*/*-server.sh 2>/dev/null || true
mkdir -p "$ENGINE_DIR/docker"
cat > "$ENGINE_DIR/docker/.env" <<ENV
TAG=$TAG
NAS_DIR=$NAS
WORK_DIR=$WORK
THREADS=$THREADS
ENV
[ -n "$DTM" ] && echo "DTM_PATH=$DTM" >> "$ENGINE_DIR/docker/.env"
mkdir -p "$WORK"
echo "✓ 已安装：engine = $ENGINE_DIR，镜像 flood-worker:$TAG，NAS = $NAS，工作目录 = $WORK，DTM = ${DTM:-包里的 5 m}"
echo; echo "▶ 环境检查（RD-A）…"
"$ENGINE_DIR/data/regions/RDA/RDA-server.sh" check || echo "（检查没通过：按上面的 FAIL 项处理，比如 NAS 没挂上、磁盘不够）"
cat <<NEXT

下一步：
  $ENGINE_DIR/data/regions/RDA/RDA-server.sh start --scenarios S33D1,S61D1     # 这台服务器负责的场景
  $ENGINE_DIR/data/regions/RDA/RDA-server.sh status | log -f | stop | report
NEXT
