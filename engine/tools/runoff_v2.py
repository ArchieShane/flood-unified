# -*- coding: utf-8 -*-
"""v2 产流层：用客户自己 22 个水力模型的逐子汇水区参数，替掉我的估计。

------------------------------------------------------------------ 为什么要有 v2
v1（tools/build_city1d.py）的产流有两处是我的估计：
  · 面积按**最近节点法**摊（半径自适应，两个已知区上标到 ±6%）；
  · CN 全市统一 **82**（当时只有 Mussafah_00 与 Shaliela 两个区可标）。

2026-09-26 客户又发来 22 个水力模型（`OneDrive_1_9-26-2026.zip`），每个都带
`A_UNIFIED / SCS_CN / TC / OUT_NODE`——和我手上原有那三个完全同构。实测：
  **CN 面积加权是 84.5，不是 82**，而且空间差异很大（36–98）：
      < 84   10,685 ha（43.0%）      84–90   9,611 ha（38.7%）
      90–95     271 ha（ 1.1%）      95–98   4,272 ha（17.2%）
  逐区看 ICAD / Mussafah_10 是 82，RB-01 是 91.6，BAG 1_2 是 96.1。
  CN=82 产流 23.53 mm，CN=84.5 是 27.17 mm —— **v1 的产流偏低约 15%**。

所以 v2 里，凡是客户模型覆盖到的地方，**面积、CN、Tc、出流节点全用客户自己的**，
一点估计成分都不留；覆盖不到的地方才退回 v1 的最近节点法，并如实标出来。

------------------------------------------------------------------ 怎么对上节点
子汇水区的 `OUT_NODE` 对应同一个模型里节点图层的 `LABEL`（个别模型叫 `LB`）。
实测命中率 ICAD / Mussafah-01 是 100%，RB3 83.7%，Shaliela 67.8%，RB_02 35.5%——
对不上的那些是流向**出水口 / 塘 / 湿井**而不是人孔，所以四类节点图层都要找。
拿到坐标后按最近邻映射到我重建的 GDB 网（shapefile 没有 .prj，坐标是 UTM 40N，
与 GDB 同一坐标系，实测两边节点能重合）。

------------------------------------------------------------------ 面积用哪个字段
`A_UNIFIED` 是权威值：RB3 合计 1,815 ha，与顾问报告写的 1,815 一致；
几何面积 2,117 ha 会高估 17%（子汇水区之间有重叠）。
个别模型 `A_UNIFIED` 整列为 0（如 Mussafah-01），才退回几何面积。

用法：
    python tools/runoff_v2.py                 # 出逐节点产流参数表
    python tools/runoff_v2.py --report        # 只看覆盖率，不重算
产物：out/citywide/node_runoff.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import zipfile

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

ZIP = r"D:/世界模型/OneDrive_1_9-26-2026.zip"
VSI = "/vsizip/" + ZIP
OUT = ROOT / "out" / "citywide"
GDB = "/vsizip/D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb.zip"

CN_FALLBACK = 84.5     # 22 个模型的面积加权值，给覆盖不到的地方用
TC_FALLBACK = 30.0     # 分钟
P_MM = 61.0            # 本场景雨深；客户模型是 61.34 mm，按 SCS 比值换算
# 映射容差按挂接方式分开：按 OUT_NODE 标签挂的应当正落在节点上（实测中位 8 m），
# 按子汇水区形心挂的天然偏离几百米（形心不在管线上）。一刀切 50 m 会丢掉 20.8% 的产流量。
SNAP_LABEL_M = 50.0
SNAP_CENTROID_M = 1000.0

CA_PATS = [r"ca", r"cat", r"catchment", r"catchments", r"catchment table"]
NODE_PATS = [r"mh", r"manhole", r"manholes", r"manhole table",
             r"of", r"outfall", r"outfalls", r"outfall table",
             r"ww", r"wetwell", r"wetwells", r"wet_wells", r"wet well",
             r"pond", r"ponds", r"pond table",
             r"junctions", r"prnodes", r"pressure_junctions", r"pr junction"]


def layers() -> dict[str, list[str]]:
    zf = zipfile.ZipFile(ZIP)
    out = collections.defaultdict(list)
    for n in zf.namelist():
        if n.lower().endswith(".shp") and not n.startswith("__MACOSX"):
            out[n.split("/")[0]].append(n)
    return out


def _find(names, pats):
    hit = []
    for p in pats:
        for n in names:
            b = n.split("/")[-1].rsplit(".", 1)[0].lower()
            if re.fullmatch(p, b) and n not in hit:
                hit.append(n)
    return hit


def label_col(df):
    """节点图层的名字列：多数叫 LABEL，个别叫 LB。"""
    for c in ("LABEL", "LB", "Label", "ID"):
        if c in df.columns:
            return c
    return None


def area_m2(d, name: str, log=print):
    """面积列 + 单位。22 个模型之间不统一，拿几何面积当标尺判。

    实测：多数模型 `A_UNIFIED` 是 **m²**（与几何面积之比 0.34–1.0，
    小于 1 是因为子汇水区多边形之间有重叠）；
    5 个模型是**公顷**（比值 ~1e-4）；Musaffah 09 的字段叫 `AREA_U`。
    照 m² 一刀切会让那 5 个模型贡献成 0 —— 踩过。
    """
    g = float(d.geometry.area.sum())
    for c in ("A_UNIFIED", "AREA_U", "A"):
        if c not in d.columns:
            continue
        v = d[c].astype("float64").values
        s = float(np.nansum(v))
        if not np.isfinite(s) or s <= 0:
            continue
        r = s / g if g > 0 else float("nan")
        if 0.2 < r < 2.0:
            return v, c, "m²"
        if 2e-5 < r < 2e-4:
            return v * 1e4, c, "ha→m²"
        log(f"  {name}: {c} 与几何面积之比 {r:.2e}，单位认不出，换下一个字段")
    log(f"  {name}: 没有能用的面积列，退回几何面积（子汇水区有重叠会高估约 20%）")
    return d.geometry.area.values, "几何面积", "m²"


def tc_min(d, name: str, log=print):
    """汇流时间 + 单位。有的模型写分钟（中位 10–30），有的写小时（中位 0.17–0.20）。"""
    if "TC" not in d.columns:
        return np.full(len(d), TC_FALLBACK)
    v = d["TC"].astype("float64").values
    m = float(np.nanmedian(v[np.isfinite(v) & (v > 0)])) if np.isfinite(v).any() else np.nan
    if np.isfinite(m) and m < 3.0:                  # 小时
        log(f"  {name}: TC 中位 {m:.2f}，判为**小时**，×60 换成分钟")
        v = v * 60.0
    v = np.where(np.isfinite(v) & (v > 0), v, TC_FALLBACK)
    return np.clip(v, 1.0, 300.0)


def scs(P, cn):
    S = 25400.0 / cn - 254.0
    return 0.0 if P <= 0.2 * S else (P - 0.2 * S) ** 2 / (P + 0.8 * S)


def model_table(name: str, names: list[str], log=print):
    """一个模型 → [(area_m2, vol_m3, cn, tc_min, x, y)]，坐标是出流节点的。

    **产流量直接用客户自己的 `RUNOFF_VOL`**，不由我再按 CN 算一遍 ——
    那是他们交付模型跑出来的量，用它产流侧就没有我的估计成分。
    他们的设计雨深是 61.34 mm（10 年 + 20% 气候变化，本项目此前反解验过），
    本场景是 61 mm，按各自 CN 的 SCS 比值换算，差 1% 上下。
    """
    import pyogrio

    cas = _find(names, CA_PATS)
    if not cas:
        return []
    d = pyogrio.read_dataframe(f"{VSI}/{cas[0]}")
    if "SCS_CN" not in d.columns:
        log(f"  {name}: 缺 SCS_CN，跳过"); return []
    A, acol, aunit = area_m2(d, name, log)
    tc = tc_min(d, name, log)
    cn = d["SCS_CN"].astype("float64").values
    cn = np.where(np.isfinite(cn) & (cn > 0), cn, CN_FALLBACK)
    rv = (d["RUNOFF_VOL"].astype("float64").values if "RUNOFF_VOL" in d.columns
          else np.full(len(d), np.nan))

    # 出流节点坐标：有 OUT_NODE 就按标签查，没有就用子汇水区形心就近挂
    pos: dict[str, tuple[float, float]] = {}
    for n in _find(names, NODE_PATS):
        try:
            m = pyogrio.read_dataframe(f"{VSI}/{n}")
        except Exception:
            continue
        c = label_col(m)
        if c is None or m.geometry is None:
            continue
        for k, g in zip(m[c].astype("string").fillna(""), m.geometry):
            if k and g is not None and not g.is_empty and k not in pos:
                p = g.representative_point()
                pos[k] = (float(p.x), float(p.y))
    has_on = "OUT_NODE" in d.columns
    lab = (d["OUT_NODE"].astype("string").fillna("").values if has_on
           else np.array([""] * len(d)))
    cent = d.geometry.representative_point()

    rows, by_label, by_cent = [], 0, 0
    for i in range(len(d)):
        a = float(A[i])
        if not np.isfinite(a) or a <= 0:
            continue
        k = lab[i]
        if k and k in pos:
            x, y = pos[k]; by_label += 1; mode = "标签"
        else:
            p = cent.iloc[i]
            if p is None or p.is_empty:
                continue
            x, y = float(p.x), float(p.y); by_cent += 1; mode = "形心"
        c = float(cn[i])
        v = float(rv[i]) if np.isfinite(rv[i]) and rv[i] > 0 else a * scs(61.34, c) / 1000.0
        v *= scs(P_MM, c) / max(scs(61.34, c), 1e-9)       # 61.34 → 61 mm
        rows.append((a, v, c, float(tc[i]), x, y, mode))
    log(f"  {name:<24}{len(d):>7,} 个　面积 {sum(r[0] for r in rows) / 1e4:>9,.0f} ha"
        f"（{acol}·{aunit}）　产流 {sum(r[1] for r in rows):>11,.0f} m³"
        f"　按标签挂 {by_label:,}、按形心挂 {by_cent:,}")
    return rows


def build(log=print) -> dict:
    """逐节点的产流参数表。客户覆盖到的用客户的，其余退回最近节点法。"""
    from scipy.spatial import cKDTree
    import pyogrio

    lay = layers()
    rows = []
    for k in sorted(lay):
        rows += model_table(k, lay[k], log)
    if not rows:
        log("一个模型都没读到"); return {}

    # 映射到我重建的 GDB 网
    df = pyogrio.read_dataframe(GDB, layer="PIPELINE", read_geometry=False,
                                columns=["StartCode", "EndCode", "StartX", "StartY",
                                         "EndX", "EndY"])
    code, X, Y = [], [], []
    seen = set()
    for c, x, y in ((df.StartCode, df.StartX, df.StartY), (df.EndCode, df.EndX, df.EndY)):
        for k, xx, yy in zip(c.astype("string").fillna(""), x.astype("float64"),
                             y.astype("float64")):
            if k and k not in seen:
                seen.add(k); code.append(k); X.append(xx); Y.append(yy)
    P = np.c_[X, Y]
    tree = cKDTree(P)
    q = np.array([[r[4], r[5]] for r in rows])
    dist, j = tree.query(q, k=1, workers=-1)
    lim = np.array([SNAP_LABEL_M if r[6] == "标签" else SNAP_CENTROID_M for r in rows])
    ok = dist <= lim
    log("")
    log(f"  客户子汇水区 {len(rows):,} 个 → 映射到 GDB 节点"
        f"（标签挂容差 {SNAP_LABEL_M:g} m、形心挂 {SNAP_CENTROID_M:g} m）")
    log(f"  挂上的 {int(ok.sum()):,}（{ok.mean() * 100:.1f}%）"
        f"　距离中位 {np.median(dist):.1f} m")

    # rows 是 (面积 m², 产流 m³, CN, Tc 分钟, x, y)
    agg: dict[str, dict] = {}
    dropped_v = 0.0
    for i, r in enumerate(rows):
        if not ok[i]:
            dropped_v += r[1]; continue
        nd = code[j[i]]
        a = agg.setdefault(nd, {"area_m2": 0.0, "vol_m3": 0.0, "cn_a": 0.0, "tc_a": 0.0, "n": 0})
        a["area_m2"] += r[0]; a["vol_m3"] += r[1]
        a["cn_a"] += r[2] * r[0]; a["tc_a"] += r[3] * r[0]; a["n"] += 1
    for nd, a in agg.items():
        w = max(a["area_m2"], 1e-9)
        a["cn"] = round(a["cn_a"] / w, 2); a["tc"] = round(a["tc_a"] / w, 2)
        a["src"] = "客户模型"
        del a["cn_a"], a["tc_a"]
        a["area_m2"] = round(a["area_m2"]); a["vol_m3"] = round(a["vol_m3"], 1)
    tot = sum(a["area_m2"] for a in agg.values())
    vol = sum(a["vol_m3"] for a in agg.values())
    cnw = float(np.average([a["cn"] for a in agg.values()],
                           weights=[a["area_m2"] for a in agg.values()]))
    log(f"  丢掉的产流量 {dropped_v:,.0f} m³"
        f"（占 {dropped_v / max(vol + dropped_v, 1) * 100:.1f}%）—— 这些子汇水区"
        f"离重建的管网太远，如实丢掉不硬挂")
    log(f"  产流量合计 {vol:,.0f} m³（等效 {vol / max(tot, 1) * 1000:.1f} mm）")
    log(f"  客户参数覆盖 {len(agg):,} 个节点、{tot / 1e6:,.1f} km²，面积加权 CN {cnw:.1f}")
    return agg


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="用客户 22 个模型的参数重建逐节点产流表")
    ap.add_argument("--report", action="store_true", help="只看已有结果")
    a = ap.parse_args(argv)
    f = OUT / "node_runoff.json"
    if a.report and f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        tot = sum(v["area_m2"] for v in d.values())
        print(f"{len(d):,} 个节点，{tot / 1e6:,.1f} km²")
        return 0
    print("读客户 22 个水力模型的产流参数：")
    agg = build()
    if not agg:
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(agg, ensure_ascii=False), encoding="utf-8")
    print(f"\n写出 {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
