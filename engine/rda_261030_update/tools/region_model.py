# -*- coding: utf-8 -*-
"""区域一二维建模：从 data/regions/<区域>（区域数据）+ config/model_rules.toml（公共规则）+ data/common（雨型）搭出一个场景的全部输入：
    <工作目录>/<名>_L1.inp                  一维 SWMM 输入
    <工作目录>/c2d/pilot_<名>_L1.json       二维 / 耦合配置（couple2way_v2 读）
    <工作目录>/c2d/dem_<名>.tif             二维地形（DTM 裁剪 + 深坑填平由 pilot 的 dem_edits 在耦合时做）
    <工作目录>/c2d/surf_<名>.tif            下垫面分类栅格（糙率分区）
    <工作目录>/run/<区域>/inputs_<名>_L1.json   逐项输入登记（页面「推演参数」读，形状同 F 系列 inputs_*.json）

逻辑沿用 main 上 F 系列的 rda_gov_build.py / rda_catchments.py / bldg_holes_prep.py / v_build.py，但：
  · 不再读 Windows 路径、client_data、Makani 在线库、AECOM 原始文件，全部读区域数据包；
  · 所有取值走公共规则文件（model_rules.toml），不在代码里写死；
  · 只做 L1（永久设施）。L2 / L2planned 要数据里有明确标注，且要先让用户确认，这里不做。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import model_rules as MR  # noqa: E402
import region_net as RN  # noqa: E402
import runoff as RO  # noqa: E402

STATE = "L1"             # 缺省态；v2 数据包按 build_scenario(state=…) 传入（L1 / L2 / L2planned，见 region_features.py）
BLD_TABLES = ("bas_building", "bdms_bldg_landuse", "udm_bldroofsurface")
PARK_TABLES = ("ud_parkingarea", "udm_parking")
GREEN_TABLES = ("ud_greenarea", "upc_greenarea", "udm_park", "udm_irrigationsurface", "udm_puttinggreen")
WATER_TABLES = ("udm_watersurface", "udm_seasurface", "udm_fountainsurface", "udm_canalwatercoursesurface")
PAVE_TABLES = ("udm_roadsurface", "udm_roadshoulder", "udm_roadisland", "udm_roadintersection",
               "main_roads_carriageway_ifp2018_1", "main_roads_shoulders_ifp2018_1", "main_roads_median_ifp2018_1")
MATERIAL_NAME = {"Reinforced Concrete": "Reinforced Concrete - RCP", "GRP": "Glass Reinforced Plastic - GRP", "UPVC": "Polyvinyl Chloride - PVC",
                 "PVC": "Polyvinyl Chloride - PVC", "HDPE": "High Density Polyethylene - HDPE"}
SURF_CLASSES = {0: ("其他硬地", "other_hard"), 1: ("路面 / 铺装 / 停车", "road_pavement_parking"), 2: ("建筑", "building"),
                3: ("绿地 / 未建地块", "green_vacant"), 4: ("水面", "water")}
ELEV_TOL = 1.0           # 井底比单元地面高出这么多（m）→ 高架井，只溢流
sid = lambda x: re.sub(r"\s+", "_", str(x))  # noqa: E731


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


class Region:
    """一个区域的数据包 + 公共规则。"""

    def __init__(self, region: str):
        self.id = region
        self.dir = ROOT / "data" / "regions" / region
        self.cfg = tomllib.loads((self.dir / "region.toml").read_text(encoding="utf-8"))
        self.rules = MR.load()
        self.rain = json.loads((ROOT / "data" / "common" / "rain_mussafah.json").read_text(encoding="utf-8"))
        self._dtm = None

    def p(self, name: str) -> Path:
        return self.dir / name

    def dtm_file(self) -> Path:
        """推演用的 DTM。默认 data/regions/<区域>/dtm_5m.tif（5 m，随包带）；
        设了环境变量 FLOOD_DTM（单个 tif，或一个放分块 tif 的目录，如服务器上的 50 cm DTM）就从它按计算域范围裁出来用。"""
        src = os.environ.get("FLOOD_DTM", "").strip()
        if not src:
            return self.p("dtm_5m.tif")
        if self._dtm is None:
            self._dtm = _clip_dtm(self, Path(src))
        return self._dtm

    def dtm_sampler(self):
        import rasterio
        ds = rasterio.open(self.dtm_file())

        def f(xy):
            v = np.array([x[0] for x in ds.sample([tuple(p) for p in xy])], float)
            v[(v < -100) | ~np.isfinite(v)] = np.nan
            return v
        return f


def _clip_dtm(R: Region, src: Path, margin_m: float = 300.0) -> Path:
    """从 FLOOD_DTM（单个 tif 或分块目录）裁出计算域 + margin 的范围，写到工作目录缓存（多个算例共用，原子改名）。"""
    import hashlib
    import tempfile
    import geopandas as gpd
    import rasterio
    from rasterio.merge import merge
    from rasterio.windows import from_bounds
    if not src.exists():
        raise SystemExit(f"FLOOD_DTM 指向的路径不存在：{src}")
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.suffix.lower() in (".tif", ".tiff"))
    if not files:
        raise SystemExit(f"FLOOD_DTM 目录里没有 .tif：{src}")
    x0, y0, x1, y1 = gpd.read_file(R.p("catchments.gpkg")).total_bounds
    bb = (x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m)
    cache = Path(os.environ.get("SWBRAIN_SWMM_WORK") or tempfile.gettempdir())
    key = hashlib.md5(f"{src}|{len(files)}|{bb}".encode()).hexdigest()[:10]
    out = cache / f"dtm_clip_{R.id}_{key}.tif"
    if out.is_file():
        return out
    hit = []
    for f in files:
        with rasterio.open(f) as d:
            b = d.bounds
            if b.right > bb[0] and b.left < bb[2] and b.top > bb[1] and b.bottom < bb[3]:
                hit.append(f)
    if not hit:
        raise SystemExit(f"{src} 里没有任何 tif 覆盖计算域 {tuple(round(v) for v in bb)}（要 EPSG:32640）")
    dss = [rasterio.open(f) for f in hit]
    try:
        if len(dss) == 1:
            d = dss[0]
            w = from_bounds(*bb, transform=d.transform).round_offsets().round_lengths()
            a = d.read(1, window=w); tf = d.window_transform(w)
            prof = d.profile.copy()
        else:
            a, tf = merge(dss, bounds=bb)
            a = a[0]
            prof = dss[0].profile.copy()
        nod = dss[0].nodata if dss[0].nodata is not None else -9999.0
        prof.update(driver="GTiff", height=a.shape[0], width=a.shape[1], count=1, dtype="float32", nodata=nod, transform=tf,
                    compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256)
        for k in ("photometric", "interleave"):
            prof.pop(k, None)
        tmp = out.with_suffix(f".{os.getpid()}.tmp")
        with rasterio.open(tmp, "w", **prof) as w_:
            w_.write(a.astype("float32"), 1)
        os.replace(tmp, out)
        res = dss[0].res[0]
    finally:
        for d in dss:
            d.close()
    print(f"  DTM：{src}（{len(hit)} 个 tif，{res:g} m）→ 裁出 {a.shape[1]}×{a.shape[0]} 格：{out}", flush=True)
    return out


# ------------------------------------------------------------------ 计算域
def domain(R: Region, log=print):
    import geopandas as gpd
    from shapely.geometry import Polygon
    ca = gpd.read_file(R.p("catchments.gpkg"))
    close = float(MR.get("twod.domain_closing_m", R.rules))
    U = ca.union_all()
    V = U.buffer(close).buffer(-close)
    parts = sorted(getattr(V, "geoms", [V]), key=lambda p: -p.area)
    if len(parts) > 1 and parts[1].area > 0.01 * parts[0].area:
        raise SystemExit(f"计算域闭运算 {close:g} m 后仍有 {len(parts)} 块：{[round(p.area / 1e6, 3) for p in parts]} km²")
    D = Polygon(parts[0].exterior)
    log(f"  计算域：子汇水区并集 {U.area / 1e6:.2f} km² → 闭运算 {close:g} m、填洞 → {D.area / 1e6:.2f} km²")
    return D


# ------------------------------------------------------------------ 一维管网
def aecom_elements(R: Region, wet_id: str):
    """泵、泵曲线、湿井（GDB / Makani 里只有额定值，曲线和启停水深沿用 AECOM：data/regions/<区域>/aecom_elements.json）。"""
    E = json.loads(R.p("aecom_elements.json").read_text(encoding="utf-8"))
    curves, cur = defaultdict(list), None
    areas = {}
    for line in E["curves"]:
        f = line.split()
        if len(f) == 4:
            cur = f[0]; typ, x, y = f[1], float(f[2]), float(f[3])
        else:
            typ, x, y = None, float(f[1]), float(f[2])
        if f[0].startswith("PC_"):
            curves[f[0]].append((x, y))
        elif f[0].endswith("_Curve"):
            areas[f[0]] = y
    pumps = {}
    for line in E["pumps"]:
        f = line.split()                      # PU_<名> 吸水节点 出水节点 曲线 ON on_depth off_depth
        pumps[f[2]] = {"curve": curves[f[3]], "on_depth": float(f[5]), "off_depth": float(f[6]), "suction": f[1]}
    st = next(l.split() for l in E["storage"] if l.split()[0] == wet_id)
    wet = {"inv": float(st[1]), "maxd": float(st[2]), "area": areas[st[5]]}
    return pumps, wet


def features(R: Region, log=print):
    """GDB 要素 → 管 P / 点 Nd / 封堵端 / 泵 / 湿井。规则：糙率按管材（公共规则），压力管 H-W；出水口固定水位 + 拍门；泵曲线沿用 AECOM。"""
    import geopandas as gpd
    rules, sea = R.rules, float(MR.get("outfalls.sea_level_m", R.rules))
    ln = gpd.read_file(R.p("network.gpkg"), layer="lines")
    ln = ln[ln["connected"] & (ln["assetstate"] != 0)]
    dev = gpd.read_file(R.p("network.gpkg"), layer="devices")
    sj = gpd.read_file(R.p("network.gpkg"), layer="structure_junctions")
    jn = gpd.read_file(R.p("network.gpkg"), layer="junctions")
    nmat = Counter()
    P = []
    for r in ln.itertuples():
        g = r.geometry
        parts = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
        co = [c for part in parts for c in part.coords]
        mat = MATERIAL_NAME.get(str(getattr(r, "dmt_pipe_mater", "")).strip(), "Unknown")
        n = MR.manning_for(mat, rules)
        nmat[f"{mat} → {n}"] += 1
        d = num(r.diameter)
        P.append({"id": f"G{len(P) + 1}", "xy": [co[0][:2], co[-1][:2]], "vx": [c[:2] for c in co[1:-1]], "len": num(r.measuredlength) or g.length,
                  "d_m": d / 1000 if d else None, "up": num(r.upelev), "dn": num(r.downelev), "n": n, "src": r.assetid,
                  "force": str(getattr(r, "dmt_pipe_subty", "")) == "PressrLine"})
    rim_v = {a: num(v) for a, v in zip(sj.assetid, sj.rimelev)}
    Nd, seen = [], set()

    def add(n):
        base, k = n["id"], 1
        while n["id"] in seen:
            k += 1; n["id"] = f"{base}_{k}"
        seen.add(n["id"]); Nd.append(n)
    keep_of = [o for o in R.cfg["network"]["outfalls"] if o in set(dev.loc[dev.ASSETGROUP == 33, "assetid"])]
    pump_dev, wet_devs = [], []
    for d in dev.itertuples():
        a, g, xy = d.assetid, int(d.ASSETGROUP), (d.geometry.x, d.geometry.y)
        inv = num(d.elevation)
        if g == 28:
            add({"id": sid(a), "xy": xy, "kind": "inlet", "rim": None, "inv": inv})
        elif g == 32:
            add({"id": sid(a), "xy": xy, "kind": "mh", "rim": rim_v.get(a), "inv": inv})
        elif g == 33:
            add({"id": sid(a), "xy": xy, "kind": "outfall", "inv": inv, "bc": f"FIXED {sea:.3f}" if a in keep_of else "FREE", "flap": a in keep_of})
        elif g == 22:
            add({"id": sid(a), "xy": xy, "kind": "junction", "inv": None, "rim": None})
        elif g == 5:
            if a.startswith("AE_"):
                pump_dev.append((a, xy))          # AECOM 泵（每台一个点）
            else:
                wet_devs.append((a, xy, inv))     # 泵站（湿井）SW_ABT_*_PSS_*（候选：只取 AECOM 里有湿井数据的那个）
    # 泵与湿井：位置来自 GDB，曲线 / 启停水深来自 AECOM（aecom_elements.json）
    E_st = {l.split()[0] for l in json.loads(R.p("aecom_elements.json").read_text(encoding="utf-8"))["storage"]}
    wet_dev = next((w for w in wet_devs if w[0] in E_st), None)
    wetwell, pumps = None, []
    if wet_dev:
        epumps, ew = aecom_elements(R, wet_dev[0])
        wetwell = {"id": sid(wet_dev[0]), "xy": wet_dev[1], "inv": ew["inv"], "maxd": ew["maxd"], "area": ew["area"]}
        add({"id": wetwell["id"], "xy": wet_dev[1], "kind": "wetwell", "inv": None, "rim": None})
        for a, xy in pump_dev:
            hit = next((k for k in epumps if sid(a).endswith(sid(k.split(":")[-1]))), None)
            if hit is None:
                log(f"  ⚠ 泵 {a} 在 aecom_elements.json 里找不到对应曲线，跳过")
                continue
            e = epumps[hit]
            add({"id": sid(a), "xy": xy, "kind": "pump", "inv": None, "rim": None})
            pumps.append({"id": sid(a), "xy": xy, "on_elev": wetwell["inv"] + e["on_depth"], "off_elev": wetwell["inv"] + e["off_depth"], "curve": e["curve"]})
    caps = np.c_[jn.geometry.x, jn.geometry.y] if len(jn) else np.zeros((0, 2))
    return P, Nd, keep_of, pumps, wetwell, caps, dict(nmat)


def network(R: Region, D, reg: dict, log=print, state: str = STATE):
    """管网：build_from_features，再按规则改：压力管 H-W、封死计算域外的非结构节点和泵出口。
    数据包 region.toml [network] format = "v2" 走 region_features.features_v2（按 ASSET_STAGE 分态）；否则走旧 Utility Network 数据包 features()（只有 L1）。"""
    from shapely.geometry import Point
    from shapely.prepared import prep
    rules = R.rules
    v2 = R.cfg["network"].get("format") == "v2"
    if v2:
        import region_features as RF
        P, Nd, keep_of, pumps, wetwell, caps, nmat, finfo = RF.features_v2(R, state, log)
        reg["facilities"] = finfo
    else:
        if state != "L1":
            raise SystemExit("旧数据包只支持 L1；L2 / L2planned 要 v2 数据包（region.toml [network] format = \"v2\"）")
        P, Nd, keep_of, pumps, wetwell, caps, nmat = features(R, log)
    byid = {p["id"]: p for p in P}
    dtm = R.dtm_sampler()
    net, N, info = RN.build_from_features(P, Nd, keep_of, pumps, wetwell, D, log, caps=caps, dtm=dtm)
    if v2:     # 最终进入模型的管线按 STATUS 统计：Abandoned / Expired / Removed 的也用了（剔除会断连通性），参数报告里写明
        from collections import Counter as _C
        pb = finfo.pop("_pipe_by_id")
        used = [pb[c[0]] for c in net["CO"] if c[0] in pb]
        cnt = _C(s for s, _ in used)
        odd = [u for s, u in used if s in ("Abandoned", "Expired", "Removed")]
        finfo["pipes_used"] = {"n": len(used), "by_STATUS": dict(cnt), "n_abandoned_expired_removed": len(odd), "abandoned_expired_removed_unitids": odd[:300],
                               "note": ("STATUS 为 Abandoned / Expired / Removed 的管线也进了模型（不使用这些状态的管线会造成管网连通性问题）" if odd else
                                 "规则：管线不分状态全用，含 STATUS 为 Abandoned / Expired / Removed 的（不使用这些状态的管线会造成管网连通性问题）。本区域进模型的管线里没有这三种状态（只有 " + "、".join(sorted(cnt)) + "），实际用了 0 根")}
        log(f"  进模型的管线 {len(used)} 根，按 STATUS {dict(cnt)}；其中 Abandoned / Expired / Removed {len(odd)} 根（规则：保留，参数报告里写明）")
    reg["source"] = {"source": (f"2026-10-30 全市治理后数据 PIPELINE（不分状态）+ RD-A 汇水区附属设施（按 ASSET_STAGE 分态），data/regions/{R.id}/network_v2.gpkg" if v2 else
                                f"治理后 GDB（出水口子网，data/regions/{R.id}/network.gpkg）+ AECOM 泵 / 湿井（aecom_elements.json）"),
                     **{k: v for k, v in info.items() if k != "wetwell"}}
    reg["pipe_n_by_material"] = nmat
    hw = float(MR.get("pipes.force_main_hazen_williams_c", rules))
    fm = {c[0] for c in net["CO"] if byid.get(c[0], {}).get("force")}
    net["XS"] = [(x[0], "FORCE_MAIN", x[2], hw, 0, 0, 1) if x[0] in fm else x for x in net["XS"]]
    reg["force_mains"] = sorted(byid[c]["src"] for c in fm)
    sea = float(MR.get("outfalls.sea_level_m", rules))
    reg["outfalls"] = [{"id": o[0], "invert_m": round(o[1], 3), "boundary": o[2], "flap": o[3]} for o in net["OF"]]
    kinds = {n["id"]: n["kind"] for n in Nd}
    struct = {k for k, v in kinds.items() if v in ("mh", "inlet")}
    pD = prep(D)
    sealed_out = [k for k, j in net["J"].items() if k not in struct and j[3] < RN.SEAL_SUR and not pD.contains(Point(net["coords"][k]))]
    for k in sealed_out:
        net["J"][k][3] = RN.SEAL_SUR
    pump_out = {p[2] for p in net["PU"]}
    for k in pump_out:
        if k in net["J"]:
            net["J"][k][3] = RN.SEAL_SUR
    split_multi_inlet_outfalls(net, reg, log)
    reg["sealed"] = {"outside_domain_nonstructure": len(sealed_out), "pump_outlets": sorted(pump_out & set(net["J"])), "end_caps": info.get("sealed_end_caps")}
    return net, N, kinds, struct


def split_multi_inlet_outfalls(net, reg: dict, log=print):
    """SWMM 动力波下一个出水口节点只能接一根管（ERROR 141）。接了多根的，拆成「井 + 极短连接管 + 出水口」：
    原管全部改接新井（井底 = 出水口底），新井经 0.5 m 连接管接到出水口（管径取接入管最大值）。返回拆了哪些。"""
    inc = defaultdict(list)
    for c in net["CO"]:
        for n in (c[1], c[2]):
            inc[n].append(c)
    dia = {x[0]: float(x[2]) for x in net["XS"]}
    done = []
    for i, (k, inv, bc, flap) in enumerate(list(net["OF"])):
        links = inc.get(k, [])
        if len(links) <= 1:
            continue
        j = f"{k}_J"
        net["CO"] = [tuple([c[0], j if c[1] == k else c[1], j if c[2] == k else c[2], *c[3:]]) if k in (c[1], c[2]) else c for c in net["CO"]]
        d = max(dia[c[0]] for c in links)
        crown = max(max(float(c[5] if c[1] == k else c[6]) + dia[c[0]] for c in links), d)
        net["J"][j] = [inv, crown + 0.5, 0.0, 0.0, 0.0]
        x, y = net["coords"][k]
        net["coords"][j] = (x + 0.2, y + 0.2)
        net["CO"].append((f"{k}_LNK", j, k, 0.5, 0.013, 0.0, 0.0, 0, 0))
        net["XS"].append((f"{k}_LNK", "CIRCULAR", d, 0, 0, 0, 1))
        done.append({"outfall": k, "links": [c[0] for c in links]})
    if done:
        reg["outfall_split"] = done
        log(f"  出水口接了多根管，拆成「井 + 连接管 + 出水口」：{[d['outfall'] for d in done]}")
    return done


def node_min_depth(net):
    """每个节点接入管的最高管顶（相对节点底）：ground_basis = cell 时节点深度不能比它浅"""
    inv = {k: v[0] for k, v in net["J"].items()}
    dia = {x[0]: float(x[2]) for x in net["XS"]}
    md = defaultdict(float)
    for c in net["CO"]:
        md[c[1]] = max(md[c[1]], float(c[5]) + dia[c[0]]); md[c[2]] = max(md[c[2]], float(c[6]) + dia[c[0]])
    return {k: round(md[k], 3) for k in inv if k in md}


def exchange_nodes(R: Region, net, N, kinds, D, reg):
    """交换节点 = 计算域内、没封死的节点；雨水口（AECOM 清单里的 inlet）→ inlet，其余（检查井、连接点、高架井、不在 AECOM 清单的雨水口）→ 只溢流"""
    import pandas as pd
    from shapely.geometry import Point
    from shapely.prepared import prep
    pD = prep(D)
    dtm = R.dtm_sampler()
    ax = pd.read_csv(R.p("aecom_exchange_nodes.csv"))
    aecom_inlet = {sid(n) for n in ax[ax["type"] == "inlet"]["node"]}
    keys = [k for k in N if k in net["J"] and net["J"][k][3] < RN.SEAL_SUR and pD.contains(Point(N[k][0], N[k][1]))]
    z = dtm([N[k][:2] for k in keys])
    xt, elevated = {}, []
    for k, zg in zip(keys, z):
        if np.isfinite(zg) and net["J"][k][0] > zg + ELEV_TOL:
            elevated.append(k)
        xt[k] = "inlet" if kinds.get(k) == "inlet" and k in aecom_inlet and k not in elevated else "overflow"
    asset = {"inlet": "雨水口（GDB INL / CPD）", "mh": "检查井流槽", "junction": "连接点 / 拍门", "pump": "泵", "wetwell": "湿井", "outfall": "出水口"}
    reg["exchange"] = {"nodes": len(keys), "by_type": dict(Counter(xt.values())),
                       "by_asset": dict(Counter(asset.get(kinds.get(k), "连接点 JN") for k in keys)), "elevated_overflow_only": elevated,
                       "inlet_rule": "以 AECOM 清单为准（aecom_exchange_nodes.csv）；GDB 里不在清单的 INL / CPD 只溢流"}
    return {k: N[k] for k in keys}, xt


def init_fill(txt, frac):
    """给 JUNCTIONS 写初始水深：节点水位 = 相连管端中最低的那个管底 + 该管「断面 frac 有水」对应的水深。出水口、湿井不动。返回 (新文本, 统计)。"""
    from scipy.optimize import brentq
    yd = brentq(lambda y: (lambda th: (th - np.sin(th)) / (2 * np.pi))(2 * np.arccos(1 - 2 * y)) - frac, 1e-6, 1 - 1e-6)
    S, cur = {}, None
    for l in txt.splitlines():
        s = l.split(";")[0].strip()
        if not s:
            continue
        if s.startswith("["):
            cur = s.strip("[]"); S[cur] = []; continue
        S[cur].append(s.split())
    inv = {r[0]: float(r[1]) for r in S.get("JUNCTIONS", [])}
    ymax = {r[0]: float(r[2]) for r in S.get("JUNCTIONS", [])}
    for sec in ("OUTFALLS", "STORAGE"):
        for r in S.get(sec, []):
            inv[r[0]] = float(r[1])
    dia = {r[0]: float(r[2]) for r in S.get("XSECTIONS", []) if r[1] in ("CIRCULAR", "FORCE_MAIN")}
    best = {}
    for r in S.get("CONDUITS", []):
        if r[0] not in dia:
            continue
        for n, off in ((r[1], float(r[5])), (r[2], float(r[6]))):
            if n in ymax:
                z = inv[n] + off
                if n not in best or z < best[n][0]:
                    best[n] = (z, dia[r[0]])
    y0 = {n: min(max(z + yd * d - inv[n], 0.0), ymax[n]) for n, (z, d) in best.items()}
    out, cur = [], None
    for l in txt.splitlines():
        s = l.strip()
        if s.startswith("["):
            cur = s.strip("[]")
        elif cur == "JUNCTIONS" and s and not s.startswith(";"):
            p = l.split()
            if p[0] in y0:
                p[3] = f"{y0[p[0]]:.3f}"
                l = "  ".join(p)
        out.append(l)
    vol = full = 0.0
    seg = lambda y, d: (d * d / 8) * ((th := 2 * np.arccos(np.clip(1 - 2 * np.clip(y, 0, d) / d, -1, 1))) - np.sin(th))
    for r in S.get("CONDUITS", []):
        if r[0] not in dia:
            continue
        d, L = dia[r[0]], float(r[3])
        y1 = y0.get(r[1], 0.0) - float(r[5]); y2 = y0.get(r[2], 0.0) - float(r[6])
        vol += seg((y1 + y2) / 2, d) * L; full += np.pi * d * d / 4 * L
    return "\n".join(out) + "\n", {"frac_area": frac, "y_over_D": round(float(yd), 4), "nodes_set": len(y0), "pipe_vol_init_m3": round(vol), "pipe_vol_full_m3": round(full), "share": round(vol / full, 4)}


# ------------------------------------------------------------------ SWMM inp
def write_inp(path: Path, title: str, net, H, t, sim_h: float, rules: dict, threads: int, log=print):
    """一维 SWMM 输入。[OPTIONS] 的取值全部来自公共规则（swmm.options）；时间、报告节点等是这次运行的事。"""
    eh, em = int(sim_h), int(round((sim_h % 1) * 60))
    o = dict(MR.swmm_options(rules))
    order = ["FLOW_UNITS", "INFILTRATION", "FLOW_ROUTING", "LINK_OFFSETS", "FORCE_MAIN_EQUATION", "MIN_SLOPE", "ALLOW_PONDING", "SKIP_STEADY_STATE"]
    L = [f"[TITLE]\n;; {title}\n", "[OPTIONS]"]
    L += [f"{k}  {o[k]}" for k in order]
    L += ["START_DATE  04/16/2024", "START_TIME  00:00:00", "REPORT_START_DATE  04/16/2024", "REPORT_START_TIME  00:00:00",
          "END_DATE  04/16/2024", f"END_TIME  {eh:02d}:{em:02d}:00", "SWEEP_START  01/01", "SWEEP_END  12/31"]
    L += [f"{k}  {o[k]}" for k in ("DRY_DAYS", "REPORT_STEP", "WET_STEP", "DRY_STEP", "ROUTING_STEP", "VARIABLE_STEP", "MINIMUM_STEP", "LENGTHENING_STEP",
                                    "NORMAL_FLOW_LIMITED", "SURCHARGE_METHOD", "MAX_TRIALS", "HEAD_TOLERANCE")]
    L += [f"THREADS  {threads}", "", "[REPORT]", "INPUT  NO", "CONTROLS  NO", "SUBCATCHMENTS  NONE", "NODES  NONE", "LINKS  NONE", "",
          "[EVAPORATION]", f"CONSTANT  {MR.get('swmm.options.EVAPORATION_CONSTANT', rules)}", "", "[JUNCTIONS]"]
    for k, (inv, md, ini, sur, pa) in net["J"].items():
        L.append(f"{k}  {inv:.3f}  {md:.3f}  {ini:.3f}  {sur:.3f}  {pa:.2f}")
    L += ["", "[OUTFALLS]"] + [f"{k}  {inv:.3f}  {typ}  {gate}" for k, inv, typ, gate in net["OF"]]
    L += ["", "[STORAGE]"] + [f"{k}  {inv:.3f}  {md:.3f}  {ini:.3f}  {typ}  {cv}  0  0" for k, inv, md, ini, typ, cv in net["ST"]]
    L += ["", "[CONDUITS]"] + [f"{k}  {a}  {b}  {ln:.3f}  {n:.4f}  {o1:.3f}  {o2:.3f}  0  0" for k, a, b, ln, n, o1, o2, *_ in net["CO"]]
    L += ["", "[PUMPS]"] + [f"{k}  {a}  {b}  {c}  {s}  {on:.3f}  {off:.3f}" for k, a, b, c, s, on, off in net["PU"]]
    L += ["", "[XSECTIONS]"] + [f"{k}  {typ}  {g1:.4f}  {g2:g}  {g3:g}  {g4:g}  {nb}" for k, typ, g1, g2, g3, g4, nb in net["XS"]]
    if net["LO"]:
        L += ["", "[LOSSES]"] + ["  ".join(r) for r in net["LO"]]
    L += ["", "[CURVES]"]
    seen = set()
    for k, typ, x, y in net["CU"]:
        L.append(f"{k}  {typ if k not in seen else ''}  {x:.5f}  {y:.6f}"); seen.add(k)
    L += ["", "[INFLOWS]"]
    keep = {n: q for n, q in H.items() if q.max() > 1e-7}
    for n in sorted(keep):
        L.append(f"{n}  FLOW  TS_{n}  FLOW  1.0  1.0  0")
    L += ["", "[TIMESERIES]"]
    for n in sorted(keep):
        q = keep[n]; nz = np.nonzero(q > 1e-7)[0]
        lo, hi = max(int(nz[0]) - 1, 0), min(int(nz[-1]) + 2, len(t))
        for i in range(lo, hi):
            L.append(f"TS_{n}  {t[i]:.5f}  {q[i]:.6g}")
        if hi < len(t):
            L.append(f"TS_{n}  {t[-1]:.5f}  0")
    L += ["", "[COORDINATES]"] + [f"{k}  {x:.2f}  {y:.2f}" for k, (x, y) in net["coords"].items()]
    if net["VX"]:
        L += ["", "[VERTICES]"] + [f"{k}  {x:.2f}  {y:.2f}" for k, x, y in net["VX"]]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log(f"  → {path.name}（{path.stat().st_size / 1048576:.1f} MB）：井 {len(net['J']):,}、管 {len(net['CO']):,}、出水口 {len(net['OF'])}、蓄水 {len(net['ST'])}、泵 {len(net['PU'])}、入流节点 {len(keep):,}")


# ------------------------------------------------------------------ 子汇水区、产流
def _layer(R: Region, name: str):
    import geopandas as gpd
    import pyogrio
    if name not in {l[0] for l in pyogrio.list_layers(R.p("makani.gpkg"))}:
        return None
    return gpd.read_file(R.p("makani.gpkg"), layer=name)


def _geoms(R: Region, tables, where=None):
    out, cnt = [], {}
    for t in tables:
        g = _layer(R, t)
        if g is None or not len(g):
            cnt[t] = 0
            continue
        if where is not None:
            g = g[where(g)]
        gg = [x for x in g.geometry if x is not None and x.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") and not x.is_empty]
        cnt[t] = len(gg); out += gg
    return out, cnt


def composite_subs(R: Region, log=print):
    """子汇水区 → 产流子区表。成对地块（透水 + 不透水两块几何相同）按几何去重，按不透水比例 f（Makani 1 m 栅格上数像元）拆开；不成对的原样。
    规则：runoff.impervious_fraction（不透水 = 建筑 ∪ 道路 ∪ 停车；透水 = 绿地 ∪ 水面 ∪ 未建地块；没画到的按不透水）。"""
    import geopandas as gpd
    import pandas as pd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    cat = gpd.read_file(R.p("catchments.gpkg"))
    cat["wkb"] = cat.geometry.normalize().to_wkb()
    res = float(MR.get("runoff.impervious_fraction.raster_cell_m", R.rules))
    cn_imp = float(MR.get("runoff.impervious_cn", R.rules))
    x0, y0, x1, y1 = cat.total_bounds + np.array([-5, -5, 5, 5])
    W, H = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    tf = from_origin(x0, y1, res, res)
    ras = lambda g: rasterize(((x, 1) for x in g), out_shape=(H, W), transform=tf, fill=0, dtype="uint8").astype(bool) if g else np.zeros((H, W), bool)  # noqa: E731
    bld, c1 = _geoms(R, BLD_TABLES); pave, c2 = _geoms(R, PAVE_TABLES); park, c3 = _geoms(R, PARK_TABLES)
    green, c4 = _geoms(R, GREEN_TABLES); water, c5 = _geoms(R, WATER_TABLES)
    vac, c6 = _geoms(R, ("udm_plot",), lambda g: g["construction_status"].astype(str).str.lower().str.startswith("not constructed"))
    imp = ras(bld) | ras(pave) | ras(park)
    perv = (ras(green) | ras(water) | ras(vac)) & ~imp
    counts = {**c1, **c2, **c3, **c4, **c5, **c6}
    rows = []
    for _, g in cat.groupby("wkb", sort=False):
        imp_r = g[g.LABEL.str.startswith("12SUI")]
        pv = g[~g.LABEL.str.startswith("12SUI")]
        base = pv.iloc[0] if len(pv) else g.iloc[0]
        rows.append({"label": base.LABEL, "out_node_id": int(base.OUT_NODEID), "out_node": str(base.OUT_NODE), "area_m2": float(base.geometry.area),
                     "tc_min": float(base.TC), "cn_perv": float(base.SCS_CN), "paired": len(g) == 2 and len(imp_r) == 1 and len(pv) == 1, "geometry": base.geometry})
    Pl = gpd.GeoDataFrame(rows, crs=cat.crs)
    pid = rasterize([(geom, i + 1) for i, geom in enumerate(Pl.geometry)], out_shape=(H, W), transform=tf, fill=0, dtype="int32")
    n = len(Pl) + 1
    tot = np.bincount(pid.ravel(), minlength=n).astype(float)
    n_perv = np.bincount(pid.ravel(), weights=perv.ravel(), minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        f = np.where(tot > 0, 1.0 - n_perv / tot, np.nan)[1:]
    Pl["f"] = np.round(f, 3)
    small = Pl.paired & (tot[1:] < 4)                      # 小于 4 格的地块栅格数不准：f 取全域成对地块面积加权
    if small.any():
        fw = float(np.average(Pl.f[Pl.paired & ~small], weights=Pl.area_m2[Pl.paired & ~small]))
        Pl.loc[small, "f"] = round(fw, 3)
    subs = []
    for r in Pl.itertuples():
        if r.paired:
            subs.append((r.label.replace("12SU", "12SUI", 1), r.out_node, r.area_m2 * r.f, cn_imp, r.tc_min, "不透水"))
            subs.append((r.label, r.out_node, r.area_m2 * (1 - r.f), r.cn_perv, r.tc_min, "透水"))
        else:
            subs.append((r.label, r.out_node, r.area_m2, r.cn_perv, r.tc_min, "不成对"))
    df = pd.DataFrame(subs, columns=["label", "node", "area_m2", "cn", "tc_min", "part"])
    pr = Pl[Pl.paired]
    st = {"n_client": len(cat), "n_plots": len(Pl), "n_paired": int(Pl.paired.sum()), "n_single": int((~Pl.paired).sum()),
          "area_client_ha": round(float(cat.A.sum()), 2), "area_ha": round(float(Pl.area_m2.sum() / 1e4), 2), "f_aw": round(float(np.average(pr.f, weights=pr.area_m2)), 3),
          "small_plots_fallback": int(small.sum()), "makani_counts": counts}
    log(f"  子汇水区：{len(cat):,} 面 → 去重 {len(Pl):,} 块（成对 {st['n_paired']:,}），f 面积加权 {st['f_aw']}，产流子区 {len(df):,} 个")
    return df, st


def rain_shape(R: Region, dur_h: float) -> tuple[np.ndarray, str]:
    """按历时取 Mussafah 的「10 年一遇含 CC」曲线的无量纲累计形状（data/common/rain_mussafah.json 的 scenario_pick）。"""
    pick = R.rain["scenario_pick"].get(f"{dur_h:g}h")
    if not pick:
        raise SystemExit(f"雨型里没有 {dur_h:g} h 的曲线")
    ks = [k for k in R.rain["series"] if k.endswith(pick)]
    if len(ks) != 1:
        raise SystemExit(f"雨型 {pick} 匹配到 {len(ks)} 条：{ks}")
    return np.array(R.rain["series"][ks[0]]["cum_mm"], float), ks[0]


def inflows(R: Region, subs, net, kinds, rain_mm: float, dur_h: float, tail_h: float, reg: dict, log=print):
    """复合 CN 产流 → 径流（单位线）挂到 AECOM 出口坐标最近的结构节点（检查井 / 雨水口）。"""
    import geopandas as gpd
    from scipy.spatial import cKDTree
    shape, shape_name = rain_shape(R, dur_h)
    subs = subs[subs.area_m2 > 0].copy()
    subs["tc_min"] = subs.tc_min.clip(lower=float(MR.get("runoff.min_tc_min", R.rules)))
    dt_h = float(MR.get("runoff.timestep_min", R.rules)) / 60.0
    sim_h = dur_h + tail_h
    t, P = RO.hyetograph(rain_mm, dur_h, dt_h, shape, 0.0, total_h=sim_h)
    H0 = RO.node_hydrographs(subs, t, P, dt_h, log=log)
    exc = np.zeros(len(t))
    for cn, g in subs.groupby("cn"):
        exc += RO.scs_cum_excess(P, float(cn)) * g.area_m2.sum()
    exc /= subs.area_m2.sum()
    tot = sum(float(q.sum() * dt_h * 3600) for q in H0.values())
    man = gpd.read_file(R.p("aecom_manholes.gpkg"))
    co = {str(l): (g.x, g.y) for l, g in zip(man["LABEL"], man.geometry)}
    keys = [k for k in net["J"] if kinds.get(k) in ("mh", "inlet")]
    tr = cKDTree(np.array([net["coords"][k] for k in keys]))
    H, dist = defaultdict(lambda: np.zeros(len(t))), []
    for n, q in H0.items():
        d, i = tr.query(co[str(n)])
        H[keys[i]] += q; dist.append(d)
    dist = np.array(dist)
    reg["runoff"] = {"storm": f"S{rain_mm:g}D{dur_h:g}", "rain_mm": rain_mm, "dur_h": dur_h, "shape": f"Mussafah_00.inp · {shape_name}", "sim_h": sim_h,
                     "subs_n": len(subs), "subs_ha": round(float(subs.area_m2.sum() / 1e4), 2), "excess_mm_aw": round(float(exc[-1]), 2), "runoff_m3": round(tot),
                     "outlets": len(H0), "to_nodes": len(H),
                     "remap_dist_m": {"median": round(float(np.median(dist)), 2), "p95": round(float(np.percentile(dist, 95)), 1), "max": round(float(dist.max()), 1),
                                      "n_gt_5m": int((dist > float(R.cfg.get("catchments", {}).get("far_outlet_m", 5))).sum())}}
    log(f"  入流：{len(H0):,} 个 AECOM 出口 → {len(H):,} 个结构节点，改挂距离中位 {np.median(dist):.2f} m、最大 {dist.max():.1f} m；净雨 {exc[-1]:.1f} mm，入流 {tot:,.0f} m³")
    return t, exc, dict(H), sim_h


# ------------------------------------------------------------------ 二维：下垫面、建筑挖洞、地形
def surface_raster(R: Region, D, out: Path, bld_n: float, reg: dict, res: float = 2.0, log=print):
    """Makani 下垫面 → 分类栅格（2 m）：建筑 2 > 路面 / 铺装 / 停车 1 > 水面 4 > 绿地 / 未建地块 3 > 其他硬地 0。"""
    import rasterio
    from rasterio.features import geometry_mask, rasterize
    from rasterio.transform import from_origin
    x0, y0, x1, y1 = np.array(D.bounds) + np.array([-30, -30, 30, 30])
    W, H = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    tf = from_origin(x0, y1, res, res)
    ras = lambda g: rasterize(((x, 1) for x in g), out_shape=(H, W), transform=tf, fill=0, dtype="uint8").astype(bool) if g else np.zeros((H, W), bool)  # noqa: E731
    bld, c1 = _geoms(R, BLD_TABLES); pave, c2 = _geoms(R, PAVE_TABLES); park, c3 = _geoms(R, PARK_TABLES)
    green, c4 = _geoms(R, GREEN_TABLES); water, c5 = _geoms(R, WATER_TABLES)
    vac, c6 = _geoms(R, ("udm_plot",), lambda g: g["construction_status"].astype(str).str.lower().str.startswith("not constructed"))
    c = np.zeros((H, W), np.uint8)
    c[ras(green) | ras(vac)] = 3; c[ras(water)] = 4; c[ras(pave) | ras(park)] = 1; c[ras(bld)] = 2
    with rasterio.open(R.dtm_file()) as ds:
        crs = ds.crs
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.tmp")
    with rasterio.open(tmp, "w", driver="GTiff", width=W, height=H, count=1, dtype="uint8", crs=crs, transform=tf, compress="deflate") as w:
        w.write(c, 1)
    os.replace(tmp, out)
    inside = ~geometry_mask([D], out_shape=(H, W), transform=tf)
    rough = MR.get("twod.surface_roughness", R.rules)
    n = {str(k): float(rough[v[1]]) for k, v in SURF_CLASSES.items()}
    n["2"] = float(bld_n)
    reg["surface"] = {"raster": out.name, "res_m": res, "makani_counts": {**c1, **c2, **c3, **c4, **c5, **c6},
                      "share_in_domain": {SURF_CLASSES[k][0]: round(float((c[inside] == k).mean()), 4) for k in SURF_CLASSES}, "n": {SURF_CLASSES[int(k)][0]: v for k, v in n.items()}}
    log(f"  下垫面（计算域内占比）：{reg['surface']['share_in_domain']}")
    return {"raster": f"$C2D/{out.name}", "n": n, "default_n": float(rough["default"]), "labels": {str(k): v[0] for k, v in SURF_CLASSES.items()}}


def building_holes(R: Region, D, reg: dict, log=print):
    """建筑挖洞（规则 twod.buildings）：Makani 建筑合并溶解 → 栅格化 → 闭运算并掉一格宽的缝 → 填天井 → 去对角相接 → 去掉离边界近的格 → 描边成阶梯多边形。"""
    from rasterio.features import rasterize, shapes
    from rasterio.transform import from_origin
    from scipy import ndimage as ndi
    from shapely.geometry import Polygon, shape
    from shapely.ops import unary_union
    cell = float(MR.get("twod.buildings.raster_cell_m", R.rules)); edge = float(MR.get("twod.buildings.edge_buffer_m", R.rules)); min_area = float(MR.get("twod.buildings.min_area_m2", R.rules))
    geoms, cnt = _geoms(R, BLD_TABLES)
    U = unary_union([g.intersection(D) for g in geoms if g.intersects(D)])
    parts = [p for p in getattr(U, "geoms", [U]) if p.geom_type == "Polygon" and not p.is_empty]
    x0, y0, x1, y1 = D.bounds
    Wc, Hc = int(np.ceil((x1 - x0) / cell)) + 2, int(np.ceil((y1 - y0) / cell)) + 2
    tf = from_origin(x0 - cell, y1 + cell, cell, cell)
    B = rasterize(((p, 1) for p in parts), out_shape=(Hc, Wc), transform=tf, fill=0, dtype="uint8").astype(bool)
    B = ndi.binary_closing(B, structure=np.ones((3, 3)))
    B = ndi.binary_fill_holes(B)
    for _ in range(20):
        a, b, c, d = B[:-1, :-1], B[:-1, 1:], B[1:, :-1], B[1:, 1:]
        pinch = (a & d & ~b & ~c) | (b & c & ~a & ~d)
        if not pinch.any():
            break
        r, k = np.nonzero(pinch); B[r, k + 1] = True
    B &= rasterize([(D.buffer(-edge), 1)], out_shape=(Hc, Wc), transform=tf, fill=0, dtype="uint8").astype(bool)
    lab, n = ndi.label(B)
    keep = np.flatnonzero(np.bincount(lab.ravel())[1:] * cell * cell >= min_area) + 1
    B = np.isin(lab, keep)
    P = [Polygon(shape(g).exterior).simplify(0.01) for g, v in shapes(B.astype("uint8"), mask=B, transform=tf, connectivity=4) if v == 1]
    P = [q for q in P if q.is_valid and q.area >= min_area]
    holes = [[[round(x, 2), round(y, 2)] for x, y in list(q.exterior.coords)[:-1]] for q in P]
    reg["building_holes"] = {"n_holes": len(holes), "hole_area_ha": round(sum(q.area for q in P) / 1e4, 2), "n_buildings_dissolved": len(parts),
                             "bld_area_ha": round(U.area / 1e4, 2), "method": "raster", "cell_m": cell, "edge_m": edge, "min_area_m2": min_area, "makani_counts": cnt}
    log(f"  建筑挖洞：Makani 建筑 {len(parts):,} 栋（{U.area / 1e4:.1f} ha）→ {len(holes)} 个洞、{reg['building_holes']['hole_area_ha']} ha")
    return {"holes": holes}


def write_dem(R: Region, D, out: Path):
    import rasterio
    from rasterio.windows import from_bounds
    X0, Y0, X1, Y1 = D.bounds
    m = float(MR.get("twod.dem_clip_margin_m", R.rules))
    with rasterio.open(R.dtm_file()) as ds:
        win = from_bounds(X0 - m, Y0 - m, X1 + m, Y1 + m, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win)
        prof = ds.profile.copy(); prof.update(width=z.shape[1], height=z.shape[0], transform=ds.window_transform(win), compress="deflate")
        tmp = out.with_name(f"{out.stem}.{os.getpid()}.tmp")
        with rasterio.open(tmp, "w", **prof) as w:
            w.write(z, 1)
        os.replace(tmp, out)        # 同一场景的几个态并行时共用这个文件：原子改名，内容相同
    return z.shape


def dem_edits(R: Region, reg: dict):
    p = R.p("dem_edits.json")
    eds = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    reg["dem_fills"] = [{"id": e["id"], "crest_m": e["crest_m"], "src": f"data/regions/{R.id}/dem_edits.json"} for e in eds]
    return eds


# ------------------------------------------------------------------ 一个场景的全部输入
def build_scenario(R: Region, tag: str, work: Path, threads: int, log=print, tail_h: float = 10.0, state: str = STATE, grid_m: float | None = None) -> dict:
    """tag = S<雨量>D<历时>，state = L1 / L2 / L2planned。返回 {name, state, inp, pilot, reg, sim_h, ...}（reg = 逐项输入登记）。
    grid_m = 二维网格名义精度（m），写进 reg["grid"]（页面标注用）。"""
    m = re.fullmatch(r"S(\d+)D(\d+)", tag)
    if not m:
        raise SystemExit(f"场景格式应为 S<雨量mm>D<历时h>：{tag}")
    rain_mm, dur_h = float(m.group(1)), float(m.group(2))
    name = f"{R.id}_{tag}"
    c2d = work / "c2d"; c2d.mkdir(parents=True, exist_ok=True)
    reg: dict = {"name": name, "state": state, "grid": {"res_m": grid_m, "label": f"{grid_m:g} m" if grid_m else None, "note": "二维非结构三角网的名义网格精度"}, "rules": f"{R.rules['meta']['series']} {R.rules['meta']['version']}"}
    log(f"[{tag}] 建模 {name}")
    D = domain(R, log)
    net, N, kinds, struct = network(R, D, reg, log, state)
    Nx, xt = exchange_nodes(R, net, N, kinds, D, reg)
    subs, sub_stats = composite_subs(R, log)
    reg["catchments"] = sub_stats
    t, exc, H, sim_h = inflows(R, subs, net, kinds, rain_mm, dur_h, tail_h, reg, log)
    inp = work / f"{name}_{state}.inp"
    write_inp(inp, f"{R.id} · {state} · {tag}（tools/region_model.py）", net, H, t, sim_h, R.rules, threads, log)
    txt = inp.read_text(encoding="utf-8")
    frac = float(MR.get("init_state.pipe_fill_fraction_of_area", R.rules))
    txt, reg["init_fill"] = init_fill(txt, frac)
    inp.write_text(txt, encoding="utf-8")
    reg["routing"] = {k: str(MR.get(f"swmm.options.{k}", R.rules)) for k in ("ROUTING_STEP", "VARIABLE_STEP", "MINIMUM_STEP")}
    log(f"  初始水量：{reg['init_fill']}")
    shape = write_dem(R, D, c2d / f"dem_{name}.tif")
    # 挖洞要相对「简化后的计算域」（pilot 里的 domain_polygon，ANUGA 用它当外边界）来做：洞不能伸到外边界外面
    from shapely.geometry import Polygon
    poly = [[round(x, 1), round(y, 1)] for x, y in D.simplify(float(MR.get("twod.domain_simplify_m", R.rules))).exterior.coords[:-1]]
    holes = building_holes(R, Polygon(poly), reg, log)
    bld_n = float(MR.get("twod.surface_roughness.building", R.rules)) if holes["holes"] else float(MR.get("twod.surface_roughness.building_without_holes", R.rules))
    sc = surface_raster(R, D, c2d / f"surf_{name}.tif", bld_n, reg, log=log)
    il = R.rules["exchange"]["inlet"]
    inlet = {"P": round(il["grate_weir_sides"] * il["grate_size_m"], 3), "A": il["grate_open_area_m2"], "clog": il["clog_factor"]}
    reg["inlet"] = inlet
    eds = dem_edits(R, reg)
    pm = {"name": name, "state": state, "box": list(D.bounds), "domain_polygon": poly, "R_served_m": 1e9,   # 全域都有子汇水区：二维上不直接下雨
          "nodes": {k: [round(v[0], 2), round(v[1], 2)] for k, v in Nx.items()}, "ponds": [], "rain_t_h": [round(float(x), 4) for x in t],
          "rain_excess_mm": [round(float(x), 4) for x in exc], "note": f"{R.id} {state} {tag}（tools/region_model.py，规则 {reg['rules']}）",
          "xchg_type": xt, "inlet": inlet, "surface_classes": sc, "dem_edits": eds, "building_holes": holes,
          "ground_basis": MR.get("exchange.ground_basis", R.rules), "node_min_depth": node_min_depth(net)}
    pilot = c2d / f"pilot_{name}_{state}.json"
    pilot.write_text(json.dumps(pm, ensure_ascii=False), encoding="utf-8")
    reg.update(inp=str(inp), sim_h=sim_h, domain_km2=round(D.area / 1e6, 3), n_junctions=len(net["J"]), n_conduits=len(net["CO"]), n_outfalls=len(net["OF"]),
               n_pumps=len(net["PU"]), n_storage=len(net["ST"]), ground_basis=pm["ground_basis"], infil_mmh=None, dem_shape=list(shape))
    rd = work / "run" / R.id; rd.mkdir(parents=True, exist_ok=True)
    (rd / f"inputs_{tag}_{state}.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log(f"  → pilot {pilot.name}、dem、surf、inputs_{tag}_{state}.json")
    return {"name": name, "state": state, "inp": inp, "pilot": pilot, "reg": reg, "sim_h": sim_h, "rain_mm": rain_mm, "dur_h": dur_h, "domain": D}
