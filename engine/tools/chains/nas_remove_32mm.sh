#!/usr/bin/env bash
# 客户把 32 mm 更正为 33 mm：把 NAS 上 15 m 网格的 32 mm 旧结果删掉（5 m 的 …G5 不动）。
#   bash nas_remove_32mm.sh list              # 只列出会删什么（缺省），不改任何东西
#   CONFIRM=yes bash nas_remove_32mm.sh run   # 真删（不可恢复）
# 匹配规则（只看每个目录/文件自己的名字，匹配到目录就整个删、不再往里找）：
#   名字里有 M32H<数字> / m32h<数字>，后面是结尾或 _ . 等，不是 G：C1_M32H1_L1.inp、c1_stats_M32H24.json、flood2d_c1_m32h1、MS_M32H2_… 会删；C1_M32H1G5_…、flood2d_c1_m32h1g5 不会
#   交付目录 …模型版_32mm<数字>h（结尾，没有 _5m 后缀）
# 可选环境变量：NAS（缺省 /media/pop-fs/flood_data/result）
set -euo pipefail
NAS=${NAS:-/media/pop-fs/flood_data/result}
[ -d "$NAS" ] || { echo "✗ $NAS 不存在：NAS 没挂上？"; exit 1; }
A='.*/[^/]*[Mm]32[Hh][0-9]+([^0-9GgHh][^/]*)?'
B='.*/[^/]*模型版_32mm[0-9]+h'
find_hits(){ find "$NAS" -mindepth 2 \( -regextype posix-extended \( -regex "$A" -o -regex "$B" \) \) -prune -print0; }
case "${1:-list}" in
  list)
    echo "== 会删的（15 m 的 32 mm；5 m 的 G5 不在其中）"
    find_hits | xargs -0 -r du -sh | sort -k2
    echo "== 合计"; find_hits | xargs -0 -r du -sc | tail -1
    ;;
  run)
    [ "${CONFIRM:-}" = yes ] || { echo "先 list 看清单；确认后：CONFIRM=yes bash $0 run"; exit 1; }
    find_hits | xargs -0 -r rm -rf -v | tail -n 5
    echo "完成。再 list 一次应为空。"
    ;;
  *) echo "用法：bash $0 list|run"; exit 1 ;;
esac
