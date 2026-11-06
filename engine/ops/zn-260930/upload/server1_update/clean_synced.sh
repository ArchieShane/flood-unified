#!/usr/bin/env bash
# 删除本机上已同步到 NAS 的大文件，腾本地磁盘（先用 sync_to_nas.sh 同步并 verify 通过）。
# 只删三类"可以从 NAS 拿回来、后续推演又不读"的大文件，而且逐个文件和 NAS 比校验和，有一个不一致就整批不删：
#   /app/out/交付/C1_*/          各情景的交付目录（里面是 c2d 结果的改名副本：.sww、npz、GeoTIFF 等）
#                                不动 交付/C1汇水区_*（U61 等计算域从这里读）和交付目录以外的 out/ 文件
#   /work/c2d/anuga_out/*.sww    后处理生成的 .sww
#   /work/c2d/tif/*              后处理生成的 GeoTIFF
# 缺省不删 *_2d.npz：c1_batch.py 以"twoway json + npz 都在"判断一个态算完了，删了 npz 再起调度器会把这个态从头重算；
# c1_post / c1_hs_series 也要读它。确定这些情景本机不会再算、不会再做后处理，才加 NPZ=1。
# twoway_*.json、*.rpt、*.inp、dem、logs、console_data 都很小、又是本机网页和流程要读的，一律不删。
#
#   bash clean_synced.sh list     # 只读：列出要删的文件、总大小，并和 NAS 比校验和（慢）
#   bash clean_synced.sh run      # 同上，全部一致后要输入 yes 才删
# 可选环境变量：NAS、NAME、C（同 sync_to_nas.sh）、NPZ=1（连 *_2d.npz 一起删）
set -euo pipefail
C=${C:-swbrain}
NAS=${NAS:-/media/pop-fs/flood_data/result}
NAME=${NAME:-$(hostname -s)}
DEST="$NAS/$NAME"

command -v rsync >/dev/null || { echo "本机没有 rsync"; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行（要从它读挂载路径）"; exit 1; }
mount_src(){ docker inspect "$C" --format '{{range .Mounts}}{{.Destination}}={{.Source}}{{"\n"}}{{end}}' | sed -n "s#^$1=##p"; }
WORK=$(mount_src /work); OUT=$(mount_src /app/out)
[ -n "$WORK" ] && [ -n "$OUT" ] || { echo "读不到 $C 的挂载（/work、/app/out）"; exit 1; }
[ -f "$DEST/MANIFEST.txt" ] || { echo "✗ $DEST/MANIFEST.txt 不存在：本机还没完整同步到 NAS"; exit 1; }
[ -f "$DEST/_sync.pid" ] && kill -0 "$(cat "$DEST/_sync.pid")" 2>/dev/null && { echo "✗ 同步还在跑，等它完成"; exit 1; }
# 有计算容器在跑就不删（后处理可能正在读写这些文件）
if docker ps --format '{{.Image}}' | grep -q '^anuga-swmm'; then echo "✗ 有 anuga-swmm 计算容器在跑，等推演和后处理结束再清理"; exit 1; fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
# 候选清单：相对各自根目录的路径，\0 分隔（目录名有中文和空格）
{ cd "$WORK/c2d"
  find anuga_out -maxdepth 1 -type f -name '*.sww' -print0 2>/dev/null || true
  find tif -maxdepth 1 -type f -print0 2>/dev/null || true
  if [ "${NPZ:-0}" = 1 ]; then find . -maxdepth 1 -type f -name '*_2d.npz' -printf '%P\0'; fi; } > "$TMP/work"
{ cd "$OUT"; find 交付 -mindepth 2 -type f -path '交付/C1_*' -print0 2>/dev/null || true; } > "$TMP/out"
cd /

cnt(){ tr -cd '\0' < "$1" | wc -c; }
size(){ [ "$(cnt "$2")" -gt 0 ] && (cd "$1" && du -cb --files0-from="$2" | tail -1 | cut -f1) || echo 0; }
n_w=$(cnt "$TMP/work"); n_o=$(cnt "$TMP/out")
[ $((n_w + n_o)) -gt 0 ] || { echo "没有可删的文件"; exit 0; }
s_w=$(size "$WORK/c2d" "$TMP/work"); s_o=$(size "$OUT" "$TMP/out")
echo "本机 $NAME，对照 $DEST"
echo "== 要删的文件（NPZ=${NPZ:-0}）"
echo "  $WORK/c2d：$n_w 个，$(numfmt --to=iec $s_w)，最大的几个："
[ "$n_w" -gt 0 ] && (cd "$WORK/c2d" && du -h --files0-from="$TMP/work" | sort -h | tail -8 | sed 's/^/    /')
echo "  $OUT/交付/C1_*：$n_o 个，$(numfmt --to=iec $s_o)，按目录："
[ "$n_o" -gt 0 ] && tr '\0' '\n' < "$TMP/out" | cut -d/ -f1-2 | sort | uniq -c | sed 's/^/    /'
echo "  合计 $(numfmt --to=iec $((s_w + s_o)))"

echo; echo "== 和 NAS 逐个比校验和（要把两边都读一遍，比较慢）"
RS=(rsync -rt --no-perms --no-owner --no-group -n -c --itemize-changes --from0)
: > "$TMP/cmp"
# rsync 本身出错（NAS 读不了、文件读失败）也算不一致：不能因为比不了就当成一致去删
if [ "$n_w" -gt 0 ]; then "${RS[@]}" --files-from="$TMP/work" "$WORK/c2d/" "$DEST/work/c2d/" >> "$TMP/cmp" || { echo "✗ 和 NAS 比对出错（见上），本次一个都没删"; exit 1; }; fi
if [ "$n_o" -gt 0 ]; then "${RS[@]}" --files-from="$TMP/out" "$OUT/" "$DEST/runtime/out/" >> "$TMP/cmp" || { echo "✗ 和 NAS 比对出错（见上），本次一个都没删"; exit 1; }; fi
bad=$(grep -E '^[<>c]f' "$TMP/cmp" || true)
if [ -n "$bad" ]; then
  echo "$bad" | head -30; echo "✗ 上面这些文件 NAS 上没有或内容不同：先 bash sync_to_nas.sh start 补传、verify 通过后再清理。本次一个都没删。"; exit 1
fi
echo "✓ 全部和 NAS 一致"

[ "${1:-list}" = run ] || { echo; echo "（只读模式，没有删除。确认要删：bash $0 run）"; exit 0; }
echo; read -r -p "确认删除上面 $((n_w + n_o)) 个文件（$(numfmt --to=iec $((s_w + s_o)))）？删除不可恢复，只能从 NAS 拷回。输入 yes 继续：" ans
[ "$ans" = yes ] || { echo "没删"; exit 0; }
(cd "$WORK/c2d" && xargs -0 -r rm -f < "$TMP/work")
(cd "$OUT" && xargs -0 -r rm -f < "$TMP/out" && find 交付 -mindepth 1 -path '交付/C1_*' -type d -empty -delete)
echo "$(date '+%F %T') 删除 $((n_w + n_o)) 个文件 $(numfmt --to=iec $((s_w + s_o)))（NPZ=${NPZ:-0}），NAS 备份在 $DEST" | tee -a "$DEST/_clean.log"
df -h "$WORK" "$OUT" | sed 's/^/  /'
