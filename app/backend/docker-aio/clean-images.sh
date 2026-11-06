#!/usr/bin/env bash
# 删除 flood-aio 相关镜像（先停止并删除用到它们的容器）。
#
#   ./clean-images.sh                删除所有 flood-aio:* 镜像（逐项确认）
#   ./clean-images.sh --yes          不再逐项确认
#   ./clean-images.sh --keep v3      保留某个标签（可重复），只删其余的（升级后清理旧版本用）
#   ./clean-images.sh --purge-data   在上面基础上，还删除数据库和缓存目录（pgdata/ cache/）——不可恢复，
#                                    必须手敲 DELETE-DATABASE 确认，--yes 不能跳过这一步
#
# 只动名为 $IMAGE（默认 flood-aio）的仓库。**不会**删基础库镜像（local/postgis…，别的容器在用）、也不动 data/ 与 backups/。
# 删镜像不影响数据：库文件在宿主机的 pgdata/，之后 docker load + ./run.sh <标签> 就能原样起来。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO="${IMAGE:-flood-aio}"; NAME="${NAME:-flood-aio}"
PGDATA_DIR="${PGDATA_DIR:-$PWD/pgdata}"; CACHE_DIR="${CACHE_DIR:-$PWD/cache}"
YES=0; PURGE=0; KEEP=()
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1 ;;
    --purge-data) PURGE=1 ;;
    --keep) shift; KEEP+=("${1:?--keep 需要一个标签}") ;;
    *) echo "未知参数 $1" >&2; exit 2 ;;
  esac
  shift
done

# 候选：REPO 下的所有标签（排除 --keep）
mapfile -t ALL < <(docker images "$REPO" --format '{{.Tag}} {{.ID}}' | grep -v '^<none>' || true)
TAGS=()
for line in "${ALL[@]}"; do
  tag=${line%% *}
  skip=0; for k in "${KEEP[@]:-}"; do [ "$k" = "$tag" ] && skip=1; done
  [ $skip = 1 ] || TAGS+=("$tag")
done

if [ ${#TAGS[@]} -eq 0 ]; then
  echo "没有要删除的 $REPO 镜像。"
else
  echo "将删除镜像："
  for line in "${ALL[@]}"; do
    tag=${line%% *}; id=${line##* }
    for t in "${TAGS[@]}"; do [ "$t" = "$tag" ] && echo "  $REPO:$tag  ($id)"; done
  done
  [ ${#KEEP[@]} -gt 0 ] && echo "保留：${KEEP[*]}"
  if [ $YES = 0 ]; then read -r -p "确认删除？[y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo "已取消。"; exit 1; }; fi

  DEL_REFS=(); for t in "${TAGS[@]}"; do DEL_REFS+=("$REPO:$t"); done
  # 镜像在删完这些标签后是否还有别的名字（别的仓库 / 保留的标签）。还有 → 只是去掉名字，镜像仍被引用，不能动容器
  survives() {   # $1 = 镜像 ID
    local t
    for t in $(docker image inspect "$1" --format '{{join .RepoTags " "}}' 2>/dev/null); do
      local gone=0; for d in "${DEL_REFS[@]}"; do [ "$d" = "$t" ] && gone=1; done
      [ $gone = 0 ] && return 0
    done
    return 1
  }
  # 要停并删的容器：创建时用的镜像名正是要删的标签；或者它用的镜像在删完后没有别的名字了（否则 docker rmi 删不掉）
  for c in $(docker ps -aq); do
    ref=$(docker inspect --type container -f '{{.Config.Image}}' "$c")
    cid=$(docker inspect --type container -f '{{.Image}}' "$c")
    hit=0
    for d in "${DEL_REFS[@]}"; do [ "$d" = "$ref" ] && hit=1; done
    if [ $hit = 0 ]; then
      for line in "${ALL[@]}"; do
        tag=${line%% *}; id=${line##* }
        for t in "${TAGS[@]}"; do
          if [ "$t" = "$tag" ] && [[ "$cid" == *"$id"* ]] && ! survives "$id"; then hit=1; fi
        done
      done
    fi
    if [ $hit = 1 ]; then
      cn=$(docker inspect --type container -f '{{.Name}}' "$c" | tr -d /)
      echo "停止并删除容器 $cn（用的是 $ref）..."
      docker stop -t 120 "$c" >/dev/null 2>&1 || true
      docker rm "$c" >/dev/null
    fi
  done
  for d in "${DEL_REFS[@]}"; do
    docker rmi "$d" >/dev/null && echo "已删除 $d" || echo "⚠ 删除 $d 失败（可能还有别的容器在用）" >&2
  done
  docker image prune -f >/dev/null     # 顺手清掉因此变成无标签的残留层
fi

if [ $PURGE = 1 ]; then
  echo
  echo "⚠ 将永久删除：$PGDATA_DIR（数据库）和 $CACHE_DIR（溢水点缓存）。已登记的仿真任务都会没有。"
  echo "  如果还要保留数据，先中止并执行 ./backup-db.sh（容器要在运行）。"
  read -r -p "确认请输入 DELETE-DATABASE：" a
  [ "$a" = "DELETE-DATABASE" ] || { echo "已取消，数据未动。"; exit 1; }
  docker inspect --type container "$NAME" >/dev/null 2>&1 && { docker stop -t 120 "$NAME" >/dev/null 2>&1 || true; docker rm "$NAME" >/dev/null; }
  rm -rf "$PGDATA_DIR" "$CACHE_DIR"
  echo "已删除 pgdata 与 cache。"
else
  echo "数据库（$PGDATA_DIR）、数据目录与备份都没动。"
fi
