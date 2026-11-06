# -*- coding: utf-8 -*-
"""把 couple2way_v2 存的二维水深帧（npz）写成 ANUGA 标准 .sww（容器内跑）。

为什么（2026-09-28）：couple2way_v2 为省盘和时间关了 ANUGA 自己的 .sww 输出（set_store(False)），只存 npz；
客户要 .sww。这里按同样的参数（pilot_<名>_<态>.json 的计算域多边形 / 矩形框 + 网格尺寸）重建**同一张三角网**，
逐帧把 stage = 地面高程 + 形心水深 写进 .sww。

限制（写进 .sww 的说明）：
  · 全市 / 29.RB2 / Madinat：只有 stage 与 elevation；**没有流速 / 动量**（当时没存，补不回来），xmomentum / ymomentum 全为 0
  · C1 起（C2D_SAVE_UV=1）：npz 里有每帧的 uh / vh，写进 xmomentum / ymomentum，.sww 带流速
  · 帧间隔 5 分钟（npz 就是这个间隔），形心水深是 float16（精度约 1 mm 量级）
  · 顶点值由 ANUGA 从形心外推（与模拟时的 sww 一致的做法）

输出网格可单独设（2026-10）：--out-res 给 .sww 另建一张该尺寸的三角网（同一计算域），形心值从计算网的形心
**线性插值**过去（凸包外取最近）；地面高程也是插值的计算网高程（含按态改的地形），stage = 高程 + 水深，两者自洽。
不给 --out-res = 与计算网同一张网，原样写出（不插值）。比计算网细不会多出信息，只是更平滑。

用法（容器内）：python /work/c2d/npz_to_sww.py <名> <态> [计算网格m] [--out-res 输出网格m]
产物：/work/c2d/anuga_out/<名>_<态>.sww
"""
import argparse
import json
import time

import numpy as np

W = "/work"; C2D = f"{W}/c2d"
ap = argparse.ArgumentParser()
ap.add_argument("name"); ap.add_argument("state")
ap.add_argument("res", nargs="?", type=float, default=None, help="计算网格尺寸 m（缺省读 twoway_<名>_<态>.json 的 res_m，没有则 30）")
ap.add_argument("--out-res", type=float, default=None, help=".sww 的输出网格尺寸 m（缺省 = 计算网格）")
a = ap.parse_args()
name, state = a.name, a.state
TAG = f"{name}_{state}"
if a.res is None:
    try:
        a.res = float(json.load(open(f"{C2D}/twoway_{TAG}.json", encoding="utf-8"))["res_m"])
    except (OSError, KeyError, ValueError):
        a.res = 30.0
RES = a.res
RES_OUT = a.out_res if a.out_res else RES

import anuga  # noqa: E402
import rasterio  # noqa: E402
from anuga.file.sww import SWW_file  # noqa: E402

t0 = time.time()
meta = json.load(open(f"{C2D}/pilot_{TAG}.json", encoding="utf-8"))
z = np.load(f"{C2D}/{TAG}_2d.npz")
poly = meta.get("domain_polygon")


def make_domain(res):
    if poly:
        BH = (meta.get("building_holes") or {}).get("holes")          # 建筑挖洞：和耦合计算同一张网（couple2way_v2 同样的 interior_holes）
        kw = {"interior_holes": BH, "hole_tags": [{"wall": list(range(len(h)))} for h in BH]} if BH else {}
        return anuga.create_domain_from_regions([tuple(p) for p in poly], {"exterior": list(range(len(poly)))},
                                                maximum_triangle_area=res * res / 3.0, **kw)
    with rasterio.open(f"{C2D}/dem_{name}.tif") as ds:
        b = ds.bounds
    Lx, Ly = b.right - b.left, b.top - b.bottom
    return anuga.rectangular_cross_domain(max(int(Lx / res), 4), max(int(Ly / res), 4), len1=Lx, len2=Ly, origin=(b.left, b.bottom))


def centroids(d):
    return np.asarray(d.geo_reference.get_absolute(d.centroid_coordinates), float)


zxy = z["xy"].astype(float)
SAME = abs(RES_OUT - RES) < 1e-9
if SAME:
    # 与计算网同一张网：先核对重建的网确实是那张网
    domain = make_domain(RES)
    cxy = centroids(domain)
    n = len(domain)
    assert n == len(zxy), f"三角单元数对不上：重建 {n:,}，npz {len(zxy):,}（计算网格尺寸给错了？）"
    err = float(np.abs(cxy - zxy).max())
    assert err < 1.0, f"形心坐标对不上（最大差 {err:.2f} m）：不是同一张网"
    print(f"{TAG}: 重建 {n:,} 个三角单元 @ {RES:g} m，形心与 npz 最大差 {err:.3f} m（{time.time() - t0:.0f} s）", flush=True)

    def remap(v):
        return v
else:
    # 另建输出网，计算网形心 → 输出网形心：线性插值，凸包外最近邻（权重算一次，逐帧复用）
    from scipy.spatial import Delaunay, cKDTree
    domain = make_domain(RES_OUT)
    cxy = centroids(domain)
    n = len(domain)
    tri = Delaunay(zxy)
    s = tri.find_simplex(cxy)
    inside = s >= 0
    T = tri.transform[s[inside]]
    bary = np.einsum("ijk,ik->ij", T[:, :2], cxy[inside] - T[:, 2])
    W3 = np.c_[bary, 1.0 - bary.sum(1)]
    V3 = tri.simplices[s[inside]]
    near = cKDTree(zxy).query(cxy[~inside])[1]
    print(f"{TAG}: 计算网 {len(zxy):,} 单元 @ {RES:g} m → 输出网 {n:,} 单元 @ {RES_OUT:g} m"
          f"（凸包外最近邻 {int((~inside).sum()):,} 个；{time.time() - t0:.0f} s）", flush=True)

    def remap(v):
        o = np.empty(n, float)
        o[inside] = (v[V3] * W3).sum(1)
        o[~inside] = v[near]
        return o

el = remap(z["elev"].astype(float))
domain.set_quantity("elevation", el, location="centroids")
domain.set_quantity("stage", el, location="centroids")
domain.geo_reference.zone = 40          # UTM 40N（EPSG:32640）；不写的话 sww 里是 -1
try:
    domain.geo_reference.hemisphere = "northern"
except Exception:
    pass
domain.set_name(TAG); domain.set_datadir(f"{C2D}/anuga_out")
domain.set_quantities_to_be_stored({"elevation": 1, "stage": 2, "xmomentum": 2, "ymomentum": 2})
domain.set_minimum_storable_height(0.0)
wr = SWW_file(domain)
wr.store_connectivity()
st = domain.quantities["stage"]
T = z["t_h"].astype(float) * 3600.0
F = z["frames"]
UV = "uh" in z.files          # couple2way_v2 开 C2D_SAVE_UV=1 时每帧另存了单宽流量（C1 起）
if UV:
    UH, VH = z["uh"], z["vh"]
    xm, ym = domain.quantities["xmomentum"], domain.quantities["ymomentum"]
for i in range(len(T)):
    domain.set_time(T[i])
    st.set_values(el + np.maximum(remap(F[i].astype(float)), 0.0), location="centroids")
    if UV:
        xm.set_values(remap(UH[i].astype(float)), location="centroids"); ym.set_values(remap(VH[i].astype(float)), location="centroids")
    domain.distribute_to_vertices_and_edges()
    wr.store_timestep()
    if i % 12 == 11:
        print(f"  {i + 1}/{len(T)} 帧（{time.time() - t0:.0f} s）", flush=True)
print(f"→ {C2D}/anuga_out/{TAG}.sww（{len(T)} 帧，输出网格 {RES_OUT:g} m{'' if SAME else f'（由 {RES:g} m 计算网插值）'}，{'水位 + 动量（有流速）' if UV else '只有水位与地面高程，无流速'}；{time.time() - t0:.0f} s）", flush=True)
