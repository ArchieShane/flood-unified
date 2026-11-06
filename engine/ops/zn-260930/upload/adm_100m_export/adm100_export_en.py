#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADM 全市 100 m（L2）成果 → 客户英文版（2026-10-19）：路径、文件内容里都不留中文。

做什么（对 NAS 上的原始成果只读，写到另一个目录 DEST）：
  1. 在 <NAS_BASE>/result/*/ 下找 ADM_City_<雨量>mm<历时>h_100m（9 场：33/61/96 mm × 1/2/24 h），同一场出现在多个主机目录时选文件最多（其次最新）的一份
  2. 逐文件拷到 DEST/<场次>/…，目录名、文件名改英文
  3. 文件内容改英文：
       .csv / .xlsx    表头、行名、状态值（xlsx 改 sharedStrings）
       .json / .geojson 键与值（泵站 / 塘的备注等），路径字符串同步改名
       .md             00_说明.md、计算设置.md 重写成英文版（数字取自原文件与 ad_stats.json）
       .inp / .rpt     含中文的注释行 / 行（注释行去掉中文，非注释行含中文就报错，不改模型）
       .tif            波段描述 / 标签（rasterio）
       .sww / .npz / 其余二进制  原样拷贝（只检查 NetCDF 属性 / zip 成员名里有没有中文）
  4. 全部拷完后再扫一遍 DEST：路径、文本内容、xlsx 字符串、tif 元数据、.sww 文件头、.npz 成员名，任何中文都报错
  5. 写 DEST/README.md（目录结构与文件说明，英文）和 DEST/MANIFEST.csv（相对路径、字节数、sha256）

安全：不对 NAS 写任何东西；DEST 不能在 <NAS_BASE>/result 或 shp_dat 里面，也不能包含它们；DEST 必须是空目录或本脚本建过的目录（有 .adm100_export_en 标记）。
用法（服务器上建议用 adm_100m_export_en.sh 在 anuga-swmm:1.0 容器里跑：tif 元数据要 rasterio）：
  python3 adm100_export_en.py list
  python3 adm100_export_en.py export /目标目录 [--dry-run] [--include 正则] [--allow-partial] [--no-hash]
  python3 adm100_export_en.py verify /目标目录
  python3 adm100_export_en.py selftest                    # 用 NAS_BASE 下的成果在临时目录跑一遍并验证（--nas-base 指定）
映射表没覆盖的中文不会被悄悄吞掉：export 结束时会列出来并返回非 0（把列出的词发给我补进映射表）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

CJK = re.compile(r"[⺀-⿟　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")
MARK = ".adm100_export_en"
RAINS = [(33, 1), (33, 2), (33, 24), (61, 1), (61, 2), (61, 24), (96, 1), (96, 2), (96, 24)]
EXPECT = [f"ADM_City_{mm}mm{h}h_100m" for mm, h in RAINS]
DIRNAME = re.compile(r"^ADM_City_(\d+)mm(\d+)h_100m$")
TEXT_EXT = {".csv", ".json", ".geojson", ".md", ".txt", ".inp", ".rpt"}

# ---------------------------------------------------------------- 路径映射（长的在前）
PATH_MAP = [
    ("L2_已建成调蓄", "L2_built_storage"),
    ("积水点结果表", "hotspot_results"),
    ("积水点图层", "hotspot_layer"),
    ("耦合运行记录", "coupled_run_log"),
    ("汇水区汇总", "catchment_summary"),
    ("一维报告", "1d_report"),
    ("最大水深", "may_depth"),
    ("计算设置", "calc_settings"),
    ("现状泵站", "existing_pumps"),
    ("已建成塘", "built_ponds"),
    ("00_说明", "00_README"),
    ("积水点", "hotspots"),
    ("计算域", "domain"),
    ("统计", "stats"),
    ("公共", "common"),
    ("一维", "1d"),
    ("二维", "2d"),
]
PATH_MAP.sort(key=lambda kv: -len(kv[0]))


def tr_path(s: str) -> str:
    for a, b in PATH_MAP:
        s = s.replace(a, b)
    return s


# ---------------------------------------------------------------- 文本映射：正则模板在前，子串在后（子串按长度降序）
REGEX = [
    (r"全市 L2 pond_link：设计库容 ([\d,\.]+) m³（来源库容 / 占地）", r"citywide L2 pond_link: design volume \1 m3 (source volume / footprint area)"),
    (r"全市 L2 pond_link：设计库容 ([\d,\.]+) m³（来源深度 × 占地）", r"citywide L2 pond_link: design volume \1 m3 (source depth x footprint area)"),
    (r"全市 L2 pond_link：设计库容 ([\d,\.]+) m³（缺省深度 ([\d\.]+) m × 占地）", r"citywide L2 pond_link: design volume \1 m3 (default depth \2 m x footprint area)"),
    (r"缺省深度 ([\d\.]+) m × 占地", r"default depth \1 m x footprint area"),
    (r"台账库容 ([^、]+)、周边 (\d+) 根管管内底全部无效，全市 L2 的 (\d+) 座里没有它", r"register volume \1, invert levels of all \2 surrounding pipes invalid; not among the \3 ponds of the citywide L2"),
    (r"Parsons 模型里库容 ([^、]+)、深度 ([^（]+)（([^）]*)），没有蓄水能力，不建", r"Parsons model volume \1, depth \2 (\3): no storage capacity, not modelled"),
    (r"域内留存（([\d\.]+) h 末）", r"retained in domain (at end of \1 h)"),
    (r"客户备注 (\d+×\d+)", r"client note \1"),
    (r"Parsons 压力管（([^）]*)）", r"Parsons pressure main (\1)"),
    (r"Parsons 同址泵 PUMP_FLOW（([^）]*)）", r"Parsons co-located pumps PUMP_FLOW (\1)"),
    (r"Parsons 子模型塘（(\d+) 版）", r"Parsons sub-model pond (\1 version)"),
]
SUBS = {
    # 积水点结果表表头 / 值
    "200 m 内最大水深 m": "max depth within 200 m (m)",
    "最近单元水深 m": "nearest cell depth (m)",
    "积水面积 ≥0.10 m (ha)": "ponded area >=0.10 m (ha)",
    "积水面积 ≥0.30 m (ha)": "ponded area >=0.30 m (ha)",
    "积水面积 ≥0.10 m（ha）": "ponded area >=0.10 m (ha)",
    "积水面积 ≥0.30 m（ha）": "ponded area >=0.30 m (ha)",
    "最大积水量出现时刻 h": "time of peak ponded volume (h)",
    "最大积水量 m³": "peak ponded volume (m3)",
    "最大水深时刻 h": "time of max depth (h)",
    "水深≥0.10 m 起始 h": "first time depth >=0.10 m (h)",
    "水深≥0.10 m 持续 min": "duration depth >=0.10 m (min)",
    "是否积水": "flooded?",
    "浅积水（0.10–0.30 m）": "shallow ponding (0.10-0.30 m)",
    "积水（≥0.30 m）": "flooded (>=0.30 m)",
    "ADM 序号": "ADM_ref",
    "所属片区": "area",
    "积水点：积水（≥0.30 m）个数": "hotspots: flooded (>=0.30 m), count",
    "积水点：浅积水（0.10–0.30 m）个数": "hotspots: shallow ponding (0.10-0.30 m), count",
    "积水点总数": "number of hotspots",
    "全域最大水深 m": "domain max depth (m)",
    "二维计算域面积 ha": "2D domain area (ha)",
    "最深点离最近积水点 m": "deepest point distance to nearest hotspot (m)",
    "最深点最近的积水点": "hotspot nearest to deepest point",
    "最深点水深 m": "deepest point depth (m)",
    "水量对账 m³：": "water balance (m3): ",
    # 水量对账键
    "一维：域内井溢流（=交给二维）": "1D: manhole flooding inside domain (handed to 2D)",
    "一维：域外节点溢流（只算一维，记为损失）": "1D: node flooding outside domain (1D only, counted as loss)",
    "一维：外部入流（净雨）": "1D: external inflow (net rain)",
    "一维：出水口出流": "1D: outfall outflow",
    "一维：连续性误差 %": "1D: continuity error (%)",
    "直接落在二维的雨": "rain falling directly on 2D",
    "进塘（二维取水）": "into ponds (2D intake)",
    "边界出流（余项）": "boundary outflow (residual)",
    # 塘 / 泵站 / 其他字符串
    "已建成塘（含分不清规划的 Parsons 塘）；L2 = L1 + 这些塘。空 = 计算域内没有塘，L2 同 L1": "built ponds (including Parsons ponds whose planned/built status is unclear); L2 = L1 + these ponds. Empty = no ponds in the domain, L2 equals L1",
    "无数据：不建泵，列为局限": "no data: pump not modelled (listed as a limitation)",
    "未知：需要用户定": "unknown: to be decided by the user",
    "停用（客户备注 Out of Service）": "out of service (client note)",
    "客户 GDB 压力管": "client GDB pressure main",
    "客户库没有井深": "no well depth in the client database",
    "客户模型导出塘": "pond exported from client model",
    "已建成塘（L2 用）": "built pond (used in L2)",
    "L1 蓄水设施": "L1 storage facility",
    "Parsons 子模型塘": "Parsons sub-model pond",
    "Parsons 现状塘": "Parsons existing pond",
    "来源库容 / 占地": "source volume / footprint area",
    "来源深度 × 占地": "source depth x footprint area",
    "city_run_250m 同一计算域": "same computation domain as city_run_250m",
    "ADM City_积水点": "ADM City_hotspots",
    "客户备注": "client note",
    "全域": "domain",
    "积水点": "hotspot",
    "汇水区": "catchment",
    "区域": "region",
    "指标": "metric",
    "位置": "location",
    "重要性": "criticality",
}
SUB_ORDER = sorted(SUBS, key=lambda k: -len(k))
UNMAPPED: dict = {}


def tr(s: str) -> str:
    """翻译一个字符串（模板 → 子串 → 路径片段）。含中文且没被映射表覆盖的，登记到 UNMAPPED。"""
    if not CJK.search(s):
        return s
    if "/" in s or "\\" in s:
        s = tr_path(s)                       # 像路径的字符串先按路径片段改名
    for pat, rep in REGEX:
        s = re.sub(pat, rep, s)
    for k in SUB_ORDER:
        if k in s:
            s = s.replace(k, SUBS[k])
    s = tr_path(s)
    if CJK.search(s):
        UNMAPPED[s[:120]] = UNMAPPED.get(s[:120], 0) + 1
    return s


EXACT = {"否": "no", "积水点": "hotspot_id", "汇水区": "catchment", "位置": "location", "区域": "region", "指标": "metric", "重要性": "criticality"}


def tr_cell(s: str) -> str:
    return EXACT[s] if s in EXACT else tr(s)


# ---------------------------------------------------------------- 各类文件
def tr_json_obj(o):
    if isinstance(o, dict):
        return {tr(k): tr_json_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [tr_json_obj(v) for v in o]
    if isinstance(o, str):
        return tr_cell(o)
    return o


def do_json(src: Path, dst: Path) -> None:
    obj = json.loads(src.read_text(encoding="utf-8-sig"))
    dst.write_text(json.dumps(tr_json_obj(obj), ensure_ascii=False, indent=1), encoding="utf-8")


def do_csv(src: Path, dst: Path) -> None:
    with open(src, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    with open(dst, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows([[tr_cell(c) for c in r] for r in rows])


def do_xlsx(src: Path, dst: Path) -> None:
    """只改 xl/sharedStrings.xml 里的 <t> 文本（openpyxl 写的表字符串都在那里）。"""
    import html
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "xl/sharedStrings.xml":
                txt = data.decode("utf-8")
                txt = re.sub(r"(<t(?: [^>]*)?>)(.*?)(</t>)", lambda m: m.group(1) + html.escape(tr_cell(html.unescape(m.group(2))), quote=False) + m.group(3), txt, flags=re.S)
                data = txt.encode("utf-8")
            elif it.filename.startswith("xl/worksheets/") and CJK.search(html.unescape(data.decode("utf-8", "ignore"))):      # 字符是数字字符引用（&#31215;）：先还原再查
                txt = data.decode("utf-8")      # 内联字符串（没有 sharedStrings 时）
                txt = re.sub(r"(<t(?: [^>]*)?>)(.*?)(</t>)", lambda m: m.group(1) + html.escape(tr_cell(html.unescape(m.group(2))), quote=False) + m.group(3), txt, flags=re.S)
                data = txt.encode("utf-8")
            elif it.filename.startswith("xl/theme/") and CJK.search(html.unescape(data.decode("utf-8", "ignore"))):
                FONTS = {"宋体": "SimSun", "黑体": "SimHei", "微软雅黑": "Microsoft YaHei", "楷体": "KaiTi", "仿宋": "FangSong", "新宋体": "NSimSun"}      # Office 默认主题里的中文字体名
                txt = html.unescape(data.decode("utf-8"))
                txt = re.sub(r'typeface="([^"]*)"', lambda m: 'typeface="%s"' % (FONTS.get(m.group(1), "Arial") if CJK.search(m.group(1)) else m.group(1)), txt)
                data = txt.encode("utf-8")
            zout.writestr(it, data)


def do_textlines(src: Path, dst: Path, kind: str) -> None:
    """.inp / .rpt：含中文的注释行（; 开头）去掉中文；.rpt 里别的含中文行同样去掉中文；.inp 里非注释行含中文 = 报错（不改模型）。"""
    n_fix = 0
    with open(src, encoding="utf-8", errors="replace", newline="") as fi, open(dst, "w", encoding="utf-8", newline="") as fo:
        for i, ln in enumerate(fi, 1):
            if CJK.search(ln):
                if ln.lstrip().startswith(";"):
                    m = re.search(r"· L2 · (\d+) mm / (\d+) h", ln)
                    if "一维管网" in ln and m:
                        ln = ";; ADM City (Abu Dhabi, whole city) 1D network (clipped from the citywide model rebuilt from the client GDB) - L2 - " + m.group(1) + " mm / " + m.group(2) + " h\n"
                    else:
                        ln = re.sub(r"[ ]{2,}", " ", CJK.sub("", ln))
                        if not ln.endswith("\n"):
                            ln += "\n"
                    n_fix += 1
                elif kind == "rpt":
                    ln = CJK.sub("", ln); n_fix += 1
                else:
                    raise SystemExit(f"✗ {src}:{i} 非注释行含中文，不敢改模型：{ln.strip()[:100]}")
            fo.write(ln)
    if n_fix:
        print(f"      {kind}：{n_fix} 行去掉中文 → {dst.name}")


def do_tif(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)
    try:
        import rasterio
    except ImportError:
        raise SystemExit("✗ 没有 rasterio：tif 的波段描述里有中文，要在 anuga-swmm:1.0 容器里跑（用 adm_100m_export_en.sh）")
    with rasterio.open(dst, "r+") as ds:
        for b in range(1, ds.count + 1):
            d = ds.descriptions[b - 1]
            if d and CJK.search(d):
                ds.set_band_description(b, "max depth over the whole simulation (m); 0 = dry (< 0.02 m counted as 0); nodata = outside the computation domain")
        tags = ds.tags()
        if any(CJK.search(str(k) + str(v)) for k, v in tags.items()):
            ds.update_tags(**{k: tr(str(v)) for k, v in tags.items()})


def sww_cjk(p: Path) -> list:
    """.sww 是 NetCDF：只看全局属性、变量名和变量属性（数据区是二进制，按字节扫会误报）。读不了（没有 netCDF4 / scipy，或不是 NetCDF）就跳过。"""
    import warnings
    warnings.simplefilter("ignore")
    texts = []
    try:
        try:
            import netCDF4
            ds = netCDF4.Dataset(str(p))
            texts += [str(k) + str(ds.getncattr(k)) for k in ds.ncattrs()]
            for name, v in ds.variables.items():
                texts.append(name); texts += [str(k) + str(v.getncattr(k)) for k in v.ncattrs()]
            ds.close()
        except ImportError:
            from scipy.io import netcdf_file
            ds = netcdf_file(str(p), "r", mmap=True)
            texts += [str(k) + str(v) for k, v in ds._attributes.items()]
            for name, v in ds.variables.items():
                texts.append(name); texts += [str(k) + str(x) for k, x in v._attributes.items()]
            ds.close()
    except Exception:
        return []
    return ["sww 属性里有中文：" + t[:60] for t in texts if CJK.search(t)]


def binary_cjk_problems(p: Path) -> list:
    out = []
    suf = p.suffix.lower()
    try:
        if suf == ".sww":
            out.extend(sww_cjk(p))
        elif suf == ".npz":
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if CJK.search(n):
                        out.append(f"npz 成员名 {n}")
        elif suf in (".tif", ".tiff"):
            import rasterio
            with rasterio.open(p) as ds:
                s = " ".join(str(d) for d in ds.descriptions if d) + json.dumps(ds.tags(), ensure_ascii=False)
                for b in range(1, ds.count + 1):
                    s += json.dumps(ds.tags(b), ensure_ascii=False)
                if CJK.search(s):
                    out.append("tif 元数据里有中文")
        elif suf == ".xlsx":
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith(".xml") and CJK.search(__import__("html").unescape(z.read(n).decode("utf-8", "ignore"))):
                        out.append(f"xlsx {n} 里有中文")
    except ImportError:
        out.append("（没有 rasterio，tif 元数据没检查）") if suf in (".tif", ".tiff") else None
    return out


# ---------------------------------------------------------------- 英文版 md
def pnum(rx: str, s: str, what: str, flags=0):
    m = re.search(rx, s, flags)
    if not m:
        raise SystemExit(f"✗ 计算设置.md 里找不到「{what}」（格式变了？）：{rx}")
    return m.groups()


def write_readme_md(src_dir: Path, dst_dir: Path, scen: str) -> None:
    mm, h = DIRNAME.match(scen).groups()
    stats = json.loads((src_dir / "统计" / "ad_stats.json").read_text(encoding="utf-8"))
    z = stats["zones"]["L2"]; bal = stats["balance"]["L2"]; hs = stats["hotspots"]["L2"]
    n_f = sum(1 for v in hs.values() if v["may_depth_200m"] >= 0.30)
    n_s = sum(1 for v in hs.values() if 0.10 <= v["may_depth_200m"] < 0.30)
    flood1d = bal.get("一维：域内井溢流（=交给二维）", 0)
    (dst_dir / "00_README.md").write_text("\n".join([
        f"# Abu Dhabi whole city - {mm} mm / {h} h - L2 results (100 m grid)", "",
        f"| State | Folder | Domain max depth | Area >=0.30 m | Peak ponded volume | Hotspots ({stats['n_hotspots']}): flooded / shallow ponding | 1D flooding inside domain |", "|---|---|---|---|---|---|---|",
        f"| L2 | `L2_built_storage/` | {z['may_depth_m']} m | {z['area_ge_0p30_ha']} ha | {z['peak_vol_m3']:,} m3 | {n_f} / {n_s} | {flood1d:,} m3 |", "",
        "This batch computes L2 only (existing network + existing pump stations + built ponds). There is no L1 and no L2-L1 comparison.", "",
        "Folders: `hotspots/` (hotspot results table csv/xlsx, hotspot layer geojson), `stats/catchment_summary.csv`, `L2_built_storage/1d/` and `2d/` "
        "(.sww, .npz, max-depth tif), `common/`, `calc_settings.md`. See the top-level README.md for file descriptions.", ""]), encoding="utf-8")


def write_settings_md(src_dir: Path, dst_dir: Path, scen: str) -> None:
    mm, h = DIRNAME.match(scen).groups()
    s = (src_dir / "计算设置.md").read_text(encoding="utf-8")
    stats = json.loads((src_dir / "统计" / "ad_stats.json").read_text(encoding="utf-8"))
    (km2,) = pnum(r"全市，([\d,\.]+) km²", s, "计算域面积")
    comps, nodes, nin, pipes, outf = pnum(r"（(\d+) 块、([\d,]+) 个节点，其中域内 ([\d,]+)），([\d,]+) 根管、(\d+) 个出水口", s, "一维规模")
    cn, tc = pnum(r"CN ([\d\.]+)、Tc ([\d\.]+) min", s, "CN / Tc")
    (exc,) = pnum(r"净雨 ([\d\.]+) mm", s, "净雨")
    (rs,) = pnum(r"服务半径 ([\d\.]+) m", s, "服务半径")
    (ntri,) = pnum(r"（约 ([\d,]+) 个）", s, "三角单元数")
    (npump,) = pnum(r"现状泵站 (\d+) 座", s, "泵站数")
    (npond,) = pnum(r"已建成塘 (\d+) 座", s, "塘数")
    pj = json.loads((src_dir / "公共" / "现状泵站.json").read_text(encoding="utf-8"))
    nst = len(pj.get("stations", []))
    res = 100
    rows = [
        ("Scenario", f"{mm} mm / {h} h; design hyetograph of the same duration taken from the client's Mussafah model and scaled to the depth; simulated {stats['sim_h']:g} h (duration + 4 h)"),
        ("Domain", f"Abu Dhabi whole city, {km2} km2 (network service area union the surface catchments of 23 ponds, buffered by 300 m, largest piece, holes filled); free outflow on all sides"),
        ("Hotspots", f"{stats['n_hotspots']} ADM hotspots inside the domain (common/hotspots.json)"),
        ("1D", f"EPA SWMM 5.2, citywide 1D network (rebuilt from the client GDB) clipped to the domain: connected components touching the domain are taken whole ({comps} components, {nodes} nodes of which {nin} inside the domain), {pipes} pipes, {outf} outfalls; dynamic wave, surface ponding off (surcharge is handed to 2D)"),
        ("Runoff", f"SCS CN {cn}, Tc {tc} min (citywide uniform values, same as the other ADM batches; client sub-catchment parameters not used), net rain {exc} mm, injected into 1D by node service area (service radius {rs} m); ground farther than the service radius from the nearest manhole receives net rain directly on 2D"),
        ("2D", f"ANUGA, unstructured triangular mesh, maximum triangle area {res}^2/3 m2 (about {ntri} cells), Manning n = 0.030"),
        ("Terrain", f"50 cm DTM block-averaged to {res} m before assignment to the triangles: effective resolution {res} m; small-scale relief (narrow channels, kerbs, small depressions) is smoothed out; depths and areas are not directly comparable with the 30 m, 15 m and 5 m versions"),
        ("Coupling", "couple2way_v2, same-process two-way coupling, 60 s exchange step; manholes inside the domain exchange water (orifice Cd 0.6, DN600), surcharge is spread over the 4 cells around the manhole"),
        ("L2", f"existing network + {npump} existing pump stations inside the domain ({max(nst - int(npump), 0)} further stations not modelled for lack of data, see common/existing_pumps.json) + {npond} built ponds "
               "(flat-bottom storage nodes with flap-gated inlets; ground water inside the pond footprint flows directly into the pond; wiring: the citywide-L2 ponds use the pond_link wiring, the others use manholes within 8 m of the pond, "
               "or are stand-alone storage that only collects 2D water). This batch computes L2 only: there is no L1 and no L2-L1 comparison"),
        ("Hotspot metrics", f"cells within 200 m of the hotspot: max depth, ponded area (>=0.10 / >=0.30 m), peak ponded volume; with {res} m cells only a handful of cells fall within 200 m, so the nearest-cell depth is given as well"),
        ("Output", "2D frames every 5 minutes (inside .sww / .npz); .sww and max-depth GeoTIFF are in UTM 40N (EPSG:32640)"),
    ]
    (dst_dir / "calc_settings.md").write_text("\n".join([f"# Abu Dhabi whole city - {mm} mm / {h} h - {res} m - calculation settings", "", "| Item | Setting |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rows] + [""]), encoding="utf-8")


def write_top_readme(dest: Path, scens: list) -> None:
    rows = "\n".join(f"| `{s}/` | {DIRNAME.match(s).group(1)} mm | {DIRNAME.match(s).group(2)} h |" for s in scens)
    (dest / "README.md").write_text(f"""# Abu Dhabi whole city - 100 m 2D grid - L2 results

Nine design storms (33 / 61 / 96 mm x 1 / 2 / 24 h), 1D/2D coupled model (EPA SWMM 5.2 + ANUGA), 2D mesh 100 m, state L2 only
(existing network + existing pump stations inside the domain + built ponds). Computation domain: Abu Dhabi whole city, about 1,234 km2.
Coordinates: UTM zone 40N (EPSG:32640) unless stated otherwise (GeoJSON files are WGS84).

| Folder | Rainfall | Duration |
|---|---|---|
{rows}

## Folder content (identical for every scenario)

| Path | Content |
|---|---|
| `00_README.md` | one-line result summary of the scenario |
| `calc_settings.md` | calculation settings and assumptions |
| `hotspots/hotspot_results.csv` (.xlsx) | one row per ADM hotspot: max depth within 200 m, nearest-cell depth, ponded area, peak ponded volume, times, flooded flag |
| `hotspots/hotspot_layer.geojson` | hotspot points with the same metrics (WGS84) |
| `stats/catchment_summary.csv` | domain-wide statistics and water balance |
| `stats/ad_stats.json` | all statistics in JSON |
| `common/domain.geojson`, `existing_pumps.json`, `built_ponds.json`, `hotspots.json` | model inputs used |
| `L2_built_storage/1d/*.inp`, `*_1d_report.rpt` | SWMM input and report of the 1D model |
| `L2_built_storage/2d/*.sww` | ANUGA output (2D water depth every 5 minutes), UTM 40N |
| `L2_built_storage/2d/*_2d.npz` | 2D cell centres, areas, elevations, max depth and depth frames (numpy) |
| `L2_built_storage/2d/*_may_depth.tif` | max depth GeoTIFF (m), 0 = dry (< 0.02 m), nodata = outside the domain |
| `L2_built_storage/2d/L2_coupled_run_log.json` | coupling run log (water volumes exchanged between 1D and 2D) |

`MANIFEST.csv` lists every file with its size and sha256.

## Notes

* Terrain is the 50 cm DTM block-averaged to 100 m: small-scale relief is smoothed out, so depths and areas are not directly comparable with finer-grid versions.
* Only L2 was computed; there is no L1 and no L2-L1 comparison.
* Some existing pump stations have no capacity data and are not modelled; see `common/existing_pumps.json`.
""", encoding="utf-8")


# ---------------------------------------------------------------- 发现 / 拷贝 / 校验
def discover(nas: Path, include: str | None) -> dict:
    found: dict = {}
    root = nas / "result"
    for host in sorted(p for p in root.iterdir() if p.is_dir()):
        for d in sorted(host.iterdir()):
            if d.is_dir() and DIRNAME.match(d.name) and (not include or re.search(include, d.name)):
                n = sum(1 for _ in d.rglob("*") if _.is_file())
                found.setdefault(d.name, []).append((n, d.stat().st_mtime, d))
    return found


def pick(cands: list) -> Path:
    return sorted(cands, key=lambda c: (-c[0], -c[1]))[0][2]


def safe_dest(dest: Path, nas: Path) -> None:
    d, n = dest.resolve(), nas.resolve()
    for sub in ("result", "shp_dat"):
        s = n / sub
        if d == s or s in d.parents or d in s.parents:
            raise SystemExit(f"✗ 目标 {d} 不能在 {s} 里面，也不能包含它")
    if dest.exists() and any(dest.iterdir()) and not (dest / MARK).exists():
        raise SystemExit(f"✗ {dest} 不是空目录，也不是本脚本建过的目录（没有 {MARK}）")


def copy_one(src: Path, dst: Path) -> None:
    rel = src.suffix.lower()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if rel == ".json" or rel == ".geojson":
        if CJK.search(src.read_text(encoding="utf-8-sig", errors="replace")):
            do_json(src, dst)
        else:
            shutil.copy2(src, dst)
    elif rel == ".csv":
        do_csv(src, dst)
    elif rel == ".xlsx":
        do_xlsx(src, dst)
    elif rel in (".inp", ".rpt"):
        do_textlines(src, dst, rel[1:])
    elif rel in (".tif", ".tiff"):
        do_tif(src, dst)
    else:
        shutil.copy2(src, dst)


def export_scenario(src_dir: Path, dest: Path, dry: bool) -> int:
    n = 0
    out_root = dest / src_dir.name
    for p in sorted(src_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("._") or p.name == ".DS_Store":
            continue
        rel = p.relative_to(src_dir)
        if rel.as_posix() in ("00_说明.md", "计算设置.md"):
            continue
        dst = out_root / Path(*[tr_path(x) for x in rel.parts])
        n += 1
        if dry:
            print(f"    {rel.as_posix()}  ->  {dst.relative_to(dest).as_posix()}")
            continue
        if dst.exists() and dst.stat().st_mtime >= p.stat().st_mtime and p.suffix.lower() in (".sww", ".npz") and dst.stat().st_size == p.stat().st_size:
            continue            # 大文件已拷过（同大小、不比源旧）就跳过，可重复执行
        copy_one(p, dst)
    if not dry:
        write_readme_md(src_dir, out_root, src_dir.name)
        write_settings_md(src_dir, out_root, src_dir.name)
    return n


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verify(dest: Path) -> int:
    bad = []
    files = [p for p in dest.rglob("*") if p.is_file()]
    for p in files:
        rel = p.relative_to(dest).as_posix()
        if CJK.search(rel):
            bad.append((rel, "路径里有中文"))
        suf = p.suffix.lower()
        if suf in TEXT_EXT:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for i, ln in enumerate(fh, 1):
                    if CJK.search(ln):
                        bad.append((rel, f"第 {i} 行有中文：{ln.strip()[:80]}")); break
        for why in binary_cjk_problems(p):
            if why:
                bad.append((rel, why))
    print(f"扫描 {len(files)} 个文件：" + ("✓ 路径、文本内容、xlsx、tif 元数据、.sww 属性、.npz 成员名里都没有中文" if not bad else f"✗ {len(bad)} 处有中文"))
    for rel, why in bad[:40]:
        print(f"  ✗ {rel}：{why}")
    return 1 if bad else 0


def cmd_export(a) -> int:
    nas = Path(a.nas_base); dest = Path(a.dest)
    safe_dest(dest, nas)
    found = discover(nas, a.include)
    missing = [s for s in EXPECT if s not in found and (not a.include or re.search(a.include, s))]
    for s in EXPECT:
        for n, _, d in found.get(s, []):
            print(f"  {s}  {d.parent.name}  {n} 个文件" + ("" if len(found[s]) == 1 else "（多份，取文件最多的）"))
    if missing:
        print(f"! 缺 {len(missing)} 场：{missing}")
        if not a.allow_partial and not a.dry_run:
            raise SystemExit("✗ 不齐不导出（加 --allow-partial 只导出已有的）")
    scens = [s for s in EXPECT if s in found]
    if not a.dry_run:
        dest.mkdir(parents=True, exist_ok=True); (dest / MARK).write_text("export by adm100_export_en.py\n")
    for s in scens:
        src = pick(found[s])
        print(f"== {s}  ←  {src}")
        n = export_scenario(src, dest, a.dry_run)
        print(f"   {n} 个文件")
    if a.dry_run:
        return 0
    write_top_readme(dest, scens)
    if not a.no_hash:
        print("== 写 MANIFEST.csv（sha256）")
        with open(dest / "MANIFEST.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["path", "bytes", "sha256"])
            for p in sorted(dest.rglob("*")):
                if p.is_file() and p.name not in (MARK, "MANIFEST.csv"):
                    w.writerow([p.relative_to(dest).as_posix(), p.stat().st_size, sha256(p)])
    rc = 0
    if UNMAPPED:
        rc = 1
        print("✗ 下面这些含中文的字符串映射表没覆盖（只翻译了能翻译的部分，请把它们发给我补映射表）：")
        for k, c in sorted(UNMAPPED.items(), key=lambda kv: -kv[1])[:40]:
            print(f"   [{c}] {k}")
    return verify(dest) or rc


def cmd_list(a) -> int:
    found = discover(Path(a.nas_base), a.include)
    for s in EXPECT:
        if s in found:
            for n, _, d in found[s]:
                print(f"  ✓ {s}  {d.parent.name}  {n} 个文件")
        else:
            print(f"  ✗ {s}  没有")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["list", "export", "verify", "selftest"])
    ap.add_argument("dest", nargs="?")
    ap.add_argument("--nas-base", default=os.environ.get("NAS_BASE", "/media/pop-fs/flood_data"))
    ap.add_argument("--include")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument("--no-hash", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "verify":
        return verify(Path(a.dest or "."))
    if a.cmd == "selftest":
        import tempfile
        a.dest = tempfile.mkdtemp(prefix="adm100_export_selftest_"); a.allow_partial = True; a.dry_run = False
        print(f"selftest：目标 {a.dest}")
    if not a.dest:
        ap.error("export 需要目标目录")
    return cmd_export(a)


if __name__ == "__main__":
    raise SystemExit(main())
