# -*- coding: utf-8 -*-
"""L2+suggested 地面泵并入优化（2026-10-24，用户：「先做造价–效果曲线和地面泵并入候选」）：洼坑代理模型。

一维目标 J 只看检查井溢流，看不见「水流进封闭洼坑、管网收不走」—— 地面泵抽洼坑，J 不变。所以给热点附近的洼坑做一个快速代理：
  洼坑     DTM 填洼（skimage 形态学重建）后深 ≥ 0.10 m、容积 ≥ 30 m³、离热点 ≤ 200 m 的连片；
           剔除：含 DTM = 0.00 m 格的（缺测填 0）、最深点在已建地块内且 < 0 m 的（私人地块深坑）——同 l2s_sumps_v3 第 2 步
  库容曲线 用二维网格单元（npz 的 elev / area，和验收判据同一套网格）：水位 → 容积、≥0.30 m 面积、≥0.10 m 面积
  进水过程 跑一次「吸水」二维：基线态 + 每个洼坑放一台 20 m³/s 的地面泵（关泵水深 0），抽走的累计量就是洼坑的进水过程线 I_b(t)
           （couple2way_v2 2026-10-24 起每帧记各取水点累计抽水量 cum_m3）
  来水井   基线态溢流的井，沿 DTM 最陡下降追踪到底，落在洼坑（外扩 15 m）里的算它的来水井；
           某方案下的进水 = I_b(t) ×（直接产流 + 来水井溢流_方案）/（直接产流 + 来水井溢流_基线），直接产流 = max(∫I_b − 来水井溢流_基线, 0.2 ∫I_b)
  排空     基线（L2P）二维里洼坑雨后的退水速度（−dV/dt 的 90 % 分位）= 自然排空能力 d（井口回流、下渗），各方案同样扣（2026-10-24 加：
           不扣时代理的「雨停 6 h 残留」比二维实算大约 7 倍）
  演算     V(t+Δt) = clip(V + (I − q_泵·[V>0] − d·[V>0]) Δt, 0, V_满)；超过 V_满 的溢走（不再计）
  度量     每个洼坑：峰值 ≥0.30 m 面积 a30（判据 A）；按热点合计，除以基线同值进 J。
           雨停 6 h 时 ≥0.10 m 面积 a10（判据 C）也算，但不进 J：2026-10-24 用 OPTC / OPTCS / OPTCS2 的二维实算检验，
           a30 有无一致 95 %、相关 0.92；a10 相关只有 −0.16（退水快慢取决于雨后管网状态，代理估不准），C 留给二维验证（用户同意）

子命令：
  build     生成 pits.json 和「吸水」态的 pilot（之后用 l2opt_run2d 跑 <前缀>_<雨>:PITSINK）
  inflow    读吸水二维结果 + 基线一维溢流，补进水过程线、来水井、直接产流
  validate  用已跑过二维的方案态（如 OPTC,OPTCS,OPTCS2）比代理和二维实算
用法：python tools/l2s_pits.py build --run RDA_96C --hs ADM-75,...
      python tools/l2s_pits.py inflow --run RDA_96C
      python tools/l2s_pits.py validate --run RDA_96C --states OPTC,OPTCS,OPTCS2
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
DTM = "D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF"
SINK_Q = 20.0
LEVEL_STEP = 0.02
A30, A10, REC_H = 0.30, 0.10, 6.0


# ---------------------------------------------------------------- 代理（greedy / prune 调用）
class Pits:
    def __init__(self, path):
        self.D = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.P = {p["id"]: p for p in self.D["pits"]}
        for p in self.P.values():
            p["_V"] = np.array(p["curve"]["vol_m3"])
            p["_a30"] = {h: np.array(v) for h, v in p["curve"]["a30_m2"].items()}; p["_a10"] = {h: np.array(v) for h, v in p["curve"]["a10_m2"].items()}

    def route(self, pid, storm, nf, q=0.0):
        """一个洼坑在一场雨下的 {热点: (峰值 a30, 雨停 6 h a10)}，nf = 该方案的一维溢流 {井: (m³, m³/s)}"""
        p = self.P[pid]; S = p["storms"].get(storm)
        if not S:
            return {h: (0.0, 0.0) for h in p["_a30"]}
        o_b = S["contrib_overflow_m3"]; o_c = sum(nf.get(w, (0.0, 0.0))[0] for w in S["contrib"])
        f = (S["direct_m3"] + o_c) / max(S["direct_m3"] + o_b, 1e-6)
        t = np.array(S["t_h"]); I = np.array(S["inflow_m3s"]) * f; dt = float(np.diff(t).mean()) * 3600.0
        V = 0.0; vmax = float(p["_V"][-1]); vpk = 0.0; v6 = 0.0
        t6 = self.D["rain_end_h"][storm] + REC_H
        d = float(S.get("drain_m3s", 0.0))
        for k in range(len(t)):
            V = min(max(V + (I[k] - q - d) * dt, 0.0), vmax)
            vpk = max(vpk, V)
            if t[k] <= t6 + 1e-6:
                v6 = V
        return {h: (float(np.interp(vpk, p["_V"], p["_a30"][h])), float(np.interp(v6, p["_V"], p["_a10"][h]))) for h in p["_a30"]}

    def metrics(self, storm, nf, sumps, use_c=False):
        """各热点 a30（use_c 时加 a10）合计，m²；sumps = {洼坑: q m³/s}"""
        out = {}
        for pid in self.P:
            for h, (a, b) in self.route(pid, storm, nf, sumps.get(pid, 0.0)).items():
                out[h] = out.get(h, 0.0) + a + (b if use_c else 0.0)
        return out


PIT_MIN_M2 = 100.0     # 基线洼坑度量 < 100 m² 的热点不进 J（太小，比值不稳；Claude 自定）


class PitTerm:
    """贪心 / 剪枝用：metrics 里加 "pit"（各热点 a30 + a10，m²），J 里加 Σ 雨 Σ 热点 pit / 基线 pit"""

    def __init__(self, path, C):
        self.M = Pits(path); self.C = C

    def sumps(self, ids):
        q = {}
        for i in ids:
            for pid, v in (self.C[i].get("pit_q") or {}).items():
                q[pid] = q.get(pid, 0.0) + v
        return q

    def add(self, m, storm, nf, ids):
        m = dict(m); m["pit"] = {h: round(v, 1) for h, v in self.M.metrics(storm, nf, self.sumps(ids)).items()}
        return m


def pit_j(m, b, hs):
    """一场雨的洼坑项（b = 基线 metrics）"""
    if "pit" not in m or "pit" not in b:
        return 0.0
    return sum(m["pit"].get(h, 0.0) / b["pit"][h] for h in hs if b["pit"].get(h, 0.0) >= PIT_MIN_M2)


# ---------------------------------------------------------------- build
def build(a):
    import geopandas as gpd
    import rasterio
    from rasterio.features import shapes
    from scipy import ndimage
    from shapely.geometry import Point, mapping, shape
    from skimage.morphology import reconstruction
    H = __import__("hotspot_src").load(32640).set_index("hotspot_id")
    hs = a.hs.split(",")
    xs = [H.loc[h].geometry.x for h in hs]; ys = [H.loc[h].geometry.y for h in hs]
    pad = 800.0
    with rasterio.open(DTM) as ds:
        win = rasterio.windows.from_bounds(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad, ds.transform)
        z = ds.read(1, window=win).astype(float); tf = ds.window_transform(win)
    bad = z < -100
    z[bad] = np.nanmax(z[~bad])
    seed = z.copy(); seed[1:-1, 1:-1] = z.max()
    fill = reconstruction(seed, z, method="erosion")
    dep = fill - z
    lab, n = ndimage.label(dep > 0.01)
    cell = abs(tf.a * tf.e)
    vols = ndimage.sum(dep, lab, index=np.arange(1, n + 1)) * cell
    maxd = ndimage.maximum(dep, lab, index=np.arange(1, n + 1))
    P = gpd.read_file(a.sitedata, layer="plots"); built = P[P.construction_status.astype(str).str.lower().str.startswith("constructed")]
    pits, skipped = [], []
    for i in np.flatnonzero((vols >= a.min_vol) & (maxd >= 0.10)):
        m = lab == i + 1
        g = [shape(s) for s, v in shapes(m.astype("uint8"), mask=m, transform=tf) if v == 1]
        from shapely.ops import unary_union
        g = unary_union(g)
        d = {h: H.loc[h].geometry.distance(g) for h in hs}; h = min(d, key=d.get)
        if d[h] > a.radius:
            continue
        r, c = np.unravel_index(np.argmax(np.where(m, dep, -1)), m.shape)
        bx, by = rasterio.transform.xy(tf, r, c)
        zb = float(z[r, c]); spill = float(fill[r, c])
        rec = {"hotspot": h, "hs_dist_m": round(d[h]), "x": round(bx, 1), "y": round(by, 1), "bottom_m": round(zb, 2), "spill_m": round(spill, 2),
               "dtm_vol_m3": round(float(vols[i])), "max_depth_m": round(float(maxd[i]), 2), "geom": g}
        if (z[m] == 0.0).any() or bad[m].any():
            rec["why"] = "DTM 填 0 / 缺测"; skipped.append(rec); continue
        if zb < 0 and built.intersects(Point(bx, by)).any():
            rec["why"] = "已建地块内深坑"; skipped.append(rec); continue
        pits.append(rec)
    # 库容曲线：二维网格单元
    zb_ = np.load(C2D / f"{a.prefix}_{a.storms.split(',')[0]}_{a.base}_2d.npz")
    xy = zb_["xy"].astype(float); el = zb_["elev"].astype(float); ar = zb_["area"].astype(float)
    import shapely
    keep = []
    for k, p in enumerate(pits):
        poly = p["geom"].buffer(5.0)
        c = shapely.contains_xy(poly, xy[:, 0], xy[:, 1])
        if c.sum() < 1:
            p["why"] = "比二维网格单元还小"; skipped.append(p); continue
        e = el[c]; A = ar[c]
        lv = np.arange(e.min(), max(p["spill_m"], e.min() + LEVEL_STEP) + 1e-9, LEVEL_STEP)
        vol = [float((np.clip(L - e, 0, None) * A).sum()) for L in lv]
        # 面积按热点 200 m 圈分开算（一个大洼坑可能跨几个热点，判据按圈计）
        rings = {h: np.hypot(xy[c, 0] - H.loc[h].geometry.x, xy[c, 1] - H.loc[h].geometry.y) <= a.radius for h in hs}
        rings = {h: r for h, r in rings.items() if r.any()}
        p.update(id=f"PIT{len(keep) + 1:02d}", n_cells=int(c.sum()), hotspots=sorted(rings),
                 curve={"level_m": [round(float(x), 3) for x in lv], "vol_m3": [round(v, 1) for v in vol],
                        "a30_m2": {h: [round(float(A[r & ((L - e) >= A30)].sum()), 1) for L in lv] for h, r in rings.items()},
                        "a10_m2": {h: [round(float(A[r & ((L - e) >= A10)].sum()), 1) for L in lv] for h, r in rings.items()}})
        if not rings:
            p["why"] = "二维网格单元都不在热点圈内"; skipped.append(p); continue
        if p["curve"]["vol_m3"][-1] < a.min_vol:
            p["why"] = f"二维网格上的库容只有 {p['curve']['vol_m3'][-1]:.0f} m³"; skipped.append(p); continue
        keep.append(p)
    for k, p in enumerate(keep):
        p["id"] = f"PIT{k + 1:02d}"
    o = ROOT / "out" / "l2opt" / a.run
    info = {s: json.loads((ROOT / "out" / f"{a.prefix.lower()}_inputs_{s}_L1.json").read_text(encoding="utf-8"))["dur_h"] for s in a.storms.split(",")}
    out = {"rule": __doc__.split("子命令")[0].strip(), "base": a.base, "storms": a.storms.split(","), "rain_end_h": info,
           "pits": [{**{k: v for k, v in p.items() if k != "geom"}, "poly": [list(c) for c in max(getattr(p["geom"].buffer(5.0), "geoms", [p["geom"].buffer(5.0)]), key=lambda q: q.area).exterior.coords],
                     "storms": {}} for p in keep],
           "skipped": [{**{k: v for k, v in p.items() if k not in ("geom", "curve")}} for p in skipped]}
    (o / "pits.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    (o / "pits.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs_epsg": 32640, "features": [
        {"type": "Feature", "properties": {"id": p.get("id", ""), "hotspot": p["hotspot"], "why": p.get("why", ""), "vol": p["dtm_vol_m3"], "depth": p["max_depth_m"]},
         "geometry": mapping(p["geom"])} for p in keep + skipped]}, ensure_ascii=False), encoding="utf-8")
    for p in keep:
        print(f"  {p['id']} {p['hotspot']}（{p['hs_dist_m']} m）坑底 {p['bottom_m']} 溢出 {p['spill_m']} m，DTM 容积 {p['dtm_vol_m3']} m³，二维 {p['n_cells']} 格 / {p['curve']['vol_m3'][-1]:.0f} m³")
    for p in skipped:
        print(f"  跳过 {p['hotspot']} ({p['x']:.0f}, {p['y']:.0f}) {p['dtm_vol_m3']} m³：{p['why']}")
    # 吸水态 pilot
    for s in a.storms.split(","):
        name = f"{a.prefix}_{s}"
        shutil.copyfile(WORK / f"{name}_{a.base}.inp", WORK / f"{name}_PITSINK.inp")
        meta = json.loads((C2D / f"pilot_{name}_{a.base}.json").read_text(encoding="utf-8"))
        meta["state"] = "PITSINK"
        meta["pumps2d"] = list(meta.get("pumps2d") or []) + [{"id": p["id"], "q_m3s": SINK_Q, "off_m": 0.0, "sump_polygon": q["poly"], "xy": [p["x"], p["y"]], "to_xy": None}
                                                             for p, q in zip(keep, out["pits"])]
        (C2D / f"pilot_{name}_PITSINK.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"→ {o / 'pits.json'}：洼坑 {len(keep)} 个，跳过 {len(skipped)} 个；吸水态 pilot 已写（{a.storms}）")


# ---------------------------------------------------------------- inflow
def d8_end(z, tf, x, y, steps=4000):
    import rasterio
    r, c = rasterio.transform.rowcol(tf, x, y)
    H, W = z.shape
    for _ in range(steps):
        if not (1 <= r < H - 1 and 1 <= c < W - 1):
            break
        w = z[r - 1:r + 2, c - 1:c + 2] - z[r, c]
        w = w / np.array([[1.414, 1, 1.414], [1, 1, 1], [1.414, 1, 1.414]])
        k = int(np.argmin(w))
        if w.flat[k] >= 0:
            break
        r, c = r + k // 3 - 1, c + k % 3 - 1
    return rasterio.transform.xy(tf, r, c)


def inflow(a):
    import rasterio
    from shapely.geometry import Point, Polygon
    sys.path.insert(0, str(HERE))
    import c1_design_l2s4 as V4
    o = ROOT / "out" / "l2opt" / a.run
    D = json.loads((o / "pits.json").read_text(encoding="utf-8"))
    base = D["base"]
    inp = (WORK / f"{a.prefix}_{D['storms'][0]}_{base}.inp").read_text(encoding="utf-8")
    nxy = {q[0]: (float(q[1]), float(q[2])) for q in (ln.split() for ln in re.search(r"\[COORDINATES\]\n(.*?)(?:\n\[|\Z)", inp, re.S).group(1).splitlines()) if len(q) >= 3}
    polys = {p["id"]: Polygon(p["poly"]).buffer(15.0) for p in D["pits"]}
    xs = [p["x"] for p in D["pits"]]; ys = [p["y"] for p in D["pits"]]
    with rasterio.open(DTM) as ds:
        win = rasterio.windows.from_bounds(min(xs) - 2500, min(ys) - 2500, max(xs) + 2500, max(ys) + 2500, ds.transform)
        z = ds.read(1, window=win).astype(float); tf = ds.window_transform(win)
    z[z < -100] = 99.0
    import shapely
    for s in D["storms"]:
        nf = V4.node_flood(C2D / f"{a.prefix}_{s}_{base}_2way.rpt")
        zb = np.load(C2D / f"{a.prefix}_{s}_{base}_2d.npz"); xyb = zb["xy"].astype(float); arb = zb["area"].astype(float)
        frb = zb["frames"].astype(np.float32); tb = zb["t_h"].astype(float); rain_end = D["rain_end_h"][s]
        R = json.loads((C2D / f"twoway_{a.prefix}_{s}_PITSINK.json").read_text(encoding="utf-8"))
        zz = np.load(C2D / f"{a.prefix}_{s}_PITSINK_2d.npz"); t = zz["t_h"].astype(float)
        cum = {q["id"]: np.array(q["cum_m3"], float) for q in R["pumps2d"]}
        ends = {}
        for w, v in nf.items():
            if v["vol_m3"] > 1.0 and w in nxy:
                ex, ey = d8_end(z, tf, *nxy[w]); pt = Point(ex, ey)
                for pid, pg in polys.items():
                    if pg.contains(pt):
                        ends.setdefault(pid, []).append(w)
        for p in D["pits"]:
            cu = cum.get(p["id"])
            if cu is None or len(cu) != len(t):
                print(f"  ! {s} {p['id']} 没有逐帧抽水量"); continue
            I = np.diff(np.r_[0.0, cu]) / (np.diff(np.r_[0.0, t]) * 3600.0)
            tot = float(cu[-1]); cw = ends.get(p["id"], [])
            ob = float(sum(nf[w]["vol_m3"] for w in cw))
            cb = shapely.contains_xy(Polygon(p["poly"]), xyb[:, 0], xyb[:, 1])
            Vb = (frb[:, cb] * arb[cb]).sum(1) if cb.any() else np.zeros(len(tb))
            dV = -np.diff(Vb) / (np.diff(tb) * 3600.0); m_ = (tb[1:] >= rain_end) & (Vb[1:] > 1.0)
            drain = float(np.percentile(np.clip(dV[m_], 0, None), 90)) if m_.any() else 0.0
            p["storms"][s] = {"t_h": [round(float(x), 4) for x in t], "inflow_m3s": [round(float(x), 5) for x in I], "inflow_total_m3": round(tot), "drain_m3s": round(drain, 4),
                              "contrib": cw, "contrib_overflow_m3": round(ob, 1), "direct_m3": round(max(tot - ob, 0.2 * tot), 1)}
            print(f"  {s} {p['id']} {p['hotspot']}：进水 {tot:,.0f} m³，峰值 {I.max():.3f} m³/s，来水井 {len(cw)} 口（基线溢流 {ob:,.0f} m³），雨后自然排空 {drain:.3f} m³/s")
    (o / "pits.json").write_text(json.dumps(D, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"→ {o / 'pits.json'}")


# ---------------------------------------------------------------- validate
def validate(a):
    import shapely
    from shapely.geometry import Polygon
    sys.path.insert(0, str(HERE))
    import c1_design_l2s4 as V4
    o = ROOT / "out" / "l2opt" / a.run
    M = Pits(o / "pits.json")
    import geopandas as gpd
    HH = __import__("hotspot_src").load(32640).set_index("hotspot_id")
    rows = []
    for st in a.states.split(","):
        for s in M.D["storms"]:
            nfv = V4.node_flood(C2D / f"{a.prefix}_{s}_{st}_2way.rpt"); nf = {k: (v["vol_m3"], v["rate_m3s"]) for k, v in nfv.items()}
            meta = json.loads((C2D / f"pilot_{a.prefix}_{s}_{st}.json").read_text(encoding="utf-8"))
            zz = np.load(C2D / f"{a.prefix}_{s}_{st}_2d.npz"); xy = zz["xy"].astype(float); ar = zz["area"].astype(float); fr = zz["frames"].astype(np.float32)
            hm = zz["hmax"].astype(float); t = zz["t_h"].astype(float); k6 = int(np.searchsorted(t, M.D["rain_end_h"][s] + REC_H))
            sumps = {}
            for q in meta.get("pumps2d") or []:
                pt = shapely.Point(*q["xy"])
                for pid, p in M.P.items():
                    if Polygon(p["poly"]).buffer(15.0).contains(pt):
                        sumps[pid] = sumps.get(pid, 0.0) + q["q_m3s"]
            for pid, p in M.P.items():
                if s not in p["storms"]:
                    continue
                c0 = shapely.contains_xy(Polygon(p["poly"]), xy[:, 0], xy[:, 1])
                pr = M.route(pid, s, nf, sumps.get(pid, 0.0))
                for h, pred in pr.items():
                    c = c0 & (np.hypot(xy[:, 0] - HH.loc[h].geometry.x, xy[:, 1] - HH.loc[h].geometry.y) <= 200.0)
                    act = (float(ar[c & (hm >= A30)].sum()), float(ar[c & (fr[min(k6, len(t) - 1)] >= A10)].sum()))
                    rows.append((st, s, pid, h, sumps.get(pid, 0.0), act, pred))
    print(f"{'态':<7}{'雨':<7}{'洼坑':<6}{'热点':<9}{'泵':>6} | {'实算 a30':>9}{'代理':>8} | {'实算 a10@6h':>11}{'代理':>8}")
    for st, s, pid, h, q, act, pred in rows:
        if max(act + pred) > 0:
            print(f"{st:<7}{s:<7}{pid:<6}{h:<9}{q:>6.2f} | {act[0]:>9.0f}{pred[0]:>8.0f} | {act[1]:>11.0f}{pred[1]:>8.0f}")
    A = np.array([r[5] for r in rows]); B = np.array([r[6] for r in rows])
    for j, nm in ((0, "a30"), (1, "a10@6h")):
        ok = (A[:, j] > 0) | (B[:, j] > 0)
        agree = ((A[:, j] > 0) == (B[:, j] > 0)).mean()
        print(f"{nm}：有无一致 {agree:.0%}；合计 实算 {A[:, j].sum():,.0f} / 代理 {B[:, j].sum():,.0f} m²；相关 {np.corrcoef(A[ok, j], B[ok, j])[0, 1] if ok.sum() > 2 else float('nan'):.2f}")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "inflow", "validate"])
    ap.add_argument("--prefix", default="RDA"); ap.add_argument("--run", required=True); ap.add_argument("--base", default="L2P")
    ap.add_argument("--storms", default="S96D1,S96D24"); ap.add_argument("--hs", default="")
    ap.add_argument("--radius", type=float, default=200.0); ap.add_argument("--min-vol", type=float, default=30.0)
    ap.add_argument("--states", default=""); ap.add_argument("--sitedata", default=str(ROOT / "out/rda/sitedata.gpkg"))
    a = ap.parse_args(argv)
    {"build": build, "inflow": inflow, "validate": validate}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
