# -*- coding: utf-8 -*-
"""1D/2D 双向耦合，第二版：给「成片」试跑用（Madinat Al Riyad，4.4 万节点）。在 anuga-swmm:1.0 镜像里跑。

第一版（couple2way.py，Mussafah_00 用、已验过）不动。第二版改的是**规模**和**两类来水**：
  · 第一版每个一维路由步逐节点查 FLOOD、每个耦合节点一个 Inlet_operator——242 个节点没事，
    4 万个就走不动。这里改成每个交换步读一次各节点的**累计溢流量**（node_get_stats.volFlooded，
    CMS 模型里单位是 m³）取差，二维那边直接按体积改形心水位，一次矢量化完成；
  · **全部**井盖都参与交换（第一版只挑一维里会冒水的那些）：地面上的水流到哪个井盖都能回管网；
  · 没有管网覆盖的地面，净雨**直接下在二维上**——靠地表汇流进塘的水只有这样才算得进去；
  · L2 里塘的占地轮廓内的地表水**直接进塘**（塘在一维，DTM 不挖，同一份库容不算两次），塘满为止。

交换规则与第一版相同：1D→2D 是一维冒出井盖的水（ALLOW_PONDING NO 下的 Flooding）；
2D→1D 是井盖处的孔口 / 淹没孔口，Cd = 0.6、DN600 井盖，截断到「格里确实有这么多水」。
判据：Σ1D→2D + Σ雨 − Σ2D→1D − Σ进塘 = 二维域内增量 + 边界出流（二维侧闭合），
      Σ1D→2D 对 SWMM 报告的 Flooding Loss（一维侧闭合）。

输出不用 ANUGA 自己的 .sww（90 万单元 × 360 步几十 GB），改为每 FRAME_S 秒存一帧形心水深 +
全程最大水深，写成 npz；逐帧出图另由 tools/pilot_frames.py 做。

用法（容器内）：python /work/c2d/couple2way_v2.py <名> <态> [网格m] [终止h] [交换步s]

2026-10-28 增量（RD-A 治理数据版，docs/RD-A_一二维重算_设计与参数登记_20261028.md；pilot 里没有这些键 = 旧行为不变）：
  · meta["xchg_type"] = {节点: "inlet" | "overflow" | "orifice"}：inlet = 雨水口，2D→1D 按 WA-726 附录 A3 洼地格栅式
    （tools/inlet_hyd.py，参数 meta["inlet"] = {P, A, clog}）；overflow = 只溢流、不收地面水；orifice / 不在表里 = 旧的 DN600 孔口
  · meta["ground_basis"] = "cell"：交换节点的地面取所在单元的二维地面高程，一维节点最大深度改成「单元地面 − 节点底」（swmm_start 前改），
    溢流从水头到达二维地面时开始，孔口 / 格栅水头按二维水深算（相当于 ICM 的「按水深交换」）
  · meta["surface_classes"] = {raster, n: {类: 曼宁 n}, infil_mmh: {类: 下渗 mm/h}, default}：按下垫面分糙率；下渗为恒定速率、受单元存水限制
  · 边界出流直接计量（ANUGA 边界通量积分），对账不再只靠余项；有 xchg_type 时另存逐节点交换量与交换方向切换次数
"""
import json, os, sys, time
import numpy as np

W = "/work"
C2D = f"{W}/c2d"
name = sys.argv[1] if len(sys.argv) > 1 else "Pilot_MAR"
state = sys.argv[2] if len(sys.argv) > 2 else "L1"
RES = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
END_H = float(sys.argv[4]) if len(sys.argv) > 4 else 6.0
DT_C = float(sys.argv[5]) if len(sys.argv) > 5 else 60.0
FRAME_S = 300.0
# 开关（默认关，已有模型行为不变；C1 两个都开，2026-09-28 参数对齐 E18 / E20）
DEM_RAW = os.environ.get("C2D_DEM_RAW") == "1"     # 地形直接取 DTM 原值（不按网格尺寸取平均）
SAVE_UV = os.environ.get("C2D_SAVE_UV") == "1"     # 每帧另存 x / y 向单宽流量（形心），npz_to_sww 写进 .sww 就有流速
TAG = f"{name}_{state}"
CD = 0.6
A_MANHOLE = np.pi * 0.3 ** 2
G = 9.81
DRY = 0.02

import anuga
import rasterio
from rasterio.enums import Resampling
from scipy.spatial import cKDTree
from swmm.toolkit import solver
from swmm.toolkit.shared_enum import NodeProperty, NodeResult, ObjectType

meta = json.load(open(f"{C2D}/pilot_{TAG}.json", encoding="utf-8"))

# ---------------------------------------------------------------- 一维
inp, rpt, out = f"{W}/{TAG}.inp", f"{C2D}/{TAG}_2way.rpt", f"{C2D}/{TAG}_2way.out"
print(f"{TAG}: 打开一维 {os.path.basename(inp)}", flush=True)
solver.swmm_open(inp, rpt, out)
# swmm_start 挪到二维建好之后（ground_basis = cell 要先按单元地面改节点深度）；open 之后、start 之前读写参数都有效
names = list(meta["nodes"]); XY = np.array([meta["nodes"][k] for k in names], float)
idx = np.array([solver.project_get_index(ObjectType.NODE, k) for k in names])
ok = idx >= 0
names = [n for n, o in zip(names, ok) if o]; XY = XY[ok]; idx = idx[ok]
rim = np.array([solver.node_get_parameter(int(i), NodeProperty.INVERT_ELEVATION)
                + solver.node_get_parameter(int(i), NodeProperty.FULL_DEPTH) for i in idx])
ponds = meta.get("ponds") or []
for p in ponds:
    p["i"] = solver.project_get_index(ObjectType.NODE, p["node"])
print(f"  一维节点 {solver.project_get_count(ObjectType.NODE):,}，参与交换 {len(idx):,}，塘 {len(ponds)}", flush=True)

# ---------------------------------------------------------------- 二维
with rasterio.open(f"{C2D}/dem_{name}.tif") as ds:
    f = 1.0 if DEM_RAW else ds.res[0] / RES
    Hh, Ww = max(int(ds.height * f), 4), max(int(ds.width * f), 4)
    dem = ds.read(1, out_shape=(Hh, Ww), resampling=Resampling.average).astype(float)   # 默认按网格尺寸取**平均**；DEM_RAW 时是原分辨率
    print(f"  地形 {'DTM 原值' if DEM_RAW else '按网格尺寸取平均'}：{Ww}×{Hh} 格 @ {ds.res[0] / f:.1f} m", flush=True)
    b = ds.bounds; nod = ds.nodata
bad = ~np.isfinite(dem) | (dem < -1000) | ((nod is not None) & (dem == nod))
dem[bad] = np.nan
dem[~np.isfinite(dem)] = np.nanmax(dem[np.isfinite(dem)]) + 50.0
x0, y0, Lx, Ly = b.left, b.bottom, b.right - b.left, b.top - b.bottom
dx, dy = Lx / Ww, Ly / Hh
poly = meta.get("domain_polygon")
# 建筑挖洞（用户 2026-10-28 同意，tools/bldg_holes_prep.py 生成）：meta["building_holes"] = {"holes": [[[x,y],…],…]}，
# 每栋建筑轮廓是网格里的洞，洞边是反射墙（不过水、不占蓄水）。没有这个键 = 不挖（已有模型不受影响）
BH = (meta.get("building_holes") or {}).get("holes") if poly else None
if poly:
    # 按汇水边界划的计算域（不规则多边形）→ 非结构三角网。单元最大面积 RES²/3：30 m 时 300 m²，
    # 与 30 m 矩形交叉网（每格 4 个 225 m² 的三角形）同一量级。矩形框会截断汇水区、带进无关的地方（2026-09-27）
    kw = {"interior_holes": BH, "hole_tags": [{"wall": list(range(len(h)))} for h in BH]} if BH else {}
    domain = anuga.create_domain_from_regions([tuple(p) for p in poly], {"exterior": list(range(len(poly)))},
                                              maximum_triangle_area=RES * RES / 3.0, **kw)
    print(f"  二维 按汇水边界划的多边形（{len(poly)} 个顶点），{len(domain):,} 个三角单元"
          + (f"；建筑挖洞 {len(BH):,} 个" if BH else ""), flush=True)
else:
    m = max(int(Lx / RES), 4); n = max(int(Ly / RES), 4)
    domain = anuga.rectangular_cross_domain(m, n, len1=Lx, len2=Ly, origin=(x0, y0))
    print(f"  二维 {Lx/1000:.1f}×{Ly/1000:.1f} km，{m}×{n} @ {RES:g} m = {m*n*4:,} 个三角单元", flush=True)
domain.set_name(TAG); domain.set_datadir(f"{C2D}/anuga_out")
try:
    domain.set_store(False)
except Exception:
    domain.store = False


def elev(x, y):
    col = np.clip((np.asarray(x) - x0) / dx, 0, Ww - 1).astype(int)
    row = np.clip((y0 + Ly - np.asarray(y)) / dy, 0, Hh - 1).astype(int)
    return dem[row, col]


# 形心一律换成**绝对坐标**再取高程：多边形网格带 geo_reference，形心坐标是相对偏移
cxy = np.asarray(domain.geo_reference.get_absolute(domain.centroid_coordinates), float)
z_c = elev(cxy[:, 0], cxy[:, 1])
# 按态改地形（C1 L2+suggested 的挡水土堤 / 隔离墩，2026-09-29）：meta["dem_edits"] = [{id, polygon:[[x,y],…], crest_m | raise_m}]
# 多边形内的单元：crest_m = 抬到这个绝对高程（低于它才抬），raise_m = 在现状地面上加这么高。没有这个键 = 不改（已有模型不受影响）
dem_edits = meta.get("dem_edits") or []
if dem_edits:
    from matplotlib.path import Path as _MP
    for e in dem_edits:
        sel = _MP(np.array(e["polygon"], float)).contains_points(cxy)
        if not sel.any():
            sel[cKDTree(cxy).query(np.array(e["polygon"], float).mean(0))[1]] = True
        z_old = z_c[sel].copy()
        z_c[sel] = np.maximum(z_c[sel], float(e["crest_m"])) if e.get("crest_m") is not None else z_c[sel] + float(e.get("raise_m", 0.0))
        e["n_cells"] = int(sel.sum()); e["raised_mean_m"] = float((z_c[sel] - z_old).mean())
        print(f"  改地形 {e.get('id')}：{e['n_cells']} 个单元，平均抬高 {e['raised_mean_m']:.2f} m", flush=True)
domain.set_quantity("elevation", numeric=z_c, location="centroids")
domain.set_quantity("friction", 0.030)
SC = meta.get("surface_classes")
infil_c = None
if SC:
    # 下垫面分类栅格（tools/rda_gov_build.py 由 Makani 生成）：按形心取类，类 → 曼宁 n、下渗速率
    with rasterio.open(SC["raster"].replace("$C2D", C2D)) as ds:
        cls = np.array([v[0] for v in ds.sample([tuple(p) for p in cxy])], int)
    nmap = {int(k): float(v) for k, v in SC["n"].items()}
    n_c = np.array([nmap.get(c, float(SC["default_n"])) for c in cls])
    domain.set_quantity("friction", numeric=n_c, location="centroids")
    imap = {int(k): float(v) for k, v in (SC.get("infil_mmh") or {}).items()}
    if imap:
        infil_c = np.array([imap.get(c, 0.0) for c in cls]) / 1000.0 / 3600.0      # m/s
    cnt = {int(c): int((cls == c).sum()) for c in np.unique(cls)}
    print(f"  下垫面分类 {cnt}；n 取值 {sorted(set(np.round(n_c, 3)))}；下渗单元 {0 if infil_c is None else int((infil_c > 0).sum())}", flush=True)
domain.set_quantity("stage", expression="elevation")
Bo = anuga.Dirichlet_boundary([float(np.nanmin(dem)) - 1.0, 0.0, 0.0])
if poly:
    domain.set_boundary({"exterior": Bo, **({"wall": anuga.Reflective_boundary(domain)} if BH else {})})
else:
    # 边界：自由出流。**不能用 Transmissive**——它照抄内侧状态，内侧一有积水就往域里补水；
    # 第一次 30 m 试跑 t=2 h 从边界「流进来」2,313 万 m³（最深 8.9 m 就在北边界内 100–230 m）
    domain.set_boundary({"left": Bo, "right": Bo, "top": Bo, "bottom": Bo})
tree = cKDTree(cxy)
areas = np.asarray(domain.areas, float)
elev_c = domain.get_quantity("elevation").centroid_values
stage_c = domain.quantities["stage"].centroid_values        # 就地改
xmom_c = domain.quantities["xmomentum"].centroid_values
ymom_c = domain.quantities["ymomentum"].centroid_values

# 井盖 → 所在单元（2D→1D 取这一格），以及冒水时分摊的 4 个最近单元
_, cell = tree.query(XY)
_, cell4 = tree.query(XY, k=4)
# 交换节点的地面取所在单元的二维地面（ground_basis = cell）：最大深度 = 单元地面 − 节点底，不浅于接入管管顶（node_min_depth）和 0.3 m
gb = None
if meta.get("ground_basis") == "cell":
    mind = meta.get("node_min_depth") or {}
    gb = np.zeros(len(idx))
    for j, i in enumerate(idx):
        inv = solver.node_get_parameter(int(i), NodeProperty.INVERT_ELEVATION)
        d0 = solver.node_get_parameter(int(i), NodeProperty.FULL_DEPTH)
        d1 = max(float(elev_c[cell[j]]) - inv, float(mind.get(names[j], 0.0)), 0.3)
        solver.node_set_parameter(int(i), NodeProperty.FULL_DEPTH, d1)
        gb[j] = d1 - d0; rim[j] = inv + d1
    print(f"  交换节点地面改取单元地面：{len(gb):,} 个，深度变化中位 {np.median(gb):+.2f} m"
          f"（5 % {np.percentile(gb, 5):+.2f}、95 % {np.percentile(gb, 95):+.2f}）", flush=True)
XT = meta.get("xchg_type") or {}
xt = np.array([{"inlet": 1, "overflow": 2}.get(XT.get(n), 0) for n in names], int)      # 0 孔口（旧）/ 1 雨水口 / 2 只溢流
INL = meta.get("inlet") or {}
if XT:
    sys.path.insert(0, C2D)
    import inlet_hyd as IH
    print(f"  交换类型：雨水口 {int((xt == 1).sum()):,}（WA-726 A3 格栅式，P {INL['P']} m、A {INL['A']} m²、折减 {INL.get('clog', 0.5)}）、"
          f"只溢流 {int((xt == 2).sum()):,}、孔口 {int((xt == 0).sum()):,}", flush=True)
solver.swmm_start(0)
# 无管网覆盖的地面：离最近井超过服务半径
dn, _ = cKDTree(XY).query(cxy, k=1)
rain_mask = dn > float(meta["R_served_m"])
t_r = np.array(meta["rain_t_h"]) * 3600.0; exc_r = np.array(meta["rain_excess_mm"]) / 1000.0
print(f"  无管网覆盖、直接下雨的单元 {rain_mask.mean()*100:.1f}%（{areas[rain_mask].sum()/1e6:.1f} km²）", flush=True)
# 塘的占地单元
from matplotlib.path import Path as MPath
for p in ponds:
    fp = p["footprint"]; polys = fp["coordinates"] if fp["type"] == "MultiPolygon" else [fp["coordinates"]]
    sel = np.zeros(len(cxy), bool)
    for poly in polys:
        sel |= MPath(np.array(poly[0])[:, :2]).contains_points(cxy)          # 台账轮廓带 Z，只取平面
    if not sel.any() and not p.get("no_surface"):
        sel[tree.query(p["xy"])[1]] = True
    if p.get("underground"):                    # 地下调蓄池（2026-10-21）：不收地表水，只经一维（泵 / 重力管）进水
        sel[:] = False
    # 塘在二维计算域外（C1 客户单元版的 PND_C1_00 等，2026-09-29）：no_surface = 只在一维里收水，塘面不进二维；塘满溢出记为一维损失
    p["cells"] = np.flatnonzero(sel)
    p["prev_fl"] = 0.0; p["captured"] = 0.0; p["overflow"] = 0.0
    p["vmax"] = float(p["vol"])
# 二维取水点（L2 临时转输：地面管 + 固定泵，tools/l2c_conveyance.py）：从低点的地面单元抽水，
# 排到出水口（离开系统）或放到指定空地的单元上（回到二维）。浮球：水深低于 off_m 不抽
pumps2d = meta.get("pumps2d") or []
for q in pumps2d:
    sel = MPath(np.array(q["sump_polygon"])).contains_points(cxy)
    if not sel.any():
        sel[tree.query(q["xy"])[1]] = True
    q["cells"] = np.flatnonzero(sel); q["pumped"] = 0.0
    q["to"] = np.atleast_1d(tree.query(q["to_xy"], k=int(q.get("to_cells", 12)))[1]) if q.get("to_xy") else None
    # C1 L2+suggested v3（2026-09-29）：to_pond = 直接打进这个塘的一维节点，按塘剩余库容封顶、塘满即停（没有这个键 = 旧行为）
    q["pond"] = next((p for p in ponds if p["node"] == q.get("to_pond") and p["i"] >= 0), None)
    if q.get("to_pond") and q["pond"] is None:
        raise SystemExit(f"二维取水点 {q['id']} 的 to_pond {q['to_pond']} 在一维里找不到")
if pumps2d:
    print(f"  二维取水点 {len(pumps2d)} 处，合计 {sum(q['q_m3s'] for q in pumps2d):.2f} m³/s", flush=True)

# ---------------------------------------------------------------- 按步交换
n_step = int(round(END_H * 3600.0 / DT_C))
prev_fl = np.zeros(len(idx))
set_prev = np.zeros(len(idx), bool)
acc = {"1to2": 0.0, "2to1": 0.0, "rain": 0.0, "pond": 0.0, "pump_out": 0.0, "pump_move": 0.0, "pump_pond": 0.0}
rows, frames, frame_t = [], [], []
fr_uh, fr_vh = [], []
hmax = np.zeros(len(cxy), np.float32)
nd_out = np.zeros(len(idx)); nd_in = np.zeros(len(idx)); nd_flip = np.zeros(len(idx), int); nd_last = np.zeros(len(idx), int)
LAG = int(meta.get("xchg_lag_steps") or 0); last_out = np.full(len(idx), -10 ** 6)
bflux0 = domain.get_boundary_flux_integral()                # ANUGA 边界通量积分（流入为正）
elapsed_d = 0.0; t_wall = time.time()
TT = {"1d": 0.0, "stats": 0.0, "xchg": 0.0, "2d": 0.0}      # 分段计时：时间花在哪一段
print(f"  开始 0→{END_H} h，交换步 {DT_C:g} s，共 {n_step} 步", flush=True)
for k in range(n_step):
    t_a, t_b = k * DT_C, (k + 1) * DT_C
    _t = time.time()
    while elapsed_d * 86400.0 < t_b - 1e-6:
        prv = elapsed_d
        elapsed_d = solver.swmm_step()
        if elapsed_d <= 0 or elapsed_d <= prv:
            break
    TT["1d"] += time.time() - _t; _t = time.time()
    # 1D→2D：各节点累计溢流取差
    fl = np.array([solver.node_get_stats(int(i)).volFlooded for i in idx])
    dv12 = np.maximum(fl - prev_fl, 0.0); prev_fl = fl
    # 冒出的水分摊到井盖附近 4 个单元：一步全灌进一个 225 m² 的三角形，水深会突变
    vol_add = np.zeros(len(cxy))
    np.add.at(vol_add, cell4.ravel(), np.repeat(dv12 / 4.0, 4))
    for p in ponds:                                      # 塘满后从塘里溢出来的水，摊到它的占地上
        if p["i"] < 0:
            continue
        pf = solver.node_get_stats(int(p["i"])).volFlooded
        d = max(pf - p["prev_fl"], 0.0); p["prev_fl"] = pf; p["overflow"] += d
        if d > 0 and len(p["cells"]):
            np.add.at(vol_add, p["cells"], d * areas[p["cells"]] / areas[p["cells"]].sum())
            dv12 = np.append(dv12, d)
    TT["stats"] += time.time() - _t; _t = time.time()
    # 雨（无管网覆盖处）
    dz = float(np.interp(t_b, t_r, exc_r) - np.interp(t_a, t_r, exc_r))
    # 2D→1D：井盖孔口
    h = stage_c - elev_c
    # 这一步正在往外冒水的井不再让地面水灌回去：没有出口的网满了以后，灌进去的水下一步
    # 就被顶出来，两边来回空转（粗网格测试里 2 h 冒 905 万、灌回 806 万 m³，而净雨只有 175 万）
    wet = (h[cell] > 0.01) & (dv12[:len(idx)] <= 0) & (xt != 2)          # 只溢流的节点（实盖检查井等）不收地面水
    if LAG:                                              # 防振荡（2026-10-28）：最近 LAG 个交换步里冒过水的节点，这一步也不吸水
        wet &= (k - last_out) > LAG
    last_out[dv12[:len(idx)] > 0] = k
    q21 = np.zeros(len(idx))
    for j in np.flatnonzero(wet):
        c = cell[j]; eta2 = stage_c[c]; zr = rim[j]
        if eta2 <= zr:
            continue
        h1 = solver.node_get_result(int(idx[j]), NodeResult.HEAD)
        if h1 < zr:
            dh = eta2 - zr                                   # 自由进水：格栅 / 井盖以上水深
        elif eta2 > h1:
            dh = eta2 - h1                                   # 淹没：地面水位与井内水头之差
        else:
            continue
        q21[j] = float(IH.grate_sump_q(dh, INL["P"], INL["A"], INL.get("clog", 0.5))) if xt[j] == 1 else CD * A_MANHOLE * np.sqrt(2 * G * dh)
    # 同一格里几个井一起抽，合计不能超过格里的水
    want = np.zeros(len(cxy)); np.add.at(want, cell, q21 * DT_C)
    have = np.maximum(h, 0.0) * areas
    scale = np.where(want > have, have / np.maximum(want, 1e-12), 1.0)
    q21 *= scale[cell]
    vol_rm = np.zeros(len(cxy)); np.add.at(vol_rm, cell, q21 * DT_C)
    # 进塘（L2）：占地内的地表水进塘，塘满为止
    pond_in = {}
    for p in ponds:
        if p["i"] < 0:
            continue
        c = p["cells"]
        if not len(c):
            continue
        hc = np.maximum(stage_c[c] - elev_c[c] - vol_rm[c] / areas[c], 0.0)
        v2 = float((hc * areas[c]).sum())
        cap = max(p["vmax"] - solver.node_get_result(int(p["i"]), NodeResult.VOLUME), 0.0)
        take = min(v2, cap)
        if take > 0:
            vol_rm[c] += hc * areas[c] * (take / v2)
            pond_in[p["i"]] = take / DT_C
            p["captured"] += take; acc["pond"] += take
    # 二维取水点：受片内现有水量限制，按各单元的水量比例取
    for q in pumps2d:
        c = q["cells"]
        hc = np.maximum(stage_c[c] - elev_c[c] - vol_rm[c] / areas[c] - float(q["off_m"]), 0.0)
        v2 = float((hc * areas[c]).sum())
        take = min(v2, float(q["q_m3s"]) * DT_C)
        if q.get("pond") is not None:
            pp = q["pond"]
            cap = float(q.get("pond_cap_frac", 1.0)) * pp["vmax"] - solver.node_get_result(int(pp["i"]), NodeResult.VOLUME) - pond_in.get(pp["i"], 0.0) * DT_C
            take = min(take, max(cap, 0.0))
        if take <= 0:
            continue
        vol_rm[c] += hc * areas[c] * (take / v2)
        q["pumped"] += take
        if q.get("pond") is not None:
            pond_in[q["pond"]["i"]] = pond_in.get(q["pond"]["i"], 0.0) + take / DT_C
            q["pond"]["pumped_in"] = q["pond"].get("pumped_in", 0.0) + take; acc["pump_pond"] += take
        elif q["to"] is not None:
            np.add.at(vol_add, q["to"], take * areas[q["to"]] / areas[q["to"]].sum()); acc["pump_move"] += take
        else:
            acc["pump_out"] += take
    # 施加到二维（按体积改形心水位，一次到位）
    h_old = np.maximum(stage_c - elev_c, 0.0)
    stage_c += (vol_add - vol_rm) / areas
    if dz > 0:
        stage_c[rain_mask] += dz
    np.maximum(stage_c, elev_c, out=stage_c)
    if infil_c is not None:                              # 二维下渗（恒定速率，受单元存水限制），离开系统
        dinf = np.minimum(infil_c * DT_C, stage_c - elev_c)
        stage_c -= dinf; acc["infil"] = acc.get("infil", 0.0) + float((dinf * areas).sum())
    # 抽走水时动量要按水深同比例缩小：流速 = 动量 / 水深，只减水深不减动量，流速会被放大，
    # 局部越算越快、水量凭空增长（第一次 30 m 试跑 t=2 h 时域内多出 2,340 万 m³，就是这个）
    h_new = stage_c - elev_c
    r = np.where((h_old > 1e-6) & (h_new < h_old), h_new / np.maximum(h_old, 1e-12), 1.0)
    xmom_c *= r; ymom_c *= r
    domain.distribute_to_vertices_and_edges()
    # 施加到一维（CMS：m³/s）
    for j in np.flatnonzero((q21 > 0) | set_prev):
        solver.node_set_total_inflow(int(idx[j]), float(q21[j]))
    set_prev = q21 > 0
    for i, q in pond_in.items():
        solver.node_set_total_inflow(int(i), float(q))
    for p in ponds:
        if p["i"] >= 0 and p["i"] not in pond_in:
            solver.node_set_total_inflow(int(p["i"]), 0.0)
    acc["1to2"] += float(dv12.sum()); acc["2to1"] += float(q21.sum() * DT_C)
    if XT:                                               # 逐节点交换量；方向切换 = 上一步冒水、这一步吸水（或反过来）
        d_now = np.where(dv12[:len(idx)] > 0, 1, np.where(q21 > 0, -1, 0))
        nd_out += dv12[:len(idx)]; nd_in += q21 * DT_C
        nd_flip += (d_now * nd_last < 0); nd_last = np.where(d_now != 0, d_now, nd_last)
    acc["rain"] += dz * float(areas[rain_mask].sum())
    TT["xchg"] += time.time() - _t; _t = time.time()
    # 二维推进
    for _ in domain.evolve(yieldstep=DT_C, finaltime=t_b, skip_initial_step=True):
        pass
    TT["2d"] += time.time() - _t
    hh = np.maximum(stage_c - elev_c, 0.0)
    np.maximum(hmax, hh.astype(np.float32), out=hmax)
    if int(round(t_b)) % int(FRAME_S) == 0:
        vol = float((hh * areas).sum()); wa = float(areas[hh > DRY].sum())
        rows.append({"t_h": t_b / 3600.0, "vol_m3": vol, "wet_m2": wa, "hmax_m": float(hh.max()),
                     **{f"acc_{k}": v for k, v in acc.items()}})
        frames.append(hh.astype(np.float16)); frame_t.append(t_b / 3600.0)
        for q in pumps2d:                                  # 各取水点累计抽水量（每帧一个值，2026-10-24：洼坑进水过程线用）
            q.setdefault("cum", []).append(round(float(q["pumped"]), 1))
        if SAVE_UV:
            fr_uh.append(xmom_c.astype(np.float16)); fr_vh.append(ymom_c.astype(np.float16))
        print(f"    t={t_b/3600:5.2f}h 域内 {vol:>10,.0f} m³ 淹没 {wa/1e4:>7,.1f} ha 最深 {hh.max():.2f} m "
              f"↓1D→2D {acc['1to2']:>9,.0f} ↑2D→1D {acc['2to1']:>9,.0f} 雨 {acc['rain']:>9,.0f} 进塘 {acc['pond']:>9,.0f}"
              f" [{time.time()-t_wall:.0f}s | 1D {TT['1d']:.0f} 读溢流 {TT['stats']:.0f} 交换 {TT['xchg']:.0f} 2D {TT['2d']:.0f}]", flush=True)

# ---------------------------------------------------------------- 收尾与对账
pond_rows = []
for p in ponds:
    v_end = solver.node_get_result(int(p["i"]), NodeResult.VOLUME) if p["i"] >= 0 else None
    pond_rows.append({"unitid": p["unitid"], "vol_design_m3": p["vol"], "vol_end_m3": v_end,
                      "surface_captured_m3": p["captured"], "pumped_in_m3": p.get("pumped_in", 0.0), "overflow_m3": p["overflow"],
                      "fill_pct": (v_end / p["vol"] * 100.0) if v_end is not None and p["vol"] else None})
solver.swmm_end(); solver.swmm_report(); solver.swmm_close()
hh = np.maximum(stage_c - elev_c, 0.0)
vin = float((hh * areas).sum())
net = acc["1to2"] + acc["rain"] - acc["2to1"] - acc["pond"] - acc["pump_out"] - acc["pump_pond"] - acc.get("infil", 0.0)
bnd = net - vin
bnd_meas = -(domain.get_boundary_flux_integral() - bflux0)  # 实测边界出流（正 = 流出）
close_err = (net - vin - bnd_meas) / max(acc["1to2"] + acc["rain"], 1e-9)
print(f"            边界出流实测 {bnd_meas:,.0f} m³；二维闭合误差（入 − 出 − 存 − 实测边界）{close_err * 100:+.2f}%"
      + (f"；下渗 {acc['infil']:,.0f} m³" if "infil" in acc else ""), flush=True)
print(f"\n  水量对账：1D→2D {acc['1to2']:,.0f}　雨 {acc['rain']:,.0f}　2D→1D {acc['2to1']:,.0f}　进塘 {acc['pond']:,.0f} m³"
      + (f"　泵排出系统 {acc['pump_out']:,.0f}　泵转到空地 {acc['pump_move']:,.0f}　泵打进塘 {acc['pump_pond']:,.0f} m³" if pumps2d else ""), flush=True)
print(f"            二维域内留存 {vin:,.0f} m³　边界出流（余项）{bnd:,.0f} m³", flush=True)
import re
rt = open(rpt, encoding="utf-8", errors="replace").read()
mm = re.search(r"Flooding Loss\s*\.*\s+(-?\d[\d.]*)\s+(-?\d[\d.]*)", rt)
if mm:
    fl1 = float(mm.group(2)) * 1000.0
    print(f"            一维侧：SWMM Flooding Loss {fl1:,.0f} m³ vs 交出去 {acc['1to2']:,.0f} m³"
          f"（差 {(acc['1to2'] - fl1) / max(fl1, 1e-9) * 100:+.1f}%）", flush=True)
mx = max(rows, key=lambda z: z["wet_m2"]) if rows else None
np.savez_compressed(f"{C2D}/{TAG}_2d.npz", xy=cxy.astype(np.float32), area=areas.astype(np.float32),
                    elev=elev_c.astype(np.float32), hmax=hmax, frames=np.array(frames), t_h=np.array(frame_t),
                    rain_mask=rain_mask, **({"uh": np.array(fr_uh), "vh": np.array(fr_vh)} if SAVE_UV else {}))
json.dump({"tag": TAG, "res_m": RES, "dt_c": DT_C, "n_tri": int(len(cxy)), "n_nodes": int(len(idx)),
           "end_h": END_H, "acc_m3": acc, "final_vol_m3": vin, "boundary_out_m3": bnd,
           "boundary_out_measured_m3": bnd_meas, "closure_err_2d": close_err,
           **({"ground_shift_m": {"median": float(np.median(gb)), "p5": float(np.percentile(gb, 5)), "p95": float(np.percentile(gb, 95))}} if gb is not None else {}),
           **({"nodes_xchg": {n: [round(float(a), 1), round(float(b), 1), int(c)] for n, a, b, c in zip(names, nd_out, nd_in, nd_flip) if a > 0 or b > 0},
               "nodes_xchg_cols": ["1to2_m3", "2to1_m3", "flips"], "n_flip_nodes": int((nd_flip > 3).sum())} if XT else {}),
           "max_wet_m2": mx["wet_m2"] if mx else 0.0, "max_wet_t_h": mx["t_h"] if mx else None,
           "hmax_m": float(hmax.max()), "wet_ever_m2": float(areas[hmax > DRY].sum()),
           "ponds": pond_rows, "pumps2d": [{"id": q["id"], "q_m3s": q["q_m3s"], "pumped_m3": q["pumped"], "to_node": q.get("to_node"), "cum_m3": q.get("cum", [])} for q in pumps2d], "dem_edits": [{k: e.get(k) for k in ("id", "crest_m", "raise_m", "n_cells", "raised_mean_m")} for e in dem_edits],
           "series": rows, "wall_s": time.time() - t_wall, "timing_s": TT},
          open(f"{C2D}/twoway_{TAG}.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"  → twoway_{TAG}.json、{TAG}_2d.npz（{time.time()-t_wall:.0f} s）", flush=True)
