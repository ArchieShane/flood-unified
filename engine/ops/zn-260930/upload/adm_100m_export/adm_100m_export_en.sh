#!/usr/bin/env bash
# ADM 全市 100 m（L2）成果 → 客户英文版：用 anuga-swmm:1.0 容器跑 adm100_export_en.py（tif 元数据要 rasterio；宿主机不需要 python / 联网）
# NAS 以只读（:ro）挂进容器，即使脚本有 bug 也写不了 NAS；目标目录单独读写挂载。三台服务器共用一个 NAS（result/<主机>/），在任意一台上跑一次就能收齐 9 场。
#
#   bash adm_100m_export_en.sh list                                   # 列出 NAS 上找到的 9 场（哪台主机、多少个文件）
#   bash adm_100m_export_en.sh export /你的目标目录 --dry-run          # 只打印 源 -> 目标
#   bash adm_100m_export_en.sh export /你的目标目录                    # 拷贝 + 路径改英文 + 内容改英文 + 扫描有无中文 + README / MANIFEST
#   bash adm_100m_export_en.sh verify /你的目标目录                    # 只重新扫描目标目录有没有中文
#   bash adm_100m_export_en.sh rename /你的目标目录                    # 预览：把导出包里文件名 / 目录名的 L2 改成 L1（只改名，不改内容）
#   bash adm_100m_export_en.sh rename /你的目标目录 --apply            # 真改（加 --files-only 只改文件名；加 --reverse 改回 L2）
# export 选项：--include 正则（只导出匹配的场次，例 --include '61mm')、--allow-partial（9 场不齐也导出已有的）、--no-hash（不算 sha256，省时间）
# 环境变量：NAS_BASE（缺省 /media/pop-fs/flood_data）、IMAGE（缺省 anuga-swmm:1.0）
# 目标目录要在 NAS 的 result / shp_dat 之外（例如 $NAS_BASE/client_delivery_en 或本机大盘）；体积大（9 场的 .sww / .npz），先 df -h 看空间。
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
IMAGE=${IMAGE:-anuga-swmm:1.0}
mode=${1:-}; [ -n "$mode" ] || { sed -n 2,15p "$0"; exit 1; }
shift
[ -d "$NAS_BASE" ] || { echo "✗ $NAS_BASE 不存在"; exit 1; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "✗ 没有镜像 $IMAGE"; exit 1; }
args=(--rm -u "$(id -u):$(id -g)" -e PYTHONIOENCODING=utf-8 -v "$HERE/adm100_export_en.py:/app/adm100_export_en.py:ro")
case "$mode" in
  list)
    docker run "${args[@]}" -v "$NAS_BASE:$NAS_BASE:ro" "$IMAGE" python /app/adm100_export_en.py list --nas-base "$NAS_BASE" "$@" ;;
  export)
    dest=${1:?缺目标目录}; shift
    mkdir -p "$dest"; dest=$(cd "$dest" && pwd -P)
    nb=$(readlink -f "$NAS_BASE")
    case "$dest/" in "$nb/result/"*|"$nb/shp_dat/"*) echo "✗ 目标不能在 result / shp_dat 里面"; exit 1;; esac
    case "$nb/result/" in "$dest/"*) echo "✗ 目标不能包含 result"; exit 1;; esac
    docker run "${args[@]}" -v "$NAS_BASE:$NAS_BASE:ro" -v "$dest:$dest" "$IMAGE" python /app/adm100_export_en.py export "$dest" --nas-base "$NAS_BASE" "$@" ;;
  verify)
    dest=${1:?缺目标目录}; dest=$(cd "$dest" && pwd -P)
    docker run "${args[@]}" -v "$dest:$dest:ro" "$IMAGE" python /app/adm100_export_en.py verify "$dest" ;;
  rename)
    dest=${1:?缺导出目录}; shift
    dest=$(cd "$dest" && pwd -P); parent=$(dirname "$dest")
    [ -f "$dest/.adm100_export_en" ] || { echo "✗ $dest 不是 export 导出的目录（没有 .adm100_export_en）"; exit 1; }
    # 不挂 NAS；导出目录读写挂载；改名日志写在导出目录外面（挂上一级目录）
    docker run "${args[@]}" -v "$HERE/rename_l2_to_l1.py:/app/rename_l2_to_l1.py:ro" -v "$parent:$parent" "$IMAGE" python /app/rename_l2_to_l1.py "$dest" "$@" ;;
  *) echo "mode 只能是 list | export | verify | rename"; exit 1;;
esac
