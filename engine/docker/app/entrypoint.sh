#!/bin/sh
# 服务容器入口。/app/out、/app/console/data、/app/exchange 是宿主机映射目录，第一次可能是空的：
# 把镜像里打包的数据快照（/app/seed，来自 swbrain.run 的运行产物）补进去，只补没有的、不覆盖已有文件（cp -n），
# 所以宿主机上换了新快照或重算之后，重启不会被旧快照盖掉。
set -e
mkdir -p /app/out /app/console/data /app/exchange
cp -rn /app/exchange_seed/. /app/exchange/ 2>/dev/null || true       # 契约样例与模式（来自代码仓库）
cp -rn /app/seed/out/. /app/out/ 2>/dev/null || true
cp -rn /app/seed/console_data/. /app/console/data/ 2>/dev/null || true
cp -rn /app/seed/exchange/. /app/exchange/ 2>/dev/null || true
exec python -m swbrain.serve --host 0.0.0.0 --port 8766 "$@"
