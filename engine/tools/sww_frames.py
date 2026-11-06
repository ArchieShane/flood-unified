# -*- coding: utf-8 -*-
"""把 ANUGA 的 .sww 抽成逐时刻水深帧，好在地图上按时间轴播放。

为什么要抽帧而不是直接给原件：
  三个 .sww 合计 4.8 GB（L1 2.48 / L2+A 1.18 / L2+B 1.18），
  每个有 129,791 个顶点 × 270 个时刻。浏览器不可能读这种东西，
  只能先在服务端降到「一帧一张小图」—— 133×493 的 PNG 一张只有几十 KB。

口径写死在这里，看图之前先看判据：
  水深  = stage − elevation，逐顶点算，再按格取**最大值**（取平均会把窄沟抹平）。
  干湿  = 水深 < DRY_M（默认 0.05 m）算干，完全透明。
          这个阈值直接决定「淹没面积」有多大，所以随产物一起写进 manifest。
  配色  = 所有态共用同一条色标（0 → VMAX），否则两张图并排看不出谁更淹。
  投影  = 网格在 UTM 40N 上算，再重投影到 EPSG:4326 出图 ——
          Leaflet 的 ImageOverlay 是按经纬度矩形线性摆放的，
          直接拿 UTM 栅格套上去会有偏移。

用法：
    python tools/sww_frames.py                      # 三态全出
    python tools/sww_frames.py --state L1 --frames 30
产物：console/data/flood2d/<态>/f###.png + console/data/flood2d/manifest.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys

import numpy as np


def _fix_proj():
    """把 PROJ 数据目录指回 rasterio 自带的那份。

    这台机器的环境变量 PROJ_LIB 指向 PostgreSQL 18 里 postgis 带的旧 proj，
    它的 DATABASE.LAYOUT.VERSION.MINOR = 2，而 rasterio 要 >= 6，
    于是 `CRS.from_epsg(32640)` 直接抛 CRSError。
    依赖调用者去 export 是不可靠的（谁都会忘），所以在这里自己纠正；
    必须在 `import rasterio` **之前**做 —— GDAL 在导入时就把这个路径读走了。
    用 find_spec 定位包路径，不触发导入。
    """
    spec = importlib.util.find_spec("rasterio")
    if not spec or not spec.origin:
        return
    d = pathlib.Path(spec.origin).parent / "proj_data"
    if (d / "proj.db").exists():
        os.environ["PROJ_LIB"] = str(d)
        os.environ["PROJ_DATA"] = str(d)


_fix_proj()

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from workdir import WORK  # noqa: E402

OUT = ROOT / "console" / "data" / "flood2d"
# 三个口径必须和 couple2way.py 里那一行 `wet = float((h > 0.02).dot(areas))` **完全一致**，
# 否则地图上的淹没范围会和四态表里的数对不上，而且没人说得清该信哪个。
DRY_M = 0.02             # 干湿阈值。**极其敏感**：t=1.83 h 时 >0.01 有 1,995 个单元、
                         # >0.05 只剩 633 个，差 3.2 倍。换阈值数就变，所以写进 manifest。
USE_CENTROID = True      # 用**形心**量（stage_c / elevation_c），不是顶点量。
                         # 顶点量是限制器重构出来的，同一场算出来最大水深只有 0.481 m，
                         # 而形心量是 0.840 m —— 耦合日志报的是形心，差 1.75 倍。
VMAX = 1.40              # 色标上限，三态共用；L1/L2+A 的峰值水深是 1.352 m
CELL = 15.0              # 出图格距，和 ANUGA 网格一致
MAX_FRAMES = 40

# 态 → sww 文件名里的标签。**L2 不单列**：区内 0 座已建成塘，该态 ≡ L1
STATES = {"L1": "L1", "L2+A": "L2", "L2+B": "L2B", "L2+A+B": "L2AB"}   # L2+A+B：Parsons 之上加本系统（tools/l2ab.py）


def ramp(v: np.ndarray) -> np.ndarray:
    """水深 → RGBA。浅→深：青 → 蓝 → 深紫，干的全透明。

    色标是**非线性**的（开方），而且最浅那档也给足不透明度 ——
    这场雨里绝大多数湿格只有 0.02–0.2 m，占色标量程的 1.4%–14%，
    线性映射 + 低透明度会让它们淡到看不见（实测第一版几乎整片消失，
    只有贴着地图放大才勉强分辨）。看不见的图等于没画。
    """
    n = np.clip(v / VMAX, 0.0, 1.0)
    ncol = np.sqrt(n)                      # 开方：把浅水段拉开
    stops = np.array([[125, 211, 252], [37, 130, 235], [29, 78, 216], [76, 29, 149]],
                     dtype=np.float32)
    pos = np.linspace(0.0, 1.0, len(stops))
    rgb = np.empty(n.shape + (3,), dtype=np.float32)
    for c in range(3):
        rgb[..., c] = np.interp(ncol, pos, stops[:, c])
    a = np.where(v > 0, np.clip(165 + 90 * ncol, 0, 255), 0)
    return np.concatenate([rgb, a[..., None]], axis=-1).astype(np.uint8)


def grid_of(x, y, cell):
    x0, x1 = float(x.min()), float(x.max())
    y0, y1 = float(y.min()), float(y.max())
    nx = max(int(np.ceil((x1 - x0) / cell)), 1)
    ny = max(int(np.ceil((y1 - y0) / cell)), 1)
    return x0, y0, nx, ny


def one(tag: str, label: str, n_frames: int, log=print) -> dict | None:
    from scipy.io import netcdf_file
    from PIL import Image
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import calculate_default_transform, reproject, Resampling

    f = WORK / "c2d" / "anuga_out" / f"Mussafah_{tag}_R61D2_2way.sww"
    if not f.exists():
        log(f"  缺 {f.name}"); return None
    nc = netcdf_file(str(f), "r", mmap=True)
    try:
        x = np.array(nc.variables["x"][:], dtype=np.float64)
        y = np.array(nc.variables["y"][:], dtype=np.float64)
        tv = np.array(nc.variables["time"][:], dtype=np.float64)
        vol3 = np.array(nc.variables["volumes"][:], dtype=np.int64)
        # sww 的 x/y 是相对 xllcorner/yllcorner 的偏移
        x0o = float(getattr(nc, "xllcorner", 0.0))
        y0o = float(getattr(nc, "yllcorner", 0.0))
        vx, vy = x + x0o, y + y0o
        if USE_CENTROID and "stage_c" in nc.variables:
            z = np.array(nc.variables["elevation_c"][:], dtype=np.float64)
            st = nc.variables["stage_c"]
            X = vx[vol3].mean(axis=1)
            Y = vy[vol3].mean(axis=1)
            # 每个三角单元的真实面积 —— 淹没面积按它加权，和耦合脚本一致；
            # **不用格子数 × 格面积**：15 m 格里平均有 4 个单元，只要一个湿整格就算湿，会高估。
            ax, ay = vx[vol3[:, 0]], vy[vol3[:, 0]]
            bx, by = vx[vol3[:, 1]], vy[vol3[:, 1]]
            cx, cy = vx[vol3[:, 2]], vy[vol3[:, 2]]
            areas = 0.5 * np.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))
        else:
            z = np.array(nc.variables["elevation"][:], dtype=np.float64)
            st = nc.variables["stage"]
            X, Y, areas = vx, vy, None
        gx0, gy0, nx, ny = grid_of(X, Y, CELL)
        col = np.clip(((X - gx0) / CELL).astype(int), 0, nx - 1)
        row = np.clip(((Y - gy0) / CELL).astype(int), 0, ny - 1)
        flat = row * nx + col

        # 先**全时刻**扫一遍求峰值，再抽帧显示。
        # 均匀抽样会漏掉真峰：L1 有 530 个时刻，抽 39 帧就是每 14 个取一个，
        # 峰值落在采样点之间时报出来的数会偏小（实测 15.38 ha vs 真值 16.03 ha，差 4%），
        # 而这个数要和四态表对得上，差 4% 就会被当成两套数互相矛盾。
        n_t = len(tv)
        series = []
        for it in range(n_t):
            dep_i = np.maximum(np.array(st[it, :], dtype=np.float64) - z, 0.0)
            dep_i[dep_i < DRY_M] = 0.0
            wa = (float((dep_i > 0).dot(areas)) / 1e4 if areas is not None
                  else float((dep_i > 0).sum()) * CELL * CELL / 1e4)
            series.append((wa, float(dep_i.max())))
        i_area = int(max(range(n_t), key=lambda k: series[k][0]))
        i_dep = int(max(range(n_t), key=lambda k: series[k][1]))
        true_a, true_ta = series[i_area][0], float(tv[i_area]) / 3600.0
        true_d, true_td = series[i_dep][1], float(tv[i_dep]) / 3600.0

        step = max(1, int(np.ceil(n_t / n_frames)))
        # 峰值面积、峰值水深那两帧**必须在播放序列里**，否则播到头也看不到最淹的那一刻
        picks = sorted(set(list(range(0, n_t, step)) + [i_area, i_dep, n_t - 1]))

        d = OUT / label.replace("+", "p")
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("f*.png"):
            old.unlink()

        tr_utm = from_origin(gx0, gy0 + ny * CELL, CELL, CELL)
        dst_tr, dw, dh = calculate_default_transform(
            "EPSG:32640", "EPSG:4326", nx, ny,
            left=gx0, bottom=gy0, right=gx0 + nx * CELL, top=gy0 + ny * CELL)

        frames, peak_a, peak_d, peak_ta, peak_td = [], 0.0, 0.0, None, None
        for k, it in enumerate(picks):
            stg = np.array(st[it, :], dtype=np.float64)
            dep = np.maximum(stg - z, 0.0)
            dep[dep < DRY_M] = 0.0
            # **数从单元算，图从格子出** —— 两者不能混：
            # 面积按三角单元真实面积加权（和 couple2way 一致），格子只负责显示。
            wet_ha = (float((dep > 0).dot(areas)) / 1e4 if areas is not None
                      else float((dep > 0).sum()) * CELL * CELL / 1e4)
            dmax = float(dep.max())
            g = np.zeros(nx * ny, dtype=np.float32)
            np.maximum.at(g, flat, dep.astype(np.float32))   # 按格取最大，别把窄沟抹平
            g = g.reshape(ny, nx)[::-1]                      # 行序翻成北在上
            th = float(tv[it]) / 3600.0
            if wet_ha > peak_a: peak_a, peak_ta = wet_ha, th
            if dmax > peak_d: peak_d, peak_td = dmax, th

            warped = np.zeros((dh, dw), dtype=np.float32)
            reproject(source=g, destination=warped,
                      src_transform=tr_utm, src_crs="EPSG:32640",
                      dst_transform=dst_tr, dst_crs="EPSG:4326",
                      resampling=Resampling.max, src_nodata=0, dst_nodata=0)
            Image.fromarray(ramp(warped), "RGBA").save(d / f"f{k:03d}.png", optimize=True)
            frames.append({"t_h": round(th, 3), "wet_ha": round(wet_ha, 2),
                           "dmax_m": round(dmax, 3), "png": f"{d.name}/f{k:03d}.png"})

        w, s_, e, n_ = (dst_tr.c, dst_tr.f + dh * dst_tr.e,
                        dst_tr.c + dw * dst_tr.a, dst_tr.f)
        mb = sum(p.stat().st_size for p in d.glob("f*.png")) / 1048576
        log(f"  {label:<6}{len(frames):>3} 帧 / {n_t} 时刻　"
            f"峰值 {true_a:,.2f} ha @ {true_ta:.2f} h"
            f"　最深 {true_d:.3f} m @ {true_td:.2f} h　{mb:.2f} MB")
        if abs(peak_a - true_a) > 0.01:
            log(f"         （抽样帧里最大只有 {peak_a:,.2f} ha —— "
                f"峰值那一帧已强制加入序列）")
        return {"state": label, "dir": d.name, "frames": frames,
                "bounds": [[round(s_, 6), round(w, 6)], [round(n_, 6), round(e, 6)]],
                "peak_wet_ha": round(true_a, 2), "peak_wet_t_h": round(true_ta, 3),
                "peak_depth_m": round(true_d, 3), "peak_depth_t_h": round(true_td, 3),
                "peak_frame": picks.index(i_area), "deep_frame": picks.index(i_dep),
                "n_t_total": n_t, "n_frames": len(frames)}
    finally:
        nc.close()


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="把 .sww 抽成逐时刻水深帧")
    ap.add_argument("--state", default=None, help="只出某一态，如 L1")
    ap.add_argument("--frames", type=int, default=MAX_FRAMES)
    a = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"抽帧：格距 {CELL:g} m　干湿阈值 {DRY_M:g} m　色标 0–{VMAX:g} m（三态共用）")
    items = []
    for label, tag in STATES.items():
        if a.state and label != a.state:
            continue
        r = one(tag, label, a.frames)
        if r:
            items.append(r)
    if not items:
        print("什么都没出"); return 1
    # 只重出一态时要**并进**已有的 manifest，不能整份覆盖 —— 否则另外两态的播放直接没了
    # （2026-09-26 重跑 L2+B 时就这样丢过一次，test_flood2d_frames 抓到的）。
    old = OUT / "manifest.json"
    if a.state and old.exists():
        keep = [x for x in json.loads(old.read_text(encoding="utf-8")).get("states", [])
                if x["state"] != a.state]
        items = sorted(keep + items, key=lambda x: list(STATES).index(x["state"]))

    mf = {"region": "Mussafah_00", "scen": "61mm/2h",
          "cell_m": CELL, "dry_m": DRY_M, "vmax_m": VMAX,
          "centroid": USE_CENTROID,
          "note": ("水深 = stage_c − elevation_c（**形心**量，不是顶点量：顶点是限制器"
                   "重构出来的，同一场的最大水深顶点值比形心值低一半左右）。"
                   f"低于 {DRY_M:g} m 算干 —— 和耦合脚本 couple2way.py 的 "
                   "`wet = (h > 0.02).dot(areas)` 同一口径，所以图上的范围和四态表对得上。"
                   "淹没面积按三角单元真实面积加权（不是格子数×格面积）；"
                   "分母是 1,453 ha 的二维计算域，不是 82 ha 的汇水区。"),
          "l2_note": "L2（已建成塘）区内 0 座，该态 ≡ L1，不单独出帧",
          "states": items}
    (OUT / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False), encoding="utf-8")
    print(f"\n→ {OUT / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
