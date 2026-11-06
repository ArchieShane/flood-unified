#!/usr/bin/env bash
# 新增一个推演区域：复制模板 data/regions/_template，并自动改好 region.toml（不用手改）。
#
#   scripts/new_region.sh <区域ID> --domain <计算域.geojson> --outfalls "出水口1,出水口2" \
#       --catchments <Catchment.shp> --manholes <Manholes.shp> --aecom-inp <AECOM.inp> \
#       [--title 名称] [--reference-dir <目录> --reference-files "a.json,b.inp"] [--exchange-from pilot.json] \
#       [--margin-m 60] [--far-outlet-m 5] [--force]
#
# 区域 ID 只能是字母 / 数字 / 下划线 / 连字符（会用在 job_id 和目录名里）。
# 做完会打印下一步（打数据包 → 检查 → 启动）。规则、雨型、公共数据源都不用动。
. "$(dirname "$0")/_lib.sh"
[ $# -ge 1 ] || { sed -n '2,10p' "$0"; exit 1; }
ID="$1"; shift
[[ "$ID" =~ ^[A-Za-z0-9_-]+$ ]] || die "区域 ID 只能是字母 / 数字 / 下划线 / 连字符：$ID"
[ "$ID" != "_template" ] || die "不能叫 _template"
TITLE="$ID"; DOMAIN=""; OUTFALLS=""; CATCH=""; MANH=""; AINP=""; REFDIR=""; REFFILES=""; EXFROM=""; MARGIN=""; FAR=""; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE="$2"; shift 2;; --domain) DOMAIN="$2"; shift 2;; --outfalls) OUTFALLS="$2"; shift 2;;
    --catchments) CATCH="$2"; shift 2;; --manholes) MANH="$2"; shift 2;; --aecom-inp) AINP="$2"; shift 2;;
    --reference-dir) REFDIR="$2"; shift 2;; --reference-files) REFFILES="$2"; shift 2;; --exchange-from) EXFROM="$2"; shift 2;;
    --margin-m) MARGIN="$2"; shift 2;; --far-outlet-m) FAR="$2"; shift 2;; --force) FORCE=1; shift;;
    *) die "不认识的参数：$1";;
  esac
done
for v in DOMAIN OUTFALLS CATCH MANH AINP; do [ -n "${!v}" ] || die "缺 --$(echo "$v" | tr 'A-Z' 'a-z' | sed 's/catch/catchments/;s/manh/manholes/;s/ainp/aecom-inp/')"; done
[ -f "$DOMAIN" ] || die "计算域文件不存在：$DOMAIN"
DST="$ENGINE_DIR/data/regions/$ID"
if [ -e "$DST" ]; then [ "$FORCE" = 1 ] || die "已存在 $DST（要覆盖加 --force）"; rm -rf "$DST"; fi
cp -R "$ENGINE_DIR/data/regions/_template" "$DST"
cp "$DOMAIN" "$DST/domain.geojson"
# 出水口 "A,B" → TOML 列表；参考文件同理
tolist() { local out="[" first=1 x; IFS=',' read -ra arr <<< "$1"; for x in "${arr[@]}"; do x="$(echo "$x" | sed 's/^ *//;s/ *$//')"; [ -n "$x" ] || continue; [ $first = 1 ] || out+=", "; out+="\"$x\""; first=0; done; echo "$out]"; }
ARGS=("region.id=$ID" "region.title=$TITLE" "network.outfalls=$(tolist "$OUTFALLS")" "inputs.catchments=$CATCH" "inputs.manholes=$MANH" "inputs.aecom_inp=$AINP")
[ -z "$REFDIR" ] || ARGS+=("inputs.reference_dir=$REFDIR")
[ -z "$REFFILES" ] || ARGS+=("inputs.reference_files=$(tolist "$REFFILES")")
[ -z "$EXFROM" ] || ARGS+=("inputs.exchange_from=$EXFROM")
[ -z "$MARGIN" ] || ARGS+=("network.margin_m=$MARGIN")
[ -z "$FAR" ] || ARGS+=("catchments.far_outlet_m=$FAR")
region_toml "$DST/region.toml" "${ARGS[@]}"
rm -f "$DST/README.md"
sed "s/__ID__/$ID/g" "$ENGINE_DIR/scripts/_region_wrapper.tmpl" > "$DST/$ID-server.sh"; chmod +x "$DST/$ID-server.sh"   # 这个区域自己的服务脚本
echo
echo "✓ 已建 $DST，region.toml 已自动填好："
grep -vE '^\s*(#|$)' "$DST/region.toml" | sed 's/^/    /'
cat <<NEXT

下一步：
  1) 打这个区域的数据包（从公共数据源裁出来；Makani 密码只走环境变量）：
       MAKANI_PASSWORD=*** python tools/region_pack.py --region $ID
  2) 用这个区域自己的服务脚本（check / start / stop / status / log）：
       data/regions/$ID/$ID-server.sh check
       data/regions/$ID/$ID-server.sh start
       data/regions/$ID/$ID-server.sh status      # 进度；log -f 看日志；stop 停止
NEXT
