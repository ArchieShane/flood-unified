#!/usr/bin/env bash
# 把这个目录里的脚本和数据铺到一台服务器的 engine 目录里（先把被覆盖的文件备份成 <文件>.bak-<时间>）。
#   ./install.sh <engine 目录，如 /opt/flood/engine>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:?用法：./install.sh <engine 目录>}"
[ -d "$DEST/tools" ] && [ -d "$DEST/data/regions" ] || { echo "✗ $DEST 不像 engine 目录（没有 tools/ 或 data/regions/）"; exit 1; }
TS="$(date +%Y%m%d-%H%M%S)"
cd "$HERE"
find tools scripts data -type f | while read -r f; do
  if [ -e "$DEST/$f" ] && ! cmp -s "$f" "$DEST/$f"; then cp -p "$DEST/$f" "$DEST/$f.bak-$TS"; fi
  mkdir -p "$(dirname "$DEST/$f")"; cp -p "$f" "$DEST/$f"
done
chmod +x "$DEST"/scripts/*.sh
echo "✔ 已安装到 $DEST（被覆盖的旧文件备份为 *.bak-$TS）"
echo "  下一步：$DEST/scripts/rda-261030.sh <服务器号> clean   （先 dry-run）"
