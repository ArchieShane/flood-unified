#!/usr/bin/env bash
# 在 109 上给 261101 的 6 个区补 makani.gpkg（下垫面：建筑 / 道路 / 停车 / 绿地 / 水面 / 地块）。Makani 库本机免密（docker exec psql），不用、不写密码。
#   scripts/g261101-makani.sh [区域ID,…]        默认 6 个区全做。要求：已有 data/regions/<ID>/catchments.gpkg（tools/g261101_pack.py 产出），镜像 flood-worker:v3，容器 popstr-pg18-5446
. "$(dirname "$0")/_lib.sh"
REGIONS="${1:-RDC,RDF,RB3,RD09,ADMLINE}"
WORK="${MAKANI_TMP:-$WORK_DIR/makani_261101}"
mkdir -p "$WORK"
docker ps --format '{{.Names}}' | grep -qx popstr-pg18-5446 || die "这台机器上没有 Makani 库容器 popstr-pg18-5446（只能在 109 上跑）"
docker run --rm -v "$ENGINE_DIR:/app" -v "$WORK:/mk" "$IMAGE" python3 /app/tools/g261101_makani.py plan --work /mk --regions "$REGIONS"
for r in ${REGIONS//,/ }; do
  for f in "$WORK/$r"/*.sql; do
    t="$(basename "$f" .sql)"
    docker exec -i popstr-pg18-5446 psql -U postgres -d makani_sync_full -At -F $'\t' < "$f" > "$WORK/$r/$t.tsv" 2> "$WORK/$r/$t.err" || true
    [ -s "$WORK/$r/$t.err" ] || rm -f "$WORK/$r/$t.err"
  done
  echo "  $r：导出完成"
done
docker run --rm -v "$ENGINE_DIR:/app" -v "$WORK:/mk" "$IMAGE" python3 /app/tools/g261101_makani.py build --work /mk --regions "$REGIONS"
