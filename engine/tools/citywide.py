# -*- coding: utf-8 -*-
"""全市 L1 / L2 缺口分析。

------------------------------------------------------------------ 为什么不照搬三个区的做法
三个汇水区是跑完整 SWMM 得到的。全市 26 万根管跑不动（RB3 一个区 5,287 个节点
一次就要十几分钟），而且**36 个汇水区没有客户的产流参数**。所以全市这一版是
**筛查式**，不是水力模型，每一步的口径都写在下面，别当成同一个东西用。

------------------------------------------------------------------ 空间单元：不用客户那 39 个多边形
客户 `catchment.catchment` 只有 39 个、431 km²，**只盖住 47.4% 的管线**，
而且 19/23 座塘所在的 Madinat Al Riyad **覆盖率是 0%**——拿它做缺口分析，
L2 会恒等于 0，结论必然是假的。

改从管网自己划：244 个出水口的 `PointCode` 能对上管线端点码，
从出水口沿管网**反向广搜**（不顺流向走——管内底有噪声，贪心走法只到 36.4%），
把每个节点归到它能到达的出水口。覆盖 74.2% 的节点；到不了任何出水口的
6.7 万个节点单列为「无登记出口」，那是台账的真实缺口，不是算漏了。

------------------------------------------------------------------ 服务面积：自适应半径，验过
最近节点法：离管网节点 ≤ R 的地面算这条网服务。R 不拍脑袋——
    R = 1.2 × 节点平均间距 = 1.2 × sqrt(服务面积 / 节点数)，迭代到收敛
k=1.2 是拿两个已知区标的：Mussafah 算 150 ha（客户图层 148.8，+1.1%），
Shaliela 算 1,502 ha（1,425.7，+5.3%）。k=1.0 会差 −14%/−23%，k=1.3 差 +8%/+18%。

------------------------------------------------------------------ 产流：客户自己的参数
SCS 曲线法，**CN = 82** —— 这是客户自己用的值（Mussafah 全区 82，
Shaliela 面积加权 82.5）。61 mm 下 S = 55.76 mm、Ia = 11.15 mm，
产流深 **23.53 mm**，与历时无关（SCS 产流量只取决于总雨深）。

------------------------------------------------------------------ 过流能力：给带，不给点估
试过三种预测量，三个标定点都撑不住，如实说：
    出口曼宁满管能力   Q_cap/预测 = 1.59 / 0.33 / 1.00（差 4.8 倍）
    单位面积能力       0.0249 / 0.0034 m³/s/ha（差 7 倍）
    沿程最卡断面       预测/反解 = 0.07 / 0.14 / 0.01（太悲观，水早从地面走了）
所以**不硬凑一个全市过流能力模型**。改用三个真区实测的「排走比例」作经验带：

    历时      Mussafah   Shaliela      RB3      → 带
     1 h        50.7%       0.0%     14.4%      0 – 51%
     2 h        58.2%       0.0%     21.2%      0 – 58%
    24 h        88.5%      47.6%     61.8%     48 – 89%

Shaliela 短历时是 0 不是算错——它的管网在 61 mm/1–2 h 下确实几乎什么都排不走
（SWMM 溢出量 ≈ 入流量）。

------------------------------------------------------------------ 一个必须说清的口径
SWMM 的「Total Flood Volume」表头写明是「**所有溢出节点的水，不论是否滞留**」。
客户这三个模型都是 `ALLOW_PONDING YES`、每井蓄水面积 100 m²，所以
`Flooding Loss = 0`——水会回落再溢出，跨节点求和会重复计
（Shaliela 1 h 溢出 401,979 m³ > 入流 359,286 m³，多 12%）。
本模块一律叫它「**溢出井盖的累计水量**」，不叫「路面积水量」。

用法：
    python tools/citywide.py                    # 全部出水口汇水域
    python tools/citywide.py --top 25           # 只列最大的 25 个
    python tools/citywide.py --cell 20          # 栅格粗一点，快
产物：out/citywide/gap.json、gap.csv、catchments.geojson
源库与 GDB 都只读。
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from collections import deque

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

GDB = "/vsizip/D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb.zip"
OUT = ROOT / "out" / "citywide"

CN = 82.0              # 客户自己的曲线数
K_RADIUS = 1.2         # 服务半径 = K × 节点平均间距（拿两个已知区标的）
CELL_M = 10.0          # 服务面积栅格
MAX_CELLS = 40_000_000  # 单个汇水域的栅格上限，超了自动放粗
DATUM = 100.0

# 三个真区实测的排走比例（1 − 溢出量/入流量），用作全市经验带
CONVEY = {1: (0.000, 0.507), 2: (0.000, 0.582), 24: (0.476, 0.885)}
ANCHOR = {   # 标定锚点：入流量、各历时溢出量，供页面与自检对照
    "Mussafah": {"inflow": 35003, 1: 17261, 2: 14643, 24: 4020},
    "Shaliela": {"inflow": 359283, 1: 401979, 2: 364829, 24: 188354},
    "RB3": {"inflow": 789896, 1: 675912, 2: 622640, 24: 301381},
}

CODE = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


def scs_excess_mm(P_mm: float, cn: float = CN) -> float:
    """SCS 产流深。只取决于总雨深与 CN，与历时无关。"""
    S = 25400.0 / cn - 254.0
    if P_mm <= 0.2 * S:
        return 0.0
    return (P_mm - 0.2 * S) ** 2 / (P_mm + 0.8 * S)


# ------------------------------------------------------------------ 管网
def load_network(log=print):
    """管线 + 出水口 → 节点坐标、邻接、每个节点归属的出水口。"""
    import pyogrio

    df = pyogrio.read_dataframe(GDB, layer="PIPELINE", read_geometry=False,
                                columns=["StartCode", "EndCode", "PIPE_DIAMETER",
                                         "LineLength", "INVERT_LEVEL_UPSTREAM",
                                         "INVERT_LEVEL_DOWNSTREAM", "StartElev", "EndElev"])
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    idx: dict[str, int] = {}

    def gid(k: str) -> int:
        v = idx.get(k)
        if v is None:
            v = len(idx); idx[k] = v
        return v

    a = np.fromiter((gid(k) for k in sc), dtype=np.int64, count=len(sc))
    b = np.fromiter((gid(k) for k in ec), dtype=np.int64, count=len(ec))
    N = len(idx)

    # 节点坐标直接从端点码解（实测与 StartX/Y 中位差 0.80 m，95% 位 1.20 m）
    xy = np.full((N, 2), np.nan)
    for k, i in idx.items():
        m = CODE.match(k)
        if m:
            xy[i, 0] = float(m.group(1)); xy[i, 1] = float(m.group(2))
    ok_xy = np.isfinite(xy[:, 0])
    log(f"  节点 {N:,}　坐标解得出的 {int(ok_xy.sum()):,}（{ok_xy.mean() * 100:.1f}%）")

    # CSR 邻接
    deg = np.bincount(np.concatenate([a, b]), minlength=N)
    off = np.zeros(N + 1, dtype=np.int64); np.cumsum(deg, out=off[1:])
    adj = np.empty(off[-1], dtype=np.int64); cur = off[:-1].copy()
    for u, v in zip(a, b):
        adj[cur[u]] = v; cur[u] += 1
        adj[cur[v]] = u; cur[v] += 1

    of = pyogrio.read_dataframe(GDB, layer="OUTFALL", read_geometry=False,
                                columns=["UNITID", "PointCode", "INVERT_LEVEL"])
    seeds, names = [], []
    for k, u in zip(of.PointCode.astype("string").fillna(""),
                    of.UNITID.astype("string").fillna("")):
        if k in idx:
            seeds.append(idx[k]); names.append(u)
    # 从出水口反向广搜。**不顺流向走**：管内底有噪声，贪心只到 36.4%
    owner = np.full(N, -1, dtype=np.int64)
    q: deque[int] = deque()
    for i, nd in enumerate(seeds):
        if owner[nd] == -1:
            owner[nd] = i; q.append(nd)
    while q:
        x = q.popleft()
        for k in range(off[x], off[x + 1]):
            y = adj[k]
            if owner[y] == -1:
                owner[y] = owner[x]; q.append(y)
    log(f"  出水口 {len(seeds)} 个　能到达出水口的节点 "
        f"{int((owner >= 0).sum()):,}（{(owner >= 0).mean() * 100:.1f}%）")

    # 管的属性，按上游节点归到汇水域（管容与出口能力要用）
    dia = df.PIPE_DIAMETER.astype("float64").values / 1000.0     # mm → m
    ln = df.LineLength.astype("float64").values
    iu = df.INVERT_LEVEL_UPSTREAM.astype("float64").values
    idn = df.INVERT_LEVEL_DOWNSTREAM.astype("float64").values
    vu = np.where((iu > 50) & (iu < 500), iu - DATUM, np.nan)
    vd = np.where((idn > 50) & (idn < 500), idn - DATUM, np.nan)
    return dict(idx=idx, N=N, a=a, b=b, xy=xy, owner=owner, seeds=seeds,
                names=names, dia=dia, len=ln, inv_up=vu, inv_dn=vd)


R_MIN, R_MAX = 20.0, 120.0   # 自适应半径的夹逼：节点少时会发散（2 个节点能解出 297 m）


def mask_to_polygon(sel, gx, gy, cell: float, simplify: float | None = None):
    """把栅格掩膜转成 4326 的多边形。返回 GeoJSON geometry，空则 None。

    meshgrid 的行是按 y **递增**排的，而栅格习惯是北在上，所以要翻一次行；
    不翻的话多边形会上下颠倒，落到几百公里外。
    """
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union, transform as shp_transform
    import pyproj

    ny, nx = len(gy), len(gx)
    m = sel.reshape(ny, nx)[::-1].astype("uint8")        # 翻成北在上
    tr = from_origin(gx[0] - cell / 2, gy[-1] + cell / 2, cell, cell)
    polys = [shape(g) for g, v in shapes(m, mask=m.astype(bool), transform=tr) if v]
    if not polys:
        return None
    u = unary_union(polys)
    if simplify:
        u = u.simplify(simplify, preserve_topology=True)
    if u.is_empty:
        return None
    to4326 = pyproj.Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True).transform
    gj = mapping(shp_transform(to4326, u))

    # 坐标位数砍到 5 位（约 1 m）。不砍的话 397 个汇水域就有 7.4 MB，
    # 网页懒加载要等很久，而 1 m 精度对规划级图层绰绰有余。
    def rnd(c):
        if isinstance(c[0], (int, float)):
            return (round(c[0], 5), round(c[1], 5))
        return [rnd(x) for x in c]

    gj["coordinates"] = rnd(gj["coordinates"])
    return gj


def served_area(tree, owner_keep, gi: int, pts, cell: float, want_geom: bool = False):
    """这一组节点的服务面积，半径自适应。返回 (面积 m², 收敛的 R)。

    R = K × 节点平均间距，而间距又要用面积算，所以迭代到收敛。
    最近邻查的是**全网**点集，所以相邻汇水域不会把同一块地都算上。
    R 夹在 [R_MIN, R_MAX]：节点只有两三个时，迭代会把半径推到几百米，
    凭空造出十几公顷的服务面积。
    """
    if len(pts) < 2:
        return (0.0, 0.0, None) if want_geom else (0.0, 0.0)
    R, A, c = 50.0, 0.0, cell
    sel = gx = gy = None
    for _ in range(8):
        pad = R + 3 * c
        w = pts[:, 0].max() - pts[:, 0].min() + 2 * pad
        h = pts[:, 1].max() - pts[:, 1].min() + 2 * pad
        nx, ny = int(w / c) + 1, int(h / c) + 1
        while nx * ny > MAX_CELLS:
            c *= 2; nx, ny = int(w / c) + 1, int(h / c) + 1
        gx = pts[:, 0].min() - pad + np.arange(nx) * c
        gy = pts[:, 1].min() - pad + np.arange(ny) * c
        GX, GY = np.meshgrid(gx, gy)
        d, j = tree.query(np.c_[GX.ravel(), GY.ravel()], k=1, workers=-1)
        sel = (d <= R) & (owner_keep[j] == gi)
        A = float(sel.sum()) * c * c
        if A <= 0:
            return (0.0, R, None) if want_geom else (0.0, R)
        Rn = float(np.clip(K_RADIUS * math.sqrt(A / len(pts)), R_MIN, R_MAX))
        if abs(Rn - R) < 0.5:
            R = Rn; break
        R = 0.5 * (R + Rn)
    if not want_geom:
        return A, R
    return A, R, mask_to_polygon(sel, gx, gy, c, simplify=c / 2)


def split_orphans(net, log=print) -> None:
    """到不了任何出水口的节点按**连通块**拆开，各自成一个汇水域。

    不拆的话，6.7 万个互不相连的节点会被当成一个 114 km² 的「汇水域」，
    17 座塘全混在里面，算出来的缺口没有物理含义。
    拆完给每块一个 −(序号+2) 的负编号，名字标成「无登记出口 #k」。
    """
    owner, N = net["owner"], net["N"]
    a, b = net["a"], net["b"]
    free = owner < 0
    if not free.any():
        return
    par = np.arange(N)

    def find(x: int) -> int:
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x

    for u, v in zip(a, b):
        if free[u] and free[v]:
            ru, rv = find(int(u)), find(int(v))
            if ru != rv:
                par[ru] = rv
    comp: dict[int, list[int]] = {}
    for i in np.nonzero(free)[0]:
        comp.setdefault(find(int(i)), []).append(int(i))
    big = sorted(comp.values(), key=len, reverse=True)
    for k, mem in enumerate(big):
        owner[mem] = -(k + 2)
    net["n_orphan"] = len(big)
    log(f"  无登记出口的 {int(free.sum()):,} 个节点拆成 {len(big):,} 个独立子网"
        f"（最大一块 {len(big[0]):,} 个节点，≥100 节点的 {sum(1 for m in big if len(m) >= 100):,} 块）")


# ------------------------------------------------------------------ 主流程
def run(P_mm: float, top: int | None, cell: float, geom_min_ha: float | None = None,
        log=print) -> list[dict]:
    net = load_network(log)
    split_orphans(net, log)
    owner, N = net["owner"], net["N"]
    q_mm = scs_excess_mm(P_mm)
    log(f"  产流深 {q_mm:.2f} mm（CN={CN:.0f}，P={P_mm:g} mm，与历时无关）")

    from scipy.spatial import cKDTree
    keep = np.isfinite(net["xy"][:, 0])
    owner_keep = owner[keep]
    tree = cKDTree(net["xy"][keep])          # 只建一次：244 个汇水域各建一遍太慢

    groups: dict[int, list[int]] = {}
    for i in range(N):
        groups.setdefault(int(owner[i]), []).append(i)
    order = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    if top:
        order = order[:top]

    ponds = load_ponds(net, log)
    recs = []
    for gi, members in order:
        pts = net["xy"][np.array(members)]
        pts = pts[np.isfinite(pts[:, 0])]
        # 几何只给够大的那些：面积小于阈值的画出来也是一堆看不清的碎块，
        # 而 403 个 ≥5 ha 的就覆盖了 93.9% 的面积和产流
        want = geom_min_ha is not None
        res = served_area(tree, owner_keep, gi, pts, cell, want_geom=want)
        A, R = res[0], res[1]
        gj = res[2] if want else None
        if A <= 0:
            continue
        if want and A / 1e4 < geom_min_ha:
            gj = None
        name = net["names"][gi] if gi >= 0 else f"（无登记出口 #{-gi - 1}）"
        v_in = A * q_mm / 1000.0
        pl = ponds.get(gi, [])
        v_l2_now = sum(p["vol_dtm_m3"] for p in pl)
        v_l2_des = sum(p["vol_design_m3"] for p in pl)
        r = {"outfall": name, "nodes": len(members), "area_ha": round(A / 1e4, 1),
             "radius_m": round(R, 1), "runoff_m3": round(v_in),
             "n_ponds": len(pl), "l2_now_m3": round(v_l2_now),
             "l2_design_m3": round(v_l2_des)}
        if gj is not None:
            r["_geometry"] = gj
        for dur, (lo, hi) in CONVEY.items():
            g1_hi = v_in * (1 - lo)          # 排走得少 → 缺口大
            g1_lo = v_in * (1 - hi)
            r[f"gap1_{dur}h_lo_m3"] = round(g1_lo)
            r[f"gap1_{dur}h_hi_m3"] = round(g1_hi)
            r[f"gap2_{dur}h_lo_m3"] = round(max(g1_lo - v_l2_des, 0.0))
            r[f"gap2_{dur}h_hi_m3"] = round(max(g1_hi - v_l2_des, 0.0))
        recs.append(r)
        log(f"  {name[:26]:<28}{len(members):>7,} 节点  R={R:>5.1f}m "
            f"{A / 1e4:>9,.0f} ha  产流 {v_in:>11,.0f} m³  塘 {len(pl)}")
    return recs


def load_ponds(net, log=print) -> dict[int, list[dict]]:
    """把 tools/pond_link.py 的结果按最近节点归到汇水域。"""
    from scipy.spatial import cKDTree

    f = ROOT / "out" / "pondlink" / "ponds_linked.json"
    if not f.exists():
        log("  没有 out/pondlink/ponds_linked.json，先跑 tools/pond_link.py"); return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    keep = np.isfinite(net["xy"][:, 0])
    gidx = np.nonzero(keep)[0]
    tree = cKDTree(net["xy"][keep])
    import pyproj
    tr = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    out: dict[int, list[dict]] = {}
    unassigned = 0
    for d in data:
        g = d["geometry"]
        cs = g["coordinates"]
        while isinstance(cs[0], (list, tuple)):
            cs = cs[0]
        x, y = tr.transform(float(cs[0]), float(cs[1]))
        dist, j = tree.query([x, y], k=1)
        gi = int(net["owner"][gidx[j]])
        if dist > 500:
            unassigned += 1
            continue
        out.setdefault(gi, []).append(
            {"unitid": d["unitid"], "dist_to_net_m": round(float(dist), 1),
             "vol_dtm_m3": float(d["dtm"]["vol_dtm_m3"]),
             "vol_design_m3": float(d["design"]["volume_design_m3"]),
             "need_dig": bool(d["design"]["need_dig"])})
    log(f"  塘 {len(data)} 座归到汇水域，{unassigned} 座离管网 >500 m 未归")
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="全市 L1/L2 缺口筛查")
    ap.add_argument("--P", type=float, default=61.0, help="累计雨深 mm")
    ap.add_argument("--top", type=int, default=None, help="只算最大的 N 个汇水域")
    ap.add_argument("--cell", type=float, default=CELL_M, help="服务面积栅格 m")
    ap.add_argument("--geojson", type=float, default=None, metavar="MIN_HA",
                    help="同时输出汇水域图层，只画面积 ≥ MIN_HA 的（建议 5）")
    a = ap.parse_args(argv)

    print(f"全市缺口筛查（P={a.P:g} mm，CN={CN:.0f}，栅格 {a.cell:g} m）：")
    recs = run(a.P, a.top, a.cell, a.geojson)
    if not recs:
        print("没有结果"); return 1

    OUT.mkdir(parents=True, exist_ok=True)
    geoms = {r["outfall"]: r.pop("_geometry") for r in recs if "_geometry" in r}
    (OUT / "gap.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    if geoms:
        fc = {"type": "FeatureCollection", "layer": "citywide_gap",
              "source": f"管网汇水域（244 个出水口反向广搜 + 最近节点服务面积），P={a.P:g} mm，CN={CN:.0f}",
              "features": [{"type": "Feature", "geometry": geoms[r["outfall"]],
                            "properties": {k: v for k, v in r.items()}}
                           for r in recs if r["outfall"] in geoms]}
        txt = json.dumps(fc, ensure_ascii=False)
        (OUT / "citywide_gap.geojson").write_text(txt, encoding="utf-8")
        cd = ROOT / "console" / "data"
        if cd.exists():
            (cd / "citywide_gap.geojson").write_text(txt, encoding="utf-8")
        print(f"  图层：{len(geoms):,} 个汇水域，{len(txt)/1024/1024:.1f} MB")
    import csv
    with (OUT / "gap.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(recs[0]))
        w.writeheader(); w.writerows(recs)

    A = sum(r["area_ha"] for r in recs)
    V = sum(r["runoff_m3"] for r in recs)
    L2n = sum(r["l2_now_m3"] for r in recs)
    L2d = sum(r["l2_design_m3"] for r in recs)
    npond = sum(r["n_ponds"] for r in recs)
    noout = [r for r in recs if r["outfall"].startswith("（无")]
    np_no = sum(r["n_ponds"] for r in noout)
    print(f"\n{len(recs)} 个汇水域　服务面积 {A:,.0f} ha（{A / 100:,.0f} km²）")
    print(f"  其中无登记出口的子网 {len(noout):,} 个　{sum(r['area_ha'] for r in noout):,.0f} ha"
          f"　{np_no}/{npond} 座塘在里面")
    print(f"产流总量（P={a.P:g} mm）　{V:,.0f} m³")
    print(f"L2 可用蓄容　现状地形 {L2n:,.0f} m³（{L2n / V * 100:.2f}%）"
          f"　挖到台账体积 {L2d:,.0f} m³（{L2d / V * 100:.1f}%）　{npond} 座塘")
    print(f"\n缺口（Gap1 = L1 排不走的；Gap2 = 再扣掉 L2 之后的）：")
    print(f"{'历时':<6}{'排走比例':>12}{'Gap1 低':>14}{'Gap1 高':>14}{'Gap2 低':>14}{'Gap2 高':>14}")
    for dur, (lo, hi) in CONVEY.items():
        g1l = sum(r[f"gap1_{dur}h_lo_m3"] for r in recs)
        g1h = sum(r[f"gap1_{dur}h_hi_m3"] for r in recs)
        g2l = sum(r[f"gap2_{dur}h_lo_m3"] for r in recs)
        g2h = sum(r[f"gap2_{dur}h_hi_m3"] for r in recs)
        print(f"{dur:>3} h{f'{lo * 100:.0f}–{hi * 100:.0f}%':>12}"
              f"{g1l:>14,.0f}{g1h:>14,.0f}{g2l:>14,.0f}{g2h:>14,.0f}")

    # L2 的收益有多少被「塘在错的地方」吃掉了
    g1_1h = sum(r["gap1_1h_lo_m3"] for r in recs)
    g2_1h = sum(r["gap2_1h_lo_m3"] for r in recs)
    used = g1_1h - g2_1h
    print(f"\nL2 用得上多少（按 1 h、Gap1 低值算）：")
    print(f"  挖到台账体积的总蓄容      {L2d:>12,.0f} m³")
    print(f"  实际减掉的缺口            {used:>12,.0f} m³　"
          f"（只用上 {used / L2d * 100 if L2d else 0:.0f}%）")
    print(f"  若塘都在有水的地方（上界） {min(L2d, g1_1h):>12,.0f} m³")
    print(f"  差额 {L2d - used:,.0f} m³ 是**塘所在的汇水域本身没那么多水**——")
    print(f"  {np_no}/{npond} 座塘落在无登记出口的小子网里，各自汇水域的产流量装不满它。")
    print("  注意：这些塘是**地表汇流**进水（见 tools/pond_link.py），真实汇流面积")
    print("  比管网服务面积大，所以上面的「用上 %.0f%%」是下界，不是定论。"
          % (used / L2d * 100 if L2d else 0))

    print("\n口径提醒：这是筛查不是水力模型。产流侧已验（Mussafah 35,003 m³ / 148.8 ha")
    print("= 23.5 mm，与 SCS CN=82 算出的 23.53 mm 一致）；服务面积已验（±6%）；")
    print("排走比例是三个真区的实测值外推，只有那三个区是真跑出来的。")
    print(f"写出 {OUT / 'gap.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
