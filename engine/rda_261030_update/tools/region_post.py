# -*- coding: utf-8 -*-
"""一个场景算完后的后处理与成果导出（L1）：
  stats        全域统计：最大水深、面积、峰值积水量、最深点、一二维水量对账（来自耦合脚本的 npz / 运行记录 / SWMM 报告）
  frames       逐帧图（flood-viewer 读）→ <NAS>/result/<主机>/runtime/console_data/flood2d_ad_<区域>_m<雨量>h<历时>g<网格>/
  delivery     交付目录（management 入库读，英文布局同 ADM 全市改造后数据）→ <NAS>/deliveries/<job_id>/
               L1_existing_facilities/{1D,2D}、common/、statistics/、params.json、ponding_ge.json
  sww_shp_dat  npz → .sww → shp + dat（DTS Cloud h2d）→ <NAS>/shp_dat/<job_id>/L1/{shp,dat} + index.json（在容器里进程内转换，不再 docker 套 docker）
每一步失败只记录、不丢前面的成果；失败的步骤写进返回的 problems，run_region.py 会标到进度里。
逐帧图的色标、干湿阈值与 adm100a_frames.py 相同（0.02 m 算干，< 0.10 m 不出图，开方取色 0–1.4 m）。
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import model_rules as MR  # noqa: E402

DRY_M, SHOW_M, VMAX = 0.02, 0.10, 1.40
TH = (0.10, 0.30)
DIFF = None
STATE_DIR = "L1_existing_facilities"          # 缺省（L1）；L2 / L2planned 见 STATE_DIRS
STATE_DIRS = {"L1": "L1_existing_facilities", "L2": "L2_temporary_facilities", "L2planned": "L2planned_planned_facilities"}
STATE_LABEL = {"L1": "L1（永久设施，ASSET_STAGE = Existing）", "L2": "L2（L1 + 临时设施，ASSET_STAGE = Temporary）", "L2planned": "L2planned（L2 + 计划设施，ASSET_STAGE = Planned）"}


# ------------------------------------------------------------------ 统计
def report_numbers(rpt: Path) -> dict:
    """SWMM 报告里的一维连续性误差（%）。"""
    if not rpt.is_file():
        return {}
    m = re.search(r"Continuity Error \(%\)\s*\.*\s*(-?[\d.]+)", rpt.read_text(encoding="utf-8", errors="replace"))
    return {"Continuity Error (%)": float(m.group(1))} if m else {}


def stats(z, tw: dict, rpt: Path, sim_h: float, wall_s) -> dict:
    xy, area, hm, fr, th = z["xy"].astype(float), z["area"].astype(float), z["hmax"].astype(float), z["frames"], np.asarray(z["t_h"], float)
    elev = z["elev"].astype(float)
    allv = np.array([float((np.asarray(fr[i], dtype=np.float64) * area).sum()) for i in range(len(th))])
    ip = int(allv.argmax())
    zone = {"max_depth_m": round(float(hm.max()), 3), "area_ge_0p10_ha": round(float(area[hm >= TH[0]].sum()) / 1e4, 2), "area_ge_0p30_ha": round(float(area[hm >= TH[1]].sum()) / 1e4, 2),
            "peak_vol_m3": round(float(allv[ip])), "peak_vol_t_h": round(float(th[ip]), 2), "domain_area_ha": round(float(area.sum()) / 1e4, 1), "n_cells": int(len(xy))}
    k = int(hm.argmax())
    deepest = {"depth_m": round(float(hm[k]), 2), "x": round(float(xy[k][0])), "y": round(float(xy[k][1])), "ground_m": round(float(elev[k]), 2), "near_hotspot": None, "near_dist_m": None}
    acc, rc = tw["acc_m3"], report_numbers(rpt)
    balance = {"1D to 2D (manhole flooding handed to 2D), m3": round(acc["1to2"]), "2D to 1D (inflow through manholes / inlets), m3": round(acc["2to1"]),
               "rain falling directly on 2D, m3": round(acc.get("rain", 0)), "taken into ponds / soakaways from 2D, m3": round(acc.get("pond", 0)),
               f"retained in the 2D domain at {sim_h:g} h, m3": round(tw["final_vol_m3"]), "boundary outflow (residual), m3": round(tw["boundary_out_m3"]),
               "1D: continuity error, %": rc.get("Continuity Error (%)"), "wall_clock_h": round(float(wall_s) / 3600, 2) if wall_s else None}
    return {"zone": zone, "deepest": deepest, "balance": balance}


# ------------------------------------------------------------------ 多态合并（同一个场景的几个态共用一个逐帧图目录 / shp_dat 目录）
STATE_ORDER = ["L1", "L2", "L2planned"]


def _ordered(keys):
    return [k for k in STATE_ORDER if k in keys] + [k for k in keys if k not in STATE_ORDER]


def _merge_viewer(out_dir: Path, man: dict, cs: dict, sj: dict, s: str) -> None:
    """flood-viewer 的 manifest.json / ad_stats.json / stats.json：已有别的态就合并进去，文件锁防并行覆盖；stats.json 最后写（= 标记完成）。"""
    import fcntl
    with open(out_dir / ".viewer.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        mp, cp, sp = out_dir / "manifest.json", out_dir / "ad_stats.json", out_dir / "stats.json"
        if mp.is_file():
            old = json.loads(mp.read_text(encoding="utf-8"))
            sts = {x["state"]: x for x in old.get("states", [])}
            sts[s] = man["states"][0]
            man["states"] = [sts[k] for k in _ordered(list(sts))]
        if cp.is_file():
            old = json.loads(cp.read_text(encoding="utf-8"))
            ks = _ordered(list(dict.fromkeys([*old.get("states", []), s])))
            cs["states"] = ks
            cs["state_names"] = {**old.get("state_names", {}), **cs["state_names"]}
            for fld in ("deepest", "balance"):
                cs[fld] = {**old.get(fld, {}), **cs[fld]}
            z = old.get("zones", {}).get("全域|all", {})
            cs["zones"] = {"全域|all": {**z, **cs["zones"]["全域|all"]}}
        if sp.is_file():
            old = json.loads(sp.read_text(encoding="utf-8"))
            ks = _ordered(list(dict.fromkeys([*old.get("states", []), s])))
            sj = {**{k: v for k, v in old.items() if k in ks and k != s}, **sj, "states": ks}
        cp.write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
        mp.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        sp.write_text(json.dumps(sj, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ 逐帧图
def ramp(v):
    n = np.clip(v / VMAX, 0.0, 1.0)
    ncol = np.sqrt(n)
    stops = np.array([[125, 211, 252], [37, 130, 235], [29, 78, 216], [76, 29, 149]], dtype=np.float32)
    pos = np.linspace(0.0, 1.0, len(stops))
    rgb = np.empty(n.shape + (3,), dtype=np.float32)
    for c in range(3):
        rgb[..., c] = np.interp(ncol, pos, stops[:, c])
    a = np.where(v >= SHOW_M, 110 + 145 * np.sqrt(n), 0)
    out = np.zeros(n.shape + (4,), np.uint8)
    out[..., :3] = rgb.astype(np.uint8)
    out[..., 3] = np.clip(a, 0, 255).astype(np.uint8)
    return out


def _grid(xy, cell):
    x0, y0 = float(xy[:, 0].min()), float(xy[:, 1].min())
    nx = int(np.ceil((xy[:, 0].max() - x0) / cell)) + 1
    ny = int(np.ceil((xy[:, 1].max() - y0) / cell)) + 1
    col = np.clip(((xy[:, 0] - x0) / cell).astype(int), 0, nx - 1)
    row = np.clip(((xy[:, 1] - y0) / cell).astype(int), 0, ny - 1)
    return x0, y0, nx, ny, row * nx + col


def _warp(g, x0, y0, nx, ny, cell):
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import calculate_default_transform, reproject
    tr = from_origin(x0, y0 + ny * cell, cell, cell)
    dtr, dw, dh = calculate_default_transform("EPSG:32640", "EPSG:4326", nx, ny, left=x0, bottom=y0, right=x0 + nx * cell, top=y0 + ny * cell)
    w = np.zeros((dh, dw), np.float32)
    reproject(source=g, destination=w, src_transform=tr, src_crs="EPSG:32640", dst_transform=dtr, dst_crs="EPSG:4326", resampling=Resampling.max, src_nodata=0, dst_nodata=0)
    return w, [[round(dtr.f + dh * dtr.e, 6), round(dtr.c, 6)], [round(dtr.f, 6), round(dtr.c + dw * dtr.a, 6)]]


def render(geo, val, cell, path):
    from PIL import Image
    x0, y0, nx, ny, idx = geo
    g = np.zeros(nx * ny, np.float32)
    np.maximum.at(g, idx, np.where(val >= SHOW_M, val, 0.0).astype(np.float32))
    w, b = _warp(g.reshape(ny, nx)[::-1], x0, y0, nx, ny, cell)
    Image.fromarray(ramp(w), "RGBA").save(path, optimize=True)
    return b


def frames(z, tw: dict, st: dict, D_wgs, out_dir: Path, meta: dict, res: float, log=print, state: str = "L1") -> Path:
    """逐帧图 + manifest.json / ad_stats.json / stats.json（flood-viewer 约定；stats.json 最后写 = 标记完成）。"""
    xy, area, hm, fr, th = z["xy"].astype(float), z["area"].astype(float), z["hmax"].astype(float), z["frames"], np.asarray(z["t_h"], float)
    geo = _grid(xy, res)
    s = state
    d = out_dir / s
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.png"):
        old.unlink()
    wet, wet10, dep, fl, bounds = np.zeros(len(th)), np.zeros(len(th)), np.zeros(len(th)), [], None
    for i in range(len(th)):
        f = fr[i].astype(float)
        wet[i] = area[f >= DRY_M].sum(); wet10[i] = area[f >= TH[0]].sum(); dep[i] = f.max()
        bounds = render(geo, f, res, d / f"f{i:03d}.png")
    dmax = np.maximum.accumulate(dep)
    for i in range(len(th)):
        fl.append({"t_h": round(float(th[i]), 3), "wet_ha": round(wet[i] / 1e4, 2), "wet10_ha": round(wet10[i] / 1e4, 2), "dmax_m": round(float(dmax[i]), 3), "png": f"{s}/f{i:03d}.png"})
    render(geo, hm, res, d / "max.png")
    ip, idp = int(wet10.argmax()), int(dep.argmax())
    man = {"job_id": meta["job_id"], "grid_m": res, "grid_label": f"{res:g} m", "region": f"{meta['title']}（{meta['domain_km2']} km²）", "scen": f"{meta['rain_mm']:g}mm/{meta['dur_h']:g}h", "dry_m": DRY_M, "show_m": SHOW_M, "vmax_m": VMAX,
           "note": "形心水深，< 0.02 m 算干；面积按三角单元真实面积加权；< 0.10 m 不出图",
           "states": [{"state": s, "dir": s, "bounds": bounds, "frames": fl, "max_png": f"{s}/max.png", "peak_wet10_ha": round(wet10[ip] / 1e4, 2), "peak_wet10_t_h": round(float(th[ip]), 3),
                       "peak_depth_m": round(float(hm.max()), 3), "peak_frame": ip, "deep_frame": idp, "n_frames": len(fl)}]}
    cs = {"job_id": meta["job_id"], "name": meta["name"], "tag": meta["tag"], "region": meta["region"], "title": meta["title"], "rain_mm": meta["rain_mm"], "dur_h": meta["dur_h"],
          "sim_h": meta["sim_h"], "res_m": res, "grid_m": res, "grid_label": f"{res:g} m", "states": [s], "state_names": {s: s}, "zones": {"全域|all": {s: st["zone"]}}, "deepest": {s: st["deepest"]}, "balance": {s: st["balance"]}, "diffs": [],
          "koujing": {"水深": "三角单元形心水深（ANUGA stage − elevation）", "干湿": "< 0.02 m 算干；< 0.10 m 不出图", "积水面积": "全程最大水深 ≥ 0.10 / ≥ 0.30 m 的单元面积之和（按三角单元真实面积）",
                      "最大积水量": "逐帧（每 5 分钟）Σ 水深 × 单元面积 的最大值", "全域": "二维计算域", "说明": f"{STATE_LABEL.get(s, s)}；二维网格 {res:g} m。没有设施清单、雨型图、热点过程线，对应区块为空"}}
    _merge_viewer(out_dir, man, cs, {"name": meta["name"], "grid_m": res, "states": [s], "domain_wgs": D_wgs, s: {"n_tri": tw.get("n_tri"), "n_nodes": tw.get("n_nodes"), "end_h": tw.get("end_h"), "res_m": res}}, s)
    log(f"  逐帧图 {len(fl)} 帧 → {out_dir}")
    return out_dir


# ------------------------------------------------------------------ 交付目录
def ensure_sww(name: str, work: Path, state: str = "L1", grid: int = 15) -> Path:
    """npz → .sww（ANUGA 重建同一张三角网）。已有就直接用；交付要带它（management 的模拟积水点读网格邻接 / 顶点），shp+dat 也用它。"""
    tg = f"{name}_{state}"
    sww = work / "c2d" / "anuga_out" / f"{tg}.sww"
    if not sww.is_file():
        r = subprocess.run([sys.executable, "-u", str(HERE / "npz_to_sww.py"), name, state, str(grid)], cwd=str(work / "c2d"), capture_output=True, text=True)
        if r.returncode != 0 or not sww.is_file():
            raise RuntimeError("npz → sww 失败：" + (r.stderr or r.stdout)[-400:])
    return sww


def write_domain(root: Path, domain, title: str, region: str | None = None, log=print) -> None:
    """页面地图上的「汇水区」→ common/domain.geojson（management 的 region_catchment 读它）。
    优先用区域自己的汇水区边界 data/regions/<区域>/domain.geojson（客户给的，经纬度）；没有才退回实际二维计算域（全市交付就是这样退的）。"""
    src = HERE.parent / "data" / "regions" / region / "domain.geojson" if region else None
    if src is not None and src.is_file():
        gj = json.loads(src.read_text(encoding="utf-8"))
        feats = [f for f in gj.get("features", []) if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")]
        if feats:
            (root / "common" / "domain.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
            log(f"  汇水区边界 {src.name}（{len(feats)} 个面）→ common/domain.geojson")
            return
    from pyproj import Transformer
    tr = Transformer.from_crs(32640, 4326, always_xy=True)
    polys = list(domain.geoms) if domain.geom_type == "MultiPolygon" else [domain]
    ring = lambda r: [[round(lo, 6), round(la, 6)] for lo, la in (tr.transform(x, y) for x, y in r.coords)]  # noqa: E731
    coords = [[ring(pg.exterior)] + [ring(i) for i in pg.interiors] for pg in polys]
    geom = {"type": "Polygon", "coordinates": coords[0]} if len(coords) == 1 else {"type": "MultiPolygon", "coordinates": coords}
    (root / "common" / "domain.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": title, "area_km2": round(domain.area / 1e6, 3), "source": "二维计算域（没有汇水区边界文件，退回用它）"}, "geometry": geom}]}, ensure_ascii=False), encoding="utf-8")
    log(f"  没有汇水区边界文件，用二维计算域 {domain.area / 1e6:.2f} km² → common/domain.geojson")


def write_hotspots(root: Path, domain, region: str | None = None, log=print) -> None:
    """客户关注的积水点（506 个，engine/data/hotspots/adm506_hotspots.json）：落在二维计算域内的写 common/hotspots.json（management 当「关注积水点」算覆盖），
    全部 506 个写 common/hotspots_all.json（域外的只列不算，覆盖率分母 = 506）。"""
    from shapely.geometry import Point
    allp = json.loads((HERE.parent / "data" / "hotspots" / "adm506_hotspots.json").read_text(encoding="utf-8"))
    ids = None
    if region:
        import tomllib
        rt = HERE.parent / "data" / "regions" / region / "region.toml"
        ids = (tomllib.loads(rt.read_text(encoding="utf-8")).get("hotspots") or {}).get("ids") if rt.is_file() else None
    inside = [h for h in allp if h["hotspot_id"] in ids] if ids else [h for h in allp if domain.contains(Point(h["x"], h["y"]))]
    (root / "common" / "hotspots.json").write_text(json.dumps(inside, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "common" / "hotspots_all.json").write_text(json.dumps(allp, ensure_ascii=False), encoding="utf-8")
    log(f"  客户热点 {len(allp)} 个，" + (f"按 region.toml [hotspots] 指定 {len(inside)} 个" if ids else f"落在二维计算域内 {len(inside)} 个"))


def delivery(root: Path, name: str, tag: str, work: Path, st: dict, meta: dict, reg: dict, params_json: Path | None, log=print, state: str = "L1", domain=None, region: str | None = None) -> Path:
    c2d = work / "c2d"
    tg = f"{name}_{state}"
    sd = root / STATE_DIRS.get(state, STATE_DIR)
    (sd / "1D").mkdir(parents=True, exist_ok=True); (sd / "2D").mkdir(exist_ok=True)
    (root / "common").mkdir(exist_ok=True); (root / "statistics").mkdir(exist_ok=True)
    shutil.copyfile(work / f"{tg}.inp", sd / "1D" / f"{tg}.inp")
    shutil.copyfile(c2d / f"{tg}_2way.rpt", sd / "1D" / f"{tg}_1D_report.rpt")
    shutil.copyfile(c2d / f"{tg}_2d.npz", sd / "2D" / f"{tg}_2d.npz")
    shutil.copyfile(c2d / f"twoway_{tg}.json", sd / "2D" / f"{state}_coupled_run_record.json")
    (root / "common" / "model_inputs").mkdir(exist_ok=True)
    for f in (c2d / f"pilot_{tg}.json", c2d / f"dem_{name}.tif", c2d / f"surf_{name}.tif"):   # 二维配置、地形、下垫面：以后在这次的基础上加 L2 sugg / L3 要用
        if f.is_file():
            shutil.copyfile(f, root / "common" / "model_inputs" / f.name)
    sww = ensure_sww(name, work, state, int(meta["res_m"]))
    shutil.copyfile(sww, sd / "2D" / f"{tg}_2D.sww")                                       # 交付必须带 .sww：模拟积水点 / 圆内积水量要它的三角网
    if domain is not None:
        write_domain(root, domain, meta["title"], region, log)
        write_hotspots(root, domain, region, log)
    else:
        (root / "common" / "hotspots.json").write_text("[]", encoding="utf-8")
    (root / "common" / f"inputs_{tag}_{state}.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    z = st["zone"]
    rows = [["region", "metric", state], [meta["title"], "domain max depth (m)", z["max_depth_m"]], [meta["title"], "flooded area >=0.10 m (ha)", z["area_ge_0p10_ha"]],
            [meta["title"], "flooded area >=0.30 m (ha)", z["area_ge_0p30_ha"]], [meta["title"], "peak flood volume (m3)", z["peak_vol_m3"]], [meta["title"], "time of peak flood volume (h)", z["peak_vol_t_h"]],
            [meta["title"], "2D domain area (ha)", z["domain_area_ha"]], [meta["title"], "deepest cell depth (m)", st["deepest"]["depth_m"]]]
    rows += [["domain", f"water balance: {k}", v] for k, v in st["balance"].items()]
    with open(root / "statistics" / "summary.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    (root / "statistics" / "run_stats.json").write_text(json.dumps({"job_id": meta["job_id"], "name": name, "tag": tag, "rain_mm": meta["rain_mm"], "dur_h": meta["dur_h"], "sim_h": meta["sim_h"],
                                                                    "grid_m": meta["res_m"], "zones": {state: z}, "deepest": {state: st["deepest"]}, "balance": {state: st["balance"]}}, ensure_ascii=False, indent=1), encoding="utf-8")
    if params_json and params_json.is_file() and params_json.resolve() != (root / "params.json").resolve():
        shutil.copyfile(params_json, root / "params.json")
    (root / f"GRID_{meta['res_m']:g}m.txt").write_text(f"二维网格精度 {meta['res_m']:g} m（非结构三角网的名义精度）\n", encoding="utf-8")   # 标记：本次成果用的网格精度
    unm = reg.get("facilities", {}).get("unmodeled") or []
    unm_txt = "".join(f"  - {u['item']}（{u['stage']}）：{u['reason']}\n" for u in unm if u.get("stage") in ("Temporary", "Planned"))
    (root / "00_README.md").write_text(f"# {meta['job_id']}\n\n{meta['title']} · {STATE_LABEL.get(state, state)} · {meta['rain_mm']:g} mm / {meta['dur_h']:g} h · **二维网格精度 {meta['res_m']:g} m**\n\n"
                                       + (f"**本态里数据缺参数、未建模的设施：**\n{unm_txt}\n" if unm_txt else "")
                                       + f"- `{STATE_DIRS.get(state, STATE_DIR)}/1D`：SWMM 输入与报告；`2D`：逐帧水深 npz、三角网 .sww 与耦合运行记录\n- `params.json`：本次推演的全部参数（规则 + 输入登记 + 一致性核对）\n"
                                       "- `ponding_ge.json`：水深 ≥ 0.15 m 的积水量\n- `statistics/`：统计；`common/`：输入登记\n" + f"- `GRID_{meta['res_m']:g}m.txt`：网格精度标记\n", encoding="utf-8")
    log(f"  交付目录 → {root}")
    return root


# ------------------------------------------------------------------ npz → sww → shp + dat
def sww_shp_dat(name: str, work: Path, shp_job_dir: Path, job_id: str, run_id=None, log=print, state: str = "L1", grid: int = 15) -> dict:
    """在容器里进程内转换：npz_to_sww.py（ANUGA）→ hydro2d_convert.py（需要 pyshp）。"""
    sww = ensure_sww(name, work, state, grid)
    out = shp_job_dir / state
    if (out / "h2d_index.json").is_file():
        log("  shp+dat 已转过，跳过")
    else:
        part = shp_job_dir / f"{state}.partial"
        shutil.rmtree(part, ignore_errors=True)
        shp_job_dir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([sys.executable, "-u", str(HERE / "h2d" / "hydro2d_convert.py"), str(sww), "-o", str(part), "--name", "boundary", "--src-crs", "EPSG:32640", "--no-csv"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("sww → shp+dat 失败：" + (r.stderr or r.stdout)[-400:])
        shutil.rmtree(out, ignore_errors=True)
        part.rename(out)
    import fcntl
    with open(shp_job_dir / ".index.lock", "w") as lk:      # 几个态共用 index.json：加锁合并
        fcntl.flock(lk, fcntl.LOCK_EX)
        ip = shp_job_dir / "index.json"
        sts = json.loads(ip.read_text(encoding="utf-8")).get("states", {}) if ip.is_file() else {}
        sts[state] = {"dir": state, "source_sww": sww.name, "converted": (out / "h2d_index.json").is_file()}
        ip.write_text(json.dumps({"job_id": job_id, "delivery": job_id, "run_id": run_id, "src_crs": "EPSG:32640", "args": ["--no-csv"],
                                  "grid_m": grid, "states": {k: sts[k] for k in _ordered(list(sts))}}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  shp+dat → {out}")
    return {"dir": str(out)}


# ------------------------------------------------------------------ 成果位置记录（页面「成果数据位置」读）
def write_outputs(root: Path, meta: dict, paths: dict, files: dict, extra: dict, log=print) -> Path:
    """<交付目录>/outputs.json：这次推演的成果都在哪（相对 NAS 根目录的路径）、用什么数据和规则跑的、以后怎么接着做 L2 sugg / L3。
    management 入库时带到页面；入库后按 region 能找到同一汇水区之前所有推演。"""
    import datetime as dt
    d = {"schema": "flood_management.run_outputs.v1", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         **{k: meta[k] for k in ("job_id", "name", "tag", "title", "rain_mm", "dur_h", "sim_h", "res_m")}, "grid": {"res_m": meta["res_m"], "label": f"{meta['res_m']:g} m"},
         "region": meta["region"], "state": meta.get("state", "L1"), "states": [meta.get("state", "L1")],
         "host": meta.get("host"), "paths": paths, "files": files,
         "continue_from": {"base_state": meta.get("state", "L1"), "how": "L2 sugg / L3 在这次的 1D inp 和 common/model_inputs 里的 pilot / dem / surf 基础上追加设施继续推演；用同一区域的 data/regions/<区域>，规则同 params.json 里的版本"},
         **extra}
    p = root / "outputs.json"
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  成果位置记录 → {p}")
    return p
