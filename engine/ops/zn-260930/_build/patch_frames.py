import pathlib, re
R = pathlib.Path(r"C:\freedo\zn-260930")
files = [R / "_build" / "ad_server.sh.tpl"] + [R / "upload" / "ad_30m" / f"server{n}" / f"ad_30m_server{n}.sh" for n in (1, 2, 3)]
FUNC = r'''
# ---- viewer 逐帧图：每个情景（c2d/ad_stats_<tag>.json 在的）写到 $RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_<区>_m<雨量>h<历时>g30/；
#      已有且比 ad_stats 新的跳过（重算 L2 后 ad_stats 变新，会自动重做）；FORCE=1 全重做。flood-viewer 最迟 2 分钟后自动出现 / 更新
frames_all(){
  local wh; wh=$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST 2>/dev/null || true)
  [ -n "$wh" ] || { echo "取不到宿主机工作目录（容器里的 SWBRAIN_SWMM_WORK_HOST），逐帧图跳过"; return 0; }
  local t reg mm h out
  for t in $TAGS; do
    docker exec "$C" test -f "/work/c2d/ad_stats_$t.json" || continue
    reg=${t%%_*}; mm=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\1/'); h=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\2/')
    out="$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_$(echo "$reg" | tr 'A-Z' 'a-z')_m${mm}h${h}g30"
    if [ -z "${FORCE:-}" ] && [ -f "$out/stats.json" ] && [ ! "$wh/c2d/ad_stats_$t.json" -nt "$out/stats.json" ]; then echo "  ✓ 逐帧图已是最新 $t"; continue; fi
    echo "  逐帧图 $t"
    docker run --rm -e AD_RESULT_ROOT="$RESULT_ROOT" -e AD_HOST="$HOST_NAME" -v "$wh:/work" -v "$NAS_BASE:$NAS_BASE" anuga-swmm:1.0 python -u /work/c2d/ad_frames.py --tag "$t" --res 30 || echo "  ✗ $t 逐帧图失败"
  done
}

case "${1:-status}" in'''
for f in files:
    s = f.read_text(encoding="utf-8")
    if "frames_all(){" in s:
        print("已有", f.name); continue
    s = s.replace('case "${1:-status}" in', FUNC.lstrip("\n"), 1)
    s = s.replace("    convert_all\n    echo \"==== $(date '+%F %T') 全部结束（算完 + 转换）\"",
                  "    convert_all\n    frames_all\n    echo \"==== $(date '+%F %T') 全部结束（算完 + 转换 + 逐帧图）\"", 1)
    s = s.replace("  convert) convert_all ;;", "  convert) convert_all ;;\n  frames)  frames_all ;;", 1)
    s = s.replace("bash $0 start|check|status|log|plan|convert", "bash $0 start|check|status|log|plan|convert|frames", 1)
    s = s.replace("for f in ad_run_30m.py ad_regions.py ad_post.py c1_batch.py; do docker cp \"$(find_src $f)\" \"$C:/app/tools/$f\"; done",
                  "for f in ad_run_30m.py ad_regions.py ad_post.py c1_batch.py; do docker cp \"$(find_src $f)\" \"$C:/app/tools/$f\"; done\n  docker cp \"$(find_src ad_frames.py)\" \"$C:/work/c2d/ad_frames.py\"", 1)
    f.write_text(s, encoding="utf-8", newline="\n")
    print("补丁", f.name, "frames_all" in s, "frames_all\n    echo" in s, "ad_frames.py\")\" \"$C:/work" in s)
