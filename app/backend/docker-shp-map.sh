#!/usr/bin/env bash
# 在服务器上生成「shp_dat 目录 ↔ 仿真任务 id」对照表（只读）。
# 库在 flood-aio 容器里，shp_dat 没挂进容器：只把各目录的 h2d_index.json（几 KB）拷进容器临时目录，
# 容器里跑 stormwater.shp_dat_map，CSV 里的目录列用宿主机真实路径。dat / shp 大文件不动。
#   用法：./docker-shp-map.sh [shp_dat 根目录] [输出 CSV]
#   默认：/media/pop-fs/flood_data/shp_dat  ./shp_dat_对照.csv
set -euo pipefail
ROOT="${1:-/media/pop-fs/flood_data/shp_dat}"
OUT="${2:-./shp_dat_对照.csv}"
AIO="${AIO:-flood-aio}"
TMP=/tmp/shp_idx

docker exec "$AIO" rm -rf "$TMP"
docker exec "$AIO" mkdir -p "$TMP"
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/shp_dat_map.py"; [ -f "$SCRIPT" ] || SCRIPT="$HERE/src/stormwater/shp_dat_map.py"   # 服务器上与本脚本同目录
[ -f "$SCRIPT" ] || { echo "找不到 shp_dat_map.py（放在本脚本同目录）" >&2; exit 1; }
docker cp "$SCRIPT" "$AIO:/app/src/stormwater/shp_dat_map.py"   # 镜像里还没有这个脚本时用
(cd "$ROOT" && find . -name h2d_index.json ! -name '._*' -print0 | tar --null -cf - -T -) \
  | docker exec -i "$AIO" tar -xf - -C "$TMP"
docker exec -e PYTHONPATH=/app/src -e PYTHONUTF8=1 "$AIO" \
  python -m stormwater.shp_dat_map "$TMP" --show-root "$ROOT" --out /tmp/shp_dat_map.csv
docker cp "$AIO:/tmp/shp_dat_map.csv" "$OUT"
docker exec "$AIO" rm -rf "$TMP" /tmp/shp_dat_map.csv
echo "对照表：$OUT"
