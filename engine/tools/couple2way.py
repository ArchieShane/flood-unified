# -*- coding: utf-8 -*-
"""1D/2D **双向**耦合：SWMM 与 ANUGA 同进程按步交换。在 anuga-swmm:1.0 镜像里跑。

为什么必须双向：单向耦合里水从井盖冒出去就再也回不来，地表水只涨不退，
淹没范围被系统性高估；更要命的是，塘一旦挖进二维地形，它收到的地表水不回到一维，
同一份库容就会被算两次（见 tools/couple_2d.py 开头那段）。

交换规则（两个方向共用同一套水力关系，这是「经得起查」的前提）：

  1D→2D  耦合运行时把 ALLOW_PONDING 关掉。SWMM 的 FLOOD 就是干净地离开一维的那部分水。
         **按路由步长累积体积**再交给二维，不采样瞬时流率——采样会系统性丢量。

  2D→1D  井盖处的孔口/堰流。设井盖高程 z_r、一维水头 h1、二维水面 h2：
           h2 > z_r 且 h1 < z_r   自由孔口入流  Q = −Cd·A·√(2g(h2−z_r))
           h1 > z_r 且 h2 > z_r   淹没孔口，按水头差双向  Q = Cd·A·sign(Δ)·√(2g|Δ|)
           其余                    不交换
         Cd = 0.6，A = 井盖开口面积（默认 DN600 → 0.283 m²）。
         回灌量再被两条约束截断：二维格里确实有那么多水、一维不会被顶到井盖以上。

判据（每次运行都打印，不闭合就不要信结果）：
    Σ(1D→2D) − Σ(2D→1D) == 二维域内增量 + 二维边界出流      （二维侧闭合）
    Σ(1D→2D) == SWMM 报告的 Flooding Loss                    （一维侧闭合）

用法（容器内）：
  python /work/c2d/couple2way.py <汇水区> <态> [网格m] [终止h] [交换步s]
"""
import json, os, sys, time
import numpy as np

W = "/work/c2d"
name = sys.argv[1] if len(sys.argv) > 1 else "Mussafah"
state = sys.argv[2] if len(sys.argv) > 2 else "L1"
RES = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
END_H = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
DT_C = float(sys.argv[5]) if len(sys.argv) > 5 else 60.0      # 交换步长 s
SCEN = sys.argv[6] if len(sys.argv) > 6 else os.environ.get("C2D_SCEN", "")
TAG = f"{name}_{state}{SCEN}"
# START_H 原来写死 11.5（24 小时雨的峰窗）。2 小时雨的峰在第 1.1 h，
# 写死会让二维从「雨已经下完」开始算。改成从耦合 json 的 window_h 读，
# 往前留半小时余量。
_meta0 = json.load(open(f"{W}/coupling_{TAG}.json", encoding="utf-8"))
START_H = max(float(_meta0.get("window_h", [11.5, 20.0])[0]) - 0.5, 0.0)

CD = 0.6                       # 孔口流量系数
A_MANHOLE = np.pi * 0.3 ** 2   # DN600 井盖开口 0.283 m²
G = 9.81

import anuga
import rasterio
from swmm.toolkit import solver
from swmm.toolkit.shared_enum import NodeProperty, NodeResult, ObjectType

# ---------------------------------------------------------------- 一维
inp = f"{W}/{TAG}_2way.inp"
rpt = f"{W}/{TAG}_2way.rpt"
out = f"{W}/{TAG}_2way.out"
print(f"{TAG}: 打开一维 {os.path.basename(inp)}", flush=True)
solver.swmm_open(inp, rpt, out)
solver.swmm_start(1)

n_node = solver.project_get_count(ObjectType.NODE)
meta = json.load(open(f"{W}/coupling_{TAG}.json", encoding="utf-8"))
names = list(np.load(f"{W}/overflow_{TAG}.npz", allow_pickle=True)["names"])
XY = np.load(f"{W}/overflow_{TAG}.npz", allow_pickle=True)["XY"]

# 参与交换的节点：一维里确实会冒水的那些
nodes = []
for nm, (px, py) in zip(names, XY):
    try:
        i = solver.project_get_index(ObjectType.NODE, str(nm))
    except Exception:
        continue
    if i < 0:
        continue
    inv = solver.node_get_parameter(i, NodeProperty.INVERT_ELEVATION)
    full = solver.node_get_parameter(i, NodeProperty.FULL_DEPTH)
    nodes.append({"name": str(nm), "i": i, "x": float(px), "y": float(py),
                  "invert": inv, "rim": inv + full})
print(f"  耦合节点 {len(nodes)} / {len(names)} 个（一维总节点 {n_node:,}）", flush=True)

# ---------------------------------------------------------------- 二维
ds = rasterio.open(f"{W}/dem_{TAG}.tif")
dem = ds.read(1).astype(float)
nod = ds.nodata
if nod is not None:
    dem[dem == nod] = np.nan
dem[~np.isfinite(dem)] = np.nanmax(dem[np.isfinite(dem)]) + 50.0
tr = ds.transform
x0, y1 = tr.c, tr.f
dx, dy = abs(tr.a), abs(tr.e)
H, Wc = dem.shape
y0 = y1 - H * dy
Lx, Ly = Wc * dx, H * dy
m = max(int(Lx / RES), 4); n = max(int(Ly / RES), 4)
print(f"  二维 {Lx/1000:.1f}×{Ly/1000:.1f} km，网格 {m}×{n} @ {RES:g}m "
      f"= {m*n*4:,} 个三角单元 …", flush=True)
t0 = time.time()
domain = anuga.rectangular_cross_domain(m, n, len1=Lx, len2=Ly, origin=(x0, y0))
domain.set_name(f"{TAG}_2way"); domain.set_datadir(f"{W}/anuga_out")
print(f"    {time.time()-t0:.0f}s", flush=True)


def elev(x, y):
    col = np.clip((np.asarray(x) - tr.c) / dx, 0, Wc - 1).astype(int)
    row = np.clip((tr.f - np.asarray(y)) / dy, 0, H - 1).astype(int)
    return dem[row, col]


domain.set_quantity("elevation", elev, location="centroids")
domain.set_quantity("friction", 0.030)
domain.set_quantity("stage", expression="elevation")
Bt = anuga.Transmissive_boundary(domain)
domain.set_boundary({"left": Bt, "right": Bt, "top": Bt, "bottom": Bt})

# 每个耦合节点找到它所在的二维单元（按形心最近）
cxy = domain.centroid_coordinates
from scipy.spatial import cKDTree
tree = cKDTree(cxy)
half = max(RES, 15.0)
live = []
for nd in nodes:
    if not (x0 + half < nd["x"] < x0 + Lx - half and y0 + half < nd["y"] < y0 + Ly - half):
        continue
    d, k = tree.query([nd["x"], nd["y"]])
    nd["cell"] = int(k)
    nd["q"] = np.zeros(1)                       # 交换流量 m³/s，闭包里读它
    poly = [[nd["x"] - half, nd["y"] - half], [nd["x"] + half, nd["y"] - half],
            [nd["x"] + half, nd["y"] + half], [nd["x"] - half, nd["y"] + half]]
    nd["op"] = anuga.Inlet_operator(domain, poly, Q=(lambda q: (lambda t: float(q[0])))(nd["q"]))
    live.append(nd)
print(f"  落在二维域内并建好交换口的 {len(live)} 个", flush=True)

elev_c = domain.get_quantity("elevation").centroid_values
areas = domain.areas

# ---------------------------------------------------------------- 按步交换
t_end_s = (END_H - START_H) * 3600.0
off_s = START_H * 3600.0
acc_1to2 = 0.0      # 累计 1D→2D，m³
acc_2to1 = 0.0      # 累计 2D→1D，m³
rows = []
print(f"  开始耦合演算 {START_H}→{END_H} h，交换步 {DT_C:g}s …", flush=True)
t_wall = time.time()
elapsed_d = 0.0
t2 = 0.0

# 先把一维快进到耦合窗口起点。这段不交换也不查节点——
# 逐路由步查 131 个节点 × 4 万步是几百万次 API 调用，白等。
ff = time.time()
while elapsed_d < off_s / 86400.0:
    prev = elapsed_d
    elapsed_d = solver.swmm_step()
    if elapsed_d <= 0 or elapsed_d <= prev:
        break
print(f"  一维快进到 {elapsed_d * 24:.2f} h（{time.time() - ff:.0f}s）", flush=True)

while t2 < t_end_s:
    # ---- 一维推进到下一个交换时刻，逐路由步累积溢流**体积**（不采样瞬时流率）
    target_d = (off_s + t2 + DT_C) / 86400.0
    vol_out = np.zeros(len(live))
    guard = 0
    while elapsed_d < target_d and guard < 100000:
        prev = elapsed_d
        elapsed_d = solver.swmm_step()
        guard += 1
        if elapsed_d <= 0:
            break
        dts = (elapsed_d - prev) * 86400.0
        for j, nd in enumerate(live):
            f = solver.node_get_result(nd["i"], NodeResult.FLOOD)     # LPS
            if f > 0:
                vol_out[j] += f / 1000.0 * dts
    q_1to2 = vol_out / DT_C                                            # m³/s

    # ---- 二维状态
    stage = domain.get_quantity("stage").centroid_values
    q_2to1 = np.zeros(len(live))
    for j, nd in enumerate(live):
        c = nd["cell"]
        h2 = float(stage[c] - elev_c[c])
        if h2 <= 0.01:
            continue
        eta2 = float(stage[c])
        h1 = solver.node_get_result(nd["i"], NodeResult.HEAD)
        zr = nd["rim"]
        if eta2 > zr and h1 < zr:                       # 自由孔口，地表水灌回管网
            q = CD * A_MANHOLE * np.sqrt(2 * G * (eta2 - zr))
        elif eta2 > zr and h1 >= zr:                    # 淹没孔口，按水头差
            if eta2 <= h1:
                continue                                # 1D→2D 那一支 SWMM 的 FLOOD 已经算过，
                                                        # 这里再加一次就是重复计量
            q = CD * A_MANHOLE * np.sqrt(2 * G * (eta2 - h1))
        else:
            continue
        # 截断一：二维格里确实有这么多水
        q = min(q, h2 * float(areas[c]) / DT_C)
        # 截断二：别把一维顶到井盖以上
        q = max(q, 0.0)
        q_2to1[j] = q

    # ---- 施加到两边
    for j, nd in enumerate(live):
        nd["q"][0] = float(q_1to2[j] - q_2to1[j])       # 正=进二维，负=从二维抽走
        if q_2to1[j] > 0:
            solver.node_set_total_inflow(nd["i"], float(q_2to1[j] * 1000.0))   # LPS
        else:
            solver.node_set_total_inflow(nd["i"], 0.0)
    acc_1to2 += float(vol_out.sum())
    acc_2to1 += float(q_2to1.sum() * DT_C)

    # ---- 二维推进一个交换步
    for _ in domain.evolve(yieldstep=DT_C, finaltime=t2 + DT_C, skip_initial_step=True):
        pass
    t2 += DT_C

    if int(t2) % 600 == 0:
        h = np.maximum(domain.get_quantity("stage").centroid_values - elev_c, 0.0)
        vol = float((h * areas).sum())
        wet = float((h > 0.02).dot(areas))
        rows.append({"t_h": START_H + t2 / 3600.0, "vol_m3": vol, "wet_m2": wet,
                     "hmax_m": float(h.max()), "acc_1to2": acc_1to2, "acc_2to1": acc_2to1})
        print(f"    t={START_H + t2/3600:5.2f}h  域内 {vol:>8,.0f} m³  淹没 {wet/1e4:>6,.1f} ha  "
              f"最大水深 {h.max():.2f} m  ↓1D→2D {acc_1to2:>8,.0f}  ↑2D→1D {acc_2to1:>8,.0f}"
              f"  [{time.time()-t_wall:.0f}s]", flush=True)

# ---------------------------------------------------------------- 收尾与对账
solver.swmm_end(); solver.swmm_report(); solver.swmm_close()
h = np.maximum(domain.get_quantity("stage").centroid_values - elev_c, 0.0)
vol_in_domain = float((h * areas).sum())
net = acc_1to2 - acc_2to1
bnd = net - vol_in_domain                       # 剩下的从四边透射出去了
print(f"\n  水量对账：1D→2D {acc_1to2:,.0f} m³　2D→1D {acc_2to1:,.0f} m³　"
      f"净入二维 {net:,.0f} m³", flush=True)
print(f"            二维域内留存 {vol_in_domain:,.0f} m³　边界出流 {bnd:,.0f} m³　"
      f"闭合差 {(net - vol_in_domain - bnd):,.3f} m³", flush=True)
import re
rt = open(rpt, encoding="utf-8", errors="replace").read()
mm = re.search(r"Flooding Loss\s*\.*\s+(-?\d[\d.]*)\s+(-?\d[\d.]*)", rt)
if mm:
    fl = float(mm.group(2)) * 1000.0
    print(f"            一维侧：SWMM 报告 Flooding Loss {fl:,.0f} m³ vs 交出去 {acc_1to2:,.0f} m³"
          f"（差 {(acc_1to2 - fl) / max(fl, 1e-9) * 100:+.1f}%）", flush=True)
mx = max(rows, key=lambda z: z["wet_m2"]) if rows else None
json.dump({"tag": TAG, "res_m": RES, "dt_c": DT_C, "n_tri": int(len(h)),
           "n_coupled": len(live), "start_h": START_H, "end_h": END_H,
           "acc_1to2_m3": acc_1to2, "acc_2to1_m3": acc_2to1,
           "final_vol_m3": vol_in_domain, "boundary_out_m3": bnd,
           "max_wet_m2": (mx["wet_m2"] if mx else 0.0),
           "max_wet_t_h": (mx["t_h"] if mx else None), "series": rows},
          open(f"{W}/twoway_{TAG}.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"  → twoway_{TAG}.json", flush=True)
