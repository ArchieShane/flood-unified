# -*- coding: utf-8 -*-
"""L2+suggested 候选生成 v3（2026-10-23）：三层约束都过了才进优化。依据 docs/RD-A_L2S可建性约束设计_20261023.md 第四节。

三层约束：
  A 客户规则   WA-726 约束登记表 + 取值决定，经 swbrain.ontology.constraints.check() 逐条判定
  B 领域知识   constraints/domain.json（DOM-*），本脚本按其数值执行
  C 物理空间   out/rda/sitedata.gpkg（Makani）+ DTM + 基线二维积水

候选只有三类（不再有地面明塘）：
  tank_gravity   溢流井 →（重力管）→ 地下调蓄池（B1 重力能进水）
  tank_pumped    溢流井旁泵站 →（压力管）→ 地下调蓄池（重力进不去时）
  ps_cluster     相邻溢流井 →（重力管）→ 一座泵站 →（压力管）→ 排海口（新建或现有 31）（B10 合并）
每个候选带逐条合规表（A / B / C）。判定：
  可建        全部通过
  有条件可建  有 unknown / review（缺数据、要审批或许可）
  不可建      有 fail，进 rejected，不进优化
造价：手册有单价的照用（第三卷 p349：HDD HDPE 压力管、排海口），其余是占位单价并在字段里注明。

用法：python tools/l2s_candidates_v3.py --prefix RDA --basis S96D1,S96D24 --out-name RDA_96C --hs ADM-75,... --groups "A+B,C,..."
产物：out/l2opt/<out-name>/candidates.json（兼容 l2opt_screen / greedy）、candidates.geojson、compliance.json、rejected.json、summary.json
"""
from __future__ import annotations

import argparse
import collections
import heapq
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
from swbrain.ontology import constraints as K  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
DTM = "D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF"
RES = 2.0
AED = 3.6725
DOM = {d["id"]: d for d in json.loads((ROOT / "swbrain/ontology/constraints/domain.json").read_text(encoding="utf-8"))["items"]}
dv = lambda k: DOM[k]["value"]   # noqa: E731
# 占位单价（USD；手册没有的项）
PH = {"station_base": 400_000, "pump_per_036": 150_000, "rc_wetwell_m3": 1_500, "tank_m3": 350, "gpipe_m": {0.3: 400, 0.45: 600, 0.6: 800, 0.75: 1100, 0.9: 1500, 1.2: 2100, 1.5: 2800},
      "air_valve": 10_000, "drain_pump": 60_000}
FM_DN = (0.315, 0.45, 0.56, 0.63, 0.8, 1.0, 1.2, 1.4, 1.6)


def fm_rate_usd(dn):
    """压力管单价：第三卷 p349 HDD HDPE（AED/m）→ USD；> DN1000 按每 100 mm +12 % 外推（标注）"""
    tab = {0.315: "WA726-3-039", 0.45: "WA726-3-040", 0.56: "WA726-3-041", 0.63: "WA726-3-042", 0.8: "WA726-3-043", 1.0: "WA726-3-044"}
    if dn in tab:
        return K.val(tab[dn]) / AED, tab[dn]
    return K.val("WA726-3-044") / AED * (1.12 ** ((dn - 1.0) / 0.1)), "WA726-3-044 外推"


def outfall_cost_usd(dn):
    tab = {0.6: "WA726-3-045", 0.7: "WA726-3-046", 0.8: "WA726-3-047", 0.9: "WA726-3-048", 1.0: "WA726-3-049"}
    for d in sorted(tab):
        if dn <= d + 1e-9:
            return K.val(tab[d]) / AED, tab[d]
    return K.val("WA726-3-049") / AED * (dn / 1.0) ** 2, "WA726-3-049 按面积外推"


def size_fm(q):
    """压力管：峰值流速 ≤ 2.0 m/s（WA726-1-378），HDPE；不够就并联"""
    vmax = K.val("WA726-1-378")
    for m in (1, 2, 3, 4):
        for d in FM_DN:
            v = q / m / (math.pi * d * d / 4)
            if v <= vmax:
                return d, m, round(v, 2)
    return FM_DN[-1], 4, round(q / 4 / (math.pi * FM_DN[-1] ** 2 / 4), 2)


def pumps_for(q):
    """DEC-04 + DOM-PUMPS：两台时每台 0.66Q；单台 > 0.5 m³/s 时改三台，每台 max(1.1Q/3, 0.33Q)"""
    if 0.66 * q <= dv("DOM-PUMPS")["three_pumps_if_each_over_m3s"]:
        return 2, 0.66 * q
    return 3, max(1.1 * q / 3, 0.66 * q / 2)


class Site:
    """2 m 栅格上的可建性：可开挖面、离建筑距离、积水、供电、道路网"""

    def __init__(self, gpkg, domain, prefix, base_storm, base_state, log=print):
        import geopandas as gpd
        import rasterio
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        from scipy import ndimage
        from scipy.spatial import cKDTree
        self.L = {lay: gpd.read_file(gpkg, layer=lay) for lay in ("buildings", "road_surface", "road_centre", "road_islands", "corridor", "substations", "utility_plots",
                                                                    "parks", "plots", "coast_excl", "shoreline", "sea", "no_dig", "trees", "utility_lines")}
        dom = domain
        x0, y0, x1, y1 = dom.total_bounds + [-1200, -1200, 1200, 1200]
        self.W, self.H = int((x1 - x0) / RES), int((y1 - y0) / RES)
        self.tf = from_origin(x0, y1, RES, RES); self.x0, self.y1 = x0, y1
        sh = (self.H, self.W)

        def ras(g, buf=0.0):
            g = [x.buffer(buf) if buf else x for x in g.geometry if x is not None and not x.is_empty]
            return rasterize(((x, 1) for x in g), out_shape=sh, transform=self.tf, fill=0, all_touched=True, dtype="uint8").astype(bool) if g else np.zeros(sh, bool)
        bld = ras(self.L["buildings"])
        self.dist_b = ndimage.distance_transform_edt(~bld) * RES
        road = ras(self.L["road_surface"]); corr = ras(self.L["corridor"]); nodig = ras(self.L["no_dig"], 5.0); sea = ras(self.L["sea"])
        util = ras(self.L["utility_plots"])
        P = self.L["plots"]
        pub = ras(P[P.primaryuseeng.astype(str) == "15"]); vac = ras(P[P.construction_status.astype(str).str.lower().str.startswith("not")])
        anyplot = ras(P); park = ras(self.L["parks"])
        isl = ras(self.L["road_islands"])
        verge = ndimage.distance_transform_edt(~road) * RES <= dv("DOM-B4")["road_verge_m"]
        self.unplotted = ~anyplot & ~road & ~corr & ~sea & ~isl & ~verge          # 路边带视为道路红线（DOM-B4）
        allowed = (park | pub | vac | self.unplotted) & ~road & ~isl & ~corr & ~nodig & ~sea & ~util & ~bld
        self.allowed = allowed; self.park = park; self.pub = pub; self.vac = vac; self.sea = sea
        log(f"  可开挖面（未扣建筑净宽）{allowed.sum() * RES * RES / 1e4:.1f} ha：公园 {(allowed & park).sum() * 4 / 1e4:.1f}、公共用地 {(allowed & pub).sum() * 4 / 1e4:.1f}、"
            f"未建地块 {(allowed & vac).sum() * 4 / 1e4:.1f}、无地块记录 {(allowed & self.unplotted).sum() * 4 / 1e4:.1f} ha")
        # 基线积水（B6）
        z = np.load(C2D / f"{prefix}_{base_storm}_{base_state}_2d.npz")
        self.flood_tree = cKDTree(z["xy"].astype(float)); self.flood_h = z["hmax"].astype(float)
        sub = self.L["substations"]; sub = sub[sub.statusindicator.astype(str).str.contains("In Service")]
        cen = sub.geometry.centroid                        # 变电站有点也有面
        self.sub_tree = cKDTree(np.c_[cen.x, cen.y])
        with rasterio.open(DTM) as ds:
            self.dtm = ds
            self._dtm = ds.read(1, window=rasterio.windows.from_bounds(x0, y0, x1, y1, ds.transform)); self._dtf = ds.window_transform(rasterio.windows.from_bounds(x0, y0, x1, y1, ds.transform))
        self._graph()
        # 离道路中心线的距离（泵站场地要能让重型车辆进出，DOM-PS-SITE）
        rc = rasterize(((g, 1) for g in self.L["road_centre"].geometry if g is not None), out_shape=sh, transform=self.tf, fill=0, all_touched=True, dtype="uint8").astype(bool)
        self.dist_road = ndimage.distance_transform_edt(~rc) * RES
        self.bbox = (x0, y0, x1, y1)

    # ---- 坐标
    def rc(self, x, y):
        return int((self.y1 - y) / RES), int((x - self.x0) / RES)

    def xy(self, r, c):
        return self.x0 + (c + 0.5) * RES, self.y1 - (r + 0.5) * RES

    def ground(self, x, y):
        c, r = ~self._dtf * (x, y)
        v = float(self._dtm[int(r), int(c)])
        return v if v > -100 else None

    def flooded(self, x, y):
        d, i = self.flood_tree.query((x, y))
        return d < 20 and self.flood_h[i] >= dv("DOM-B6")["max_baseline_depth_m"]

    def power_m(self, x, y):
        return float(self.sub_tree.query((x, y))[0])

    # ---- 道路网（压力管 / 重力管沿路）
    def _graph(self):
        from scipy.sparse import coo_matrix
        nodes, idx, E = [], {}, []

        def nid(p):
            k = (round(p[0]), round(p[1]))
            if k not in idx:
                idx[k] = len(nodes); nodes.append(k)
            return idx[k]
        for g in self.L["road_centre"].geometry:
            for ln in getattr(g, "geoms", [g]):
                cs = list(ln.coords)
                for a, b in zip(cs[:-1], cs[1:]):
                    E.append((nid(a), nid(b), math.hypot(b[0] - a[0], b[1] - a[1])))
        from scipy.spatial import cKDTree
        self.nodes = np.array(nodes, float); self.ntree = cKDTree(self.nodes)
        e = np.array(E); n = len(nodes)
        self.G = coo_matrix((np.r_[e[:, 2], e[:, 2]], (np.r_[e[:, 0], e[:, 1]].astype(int), np.r_[e[:, 1], e[:, 0]].astype(int))), shape=(n, n)).tocsr()
        self._dcache = {}

    def road_dist(self, a, b):
        """沿道路中心线的长度 + 两端接入段（DOM-B8）；接入段 > 300 m 返回 None"""
        from scipy.sparse.csgraph import dijkstra
        da, ia = self.ntree.query(a); db, ib = self.ntree.query(b)
        cmax = dv("DOM-B8")["connector_max_m"]
        if da > cmax or db > cmax:
            return None
        if ia not in self._dcache:
            self._dcache[ia] = dijkstra(self.G, indices=ia, limit=6000)
        d = self._dcache[ia][ib]
        return None if not np.isfinite(d) else float(d + da + db)

    def road_access_m(self, x, y):
        return float(self.ntree.query((x, y))[0])

    # ---- 选址
    def find_site(self, x, y, radius, area, need_b, extra_ok=None, max_sites=2, min_width=0.0):
        """离 (x, y) radius 内、可开挖、离建筑 ≥ need_b + 作业带、连片 ≥ area 的位置；返回 [(距离, 掩膜行列列表, 中心 x, y)]"""
        from scipy import ndimage
        wb = dv("DOM-B3")["work_band_m"]
        r0, c0 = self.rc(x, y); R = int(radius / RES) + 2
        ra, ca = max(r0 - R, 0), max(c0 - R, 0); rb, cb = min(r0 + R + 1, self.H), min(c0 + R + 1, self.W)
        ok = self.allowed[ra:rb, ca:cb] & (self.dist_b[ra:rb, ca:cb] >= need_b + wb)
        if extra_ok is not None:
            ok &= extra_ok[ra:rb, ca:cb]
        ok = ndimage.binary_erosion(ok, iterations=max(1, int(wb / RES)))     # 作业带：外扩 2 m 也要在可开挖面内
        if min_width > 0:                                                      # 最小宽度：只在宽 ≥ min_width 的部位落池（开运算）
            k = max(1, int(round(min_width / 2 / RES)))
            ok = ndimage.binary_opening(ok, structure=np.ones((2 * k + 1, 2 * k + 1), bool))
        lab, n = ndimage.label(ok)
        if not n:
            return []
        sizes = np.bincount(lab.ravel()) * RES * RES
        out = []
        rr, cc = np.nonzero(ok & (sizes[lab] >= area))
        if not len(rr):
            return []
        gx = ca + cc; gy = ra + rr
        d = np.hypot((gx - c0) * RES, (gy - r0) * RES)
        used = set()
        for i in np.argsort(d):
            if d[i] > radius or len(out) >= max_sites:
                break
            L_ = lab[rr[i], cc[i]]
            if L_ in used:
                continue
            used.add(L_)
            cells = self._grow(lab == L_, rr[i], cc[i], int(math.ceil(area / (RES * RES))))
            cx, cy = self.xy(ra + np.mean([p[0] for p in cells]), ca + np.mean([p[1] for p in cells]))
            out.append((float(d[i]), [(ra + p[0], ca + p[1]) for p in cells], cx, cy))
        return out

    @staticmethod
    def _grow(mask, r, c, n):
        seen = {(r, c)}; q = collections.deque([(r, c)]); out = []
        while q and len(out) < n:
            p = q.popleft(); out.append(p)
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                s = (p[0] + dr, p[1] + dc)
                if 0 <= s[0] < mask.shape[0] and 0 <= s[1] < mask.shape[1] and s not in seen and mask[s]:
                    seen.add(s); q.append(s)
        return out

    def land_mix(self, cells):
        rr = np.array([p[0] for p in cells]); cc = np.array([p[1] for p in cells])
        return {"公园": round(float(self.park[rr, cc].mean()), 2), "公共用地": round(float(self.pub[rr, cc].mean()), 2),
                "未建地块": round(float(self.vac[rr, cc].mean()), 2), "无地块记录": round(float(self.unplotted[rr, cc].mean()), 2)}

    def outline(self, cells):
        from shapely.geometry import box
        from shapely.ops import unary_union
        return unary_union([box(*self.xy(r, c)[:1], 0, 0) if False else box(self.xy(r, c)[0] - 1, self.xy(r, c)[1] - 1, self.xy(r, c)[0] + 1, self.xy(r, c)[1] + 1) for r, c in cells]).simplify(1.0)


def bclass(depth):
    lk = K.clear_width_b(depth, 1200)
    return lk if lk else (6.5, "WA726-1-118")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="RDA"); ap.add_argument("--basis", default="S96D1,S96D24"); ap.add_argument("--base", default="L2P")
    ap.add_argument("--out-name", required=True); ap.add_argument("--hs", required=True); ap.add_argument("--groups", required=True)
    ap.add_argument("--radius", type=float, default=800.0); ap.add_argument("--min-vol", type=float, default=500.0)
    ap.add_argument("--sitedata", default=str(ROOT / "out/rda/sitedata.gpkg")); ap.add_argument("--domain", default=str(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson"))
    ap.add_argument("--vol-tiers", default="1.0,1.5")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString, Point, mapping
    from shapely.ops import transform as stf
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    W = lambda g: mapping(stf(lambda x, y, z=None: T.transform(x, y), g))   # noqa: E731
    out = ROOT / "out" / "l2opt" / a.out_name; out.mkdir(parents=True, exist_ok=True)
    srcs = [f"{a.prefix}_{s}" for s in a.basis.split(",")]
    # ---- 一维：溢流井
    nf = {}
    for sr in srcs:
        for k, v in V4.node_flood(C2D / f"{sr}_{a.base}_2way.rpt").items():
            o = nf.setdefault(k, dict(v)); o["vol_m3"] = max(o["vol_m3"], v["vol_m3"]); o["rate_m3s"] = max(o["rate_m3s"], v["rate_m3s"])
    S = read_inp(WORK / f"{srcs[0]}_{a.base}.inp")
    inv = {r[0]: float(r[1]) for r in S["[JUNCTIONS]"]}; rim = {r[0]: float(r[1]) + float(r[2]) for r in S["[JUNCTIONS]"]}
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    dia = {r[0]: float(r[2]) for r in S["[XSECTIONS]"] if r[1] == "CIRCULAR"}
    outd = collections.defaultdict(float)
    for r in S["[CONDUITS]"]:
        outd[r[1]] = max(outd[r[1]], dia.get(r[0], 0.0))
    crest = {k: inv[k] + outd.get(k, 0.3) for k in inv}             # 截流口 = 出水管管顶（DOM-B1）
    H = __import__("hotspot_src").load(32640).set_index("hotspot_id")
    hs = a.hs.split(","); groups = [g.split("+") for g in a.groups.split(",")]
    grp = {h: gi for gi, g in enumerate(groups) for h in g}
    wells = []
    for k, v in nf.items():
        if k not in nxy or k not in inv or v["vol_m3"] < a.min_vol:
            continue
        d = {h: math.hypot(nxy[k][0] - H.loc[h].geometry.x, nxy[k][1] - H.loc[h].geometry.y) for h in hs}
        h = min(d, key=d.get)
        if d[h] <= a.radius:
            wells.append({"well": k, "hotspot": h, "hs_dist_m": round(d[h]), "vol": v["vol_m3"], "q": v["rate_m3s"], "x": nxy[k][0], "y": nxy[k][1],
                          "rim": rim[k], "inv": inv[k], "crest": crest[k]})
    wells.sort(key=lambda w: -w["vol"])
    print(f"溢流井 {len(wells)} 口（热点 {a.radius:g} m 内、溢流 ≥ {a.min_vol:g} m³）")
    dom = gpd.read_file(a.domain).to_crs(32640)
    site = Site(a.sitedata, dom, a.prefix, a.basis.split(",")[0], a.base)
    cands, rejected, comp = [], collections.Counter(), {}
    reasons = collections.defaultdict(list)

    def add(c, items):
        status = "不可建" if any(i["result"] == "fail" for i in items) else ("有条件可建" if any(i["result"] in ("unknown", "review") for i in items) else "可建")
        c["status"] = status
        if status == "不可建":
            rejected[c["kind"]] += 1
            reasons[c["kind"]].append([i["zh"] + "：" + i["detail"] for i in items if i["result"] == "fail"][:3])
            return
        c["id"] = {"tank_gravity": "TG", "tank_pumped": "TP", "ps_cluster": "PC"}[c["kind"]] + f"{sum(1 for x in cands if x['kind'] == c['kind']) + 1:04d}"
        c["cost_usd"] = round(c["cost_usd"]); cands.append(c); comp[c["id"]] = items

    def bl(cid, zh, ok, detail, src):          # B / C 层条目
        return {"check": cid, "zh": zh, "result": ok if isinstance(ok, str) else ("pass" if ok else "fail"), "detail": detail, "refs": [{"id": src}]}

    near_road = site.dist_road <= dv("DOM-PS-SITE")["road_access_max_m"] + 10.0     # 场地边到路 ≤ 20 m：场地中心放宽 10 m

    def ps_site(x, y, q, ww_depth, radius):
        need_area = dv("DOM-PS-SITE")["min_site_m2"] + max(0.0, q - 1.0) * dv("DOM-PS-SITE")["extra_m2_per_m3s_above_1"]
        b, bid = bclass(ww_depth)
        for d, cells, cx, cy in site.find_site(x, y, radius, need_area, b, extra_ok=near_road, max_sites=6):
            if site.flooded(cx, cy) or site.power_m(cx, cy) > dv("DOM-B5")["max_distance_to_substation_m"]:
                continue
            return d, cells, cx, cy, need_area, b, bid
        return None

    tiers = [float(t) for t in a.vol_tiers.split(",")]
    tank = dv("DOM-TANK")["effective_depth_m"]; B1 = dv("DOM-B1")
    gravity_ok_wells = set()
    # ---- ① 地下调蓄池（重力 / 泵送）
    for w in wells:
        for tf in tiers:
            req = w["vol"] * tf; V = req * (1 + K.val("WA726-1-128") / 100.0); area = V / tank
            got_g = False
            for d, cells, cx, cy in site.find_site(w["x"], w["y"], 500.0, area, bclass(tank + B1["tank_cover_m"] + 1.0)[0], min_width=dv("DOM-TANK")["min_width_m"]):
                zg = site.ground(cx, cy)
                L = site.road_dist((w["x"], w["y"]), (cx, cy)) or d * 1.3
                twl = min(zg - B1["tank_cover_m"], w["crest"] - B1["pipe_fall_per_m"] * L - B1["loss_m"])
                bottom = twl - tank; exc = zg - bottom + 0.3
                b_need, b_id = bclass(exc)
                mind = float(min(site.dist_b[r, c] for r, c in cells))
                items = K.check("Pond", {"pond_type": "underground", "in_developed_area": True, "required_volume_m3": req, "effective_volume_m3": V,
                                         "drain_mode": "pump", "gw_below_base_m": None, "outside_developer_area": True})["items"]
                items += [bl("B1_gravity", "重力能进水、开挖 ≤ 8 m", exc <= B1["max_excavation_m"], f"开挖 {exc:.1f} m（截流口 {w['crest']:.2f}，池水面 {twl:.2f}）", "DOM-B1"),
                          bl("C_building_B", "池边离建筑 ≥ B + 作业带", mind >= b_need + dv("DOM-B3")["work_band_m"], f"{mind:.1f} m，需 ≥ {b_need + 2:.1f} m", b_id),
                          bl("B5_power", "排空泵供电 ≤ 1 km", site.power_m(cx, cy) <= dv("DOM-B5")["max_distance_to_substation_m"], f"{site.power_m(cx, cy):.0f} m", "DOM-B5"),
                          bl("C_groundwater", "池底低于 0 m NADD 须地下水资料（抗浮 / 降水）", "review" if bottom < 0 else "pass", f"池底 {bottom:.2f} m", "DOM-TANK")]
                lm = site.land_mix(cells)
                items.append(bl("C_land", "用地许可", "review", f"{lm}（公共 / 未建 / 无记录地均需主管部门或业主许可，WA726-1-127）", "WA726-1-127"))
                gd = V5.pipe_d(w["q"], max((w["crest"] - twl) / max(L, 10), 1e-4), L)
                cost = V * PH["tank_m3"] + PH["gpipe_m"].get(gd, 2800) * L + PH["drain_pump"]
                c = {"kind": "tank_gravity", "well": w["well"], "wells": [w["well"]], "hotspot": w["hotspot"], "hs_dist_m": w["hs_dist_m"], "overflow_m3": round(w["vol"]),
                     "overflow_rate_m3s": round(w["q"], 3), "rim_m": round(w["rim"], 2), "inlet_z": round(w["crest"], 2), "tier": tf,
                     "pond_vol_m3": round(V), "pond_area_m2": round(len(cells) * RES * RES), "pond_depth_m": tank, "pond_top_m": round(twl, 2), "z_floor": round(bottom, 2),
                     "excavation_m": round(exc, 1), "pipe_m": round(L), "pipe_d_m": gd, "storage": "地下调蓄池", "land": lm, "px": round(cx, 1), "py": round(cy, 1),
                     "capture_cap_m3": round(min(w["vol"], V)), "cost_usd": cost, "cost_basis": "调蓄池 350 USD/m³、重力管、排空泵：占位单价",
                     "geom": site.outline(cells), "link": LineString([(w["x"], w["y"]), (cx, cy)])}
                n0 = len(cands); add(c, items)
                if len(cands) > n0:
                    got_g = True; gravity_ok_wells.add(w["well"])
                break
            if got_g:
                continue
            # 重力不行：井边泵站 → 压力管 → 调蓄池
            q = w["q"]; n, qe = pumps_for(q)
            wwd = rim[w["well"]] - (w["inv"] - 1.5)
            ps = ps_site(w["x"], w["y"], q, wwd, 80.0)
            if not ps:
                reasons["tank_pumped"].append(["井边 80 m 内没有合格泵站场地"]); rejected["tank_pumped"] += 1; continue
            _, pcells, px, py, need_area, b_ps, bid_ps = ps
            for d, cells, cx, cy in site.find_site(px, py, 800.0, area, bclass(tank + B1["tank_cover_m"] + 0.3)[0], min_width=dv("DOM-TANK")["min_width_m"]):
                zg = site.ground(cx, cy); twl = zg - B1["tank_cover_m"]; bottom = twl - tank
                L = site.road_dist((px, py), (cx, cy))
                if L is None:
                    continue
                dn, m, v = size_fm(q); rate, rid = fm_rate_usd(dn)
                wwv = 0.25 * qe * 3600.0 / K.dec("DEC-09")
                items = K.check("PumpStation", {"gravity_infeasible": True, "pump_count": n, "pump_capacity_m3s": qe, "design_flow_m3s": q,
                                                "wetwell_volume_m3": wwv, "surface_site_m2": len(pcells) * 4})["items"]
                items += K.check("PressurePipe", {"velocity_peak_ms": v, "material": "HDPE", "clearance_to_utilities_m": None, "clearance_to_building_m": None})["items"]
                items += K.check("Pond", {"pond_type": "underground", "in_developed_area": True, "required_volume_m3": req, "effective_volume_m3": V, "drain_mode": "pump",
                                          "outside_developer_area": True})["items"]
                items += [bl("B5_power", "泵站供电 ≤ 1 km", site.power_m(px, py) <= 1000, f"{site.power_m(px, py):.0f} m", "DOM-B5"),
                          bl("B6_flood", "泵站场地不积水", not site.flooded(px, py), "", "DOM-B6"),
                          bl("PS_access", "泵站场地临路（≤ 20 m，重型车辆进出）", min(site.dist_road[r, c] for r, c in pcells) <= 20, f"{min(site.dist_road[r, c] for r, c in pcells):.0f} m", "DOM-PS-SITE"),
                          bl("C_groundwater", "池底低于 0 m NADD 须地下水资料", "review" if bottom < 0 else "pass", f"{bottom:.2f} m", "DOM-TANK"),
                          bl("C_land", "用地许可", "review", f"泵站 {site.land_mix(pcells)}；池 {site.land_mix(cells)}", "WA726-1-127")]
                cost = (V * PH["tank_m3"] + PH["station_base"] + n * PH["pump_per_036"] * (qe / 0.361) ** 0.7 + wwv * 3 * PH["rc_wetwell_m3"]
                        + rate * L * m + PH["air_valve"] * max(1, int(L / 1000)) + PH["drain_pump"])
                c = {"kind": "tank_pumped", "well": w["well"], "wells": [w["well"]], "hotspot": w["hotspot"], "hs_dist_m": w["hs_dist_m"], "overflow_m3": round(w["vol"]),
                     "overflow_rate_m3s": round(q, 3), "rim_m": round(w["rim"], 2), "tier": tf, "n_pumps": n, "q_m3s": round(n * qe, 3), "pump_each_m3s": round(qe, 3),
                     "wetwell_m3": round(wwv, 1), "ps_site_m2": len(pcells) * 4, "ps_x": round(px, 1), "ps_y": round(py, 1),
                     "pond_vol_m3": round(V), "pond_area_m2": round(len(cells) * 4), "pond_depth_m": tank, "pond_top_m": round(twl, 2), "z_floor": round(bottom, 2),
                     "pipe_m": round(L), "main_d_m": dn, "n_mains": m, "v_ms": v, "storage": "地下调蓄池", "land": site.land_mix(cells), "px": round(cx, 1), "py": round(cy, 1),
                     "capture_cap_m3": round(min(w["vol"], V)), "cost_usd": cost, "cost_basis": f"压力管 {rid}（第三卷 p349）；其余占位",
                     "geom": site.outline(cells), "link": LineString([(w["x"], w["y"]), (px, py), (cx, cy)])}
                add(c, items); break
    # ---- ② 合并泵站 → 排海口
    from shapely.geometry import box as _box
    sh = gpd.GeoSeries([g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g for g in site.L["shoreline"].geometry], crs=32640).union_all().intersection(_box(*site.bbox))
    excl = site.L["coast_excl"]; ex_pts = excl[excl.src != "waterfront_protection_overlay_line"].geometry.union_all()
    wfl = excl[excl.src == "waterfront_protection_overlay_line"].geometry.union_all() if (excl.src == "waterfront_protection_overlay_line").any() else None
    B9 = dv("DOM-B9"); osites = []
    for ln in getattr(sh, "geoms", [sh]):
        for dd in np.arange(0, ln.length, B9["site_step_m"]):
            p = ln.interpolate(dd)
            if ex_pts is not None and p.distance(ex_pts) < B9["exclude_near_beach_or_facility_m"]:
                continue
            osites.append({"id": f"SEA{len(osites):03d}", "x": p.x, "y": p.y, "new": True, "wfl": wfl is not None and p.distance(wfl) < B9["waterfront_line_review_m"]})
    if "31" in nxy:
        osites.append({"id": "OF31", "x": nxy["31"][0], "y": nxy["31"][1], "new": False, "wfl": False})
    print(f"可设排口的岸点 {sum(o['new'] for o in osites)} 个（另有现有出水口 31）")
    otree = np.array([[o["x"], o["y"]] for o in osites])
    B10 = dv("DOM-B10"); clusters = set()
    for w in wells:
        mates = [u for u in wells if grp[u["hotspot"]] == grp[w["hotspot"]] and math.hypot(u["x"] - w["x"], u["y"] - w["y"]) <= B10["cluster_radius_m"]]
        clusters.add((w["well"],)); clusters.add(tuple(sorted(u["well"] for u in mates)))
    W_ = {w["well"]: w for w in wells}
    for cl in sorted(clusters, key=lambda c: (-len(c), c)):
        ws = [W_[k] for k in cl]; q = sum(u["q"] for u in ws); vol = sum(u["vol"] for u in ws)
        cx0 = float(np.mean([u["x"] for u in ws])); cy0 = float(np.mean([u["y"] for u in ws]))
        n, qe = pumps_for(q)
        ps = ps_site(cx0, cy0, q, 8.0, 150.0)
        if not ps:
            reasons["ps_cluster"].append(["聚合中心 150 m 内没有合格泵站场地"]); rejected["ps_cluster"] += 1; continue
        _, pcells, px, py, need_area, b_ps, bid_ps = ps
        Ls = [site.road_dist((u["x"], u["y"]), (px, py)) or math.hypot(u["x"] - px, u["y"] - py) * 1.3 for u in ws]
        inlet = min(u["crest"] - B1["pipe_fall_per_m"] * Li - B1["loss_m"] for u, Li in zip(ws, Ls))
        zps = site.ground(px, py); wwbot = inlet - 1.5; wwd = zps - wwbot
        dsea = np.hypot(otree[:, 0] - px, otree[:, 1] - py); best = None
        for j in np.argsort(dsea)[:4]:
            o = osites[j]; L = site.road_dist((px, py), (o["x"], o["y"]))
            if L is None:
                continue
            if best is None or L < best[1]:
                best = (o, L)
        if best is None:
            reasons["ps_cluster"].append(["泵站到排口没有沿路路径"]); rejected["ps_cluster"] += 1; continue
        o, L = best; dn, m, v = size_fm(n * qe); rate, rid = fm_rate_usd(dn)
        wwv = 0.25 * qe * 3600.0 / K.dec("DEC-09")
        items = K.check("PumpStation", {"gravity_infeasible": not any(u["well"] in gravity_ok_wells for u in ws) or None, "pump_count": n, "pump_capacity_m3s": qe,
                                        "design_flow_m3s": q, "wetwell_volume_m3": wwv, "surface_site_m2": len(pcells) * 4})["items"]
        items += K.check("PressurePipe", {"velocity_peak_ms": v, "material": "HDPE", "clearance_to_utilities_m": None, "clearance_to_building_m": None})["items"]
        items += K.check("Outfall", {"is_new": o["new"], "soffit_m_nadd": -0.8, "nrv_chamber_cover_m_nadd": 1.5, "flap_valve": True})["items"]
        items += [bl("B10_wetwell_depth", "湿井深 ≤ 10 m", wwd <= B10["max_wetwell_depth_m"], f"{wwd:.1f} m", "DOM-B10"),
                  bl("B5_power", "泵站供电 ≤ 1 km", site.power_m(px, py) <= 1000, f"{site.power_m(px, py):.0f} m", "DOM-B5"),
                  bl("B6_flood", "泵站场地不积水", not site.flooded(px, py), "", "DOM-B6"),
                  bl("PS_access", "泵站场地临路（≤ 20 m）", min(site.dist_road[r, c] for r, c in pcells) <= 20, f"{min(site.dist_road[r, c] for r, c in pcells):.0f} m", "DOM-PS-SITE"),
                  bl("B9_waterfront", "排口离滨水保护线 ≥ 50 m", "review" if o["wfl"] else "pass", o["id"], "DOM-B9"),
                  bl("C_land", "用地许可", "review", f"泵站 {site.land_mix(pcells)}", "WA726-1-127")]
        if not o["new"]:
            items.append(bl("B7_existing_outfall", "现有出水口 31 的接纳能力要核", "review", "", "DOM-B9"))
        ocost, oid = outfall_cost_usd(dn) if o["new"] else (0.0, "现有")
        glink = sum(PH["gpipe_m"].get(V5.pipe_d(u["q"], 0.002, Li), 2800) * Li for u, Li in zip(ws, Ls))
        cost = (PH["station_base"] + n * PH["pump_per_036"] * (qe / 0.361) ** 0.7 + wwv * 3 * PH["rc_wetwell_m3"] + rate * L * m
                + PH["air_valve"] * max(1, int(L / 1000)) + glink + ocost)
        c = {"kind": "ps_cluster", "well": ws[0]["well"], "wells": [u["well"] for u in ws], "hotspot": ws[0]["hotspot"], "hs_dist_m": ws[0]["hs_dist_m"],
             "overflow_m3": round(vol), "overflow_rate_m3s": round(q, 3), "rim_m": round(ws[0]["rim"], 2), "n_pumps": n, "q_m3s": round(n * qe, 3), "pump_each_m3s": round(qe, 3),
             "wetwell_m3": round(wwv, 1), "wetwell_inlet_z": round(inlet, 2), "wetwell_bottom_z": round(wwbot, 2), "ps_site_m2": len(pcells) * 4,
             "ps_x": round(px, 1), "ps_y": round(py, 1), "links": [{"well": u["well"], "L": round(Li), "inlet_z": round(u["crest"], 2)} for u, Li in zip(ws, Ls)],
             "outfall_site": o["id"], "of_xy": [round(o["x"], 1), round(o["y"], 1)], "outfall_new": o["new"], "pipe_m": round(L), "main_d_m": dn, "n_mains": m, "v_ms": v,
             "capture_cap_m3": round(vol), "cost_usd": cost, "cost_basis": f"压力管 {rid}、排口 {oid}（第三卷 p349）；泵站、湿井、重力连接管：占位",
             "geom": Point(px, py).buffer(math.sqrt(len(pcells) * 4 / math.pi)), "link": LineString([(px, py), (o["x"], o["y"])])}
        add(c, items)
    # ---- 写出
    feats = []
    for c in cands:
        g, ln = c.pop("geom"), c.pop("link", None)
        lo, la = T.transform(c.get("px", c.get("ps_x")), c.get("py", c.get("ps_y")))
        c["lon"], c["lat"] = round(lo, 6), round(la, 6)
        feats.append({"type": "Feature", "properties": {"id": c["id"], "kind": c["kind"], "status": c["status"]}, "geometry": W(g)})
        if ln is not None:
            feats.append({"type": "Feature", "properties": {"id": c["id"] + "_link", "kind": "link"}, "geometry": W(ln)})
    meta = {"rule": __doc__.split("用法")[0].strip(), "basis": srcs, "base": a.base, "wells": len(wells), "by_kind": collections.Counter(c["kind"] for c in cands),
            "by_status": collections.Counter(c["status"] for c in cands), "rejected": rejected, "domain": {k: v["value"] for k, v in DOM.items()}}
    (out / "candidates.json").write_text(json.dumps({"run": a.out_name, "base": a.base, "candidates": cands, "meta": meta}, ensure_ascii=False, default=float), encoding="utf-8")
    (out / "candidates.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    (out / "compliance.json").write_text(json.dumps(comp, ensure_ascii=False, default=float), encoding="utf-8")
    (out / "rejected.json").write_text(json.dumps({k: v[:200] for k, v in reasons.items()}, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("wells", "by_kind", "by_status", "rejected")}, ensure_ascii=False, default=float))
    top = collections.Counter(r for v in reasons.values() for x in v for r in [x[0].split("：")[0] if x else "?"])
    print("剔除原因（前 8）：", top.most_common(8))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
