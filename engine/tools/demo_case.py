# -*- coding: utf-8 -*-
"""快速验证：一个很小的合成算例，几分钟内跑通一二维耦合全链路并自动判定（2026-09-30，给换机器的人先验环境）。

不需要任何客户数据——地形、管网、雨全部由本脚本合成，可以放心入库、分发。用的是和 C1 **同一套**东西：
  一维 .inp 由 build_city1d.write_inp 写（同一个写法），产流 = SCS CN 82 + 单位线（同 build_c1），
  二维 = docker 镜像 anuga-swmm:1.0 里的 couple2way_v2.py（同一个耦合程序、同样的 C2D_DEM_RAW / C2D_SAVE_UV），
  后处理 = npz_to_tif.py（本机）+ npz_to_sww.py（容器），判定标准 = C1 的质检标准。

合成场地（UTM 40N，800 × 600 m，5 m 地形）：
  · 地面由西向东缓倾（6.0 → 4.0 m），西侧 (250 m, 300 m) 处一个 1.2 m 深的洼地 = 「热点」；
  · 一条东西向干管（10 口井，DN 300 → 500，故意偏小）排到东边界的自由出水口，外加一条南北支管；
  · 61 mm / 2 h 雨（对称峰型），模拟 3 h，10 m 网格（约 1.5 万个三角单元）。
两个态：
  L1   现状管网；
  L2P  L1 + 洼地北侧一座 3,000 m³ 的塘 + 一台 0.15 m³/s 的泵（从洼地旁的井抽进塘）——把塘收地表水、泵转输两条代码路径也测到。

用法：
  python tools/demo_case.py              # 生成 → 两态并行计算 → 后处理 → 判定，约 3–6 分钟；全部通过退出码 0
  python tools/demo_case.py --no-run     # 只生成模型文件（不起 docker；单元测试用）
工作目录：$SWBRAIN_DEMO_WORK（缺省 = $SWBRAIN_SWMM_WORK 旁边的 swbrain_demo，纯英文路径），与正式计算分开。
结果：<工作目录>/c2d/ 下的 npz / json / rpt，GeoTIFF 在 c2d/tif/，.sww 在 c2d/anuga_out/，判定表 out/demo/verify.json。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

NAME = "DEMO"
X0, Y0, LX, LY, CELL = 250000.0, 2700000.0, 800.0, 600.0, 5.0
HOT = (250.0, 300.0)                 # 洼地中心（相对坐标）
RAIN_MM, DUR_H, END_H, RES = 61.0, 2.0, 3.0, 10.0
IMAGE = "anuga-swmm:1.0"


def work_dir() -> pathlib.Path:
    if os.environ.get("SWBRAIN_DEMO_WORK"):
        return pathlib.Path(os.environ["SWBRAIN_DEMO_WORK"])
    base = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
    return base.parent / "swbrain_demo"


# ------------------------------------------------------------------ 合成
def ground(x, y):
    """相对坐标 → 地面高程 m：西高东低 + 洼地（高斯坑，深 1.2 m、半径约 60 m）+ 一条东西向路（比两侧低 0.15 m）。"""
    import numpy as np
    z = 6.0 - 2.0 * (np.asarray(x) / LX)
    z = z - 1.2 * np.exp(-(((np.asarray(x) - HOT[0]) ** 2 + (np.asarray(y) - HOT[1]) ** 2) / (2 * 35.0 ** 2)))
    z = z - 0.15 * (np.abs(np.asarray(y) - 300.0) < 10.0)
    return z


def build(W: pathlib.Path) -> dict:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    import build_city1d as B
    import runoff as R

    (W / "c2d").mkdir(parents=True, exist_ok=True)
    # 地形
    nx, ny = int(LX / CELL), int(LY / CELL)
    xs = (np.arange(nx) + 0.5) * CELL; ys = LY - (np.arange(ny) + 0.5) * CELL
    gx, gy = np.meshgrid(xs, ys)
    dem = ground(gx, gy).astype("float32")
    with rasterio.open(W / "c2d" / f"dem_{NAME}.tif", "w", driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs="EPSG:32640",
                       transform=from_origin(X0, Y0 + LY, CELL, CELL), nodata=-9999.0, compress="deflate") as ds:
        ds.write(dem, 1)
    # 管网：干管 J0..J9（x = 60..780），支管 K0..K2 接到 J3；出水口 OF1 在东边界
    pts = {f"J{i}": (60.0 + i * 80.0, 300.0) for i in range(10)}
    pts.update({"K0": (300.0, 520.0), "K1": (300.0, 440.0), "K2": (300.0, 360.0), "OF1": (795.0, 300.0)})
    xy = {k: (X0 + x, Y0 + y) for k, (x, y) in pts.items()}
    gz = {k: float(ground(x, y)) for k, (x, y) in pts.items()}
    cover = 1.5
    inv = {k: gz[k] - cover for k in pts}
    inv["OF1"] = min(inv["J9"] - 0.3, gz["OF1"] - 2.0)
    links = [(f"J{i}", f"J{i + 1}", 0.30 if i < 4 else 0.40 if i < 7 else 0.50) for i in range(9)] + [("J9", "OF1", 0.6), ("K0", "K1", 0.3), ("K1", "K2", 0.3), ("K2", "J3", 0.3)]
    pipes = []
    for n, (a, b, d) in enumerate(links):
        L = math.dist(pts[a], pts[b])
        z1, z2 = inv[a], inv[b]
        if z2 >= z1:
            z2 = z1 - 0.002 * L                          # 至少 2 ‰ 坡
            inv[b] = min(inv[b], z2)
        pipes.append({"id": f"c_{n}", "n1": a, "n2": b, "len": L, "z1": z1, "z2": z2, "d": d, "uid": ""})
    outs = {"OF1"}
    area = B.served_areas({k: v for k, v in xy.items() if k not in outs}, log=lambda *a: None)
    area["OF1"] = 0.0
    R_served = B.K_RADIUS * math.sqrt(sum(area.values()) / max(len(area) - 1, 1))
    # 雨：对称峰型（累计曲线 = 正态 CDF，峰在历时中点）→ SCS CN 82 净雨 → 单位线（同 build_c1）
    B.P_MM, B.DUR_H, B.SIM_H = RAIN_MM, DUR_H, END_H
    dt_h = 1.0 / 60.0
    shape = 0.5 * (1 + np.vectorize(math.erf)((np.linspace(0, 1, 121) - 0.5) / (0.18 * math.sqrt(2))))
    t, P_cum = R.hyetograph(RAIN_MM, DUR_H, dt_h, shape, 0.0, total_h=END_H + 1.0)
    exc = R.scs_cum_excess(P_cum, B.CN)
    q = np.convolve(np.diff(exc, append=exc[-1]), R.unit_hydrograph(B.TC_MIN, dt_h, 10_000.0), mode="full")[:len(t)]
    B.unit_runoff = lambda: [(f"{int(round(h * 60)) // 60:02d}:{int(round(h * 60)) % 60:02d}", float(v)) for h, v in zip(t, q)]
    meta0 = {"name": NAME, "box": (X0, Y0, X0 + LX, Y0 + LY), "R_served_m": R_served,
             "domain_polygon": [[X0 + 5, Y0 + 5], [X0 + LX - 5, Y0 + 5], [X0 + LX - 5, Y0 + LY - 5], [X0 + 5, Y0 + LY - 5]],
             "nodes": {k: [round(v[0], 2), round(v[1], 2)] for k, v in xy.items() if k not in outs},
             "rain_t_h": [round(float(x), 4) for x in t], "rain_excess_mm": [round(float(x), 4) for x in exc], "demo": True}
    files = {}
    # L1
    p1 = W / f"{NAME}_L1.inp"
    B.write_inp(p1, xy, gz, inv, pipes, outs, area, None, {"OF1": 0.6}, log=lambda *a: None)
    txt = p1.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", "THREADS  2", 1).replace(";; 全市一维管网（GDB 重建）", ";; 快速验证合成算例（tools/demo_case.py）", 1)
    p1.write_text(txt, encoding="utf-8")
    (W / "c2d" / f"pilot_{NAME}_L1.json").write_text(json.dumps({**meta0, "state": "L1", "ponds": []}, ensure_ascii=False), encoding="utf-8")
    files["L1"] = p1
    # L2P：塘（洼地北侧 60 × 50 m、深 1.0 m，塘底 = 轮廓内最低地面）+ 泵 J2 → POND1
    px0, py0, pw, ph = HOT[0] - 30.0, HOT[1] + 45.0, 60.0, 50.0
    zf = float(ground(np.linspace(px0, px0 + pw, 13)[:, None], np.linspace(py0, py0 + ph, 11)[None, :]).min())
    vol = pw * ph * 1.0
    poly = [[X0 + px0, Y0 + py0], [X0 + px0 + pw, Y0 + py0], [X0 + px0 + pw, Y0 + py0 + ph], [X0 + px0, Y0 + py0 + ph], [X0 + px0, Y0 + py0]]
    pxy = (X0 + px0 + pw / 2, Y0 + py0 + ph / 2)
    t2 = txt.replace("[CONDUITS]", f"[STORAGE]\nPOND1  {zf:.3f}  1.000  0  TABULAR  CRV_POND1  0  0  0\n\n[CONDUITS]", 1) if "[STORAGE]" not in txt else \
        txt.replace("[STORAGE]\n", f"[STORAGE]\nPOND1  {zf:.3f}  1.000  0  TABULAR  CRV_POND1  0  0  0\n", 1)
    t2 = t2.replace("[XSECTIONS]", "[PUMPS]\nP1  J2  POND1  PC_P1  ON  0.60  0.10\n\n[XSECTIONS]", 1)
    crv = f"PC_P1  PUMP2  0  0.1500\nPC_P1  100  0.1500\nCRV_POND1  STORAGE  0.000  {pw * ph:.1f}\nCRV_POND1  1.000  {pw * ph:.1f}\n"
    t2 = t2.replace("[CURVES]\n", "[CURVES]\n" + crv, 1) if "[CURVES]" in t2 else t2.replace("[TIMESERIES]", "[CURVES]\n" + crv + "\n[TIMESERIES]", 1)
    t2 = t2.replace("[COORDINATES]\n", f"[COORDINATES]\nPOND1  {pxy[0]:.2f}  {pxy[1]:.2f}\n", 1)
    p2 = W / f"{NAME}_L2P.inp"; p2.write_text(t2, encoding="utf-8")
    (W / "c2d" / f"pilot_{NAME}_L2P.json").write_text(json.dumps({**meta0, "state": "L2P", "ponds": [{"node": "POND1", "unitid": "POND1", "vol": vol, "xy": list(pxy),
                                                                                                     "footprint": {"type": "Polygon", "coordinates": [poly]}}]}, ensure_ascii=False), encoding="utf-8")
    files["L2P"] = p2
    for s in ("couple2way_v2.py", "npz_to_sww.py"):
        shutil.copy2(HERE / s, W / "c2d" / s)
    shutil.copy2(HERE / "chk_inp.py", W / "c2d" / "_chk_inp.py")
    return {"work": W, "inp": files, "rain_excess_mm": float(exc[-1]), "n_nodes": len(meta0["nodes"]), "pond_vol_m3": vol}


# ------------------------------------------------------------------ 计算与判定
def docker(W, *cmd, detach_name=None, threads=2):
    return ["docker", "run", "--rm", *(["-d", "--name", detach_name] if detach_name else []), "-e", f"OMP_NUM_THREADS={threads}", "-e", "C2D_DEM_RAW=1", "-e", "C2D_SAVE_UV=1",
            "-v", f"{os.environ.get('SWBRAIN_DEMO_WORK_HOST') or W.as_posix()}:/work", IMAGE, *map(str, cmd)]      # 部署包：宿主机路径


def run(W) -> dict:
    t0 = time.time()
    (W / "c2d" / "logs").mkdir(parents=True, exist_ok=True)
    r = subprocess.run(docker(W, "python", "/work/c2d/_chk_inp.py", f"{NAME}_L1", f"{NAME}_L2P", threads=1), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or "OK" not in r.stdout:
        raise SystemExit(f"读入检查没过：{(r.stdout + r.stderr)[-800:]}")
    procs = {}
    for s in ("L1", "L2P"):
        lg = open(W / "c2d" / "logs" / f"{NAME}_{s}.log", "w", encoding="utf-8")
        procs[s] = (subprocess.Popen(docker(W, "python", "-u", "/work/c2d/couple2way_v2.py", NAME, s, RES, END_H, 60), stdout=lg, stderr=subprocess.STDOUT), lg)
    for s, (p, lg) in procs.items():
        p.wait(); lg.close()
        if p.returncode:
            raise SystemExit(f"{s} 计算失败（退出码 {p.returncode}），看 {W / 'c2d' / 'logs' / f'{NAME}_{s}.log'}")
    t_run = time.time() - t0
    env = dict(os.environ, SWBRAIN_SWMM_WORK=str(W), PYTHONIOENCODING="utf-8")
    r1 = subprocess.run([sys.executable, str(HERE / "npz_to_tif.py"), NAME, "L1", "L2P"], cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    r2 = [subprocess.run(docker(W, "python", "-u", "/work/c2d/npz_to_sww.py", NAME, s, RES, threads=1), capture_output=True, text=True, encoding="utf-8", errors="replace") for s in ("L1", "L2P")]
    return {"t_run_s": round(t_run, 1), "tif_rc": r1.returncode, "tif_err": r1.stderr[-400:], "sww_rc": [x.returncode for x in r2], "sww_err": [x.stderr[-300:] for x in r2]}


def verify(W, post: dict) -> dict:
    import numpy as np
    C = W / "c2d"; rows = []

    def chk(name, ok, value, rule):
        rows.append({"check": name, "ok": bool(ok), "value": value, "rule": rule})

    res = {}
    for s in ("L1", "L2P"):
        tw = json.loads((C / f"twoway_{NAME}_{s}.json").read_text(encoding="utf-8"))
        z = np.load(C / f"{NAME}_{s}_2d.npz")
        rpt = (C / f"{NAME}_{s}_2way.rpt").read_text(encoding="utf-8", errors="replace")
        ce = [float(v) for v in re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", rpt)]
        fl = re.search(r"Flooding Loss \.+\s+([\d.]+)\s+([\d.]+)", rpt)
        n = len(tw.get("series") or []); want = int(round(END_H * 12))
        hmax = z["hmax"].astype(float); xy = z["xy"].astype(float)
        near = np.hypot(xy[:, 0] - (X0 + HOT[0]), xy[:, 1] - (Y0 + HOT[1])) < 40.0
        res[s] = {"tw": tw, "hot_hmax": float(np.nanmax(hmax[near])) if near.any() else None, "hmax": float(np.nanmax(hmax)), "nan": bool(np.isnan(hmax).any())}
        chk(f"{s} 算完（帧数）", n >= want, f"{n} / {want}", "每 5 分钟一帧，帧数够")
        chk(f"{s} 一维连续性误差", ce and abs(ce[-1]) < 2.0, f"{ce[-1]:.2f} %" if ce else "没读到", "< 2 %")
        one2 = tw["acc_m3"]["1to2"]
        flood = float(fl.group(2)) * 1000.0 if fl else None                 # CMS 单位：Flooding Loss 第二列是 10^6 ltr
        chk(f"{s} 二维结果合理", not res[s]["nan"] and 0.05 < res[s]["hmax"] < 5.0, f"最深 {res[s]['hmax']:.2f} m", "无 NaN，最深 0.05–5 m")
        chk(f"{s} 一维冒水交给了二维", one2 > 1.0, f"{one2:,.0f} m³", "> 0（合成管网故意偏小，洼地井要冒水）")
        if flood:
            d = abs(flood - one2) / max(flood, 1.0) * 100
            chk(f"{s} 一二维交换对账", d < 0.5, f"一维溢流 {flood:,.0f} / 交给二维 {one2:,.0f} m³，差 {d:.2f} %", "井全在域内：一维溢流 = 交给二维的水（差 < 0.5 %）")
    p = (res["L2P"]["tw"].get("ponds") or [{}])[0]
    chk("L2P 塘收到水", (p.get("vol_end_m3") or 0) > 1.0, f"塘末蓄水 {p.get('vol_end_m3') or 0:,.0f} m³（地表直接收 {p.get('surface_captured_m3') or 0:,.0f} m³）", "> 0")
    a, b = res["L1"]["hot_hmax"], res["L2P"]["hot_hmax"]
    chk("L2P 热点变浅", a is not None and b is not None and b < a, f"L1 {a:.3f} m → L2P {b:.3f} m" if a is not None and b is not None else "—", "塘 + 泵之后洼地最大水深下降")
    chk("GeoTIFF 导出", post.get("tif_rc") == 0 and (C / "tif" / f"{NAME}_L1_depth_max.tif").exists() and (C / "tif" / f"{NAME}_L2P_depth_max.tif").exists(), "c2d/tif/ 下两态的最大水深与逐帧 GeoTIFF" if post.get("tif_rc") == 0 else post.get("tif_err"), "npz_to_tif.py 成功")
    chk(".sww 导出", all(r == 0 for r in post.get("sww_rc", [1])) and (C / "anuga_out" / f"{NAME}_L1.sww").exists() and (C / "anuga_out" / f"{NAME}_L2P.sww").exists(), "c2d/anuga_out/ 下两态的 .sww" if all(r == 0 for r in post.get("sww_rc", [1])) else post.get("sww_err"), "npz_to_sww.py（容器）成功")
    return {"ok": all(r["ok"] for r in rows), "checks": rows, "t_run_s": post.get("t_run_s")}


def _proj_env():
    """系统里有两套 PROJ（如 PostGIS 自带的）时 rasterio 报 CRSError：指向 rasterio 自带的 proj_data（子进程也继承）。"""
    try:
        import importlib.util
        spec = importlib.util.find_spec("rasterio")
        pj = pathlib.Path(spec.origin).parent / "proj_data" if spec else None
        if pj and pj.exists():
            os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = str(pj)
    except Exception:
        pass


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    _proj_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-run", action="store_true", help="只生成模型文件")
    a = ap.parse_args(argv)
    W = work_dir()
    if any(ord(c) > 127 for c in str(W)):
        raise SystemExit(f"工作目录 {W} 有中文：SWMM 不认，设 SWBRAIN_DEMO_WORK 到纯英文路径")
    t0 = time.time()
    b = build(W)
    print(f"合成算例 → {W}：{b['n_nodes']} 口井、雨 {RAIN_MM:g} mm / {DUR_H:g} h（净雨 {b['rain_excess_mm']:.1f} mm）、塘 {b['pond_vol_m3']:,.0f} m³")
    if a.no_run:
        return 0
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        raise SystemExit(f"没有 docker 镜像 {IMAGE}：先 docker build -t {IMAGE} docker/anuga-swmm")
    print(f"两态并行计算（{RES:g} m 网格，模拟 {END_H:g} h）…", flush=True)
    post = run(W)
    v = verify(W, post)
    out = ROOT / "out" / "demo"; out.mkdir(parents=True, exist_ok=True)
    (out / "verify.json").write_text(json.dumps({**v, "work": str(W), "when": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in v["checks"]:
        print(f"  {'✓' if r['ok'] else '✗'} {r['check']:<16} {r['value']}（标准 {r['rule']}）")
    print(f"\n{'全部通过' if v['ok'] else '有未通过项'}：计算 {v['t_run_s']:.0f} s，总计 {time.time() - t0:.0f} s。判定表 out/demo/verify.json")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
