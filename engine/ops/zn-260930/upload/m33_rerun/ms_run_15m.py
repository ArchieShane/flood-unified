# -*- coding: utf-8 -*-
"""Mussafah_00（工业区地道：热点 ADM-86 / 87，同位置 ADM-14 / 189）· 模型版计算域 · 二维 15 m 网格 · 9 场雨 × 四态（2026-10-10）。

推演计划表 v29「15m格网」第 37–72 行。用户 10-10「按建议」定的口径：
  A3 计算域   客户 Musaffah_00 模型（Hydraulic Model Data Export）的汇水区并集，50 m 闭运算连成一块、补 < 2 ha 的空洞（约 1.55 km²）；
              与 C1「模型版」同一做法（计算域 = 所用客户模型的汇水区并集）；四周自由出流，域外地表来水不算
  一维        客户交付的 Mussafah_00.inp（$WORK/Mussafah.inp，含 Parsons 措施）；L1 = strip_baseline.py 剥掉 Parsons 塘 / 集水井 / 配套泵
              （$WORK/Mussafah_baseline.inp，既有泵站 RD-27 / RD-34 保留）。客户模型是 LPS、管段偏移按深度，这里统一换成 CMS
              （couple2way_v2 按 CMS 交换水量），入流序列用 Sfactor 0.001 换算、泵曲线流量 ÷ 1000
  产流        客户汇水区图层逐汇水区 CN / Tc（SCS 曲线法 + SCS 单位线，tools/runoff.py），按 646 个出流节点注入一维；
              汇水区就是计算域，二维上不直接下雨（R_served 设成 1e6 m）
  雨型        客户 Mussafah 模型里同历时的设计雨型，等比放大（同 C1）；模拟终止 = 历时 + 4 h
  四态        L1 既有管网；L2 已建成调蓄（区内 0 座，模型同 L1，照算，口径统一）；L2+planned = 客户原件（Parsons STO-PO3 塘 + W-4 / W-6 集水井 + 2 台泵）；
              L2+suggested = c1_design_l2s3.py 的 v3 规则按这场雨 L2+planned 的残余积水设计（在内存里替换 C1 专用的几处，见 PATCHES）
  二维        couple2way_v2，15 m（单元面积上限 75 m²），地形取 [terrain].dtm_path 原值（服务器上是 50 cm DTM），曼宁 0.030，交换步 60 s

文件前缀 MS_<情景>（如 MS_M33H1_L1.inp），交付到 out/交付/Mussafah_00_模型版_<雨量>mm<历时>h/，不碰 C1 与 9 月那版 Mussafah_00_61mm2h。
不改 tools/ 里任何已有脚本：c1_design_l2s3.py 读源码、在内存里替换几处再执行，替换点对不上就报错。

用法（容器里 `python tools/ms_run_15m.py ...`；源码方式用项目 .venv 的 Python）：
  python tools/ms_run_15m.py check-patches
  python tools/ms_run_15m.py check --tag M33H1
  python tools/ms_run_15m.py plan  --tag M33H1
  python tools/ms_run_15m.py run   prep                         --tag M33H1     # 计算域、汇水区参数表（部署包里已带，有就跳过）
  python tools/ms_run_15m.py run   build:M33H1                  --tag M33H1
  python tools/ms_run_15m.py run   compute:M33H1:L1,L2,L2P      --tag M33H1
  python tools/ms_run_15m.py run   design:M33H1                 --tag M33H1
  python tools/ms_run_15m.py run   compute:M33H1:L2S3           --tag M33H1
  python tools/ms_run_15m.py run   post:M33H1 [--force]         --tag M33H1
多场雨一起排队用 c1_batch.py --runner ms_run_15m.py（服务器上用 tools/chains/ms_15m_server2.sh / ms_15m_server3.sh）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import c1_pipeline as P  # noqa: E402

# ---- c1_batch.py 读这几个名字（缺省是 C1 的）
PREFIX = "MS"                 # 文件名前缀：MS_<情景>_<态>
CONTAINER = "mspipe"          # 计算容器名：mspipe_<情景>_<态>
STEP_LOG = "ms"               # 调度器的步骤日志：logs/ms_<步骤>.log
RES_M = 15
STATES3 = ["L1", "L2", "L2P"]
STATES = STATES3 + ["L2S3"]
SNAME = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2S3": "L2+suggested"}
HS = ["ADM-86", "ADM-87", "ADM-14", "ADM-189"]
DOMAIN = ROOT / "out" / "units_from_models" / "ms_model_domain.geojson"
SUBS = ROOT / "out" / "units_from_models" / "ms_subcatchments.csv"
CLIENT_MODEL = "Musaffah_00"  # swbrain/dmtmodel.py 里的模型名（汇水区图层）
CLOSE_M, HOLE_HA = 50.0, 2.0
BURIED_M = 2.5               # 井口比地面低这么多以上 = 埋地节点，不参与井盖交换（普通雨水口井口 2–5 m，和 DTM 差个一两米很常见，门槛放宽）
NO_RAIN_2D = 1.0e6            # R_served：汇水区 = 计算域，净雨全部经节点进一维，二维上不另下雨
SRC = {"L1": "Mussafah_baseline.inp", "L2": "Mussafah_baseline.inp", "L2P": "Mussafah.inp"}
# 情景名 → (雨量 mm, 历时 h)；与 C1 15 m 同名（文件前缀不同，不冲突）
SCEN = {"M33H1": (33, 1), "M33H2": (33, 2), "M33H24": (33, 24), "M61H1": (61, 1), "M61": (61, 2), "M61H24": (61, 24),
        "M96H1": (96, 1), "M96H2": (96, 2), "M96H24": (96, 24)}
BATCH_PRE = {"desc": "Mussafah 计算域、汇水区参数表、客户模型（部署包里带了就跳过）", "steps": ["prep"],
             "outs": [DOMAIN, SUBS, P.WORK / "Mussafah.inp", P.WORK / "Mussafah_baseline.inp"]}


def post_out(t: str) -> pathlib.Path:
    return ROOT / "out" / f"ms_stats_{t}.json"


def name_of(t: str) -> str:
    return f"{PREFIX}_{t}"


def end_h(t: str) -> float:
    return SCEN[t][1] + 4.0


# ------------------------------------------------------------------ c1_design_l2s3.py 的替换表
PATCHES = {
    "c1_design_l2s3.py": [
        ('name = f"C1_{a.tag}"; base = a.base', 'name = f"MS_{a.tag}"; base = a.base', 1),
        ('CA = gpd.read_file(ROOT / "console" / "data" / "hotspot_catch_C1.geojson").to_crs(32640); CA = CA[CA.kind != "合计"]',
         "CA = _ms_catch()                       # Mussafah：4 个热点共用计算域（= 汇水区）", 1),
        ('pond_fill = {p["unitid"]: (p.get("fill_pct") or 0) for p in tw.get("ponds", [])}',
         "_MP = _ms_ponds(name); pond_fill = _MP[\"fill\"]   # Parsons 的塘只在一维里：容积按 .inp、满度取 L2+planned 的最大满度", 1),
        ('pond_xy = {p["node"]: p["xy"] for p in meta.get("ponds", [])}', 'pond_xy = _MP["xy"]', 1),
        ('pond_vol = {p["node"]: float(p["vol"]) for p in meta.get("ponds", [])}', 'pond_vol = _MP["vol"]', 1),
        ('OF = json.loads((ROOT / "out" / "c1_proposed_outfalls.json").read_text(encoding="utf-8"))["outfall_labels_utm"]',
         "_OFX = _ms_outfalls(name); OF = [o[:2] for o in _OFX]; _OFN = [o[2] for o in _OFX]   # 客户模型的出水口", 1),
        ('f"PROPOSED OUTFALL #{d_of[0][1]}（{d_of[0][0]:.0f} m，新建出水口）"',
         'f"出水口 {_OFN[d_of[0][1]]} 旁新建的自由出水口（{d_of[0][0]:.0f} m）"', 1),
        ("to_pond, tgt = pick_pond(cx, cy, SUMP_K * V)",
         "to_pond, tgt = None, None              # Mussafah：Parsons 的塘没有设计轮廓、不进二维，地面取水泵直接排出水口", 1),
        ('tgt = f"PROPOSED OUTFALL #{d_of[0][1]}（{d_of[0][0]:.0f} m）"', 'tgt = f"出水口 {_OFN[d_of[0][1]]}（{d_of[0][0]:.0f} m，离开系统）"', 1),
        ("1.5 km 内没有装得下的塘，经地面管排到{tgt}", "经地面管排到{tgt}（Parsons 的塘只在一维里，地面取水不进塘）", 1),
        # 客户模型 LINK_OFFSETS = DEPTH：管段两端偏移 0 = 贴着井底 / 箱涵底（与 C1 按高程写的同一位置）
        ("BX_{m['id']}  {m['node']}  {nd}  10  0.013  {junc[m['node']][0]:.3f}  {zb:.3f}  0  0\\n",
         "BX_{m['id']}  {m['node']}  {nd}  10  0.013  0  0  0  0\\n", 1),
        # 客户原件已有 [CONTROLS]（STO-PO3 控 PMP5）：新规则插在它后面
        ('assert "[CONTROLS]" not in txt\n        txt = txt.rstrip("\\n") + "\\n\\n[CONTROLS]\\n" + "\\n".join(rules) + "\\n"',
         'txt = txt.replace("[CONTROLS]\\n", "[CONTROLS]\\n" + "\\n".join(rules) + "\\n\\n", 1) if "[CONTROLS]" in txt '
         'else txt.rstrip("\\n") + "\\n\\n[CONTROLS]\\n" + "\\n".join(rules) + "\\n"', 1),
        ('(ROOT / "out" / f"c1_{OUT}_inputs_{a.tag}.json")', '(ROOT / "out" / f"ms_{OUT}_inputs_{a.tag}.json")', 1),
    ],
}


def read_sec(path: pathlib.Path) -> dict:
    from build_c1_l2 import read_inp
    return read_inp(path)


def domain_utm():
    import geopandas as gpd
    return gpd.read_file(DOMAIN).to_crs(32640).geometry.union_all()


def _ms_catch():
    import geopandas as gpd
    D = domain_utm()
    return gpd.GeoDataFrame({"hotspot_id": HS, "kind": ["热点"] * len(HS)}, geometry=[D] * len(HS), crs=32640)


def storage_volume(S: dict, node: str) -> float:
    """[STORAGE] 节点的设计容积：TABULAR 曲线（面积–水深）按梯形积分到最大水深。"""
    row = next(r for r in S["[STORAGE]"] if r[0] == node)
    zmax, crv = float(row[2]), row[5]
    pts = []
    for r in S.get("[CURVES]", []):
        if r[0] == crv:
            v = r[2:] if len(r) >= 4 and not r[1].replace(".", "", 1).replace("-", "", 1).isdigit() else r[1:]
            pts.append((float(v[0]), float(v[1])))
    pts = sorted(pts)
    if not pts:
        return 0.0
    if pts[-1][0] < zmax:
        pts.append((zmax, pts[-1][1]))
    vol = 0.0
    for (d0, a0), (d1, a1) in zip(pts, pts[1:]):
        if d0 >= zmax:
            break
        d1c = min(d1, zmax); a1c = a0 + (a1 - a0) * (d1c - d0) / (d1 - d0) if d1 > d0 else a1
        vol += (a0 + a1c) / 2 * (d1c - d0)
    return vol


def storage_max_pct(rpt: pathlib.Path) -> dict:
    t = rpt.read_text(errors="ignore") if rpt.exists() else ""
    m = re.search(r"Storage Volume Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 7:
            try:
                out[q[0]] = float(q[6])
            except ValueError:
                pass
    return out


def _ms_ponds(name: str) -> dict:
    """L2+planned 里 Parsons 新增的塘（比 L1 多出来的、名字是 PO / STO-PO 的蓄水节点）：位置、设计容积、L2+planned 最大满度 %。"""
    W, C = P.WORK, P.C2D
    S2, S1 = read_sec(W / f"{name}_L2P.inp"), read_sec(W / f"{name}_L1.inp")
    old = {r[0] for r in S1.get("[STORAGE]", [])}
    xy = {r[0]: (float(r[1]), float(r[2])) for r in S2["[COORDINATES]"]}
    ponds = [r[0] for r in S2.get("[STORAGE]", []) if r[0] not in old and re.match(r"^(STO-)?PO", r[0], re.I)]
    pct = storage_max_pct(C / f"{name}_L2P_2way.rpt")
    return {"xy": {k: list(xy[k]) for k in ponds}, "vol": {k: storage_volume(S2, k) for k in ponds}, "fill": {k: pct.get(k, 0.0) for k in ponds}}


def _ms_outfalls(name: str) -> list:
    S = read_sec(P.WORK / f"{name}_L2P.inp")
    xy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    return [(xy[r[0]][0], xy[r[0]][1], r[0]) for r in S["[OUTFALLS]"] if r[0] in xy and not r[0].startswith("X_")]


INJECT = {"_ms_catch": _ms_catch, "_ms_ponds": _ms_ponds, "_ms_outfalls": _ms_outfalls}


def load_patched(name: str, argv: list[str]):
    path = HERE / name
    src = path.read_text(encoding="utf-8")
    for old, new, n in PATCHES.get(name, []):
        if src.count(old) != n:
            raise SystemExit(f"{name}：找不到要替换的原文（应有 {n} 处，实际 {src.count(old)} 处）：{old!r}\n"
                             f"  这个脚本和仓库里的不一样，请把这条报错发给开发者更新 ms_run_15m.py 里的替换表。")
        src = src.replace(old, new)
    mod = types.ModuleType("__main__")
    mod.__dict__.update(__file__=str(path), **INJECT)
    sys.argv = [str(path), *argv]
    sys.modules["__main__"] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return 0


def check_patches() -> int:
    bad = 0
    for name, ps in PATCHES.items():
        src = (HERE / name).read_text(encoding="utf-8")
        for old, _, n in ps:
            ok = src.count(old) == n
            bad += not ok
            print(("  ✓ " if ok else "  ✗ ") + f"{name}: {old[:60]!r}（应 {n} 处，实际 {src.count(old)} 处）")
    for f in ("pilot_frames.py", "npz_to_tif.py", "ms_post.py", "runoff.py", "build_c1_l2.py", "strip_baseline.py"):
        ok = (HERE / f).exists(); bad += not ok
        print(("  ✓ " if ok else "  ✗ ") + f"tools/{f}" + ("" if ok else " 不在"))
    print("替换点全部对得上" if not bad else f"有 {bad} 处对不上")
    return 1 if bad else 0


# ------------------------------------------------------------------ 准备：计算域、汇水区参数表、客户模型
def prep(dry: bool) -> None:
    W = P.WORK
    if not (W / "Mussafah.inp").exists():
        import datapaths
        s = datapaths.get("client_swmm_export") / "Mussafah_00.inp"
        print(f"  cp {s} → {W / 'Mussafah.inp'}")
        if not dry:
            shutil.copy2(s, W / "Mussafah.inp")
    if not (W / "Mussafah_baseline.inp").exists():
        print("  strip_baseline.py：Mussafah.inp → Mussafah_baseline.inp（剥掉 Parsons 措施）")
        if not dry:
            subprocess.run(P.py("strip_baseline.py"), check=True, cwd=ROOT, env=P.env())
    if DOMAIN.exists() and SUBS.exists():
        return
    print(f"  客户 {CLIENT_MODEL} 汇水区图层 → {DOMAIN.name}、{SUBS.name}")
    if dry:
        return
    import geopandas as gpd
    from shapely.geometry import Polygon
    import tomllib
    import runoff as R
    from swbrain import dmtmodel as M
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))   # 不经 swbrain.db：那边要 psycopg，这里用不上
    dm = cfg.setdefault("dmt_model", {})
    if not pathlib.Path(str(dm.get("dir", ""))).is_dir():          # 没配就用仓库 data/ 下解压好的客户导出
        dm["dir"] = str(ROOT / "data" / "Hydraulic Model Data Export")
    subs, derived = R.subcatchments(cfg, "Mussafah")
    if derived:
        raise SystemExit("Musaffah_00 汇水区图层缺属性（.dbf），不能按客户参数算产流")
    SUBS.parent.mkdir(parents=True, exist_ok=True)
    subs[["node", "area_m2", "cn", "tc_min"]].to_csv(SUBS, index=False, encoding="utf-8")
    c = M.load_model(cfg, CLIENT_MODEL)["catchments"].to_crs(32640)
    U = c.geometry.buffer(0).union_all().buffer(CLOSE_M).buffer(-CLOSE_M)
    if U.geom_type == "MultiPolygon":
        parts = sorted(U.geoms, key=lambda g: -g.area)
        print(f"  ! 闭运算 {CLOSE_M:g} m 后还有 {len(parts)} 块，取最大的（其余 {sum(g.area for g in parts[1:]) / 1e4:.2f} ha 丢掉）")
        U = parts[0]
    D = Polygon(U.exterior, [h for h in U.interiors if Polygon(h).area >= HOLE_HA * 1e4])
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    far = {h: round(D.distance(H.loc[h].geometry), 1) for h in HS}
    print(f"  计算域 {D.area / 1e4:,.1f} ha（汇水区并集 {c.geometry.buffer(0).union_all().area / 1e4:,.1f} ha）；热点到计算域距离 {far}")
    gpd.GeoDataFrame([{"id": "MSM", "name": f"Mussafah_00 汇水区（模型版：客户 {CLIENT_MODEL} 模型汇水区并集）", "models": CLIENT_MODEL,
                       "area_km2": round(D.area / 1e6, 3), "close_m": CLOSE_M, "hole_ha": HOLE_HA, "hotspots": ";".join(HS)}],
                     geometry=[D], crs=32640).to_crs(4326).to_file(DOMAIN, driver="GeoJSON")
    print(f"  汇水区 {len(subs):,} 个，面积 {subs.area_m2.sum() / 1e4:,.1f} ha，出流节点 {subs.node.nunique()} 个")


# ------------------------------------------------------------------ 建模：三态 .inp、DEM、pilot
def to_cms(txt: str, t_end_h: float, head: str) -> str:
    """客户模型（LPS、偏移按深度）→ CMS；地面积水关（冒出的水交给二维）；终止时刻 = 历时 + 4 h。"""
    lines = txt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sec, pump_crv = None, set()
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            sec = m.group(1).upper(); continue
        if sec == "PUMPS" and s and not s.startswith(";") and len(s.split()) >= 4:
            pump_crv.add(s.split()[3])
    d_end, h_end = divmod(int(round(t_end_h * 60)), 24 * 60)
    opt = {"FLOW_UNITS": "CMS", "ALLOW_PONDING": "NO", "END_DATE": f"01/{1 + d_end:02d}/2000", "END_TIME": f"{h_end // 60:02d}:{h_end % 60:02d}:00",
           "REPORT_START_DATE": "01/01/2000", "REPORT_START_TIME": "00:00:00", "THREADS": "1"}
    out, sec, seen, last_t = [head], None, set(), {}
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            if sec == "OPTIONS":                       # 原件里没有的选项补在节末
                while out and not out[-1].strip():
                    out.pop()
                out += [f"{k:<20} {v}" for k, v in opt.items() if k not in seen] + [""]
            sec = m.group(1).upper(); out.append(s); continue
        p = s.split()
        if sec == "OPTIONS" and p and not s.startswith(";"):
            if p[0] == "TEMPDIR":                      # 客户机器上的 Windows 路径，容器里没有
                continue
            if p[0] in opt:
                out.append(f"{p[0]:<20} {opt[p[0]]}"); seen.add(p[0]); continue
        elif sec == "INFLOWS" and len(p) >= 6 and not s.startswith(";") and p[1].upper() == "FLOW":
            p[5] = "0.001"; out.append("  ".join(p)); continue          # 序列是 LPS：Sfactor 换成 m³/s
        elif sec == "TIMESERIES" and len(p) == 3 and not s.startswith(";"):
            try:
                tv = float(p[1])
            except ValueError:
                tv = None
            if tv is not None and p[0] in last_t and tv <= last_t[p[0]]:
                continue                               # 同一时刻重复（runoff.write_inflows 末尾补的 0）：SWMM 报 ERROR 173
            if tv is not None:
                last_t[p[0]] = tv
        elif sec == "CURVES" and len(p) >= 3 and not s.startswith(";") and p[0] in pump_crv:
            p[-1] = f"{float(p[-1]) / 1000.0:.6g}"; out.append("  ".join(p)); continue   # 泵曲线流量 L/s → m³/s
        out.append(ln)
    return "\n".join(out).rstrip("\n") + "\n"


def build(t: str, dry: bool) -> None:
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.windows import from_bounds
    from shapely.geometry import Point
    from shapely.prepared import prep as sprep
    import datapaths
    import runoff as R
    W, C = P.WORK, P.C2D
    rain, dur = SCEN[t]; te = end_h(t); name = name_of(t)
    if dry:
        print(f"  （{name}：三态 .inp、dem_{name}.tif、pilot_{name}_<态>.json）"); return
    C.mkdir(parents=True, exist_ok=True)
    subs = pd.read_csv(SUBS, dtype={"node": str})
    dt_h = 1.0 / 60.0
    shape = R.read_shape(W / "Mussafah_baseline.inp", float(dur), log=print)
    tt, Pc = R.hyetograph(float(rain), float(dur), dt_h, shape, 0.0, total_h=te + 6.0)   # 过程线比模拟长 6 h：长 Tc 的汇水区尾巴不截断
    H = R.node_hydrographs(subs, tt, Pc, dt_h, log=print)
    v_all = sum(float(v.sum() * dt_h * 3600) for v in H.values())
    A = float(subs.area_m2.sum())
    exc = sum(R.scs_cum_excess(Pc, float(cn)) * a for cn, a in subs.groupby("cn").area_m2.sum().items()) / A
    print(f"{name}：{rain} mm / {dur} h，模拟 {te:g} h；汇水区 {A / 1e4:,.1f} ha，净雨 {exc[-1]:.1f} mm，产流 {v_all:,.0f} m³")
    D = domain_utm(); pd_ = sprep(D)
    info = {"name": name, "rain_mm": rain, "dur_h": dur, "sim_h": te, "runoff_m3": round(v_all), "catch_ha": round(A / 1e4, 1),
            "excess_mm": round(float(exc[-1]), 2), "domain_km2": round(D.area / 1e6, 3), "states": {}}
    X0, Y0, X1, Y1 = D.bounds
    dtm = datapaths.get("dtm")
    with rasterio.open(dtm) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win)
        tf, nod = ds.window_transform(win), ds.nodata
        prof = ds.profile.copy(); prof.update(width=z.shape[1], height=z.shape[0], transform=tf, compress="deflate",
                                               tiled=False, blockxsize=None, blockysize=None)
        prof = {k: v for k, v in prof.items() if v is not None}
        with rasterio.open(C / f"dem_{name}.tif", "w", **prof) as w:
            w.write(z, 1)
        info["dtm"] = {"path": str(dtm), "res_m": ds.res[0], "px": [int(z.shape[1]), int(z.shape[0])]}
    zg = z.astype(float); zg[(zg < -1000) | ((nod is not None) & (zg == nod))] = np.nan
    k_px = max(1, int(round(2.0 / abs(tf.a))))             # 井位 ±2 m 内地面取中位

    def ground(x, y):
        c, r = int((x - tf.c) / tf.a), int((y - tf.f) / tf.e)
        v = zg[max(r - k_px, 0):r + k_px + 1, max(c - k_px, 0):c + k_px + 1]
        return float(np.nanmedian(v)) if v.size and np.isfinite(v).any() else float("nan")
    for s in STATES3:
        Hk = R.keep_known_nodes(H, W / SRC[s], dt_h, log=print)
        out = W / f"{name}_{s}.inp"
        R.write_inflows(W / SRC[s], out, Hk, tt, log=print)
        note = {"L1": "既有管网（客户模型剥掉 Parsons 措施）", "L2": "已建成调蓄：区内 0 座，模型同 L1",
                "L2P": "L2+planned：客户原件（Parsons 2027 过渡方案）"}[s]
        head = f";; Mussafah_00 模型版 · {SNAME[s]} · {rain} mm / {dur} h · {note}（tools/ms_run_15m.py）"
        out.write_text(to_cms(out.read_text(encoding="utf-8"), te, head), encoding="utf-8")
        S = read_sec(out)
        xy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
        # 参与井盖交换的节点：计算域内的井与蓄水节点，但井口（底 + 深）比地面低 BURIED_M 以上的不算——
        # 泵站湿井 RD_34 / CH2、泵出口节点、深埋连接点（J-1）的「井口」在地下，放进来会把地面水当孔口抽走；它们的溢流只算一维
        rim = {r[0]: float(r[1]) + float(r[2]) for r in S["[JUNCTIONS]"] + S.get("[STORAGE]", [])}
        cand = [k for k in rim if k in xy and pd_.contains(Point(xy[k]))]
        gz = {k: ground(*xy[k]) for k in cand}
        buried = sorted(k for k in cand if np.isfinite(gz[k]) and rim[k] < gz[k] - BURIED_M)
        nodes = {k: [round(xy[k][0], 2), round(xy[k][1], 2)] for k in cand if k not in buried}
        v_in = sum(float(v.sum() * dt_h * 3600) for v in Hk.values())
        meta = {"name": name, "state": s, "box": D.bounds, "domain_polygon": [[round(x, 1), round(y, 1)] for x, y in D.simplify(15.0).exterior.coords[:-1]],
                "R_served_m": NO_RAIN_2D, "nodes": nodes, "ponds": [], "pumps_1d": [],
                "rain_t_h": [round(float(x), 4) for x in tt], "rain_excess_mm": [round(float(x), 4) for x in exc],
                "buried_nodes": {k: [round(rim[k], 2), round(gz[k], 2)] for k in buried},
                "note": f"Mussafah_00 {SNAME[s]}：一维 = 客户 Mussafah 模型（CMS），净雨按客户汇水区注入 {len(Hk)} 个节点；二维不直接下雨；Parsons 设施只在一维里；"
                        f"井口低于地面 {BURIED_M:g} m 以上的 {len(buried)} 个节点不参与交换"}
        (C / f"pilot_{name}_{s}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        info["states"][s] = {"src": SRC[s], "n_junctions": len(S["[JUNCTIONS]"]), "n_conduits": len(S.get("[CONDUITS]", [])),
                             "n_storage": len(S.get("[STORAGE]", [])), "n_pumps": len(S.get("[PUMPS]", [])), "n_outfalls": len(S.get("[OUTFALLS]", [])),
                             "n_inflow_nodes": len(Hk), "inflow_m3": round(v_in), "n_exchange_nodes": len(nodes), "buried_nodes": buried}
        print(f"  {s:<4} → {out.name}：井 {len(S['[JUNCTIONS]'])}、管 {len(S.get('[CONDUITS]', []))}、蓄水 {len(S.get('[STORAGE]', []))}、泵 {len(S.get('[PUMPS]', []))}；"
              f"入流节点 {len(Hk)}（{v_in:,.0f} m³），参与交换 {len(nodes)}，埋地不交换 {len(buried)}（{', '.join(buried[:6])}{' …' if len(buried) > 6 else ''}）")
    info["mesh_res_m"] = RES_M; info["n_tri_est"] = int(D.area / (0.65 * RES_M * RES_M / 3.0))
    (ROOT / "out" / f"ms_inputs_{t}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  DEM {z.shape[1]}×{z.shape[0]} @ {info['dtm']['res_m']:g} m → dem_{name}.tif；三角单元约 {info['n_tri_est']:,}")


# ------------------------------------------------------------------ 步骤表：(id, 说明, 产物, 命令列表)
def x(script, *a):
    return [P.PY, str(pathlib.Path(__file__).resolve()), "--exec", script, *map(str, a)]


def chk(name, states):
    return P.dock("python", "/work/c2d/_chk_inp.py", *[f"{name}_{s}" for s in states], threads=1)


def compute_step(t, states):
    name, W, C = name_of(t), P.WORK, P.C2D
    thr = int(os.environ.get("C2D_G5_THREADS", "4"))

    def f(dry):
        (C / "logs").mkdir(parents=True, exist_ok=True)
        names = []
        for s in states:
            n = f"{CONTAINER}_{t}_{s}"
            cmd = P.dock("sh", "-c", f"python -u /work/c2d/couple2way_v2.py {name} {s} {RES_M} {end_h(t):g} 60 > /work/c2d/logs/{name}_{s}.log 2>&1",
                         name=n, threads=thr, detach=True)
            print("  " + " ".join(cmd))
            if not dry:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL); names.append(n)
        while names and not dry:
            time.sleep(60)
            live = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
            for s in states:
                lg = C / "logs" / f"{name}_{s}.log"
                tail = lg.read_text(encoding="utf-8", errors="replace").replace("\r", "\n").strip().splitlines()[-1:] if lg.exists() else ["(还没有日志)"]
                print(f"  [{time.strftime('%H:%M')}] {s}: {tail[0][:110] if tail else ''}")
            names = [n for n in names if n in live]
        bad = [s for s in states if not (C / f"twoway_{name}_{s}.json").exists()]
        if bad and not dry:
            raise SystemExit(f"  ! {name} {bad} 容器已退出但没有结果，看 {C / 'logs'}")
    return (f"compute:{t}:{','.join(states)}", f"{t}：一二维耦合计算 {' / '.join(states)}（{RES_M} m 网格，{thr} 线程，阻塞到算完）",
            [C / f"twoway_{name}_{s}.json" for s in states] + [C / f"{name}_{s}_2d.npz" for s in states], [f])


def steps_for(t):
    name, W, C = name_of(t), P.WORK, P.C2D
    rain, dur = SCEN[t]
    return {
        "prep": ("prep", "Mussafah 计算域、汇水区参数表、客户模型 Mussafah.inp / Mussafah_baseline.inp", BATCH_PRE["outs"], [prep]),
        "build": (f"build:{t}", f"{t}：建 L1 / L2 / L2+planned（{rain} mm / {dur} h）",
                  [W / f"{name}_{s}.inp" for s in STATES3] + [C / f"pilot_{name}_{s}.json" for s in STATES3] + [C / f"dem_{name}.tif"],
                  [lambda dry: build(t, dry), chk(name, STATES3)]),
        "design": (f"design:{t}", f"{t}：按 L2+planned 的结果设计 L2+suggested v3", [W / f"{name}_L2S3.inp", C / f"pilot_{name}_L2S3.json"],
                   [x("c1_design_l2s3.py", "--rain", rain, "--tag", t, "--base", "L2P"), chk(name, ["L2S3"])]),
        "post": (f"post:{t}", f"{t}：后处理、GeoTIFF、.sww、交付目录（{rain} mm / {dur} h）", [post_out(t)],
                 [P.py("pilot_frames.py", "--name", name, "--states", ",".join(STATES), "--out", f"flood2d_ms_{t.lower()}",
                       "--region", "Mussafah_00 模型版（客户 Musaffah_00 模型汇水区并集）", "--scen", f"{rain}mm/{dur}h", "--stats-tag", t),
                  P.py("npz_to_tif.py", name, *STATES),
                  *[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", name, s, RES_M, threads=4) for s in STATES],
                  P.py("ms_post.py", "--tag", t)]),
    }


def all_steps(t):
    s = steps_for(t)
    return [s["prep"], s["build"], compute_step(t, STATES3), s["design"], compute_step(t, ["L2S3"]), s["post"]]


def parse_spec(spec, t):
    k = spec.split(":")
    if k[0] in ("prep",):
        return [steps_for(t)["prep"]]
    if k[0] in ("build", "design", "post") and len(k) == 2:
        if k[1] not in SCEN:
            raise SystemExit(f"不认识的情景 {k[1]}（认识 {' / '.join(SCEN)}）")
        return [steps_for(k[1])[k[0]]]
    if k[0] == "compute" and len(k) == 3:
        return [compute_step(k[1], k[2].split(","))]
    raise SystemExit(f"不认识的步骤：{spec}（见 plan）")


def done(outs):
    return all(pathlib.Path(o).exists() for o in outs)


def check(t) -> int:
    import datapaths
    ok = True
    print(f"Mussafah_00 · {t}（{SCEN[t][0]} mm / {SCEN[t][1]} h）· 工作目录 {P.WORK}")
    for lab, p in (("计算域", DOMAIN), ("汇水区参数表", SUBS), ("客户模型", P.WORK / "Mussafah.inp"), ("L1 底网", P.WORK / "Mussafah_baseline.inp"),
                   ("DTM", datapaths.get("dtm")), ("耦合程序", P.C2D / "couple2way_v2.py"), ("读入检查", P.C2D / "_chk_inp.py"),
                   (".sww 转换", P.C2D / "npz_to_sww.py")):
        e = bool(p) and pathlib.Path(p).exists(); ok &= e
        print(f"  {'✓' if e else '✗'} {lab:<8} {p}")
    try:
        img = subprocess.run(["docker", "image", "inspect", P.IMAGE], capture_output=True, timeout=30).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        img = False
    ok &= img
    print(f"  {'✓' if img else '✗'} 镜像     {P.IMAGE}")
    print("步骤（✓ = 产物已在）")
    for sid, desc, outs, _ in all_steps(t):
        miss = [str(o) for o in outs if not pathlib.Path(o).exists()]
        print(f"  {'·' if miss else '✓'} {sid:<26} {desc}" + (f"\n{'':32}缺 {len(miss)} 个，如 {miss[0]}" if miss else ""))
    print("\n数据与环境齐了。" if ok else "\n有 ✗ 项：先补齐。")
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--exec":       # 内部：执行打过补丁的脚本
        return load_patched(sys.argv[2], sys.argv[3:])
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["check-patches"]:
        return check_patches()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["check", "plan", "run"])
    ap.add_argument("steps", nargs="*")
    ap.add_argument("--tag", required=True, choices=list(SCEN))
    ap.add_argument("--force", action="store_true", help="产物已在也重做")
    ap.add_argument("--dry", action="store_true", help="只打印命令")
    a = ap.parse_args()
    if a.cmd == "check":
        return check(a.tag)
    if a.cmd == "run" and not a.dry:
        for d in (ROOT / "out", ROOT / "console" / "data", P.C2D / "logs"):
            d.mkdir(parents=True, exist_ok=True)
    steps = all_steps(a.tag) if a.cmd == "plan" or a.steps == ["all"] else [s for sp in a.steps for s in parse_spec(sp, a.tag)]
    for sid, desc, outs, cmds in steps:
        if a.cmd == "plan":
            print(f"\n[{'✓' if done(outs) else ' '}] {sid} — {desc}")
            for c in cmds:
                print("      " + ("（内置步骤）" if callable(c) else " ".join(map(str, c))))
            continue
        if done(outs) and not a.force:
            print(f"= {sid}：产物已在，跳过（--force 重做）"); continue
        print(f"\n▶ {sid} — {desc}", flush=True)
        for c in cmds:
            if callable(c):
                c(a.dry); continue
            print("  $ " + " ".join(map(str, c)), flush=True)
            if not a.dry:
                r = subprocess.run(c, cwd=ROOT, env=P.env())
                if r.returncode:
                    raise SystemExit(f"  ! 失败（退出码 {r.returncode}）：{sid}")
        if not a.dry and not done(outs):
            raise SystemExit(f"  ! {sid} 跑完了但产物不全：{[str(o) for o in outs if not pathlib.Path(o).exists()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
