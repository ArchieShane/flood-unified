#!/usr/bin/env bash
# 配合 fix_run50_61mm.sql：把 AAM_60mm1h_100m 交付里 L1 / L2 的两份展示用 JSON 也改成 61 mm。
#   scenario.json         profile_id R60D1 → R61D1；temporal_assumption 60 mm/h → 61 mm/h
#   delivery_summary.json scenario_label 60 mm / 1 h → 61 mm / 1 h；rainfall_total_mm 60.0 → 61.0
# 不动回执 / 水量平衡 / .sww / .inp / .rpt / GeoJSON 等求解器原始结果（它们仍是 60 mm/h 算的）。
# 只依赖 bash + sed + grep。改前把原文件备份成 *.bak_60mm（已有备份则不覆盖）；
# 旧串必须在文件里恰好出现 1 次，否则不改并退出。可重复执行：已经是 61 的会跳过。
#
# 用法：bash fix_run50_61mm_files.sh [交付目录]
set -euo pipefail

DIR="${1:-/media/pop-fs/flood_data/flood-aio-sim_deliveries/AAM_60mm1h_100m}"
[ -d "$DIR" ] || { echo "目录不存在：$DIR" >&2; exit 1; }

esc() { printf '%s' "$1" | sed 's/[][\.*^$|&]/\\&/g'; }          # 转义 sed 正则特殊字符（分隔符用 |）

patch() {  # patch <文件> <旧串> <新串>
  local f="$1" old="$2" new="$3" n
  n=$( { grep -o -F -- "$old" "$f" || true; } | wc -l)
  if [ "$n" -eq 0 ]; then
    if grep -qF -- "$new" "$f"; then echo "  跳过（已是新值）：$f  [$new]"; return; fi
    echo "  ✗ 找不到旧串 [$old]：$f，不改" >&2; exit 1
  fi
  [ "$n" -eq 1 ] || { echo "  ✗ 旧串出现 $n 次（应为 1）[$old]：$f，不改" >&2; exit 1; }
  sed -i "s|$(esc "$old")|$(esc "$new")|" "$f"
  echo "  ✓ $(basename "$(dirname "$f")")/$(basename "$f")  [$old → $new]"
}

for st in L1 L2; do
  for f in scenario.json delivery_summary.json; do
    p="$DIR/$st/$f"
    [ -f "$p" ] || { echo "缺文件：$p" >&2; exit 1; }
    [ -e "$p.bak_60mm" ] || cp -p "$p" "$p.bak_60mm"
  done
  echo "== $st"
  patch "$DIR/$st/scenario.json"         '"profile_id":"R60D1"'                             '"profile_id":"R61D1"'
  patch "$DIR/$st/scenario.json"         '"temporal_assumption":"uniform 60 mm/h for 1 h"'  '"temporal_assumption":"uniform 61 mm/h for 1 h"'
  patch "$DIR/$st/delivery_summary.json" '"scenario_label":"AAM · 60 mm / 1 h'              '"scenario_label":"AAM · 61 mm / 1 h'
  patch "$DIR/$st/delivery_summary.json" '"rainfall_total_mm":60.0'                         '"rainfall_total_mm":61.0'
  if python3 -c "pass" >/dev/null 2>&1; then          # 有 python3 就顺手校验仍是合法 JSON
    for f in scenario.json delivery_summary.json; do
      python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$DIR/$st/$f" \
        || { echo "✗ $DIR/$st/$f 不是合法 JSON，请用 .bak_60mm 还原" >&2; exit 1; }
    done
  fi
done

echo "完成。回滚：for f in \$(find '$DIR' -name '*.bak_60mm'); do cp -p \"\$f\" \"\${f%.bak_60mm}\"; done"
echo "请再执行 fix_run50_61mm.sql 改库；之后 docker restart flood-aio 清缓存。"
