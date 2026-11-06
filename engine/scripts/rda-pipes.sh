#!/usr/bin/env bash
# 给已经交付的 RD-A 结果补写 pipes.geojson（地图「雨水管网」开关用），并拷进页面容器里已入库的结果目录。
#   scripts/rda-pipes.sh [页面容器名，默认 flood-new]
# 新推演不用跑它（交付时自动写）。只读 network_v2.gpkg，只在 deliveries/RDA_*/ 下新增 pipes.geojson，不动别的文件。
. "$(dirname "$0")/_lib.sh"
PAGE="${1:-flood-new}"
need_docker
shopt -s nullglob
roots=("$NAS_DIR"/deliveries/RDA_*/); [ ${#roots[@]} -gt 0 ] || die "$NAS_DIR/deliveries 下没有 RDA_* 交付"
args=(); for r in "${roots[@]}"; do args+=("/nas/deliveries/$(basename "$r")"); done
docker run --rm -v "$ENGINE_DIR:/app:ro" -v "$NAS_DIR:/nas" "$IMAGE" python3 /app/tools/region_pipes.py /app/data/regions/RDA/network_v2.gpkg "${args[@]}"
if docker inspect "$PAGE" >/dev/null 2>&1; then
  for r in "${roots[@]}"; do j="$(basename "$r")"
    if docker exec "$PAGE" test -d "/app/data/processed/sim_deliveries/$j"; then docker cp "$r/pipes.geojson" "$PAGE:/app/data/processed/sim_deliveries/$j/pipes.geojson" && docker exec "$PAGE" sh -c "chown root:root /app/data/processed/sim_deliveries/$j/pipes.geojson && chmod 644 /app/data/processed/sim_deliveries/$j/pipes.geojson" && echo "  → 页面 $j"; fi
  done
else echo "  没有页面容器 $PAGE，只写了 NAS 上的交付"; fi
