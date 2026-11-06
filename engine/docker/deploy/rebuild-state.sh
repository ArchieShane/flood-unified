#!/usr/bin/env bash
# 换了 DTM（DATA_DIR/dtm/ 里的文件，以及 runtime/config.toml 的 [terrain]）之后，离线重算数据快照 out/state：
# 数据来自 swbrain-db 容器（镜像里烤好的三个库），不连任何外部库。旧快照先备份到 runtime/out/state.bak-<时间>。
# 用法：bash rebuild-state.sh        # 重算后重启服务
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
S="$RUNTIME_DIR/out/state"; B="$RUNTIME_DIR/out/state.bak-$(date +%Y%m%d-%H%M%S)"
[ -d "$S" ] && cp -a "$S" "$B" && echo "旧快照已备份：$B"
# 这两个缓存只按顶点集合 / 参数判断是否过期，不看 DTM 文件变没变，必须删掉才会按新 DTM 重采
rm -f "$S/vertex_dtm.parquet" "$S/inlet_catchments.parquet" "$S/inlet_catchments.key"
rm -rf "$RUNTIME_DIR/out/cache"
docker exec swbrain python -m swbrain.run --no-db --note "DTM 更换后离线重算"
docker restart swbrain
