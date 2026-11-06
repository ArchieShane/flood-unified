#!/usr/bin/env bash
# 把「原生计算结果」（AAM，ANUGA .sww + SWMM）的抽取结果登记进服务器上的 flood-aio 容器，页面随即可看。
# 抽取（读 9.7 GB 原件）在开发机上做，服务器只要抽取结果（约 1.5 GB），不需要原件：
#
#   [开发机]  cd backend && PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m stormwater.sim_native extract data/261016/原生计算结果_可拷贝
#             # 结果在 backend/data/processed/sim_deliveries/AAM_60mm1h_100m/
#             tar -C backend/data/processed/sim_deliveries -czf AAM_60mm1h_100m.tar.gz AAM_60mm1h_100m
#             scp AAM_60mm1h_100m.tar.gz <服务器>:<部署目录>/
#   [服务器]  cd <部署目录>/docker-aio     # 与 run.sh 同目录；data/ 在这里
#             ./import-native.sh AAM_60mm1h_100m.tar.gz [--by ZN]
#
# 做四件事：解到 ./data/processed/sim_deliveries/（容器里同路径可写，API 只读它）→ 修权限（容器里 API 是 uid 10001）→
# 在容器里 `python -m stormwater.sim_native register` 登记（库在容器内，写进 ./pgdata；重复执行只更新）→ 重启容器清缓存。
#
# 容器里要有 sim_native 模块（新代码）：先升级镜像（build.sh + upgrade.sh）。临时不想重建镜像，可把新代码拷进在跑的容器：
#   docker cp backend/src/stormwater/sim_native.py flood-aio:/app/src/stormwater/ 
#   docker cp backend/src/stormwater/sim_import.py flood-aio:/app/src/stormwater/ 
#   docker cp backend/src/stormwater/i18n.py       flood-aio:/app/src/stormwater/ 
# （docker cp 的改动在容器重建后会丢，已登记的任务与数据不丢；随后升级镜像即永久生效。i18n.py 只影响英文界面的态名。）
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PKG="${1:?用法：./import-native.sh <AAM_60mm1h_100m.tar.gz | 已解开的目录> [--by 缩写]}"; shift || true
BY=(); [ "${1:-}" = "--by" ] && { BY=(--by "${2:?--by 需要缩写}"); }
NAME="${NAME:-flood-aio}"; DATA_DIR="${DATA_DIR:-$PWD/data}"
KEY="AAM_60mm1h_100m"
DEST="$DATA_DIR/processed/sim_deliveries"

docker ps --format '{{.Names}}' | grep -qx "$NAME" || { echo "容器 $NAME 没在运行（先 ./run.sh <标签>）。" >&2; exit 1; }
[ -d "$DEST" ] || { echo "找不到 $DEST（DATA_DIR 不对？）" >&2; exit 1; }

echo "[1/4] 放到 $DEST/$KEY ..."
if [ -d "$PKG" ]; then
  [ "$(cd "$PKG" && pwd)" = "$(cd "$DEST/$KEY" 2>/dev/null && pwd || true)" ] || { rm -rf "$DEST/$KEY"; cp -a "$PKG" "$DEST/$KEY"; }
else
  rm -rf "$DEST/$KEY"
  tar -C "$DEST" -xzf "$PKG"
fi
[ -f "$DEST/$KEY/states.json" ] || { echo "$DEST/$KEY/states.json 不存在，抽取结果不完整。" >&2; exit 1; }

echo "[2/4] 修权限 ..."
find "$DEST/$KEY" -name '._*' -delete                       # macOS 打包带的伴生文件
chmod -R a+rX "$DEST/$KEY"
chown -R 10001 "$DEST/$KEY" 2>/dev/null || true

echo "[3/4] 在容器里登记 ..."
docker exec -u root -e PYTHONPATH=/app/src -e PYTHONUTF8=1 "$NAME" \
  python -m stormwater.sim_native register "/app/data/processed/sim_deliveries/$KEY" "${BY[@]}"

echo "[4/4] 重启容器清缓存 ..."
docker restart "$NAME" >/dev/null && echo "已重启。页面里「仿真」列表应出现 AAM 60 mm / 1 h（L1 / L2 两态）。"
