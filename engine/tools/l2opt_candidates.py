# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · M1 候选措施库 + L0 筛选（只读结果与数据，不跑水动力）。

起点 = 这个运行的 L2+planned（一维溢流 + 二维模拟积水点），土地 = tools/l2opt_land.py 的 Makani 分级。
候选类型（客户 L2 措施目录；管网扩容 / 拍门属 L1，不用）：
  pump_pond     井口截流泵 → 现有塘（有余量的）           泵 3 档（峰值溢流率 × 0.5 / 1 / 1.5，12 英寸 0.36 m³/s 一台）
  pump_newpond  井口截流泵 → 新塘（1 km 内可用地，不要求低于井口）  泵 3 档 × 塘容 2 档
  gravity       井口重力管 → 低处新塘（塘顶 ≤ 井口 − 0.5 m，管长 ≤ 500 m，≥ 20 m 宽）  每井最多 3 个塘址 × 塘容 2 档 × 塘深 2 档
  sump          地面取水泵（二维模拟积水点最深处）→ 现有塘 / 新塘    1 h / 2 h 抽完两档
  deepen        现有塘加深（L2+planned 下满了的塘）          +0.5 / +1.0 m
L0 筛选：土地只用一级 / 二级 / 二级 b（道路红线、已分配地块、不能用的格子不放塘）；重力要求塘顶低于进水口；塘址连片面积够；
         不满足的候选不进库，原因按井记在 rejected 里。
单价是占位值（docs/L2_suggested_优化试点方案_RB01.md 7.1），只用于效果相同的方案之间排序。

用法：python tools/l2opt_candidates.py --run RB01_H61 [--radius 800] [--min-vol 500] [--hs ADM-101,...]
产物：out/l2opt/<运行>/candidates.json、candidates.csv、candidates.geojson（WGS84）、summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import c1_design_l2s4 as V4  # noqa: E402
import c1_design_l2s5 as V5  # noqa: E402
from build_c1_l2 import read_inp  # noqa: E402
from workdir import WORK  # noqa: E402
import l2opt_hydraulics as HY  # noqa: E402

C2D = WORK / "c2d"
Q12 = V4.Q12                       # 12 英寸泵 0.36 m³/s
HEAD_M, PIPE_MAX, ROUTE_K = V5.HEAD_M, V5.PIPE_MAX, V5.ROUTE_K
NEWPOND_R = 1000.0                 # 泵送新塘最远 m
POND_R = 3000.0                    # 泵送现有塘最远 m
MIN_POND = 400.0                   # 新塘最小容积 m³
SITES_PER_WELL = 3
SITE_SEP = 100.0                   # 同一口井的几个塘址至少相隔 m
LAND_OK = (1, 2, 3)                # 一级 / 二级 / 二级 b
LEVEL = {1: "一级", 2: "二级", 3: "二级b"}
# 占位单价（USD），见方案 7.1
COST = {"pump": 300_000, "exc_m3": 40, "fmain_m": 600, "sump": 50_000,
        "gpipe_m": {0.30: 400, 0.45: 600, 0.60: 800, 0.75: 1100, 0.90: 1500},
        "tank_m3": 350,              # 地下调蓄池（钢筋混凝土埋地池，占位单价，2026-10-21）
        "outfall_new": 300_000}      # 新建排海口（出水口构筑物 + 拍门 + 近岸段，占位单价，2026-10-21）
# 调蓄设施（2026-10-21 用户：「调蓄设施如果没法在地面新建就可以用地下的」）：占地以「二级 b」（无地块记录、权属不明的空地）为主的，
# 地面明塘不可行，按地下调蓄池计价、二维里不收地表水（只经一维泵 / 重力管进水）；一级（公共开放空间）、二级（未建地块）按地面塘。Claude 自定
TANK_SHARE = 0.5


def storage_of(lv):
    return "地下调蓄池" if (lv or {}).get("二级b", 0.0) >= TANK_SHARE else "地面塘"


def storage_cost(lv, vol):
    return vol * (COST["tank_m3"] if storage_of(lv) == "地下调蓄池" else COST["exc_m3"])
HS6 = "ADM-101,ADM-140,ADM-205,ADM-206,ADM-291,ADM-293"


def pond_rows(rpt):
    """SWMM 报告的 Storage Volume Summary：{塘: (最大蓄水 m³, 最大 %)}"""
    import re
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Storage Volume Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 7:
            out[q[0]] = (float(q[5]) * 1000.0, float(q[6]))
    return out


class Land:
    def __init__(self, run):
        import rasterio
        from rasterio.warp import Resampling, reproject
        o = ROOT / "out" / "l2opt" / run
        with rasterio.open(o / "land_cls.tif") as d:
            self.cls = d.read(1); self.tf = d.transform
        with rasterio.open(o / "land_parcel.tif") as d:
            self.pid = d.read(1)
        self.recs = json.loads((o / "land_parcels.json").read_text(encoding="utf-8"))
        self.z = np.full(self.cls.shape, np.nan, np.float32)
        with rasterio.open(C2D / f"dem_{run}.tif") as d0:
            reproject(d0.read(1).astype(np.float32), self.z, src_transform=d0.transform, src_crs=d0.crs, dst_transform=self.tf, dst_crs="EPSG:32640",
                      src_nodata=d0.nodata, dst_nodata=np.nan, resampling=Resampling.nearest)
        self.res = self.tf.a
        self.used = np.zeros(self.cls.shape, bool)       # 已被候选占用的格子不阻止别的候选（候选之间可以互斥，由优化处理）

    def rc(self, x, y):
        from rasterio.transform import rowcol
        return rowcol(self.tf, x, y)

    def xy(self, r, c):
        from rasterio.transform import xy
        return xy(self.tf, r, c)

    def sites(self, x0, y0, rmax, need_area, ztop=None, n=SITES_PER_WELL):
        """(x0,y0) 周边 rmax（管线长，按直线 × ROUTE_K）内的塘址：可用地（且地面 ≤ ztop，若给），20 m 宽以上，连片 ≥ need_area。
        返回最多 n 个：[(管长, 塘心行列, 连片标签掩膜, 窗口偏移)]，相互至少隔 SITE_SEP。"""
        from scipy import ndimage
        res = self.res
        r0, c0 = self.rc(x0, y0)
        R = int(rmax / ROUTE_K / res) + 2
        ra, ca = max(r0 - R, 0), max(c0 - R, 0)
        sl = (slice(ra, r0 + R + 1), slice(ca, c0 + R + 1))
        elig = np.isin(self.cls[sl], LAND_OK) & np.isfinite(self.z[sl])
        if ztop is not None:
            elig &= self.z[sl] <= ztop
        if not elig.any():
            return []
        core = ndimage.binary_erosion(elig, iterations=2)
        lab, nl = ndimage.label(elig)
        if not nl:
            return []
        sizes = np.bincount(lab.ravel())[1:] * res * res
        ok = np.isin(lab, np.flatnonzero(sizes >= need_area) + 1) & core
        rr, cc = np.nonzero(ok)
        if not len(rr):
            return []
        gx, gy = self.xy(rr + ra, cc + ca)
        d = np.hypot(np.asarray(gx) - x0, np.asarray(gy) - y0) * ROUTE_K
        out, taken = [], []
        for i in np.argsort(d):
            if d[i] > rmax or len(out) >= n:
                break
            p = (gx[i], gy[i])
            if any(math.hypot(p[0] - q[0], p[1] - q[1]) < SITE_SEP for q in taken):
                continue
            taken.append(p)
            out.append((float(d[i]), int(rr[i]), int(cc[i]), lab, (ra, ca)))
        return out

    def footprint(self, site, area):
        """从塘心在所在连片里往外长出 area 的格子；返回 (掩膜, 塘顶 = 地面 90 分位, 地面最低, 实际面积, 土地级别占比, 地块)"""
        _, r1, c1, lab, (ra, ca) = site
        res = self.res
        rr, cc = np.nonzero(lab == lab[r1, c1])
        k = max(1, int(math.ceil(area / (res * res))))
        o = np.argsort(np.hypot(rr - r1, cc - c1))[:k]
        R, Cc = rr[o] + ra, cc[o] + ca
        g = self.z[R, Cc]
        lv = {LEVEL[v]: round(float((self.cls[R, Cc] == v).mean()), 2) for v in LAND_OK if (self.cls[R, Cc] == v).any()}
        pids = sorted({int(p) for p in self.pid[R, Cc] if p >= 0})
        parcels = [{k_: self.recs[p].get(k_) for k_ in ("table", "id", "plot", "use", "status", "community") if self.recs[p].get(k_) is not None} for p in pids[:6]]
        return (R, Cc), float(np.nanpercentile(g, 90)), float(np.nanmin(g)), float(len(o) * res * res), lv, parcels


def outline(mask_rc, land):
    """格子集合 → 外轮廓多边形（UTM）"""
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union
    R, C = mask_rc
    r0, r1, c0, c1 = R.min(), R.max() + 1, C.min(), C.max() + 1
    m = np.zeros((r1 - r0, c1 - c0), np.uint8); m[R - r0, C - c0] = 1
    from rasterio.transform import Affine
    tf = land.tf * Affine.translation(c0, r0)
    return unary_union([shape(g) for g, v in shapes(m, mask=m.astype(bool), transform=tf) if v == 1]).simplify(2.0)


def S_bbox(nxy, pad):
    xs = [v[0] for v in nxy.values()]; ys = [v[1] for v in nxy.values()]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="RB01_H61"); ap.add_argument("--base", default="L2P")
    ap.add_argument("--radius", type=float, default=800.0, help="候选井离热点最远 m")
    ap.add_argument("--min-vol", type=float, default=500.0, help="候选井 L2+planned 溢流量下限 m³")
    ap.add_argument("--sump-min", type=float, default=300.0, help="地面取水点（积水点）峰值积水量下限 m³")
    ap.add_argument("--sump-r", type=float, default=500.0, help="地面取水点离热点最远 m")
    ap.add_argument("--hs", default=HS6)
    # 2026-10-19 第二版：按 96 mm 控制性暴雨定尺寸（61 mm 定的塘在 96 mm 下全满），加「泵 → 出水口」
    ap.add_argument("--basis", default="", help="定尺寸用的雨（如 S96D1,S96D24：取各井 L2+planned 溢流量 / 峰值的最大值）；缺省 = 运行自己")
    ap.add_argument("--out-name", default="", help="产物目录名（缺省 = 运行名）；土地分级仍取 out/l2opt/<运行>/")
    ap.add_argument("--vol-tiers", default="1,2", help="新塘容积档（× 溢流量）")
    ap.add_argument("--outfall", action="store_true", help="加「泵 → 出水口（RBW3 外海）」候选")
    ap.add_argument("--prefix", default="RB01", help="模型名前缀：RB01 / RDA（2026-10-20 起 RD-A 共用这套脚本）")
    ap.add_argument("--outfall-re", default="RBW3", help="「泵 → 出水口」的目标出水口：名字匹配这个正则的那几个（RD-A 用 ^31$ = 海边出水口）")
    ap.add_argument("--sea", default="", help="海面多边形（GeoJSON，UTM 40N）：给每口井 / 积水片加「泵 → 最近的新建排海口」候选（2026-10-21 用户同意新建排海口）")
    ap.add_argument("--sea-step", type=float, default=250.0, help="海岸线上排海口候选点的间距 m（同一点位的泵合用一个排海口）")
    ap.add_argument("--sea-max", type=float, default=2500.0, help="离排海口最远 m")
    ap.add_argument("--tailwater", type=float, default=1.3, help="出水口尾水位 m（WA-726 表 4-5 设计高潮位 +1.3 m），压力管静扬程用")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString, Point, mapping
    from shapely.ops import transform as stf
    from swbrain import ponding
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    W = lambda g: json.loads(json.dumps(mapping(stf(lambda x, y, z=None: T.transform(x, y), g))), parse_float=lambda s: round(float(s), 6))  # noqa: E731

    run, base = a.run, a.base
    out = ROOT / "out" / "l2opt" / (a.out_name or run); out.mkdir(parents=True, exist_ok=True)
    srcs = [f"{a.prefix}_{s}" for s in a.basis.split(",") if s] or [run]          # 定尺寸用的运行
    nf = {}
    for sr in srcs:
        for k, v in V4.node_flood(C2D / f"{sr}_{base}_2way.rpt").items():
            o = nf.setdefault(k, dict(v))
            o["vol_m3"] = max(o["vol_m3"], v["vol_m3"]); o["rate_m3s"] = max(o["rate_m3s"], v["rate_m3s"])
    S = read_inp(WORK / f"{srcs[0]}_{base}.inp")
    rim = {r[0]: float(r[1]) + float(r[2]) for r in S["[JUNCTIONS]"]}
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    meta = json.loads((C2D / f"pilot_{srcs[0]}_{base}.json").read_text(encoding="utf-8"))
    prow = {}
    for sr in srcs:                                                        # 塘的余量按最满的那场算
        for k, (mx, pct) in pond_rows(C2D / f"{sr}_{base}_2way.rpt").items():
            if pct >= prow.get(k, (0.0, -1.0))[1]:
                prow[k] = (mx, pct)
    of_xy = None
    if a.outfall:
        import re as _re
        ofs = [r for r in S["[OUTFALLS]"] if _re.search(a.outfall_re, r[0])]
        of_xy = (float(np.mean([nxy[r[0]][0] for r in ofs])), float(np.mean([nxy[r[0]][1] for r in ofs])))
    sea_sites = []
    if a.sea:
        sea = gpd.read_file(a.sea).to_crs(32640).geometry.union_all()
        x0b, y0b, x1b, y1b = [float(v) for v in S_bbox(nxy, a.sea_max)]
        from shapely.geometry import box
        coast = sea.boundary.intersection(box(x0b, y0b, x1b, y1b))
        for ln in getattr(coast, "geoms", [coast]):
            if ln.length < 1:
                continue
            for dd in np.arange(0, ln.length, a.sea_step):
                q = ln.interpolate(dd); sea_sites.append((q.x, q.y))
        print(f"海岸线排海口候选点 {len(sea_sites)} 个（每 {a.sea_step:g} m 一个）")
    sea_arr = np.array(sea_sites) if sea_sites else None

    def near_sea(x, y):
        if sea_arr is None:
            return None
        d = np.hypot(sea_arr[:, 0] - x, sea_arr[:, 1] - y); i = int(d.argmin())
        return (i, float(d[i])) if d[i] <= a.sea_max else None
    vtiers = [float(v) for v in a.vol_tiers.split(",")]
    ponds = []
    for p in meta.get("ponds", []):
        mx, pct = prow.get(p["node"], (0.0, 0.0))
        cap = p["vol"]
        ponds.append({"node": p["node"], "xy": p["xy"], "cap_m3": round(cap), "max_pct": pct, "spare_m3": round(cap * (1 - pct / 100.0)),
                      "no_surface": bool(p.get("no_surface"))})
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    H = H[H.hotspot_id.isin(a.hs.split(","))]
    hx = np.c_[H.geometry.x, H.geometry.y]; hid = H.hotspot_id.tolist()
    land = Land(run)

    def near_hs(x, y):
        d = np.hypot(hx[:, 0] - x, hx[:, 1] - y); i = int(d.argmin()); return hid[i], round(float(d[i]))

    def gpipe_cost(d, L):
        return COST["gpipe_m"][d] * L

    import rasterio
    _dtm = rasterio.open("D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF")

    def zg(x, y):
        v = float(next(_dtm.sample([(x, y)]))[0])
        return v if v > -100 else 0.0

    def fm(n, L, z_src, z_dst, extra=0.0):
        """泵 + 压力管造价与压力管尺寸（参数登记表第 9 / 10 项，2026-10-20 并进候选：Darcy–Weisbach + Colebrook，Ks 按 WA-726 表 7-3，
        设计流速 ≤ 1.8 m/s、不够就多根并联；压力管单价按管径，l2opt_hydraulics.FM_COST 占位）。返回 (造价, 字段)"""
        r = HY.size_main(n, max(L, 10.0), z_src, z_dst)
        return n * COST["pump"] + extra + r["main_cost_usd"], {"main_d_m": r["main_d_m"], "n_mains": r["n_mains"], "q_m3s": r["q_m3s"],
                                                                "head_m": r["head_m"], "v_ms": r["v_ms"], "static_m": r["static_m"]}

    cands, rejected = [], {}

    def add(**c):
        c["id"] = f"{ {'pump_pond': 'PP', 'pump_newpond': 'PN', 'gravity': 'GR', 'sump': 'SU', 'deepen': 'DP', 'pump_outfall': 'PX'}[c['kind']] }{sum(1 for x in cands if x['kind'] == c['kind']) + 1:04d}".replace(" ", "")
        c["cost_usd"] = round(c["cost_usd"])
        cands.append(c)

    # ---- 候选井
    wells = []
    for k, v in nf.items():
        if k not in nxy or k not in rim or v["vol_m3"] < a.min_vol:
            continue
        h, d = near_hs(*nxy[k])
        if d <= a.radius:
            wells.append((k, v, h, d))
    wells.sort(key=lambda w: -w[1]["vol_m3"])
    print(f"{run}：候选井 {len(wells)} 口（{a.radius:g} m 内、{base} 溢流 ≥ {a.min_vol:g} m³），现有塘 {len(ponds)} 个（有余量的 {sum(p['spare_m3'] > 500 for p in ponds)} 个）")
    for k, v, h, dh in wells:
        x0, y0 = nxy[k]; vol, q = v["vol_m3"], v["rate_m3s"]
        base_n = max(1, math.ceil(q / Q12))
        tiers = sorted({max(1, round(base_n * f)) for f in (0.5, 1.0, 1.5)})
        common = {"well": k, "hotspot": h, "hs_dist_m": dh, "overflow_m3": round(vol), "overflow_rate_m3s": round(q, 3), "rim_m": round(rim[k], 2)}
        # 1) 泵 → 现有塘（最近 2 个有余量的）
        dests = sorted(((math.hypot(p["xy"][0] - x0, p["xy"][1] - y0) * ROUTE_K, p) for p in ponds if p["spare_m3"] >= 0.5 * vol), key=lambda t: t[0])
        dests = [t for t in dests if t[0] <= POND_R][:2]
        for L, p in dests:
            for n in tiers:
                cu, hy = fm(n, L, rim[k], zg(*p["xy"]))
                add(kind="pump_pond", **common, n_pumps=n, **hy, dest=p["node"], dest_spare_m3=p["spare_m3"], pipe_m=round(L),
                    capture_cap_m3=round(min(vol, p["spare_m3"])), cost_usd=cu, geom=LineString([(x0, y0), tuple(p["xy"])]))
        # 1b) 泵 → 出水口（RBW3 外海，压力管直线 × 1.2；尾水同 RBW3 固定 1.3 m）
        if of_xy:
            L = math.hypot(of_xy[0] - x0, of_xy[1] - y0) * ROUTE_K
            for n in tiers:
                cu, hy = fm(n, L, rim[k], a.tailwater)
                add(kind="pump_outfall", **common, n_pumps=n, **hy, pipe_m=round(L), of_xy=[round(of_xy[0], 1), round(of_xy[1], 1)],
                    capture_cap_m3=round(vol), cost_usd=cu, geom=LineString([(x0, y0), of_xy]))
        # 1c) 泵 → 最近的新建排海口（尾水 1.3 m，WA-726 表 4-5）
        ns = near_sea(x0, y0)
        if ns:
            i, dsea = ns; sx_, sy_ = sea_sites[i]; L = dsea * ROUTE_K
            for n in tiers:
                cu, hy = fm(n, L, rim[k], a.tailwater, extra=COST["outfall_new"])
                add(kind="pump_outfall", **common, n_pumps=n, **hy, pipe_m=round(L), of_xy=[round(sx_, 1), round(sy_, 1)], outfall_site=f"SEA{i:03d}",
                    capture_cap_m3=round(vol), cost_usd=cu, geom=LineString([(x0, y0), (sx_, sy_)]))
        # 2) 泵 → 新塘（1 km 内可用地；塘容按 --vol-tiers × 溢流量，深 2 m）
        for kv in vtiers:
            need = max(MIN_POND, kv * vol); area = need / 2.0
            st = land.sites(x0, y0, NEWPOND_R, area, n=1)
            if not st:
                rejected.setdefault(k, []).append(f"泵送新塘 {kv:g}×：{NEWPOND_R:.0f} m 内没有连片 ≥ {area:,.0f} m² 的可用地")
                continue
            m, ztop, zmin, A, lv, parcels = land.footprint(st[0], area)
            poly = outline(m, land)
            for n in tiers:
                cu, hy = fm(n, st[0][0], rim[k], ztop, extra=storage_cost(lv, A * 2.0))
                add(kind="pump_newpond", **common, n_pumps=n, **hy, pond_vol_m3=round(A * 2.0), pond_depth_m=2.0, pond_area_m2=round(A), pond_top_m=round(ztop, 2),
                    storage=storage_of(lv),
                    pipe_m=round(st[0][0]), land=lv, parcels=parcels, capture_cap_m3=round(min(vol, A * 2.0)),
                    cost_usd=cu,
                    geom=poly, link=LineString([(x0, y0), land.xy(st[0][1] + st[0][4][0], st[0][2] + st[0][4][1])]))
        # 3) 重力管 → 低处新塘
        zin = rim[k] - HEAD_M; got = False
        for kv in vtiers:
            for dep in (1.5, 2.0):
                need = max(MIN_POND, kv * vol); area = need / dep
                for si, st in enumerate(land.sites(x0, y0, PIPE_MAX, area, ztop=zin), 1):
                    m, ztop, zmin, A, lv, parcels = land.footprint(st, area)
                    if ztop > zin:
                        continue
                    L = max(st[0], 10.0); s = max((zin - ztop) / L, 1e-4)
                    d = V5.pipe_d(q, s, L)
                    got = True
                    add(kind="gravity", **common, site=si, pond_vol_m3=round(A * dep), pond_depth_m=dep, pond_area_m2=round(A), pond_top_m=round(ztop, 2),
                        storage=storage_of(lv),
                        pipe_m=round(L), pipe_d_m=d, pipe_slope=round(s, 5), land=land and lv, parcels=parcels, capture_cap_m3=round(min(vol, A * dep)),
                        cost_usd=gpipe_cost(d, L) + storage_cost(lv, A * dep), geom=outline(m, land),
                        link=LineString([(x0, y0), land.xy(st[1] + st[4][0], st[2] + st[4][1])]))
        if not got:
            rejected.setdefault(k, []).append(f"重力：{PIPE_MAX:.0f} m 管长内没有比进水口（{zin:.2f} m）低、20 m 宽以上、连片够大的可用地")

    # ---- 地面取水点（二维模拟积水点）
    r = ponding.compute(srcs[0], base, geometry=False)                     # 积水点按定尺寸的第一场雨（雨强最大那场）
    nsu = 0
    TI = Transformer.from_crs(4326, 32640, always_xy=True)
    for f in r["feats"]:
        near = [(h, d) for h, d in f.get("near") or [] if h in hid]
        dmin = min((d for _, d in near), default=1e9)
        if f["vol_peak_m3"] < a.sump_min or dmin > a.sump_r:
            continue
        nsu += 1
        x0, y0 = TI.transform(f["lon"], f["lat"])
        h, dh = near_hs(x0, y0); vol = f["vol_peak_m3"]
        sp = {"ponding_point": f["id"], "hotspot": h, "hs_dist_m": dh, "ponding_vol_m3": vol, "ponding_hmax_m": f["hmax_m"], "sx": round(x0, 1), "sy": round(y0, 1)}
        dests = sorted(((math.hypot(p["xy"][0] - x0, p["xy"][1] - y0) * ROUTE_K, p) for p in ponds if p["spare_m3"] >= 0.5 * vol), key=lambda t: t[0])
        dests = [t for t in dests if t[0] <= POND_R][:1]
        for clear_h in (1.0, 2.0):
            n = max(1, math.ceil(vol / (Q12 * clear_h * 3600.0)))
            z0 = zg(x0, y0)
            for L, p in dests:
                cu, hy = fm(n, L, z0, zg(*p["xy"]), extra=COST["sump"])
                add(kind="sump", **sp, clear_h=clear_h, n_pumps=n, **hy, dest_kind="pond", dest=p["node"], pipe_m=round(L),
                    capture_cap_m3=round(min(vol, p["spare_m3"])), cost_usd=cu, geom=Point(x0, y0), link=LineString([(x0, y0), tuple(p["xy"])]))
            if of_xy:                                                       # 地面泵 → 出水口
                L = math.hypot(of_xy[0] - x0, of_xy[1] - y0) * ROUTE_K
                cu, hy = fm(n, L, z0, a.tailwater, extra=COST["sump"])
                add(kind="sump", **sp, clear_h=clear_h, n_pumps=n, **hy, dest_kind="outfall", dest="OUTFALL", pipe_m=round(L),
                    capture_cap_m3=round(vol), cost_usd=cu, geom=Point(x0, y0), link=LineString([(x0, y0), of_xy]))
            ns = near_sea(x0, y0)                                           # 地面泵 → 最近的新建排海口
            if ns:
                i, dsea = ns; sx_, sy_ = sea_sites[i]; L = dsea * ROUTE_K
                cu, hy = fm(n, L, z0, a.tailwater, extra=COST["sump"] + COST["outfall_new"])
                add(kind="sump", **sp, clear_h=clear_h, n_pumps=n, **hy, dest_kind="outfall", dest=f"SEA{i:03d}", outfall_site=f"SEA{i:03d}", pipe_m=round(L),
                    of_xy=[round(sx_, 1), round(sy_, 1)], capture_cap_m3=round(vol), cost_usd=cu, geom=Point(x0, y0), link=LineString([(x0, y0), (sx_, sy_)]))
            for kv in vtiers:                                               # 地面泵 → 新塘（1 km 内可用地，深 2 m）
                need = max(MIN_POND, kv * vol); area = need / 2.0
                st = land.sites(x0, y0, NEWPOND_R, area, n=1)
                if not st:
                    continue
                m, ztop, zmin, A, lv, parcels = land.footprint(st[0], area)
                cu, hy = fm(n, st[0][0], z0, ztop, extra=COST["sump"] + storage_cost(lv, A * 2.0))
                add(kind="sump", **sp, clear_h=clear_h, n_pumps=n, **hy, dest_kind="newpond", dest="NEW", storage=storage_of(lv),
                    pond_vol_m3=round(A * 2.0), pond_depth_m=2.0, pond_area_m2=round(A), pond_top_m=round(ztop, 2), pipe_m=round(st[0][0]),
                    land=lv, parcels=parcels, capture_cap_m3=round(min(vol, A * 2.0)), cost_usd=cu,
                    geom=outline(m, land), link=LineString([(x0, y0), land.xy(st[0][1] + st[0][4][0], st[0][2] + st[0][4][1])]))
    # ---- 现有塘加深（满了的）
    from shapely.geometry import shape as gshape
    for p in meta.get("ponds", []):
        mx, pct = prow.get(p["node"], (0.0, 0.0))
        if pct < 99.0 or p.get("no_surface"):
            continue
        g = gshape(p["footprint"]); A = g.area
        h, dh = near_hs(*p["xy"])
        for dd in (0.5, 1.0):
            add(kind="deepen", pond=p["node"], hotspot=h, hs_dist_m=dh, pond_area_m2=round(A), add_depth_m=dd, add_vol_m3=round(A * dd),
                capture_cap_m3=round(A * dd), cost_usd=A * dd * COST["exc_m3"], geom=g)

    # ---- 写出
    feats = []
    for c in cands:
        g = c.pop("geom"); ln = c.pop("link", None)
        c["lon"], c["lat"] = [round(v, 6) for v in T.transform(*(g.centroid.coords[0]))]
        feats.append({"type": "Feature", "geometry": W(g), "properties": {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in c.items()}})
        if ln is not None:
            c["link_wgs"] = W(ln)["coordinates"]
    (out / "candidates.json").write_text(json.dumps({"run": run, "base": base, "params": vars(a), "cost_placeholder_usd": {k: v for k, v in COST.items()},
                                                    "ponds_existing": ponds, "candidates": cands, "rejected": rejected}, ensure_ascii=False, indent=0, default=str), encoding="utf-8")
    (out / "candidates.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    cols = ["id", "kind", "hotspot", "hs_dist_m", "well", "ponding_point", "pond", "overflow_m3", "n_pumps", "q_m3s", "dest", "pond_vol_m3", "pond_depth_m",
            "pond_area_m2", "pipe_m", "pipe_d_m", "add_vol_m3", "capture_cap_m3", "cost_usd", "land", "lon", "lat"]
    with open(out / "candidates.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(cols)
        for c in cands:
            w.writerow([json.dumps(c.get(k), ensure_ascii=False) if isinstance(c.get(k), dict) else c.get(k, "") for k in cols])
    from collections import Counter
    by_kind = Counter(c["kind"] for c in cands)
    by_hs = {h: dict(Counter(c["kind"] for c in cands if c["hotspot"] == h)) for h in hid}
    summ = {"run": run, "base": base, "n_wells": len(wells), "n_sump_points": nsu, "n_candidates": len(cands), "by_kind": dict(by_kind), "by_hotspot": by_hs,
            "wells_no_gravity": sum(1 for k, v in rejected.items() if any(x.startswith("重力") for x in v)),
            "wells_no_newpond": sum(1 for k, v in rejected.items() if sum(x.startswith("泵送新塘") for x in v) == 2)}
    (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
