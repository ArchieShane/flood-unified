# -*- coding: utf-8 -*-
"""ADM city 100 m (post-improvement data) · L1 / L2 post-processing and delivery (runs inside the anuga-swmm container; numpy / scipy / rasterio / pyproj only; 2026-10-20, adapted from adm250_post.py).

One scenario = whole city x one storm, one state (L1 = existing pipe network + pumps that have flow data + ponds / soakaways).
Everything that is delivered (paths and contents) is English: no Chinese; the state directory and file names say L1.
  1. hotspots/hotspot_results.csv, hotspots/hotspot_layer.geojson   ADM hotspots inside the domain: max depth within 200 m, nearest-cell depth, flooded area (>=0.10 / >=0.30 m), peak volume, timing
  2. statistics/summary.csv, statistics/run_stats.json                    domain-wide max depth, flooded area, peak volume, 1D / 2D water balance
  3. L1_existing_facilities/1D, 2D                                         .inp, .rpt, .sww (moved from /work), .npz, max-depth GeoTIFF, coupled-run record
  4. common/                                                               domain, storage objects, pumps (built / not built), data usage audit, hotspots
  Delivery directory: $AD_RESULT_ROOT/$AD_HOST/ADM_City_<rain>mm<dur>h_100m_after/ (written on the NAS directly).
  The marker /work/c2d/af_stats_<tag>.json is written last, and only if a scan of the delivery finds no Chinese in any path or content.
  Not done: comparison with other states, per-frame pictures, shp + dat.

Usage (in the container): python /work/c2d/adm100a_post.py --tag CITY_M61H2 --res 100
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import pathlib
import re
import shutil
import sys
import time
import zipfile

import numpy as np

W = pathlib.Path("/work"); C2D = W / "c2d"
TH = (0.10, 0.30)          # flooding thresholds, m
HS_R = 200.0               # hotspot influence radius, m
DRY = 0.02
SNAME = {"L1": "L1", "L2": "L2"}
DIRS = {"L1": "L1_existing_facilities", "L2": "L2_with_ponds"}
CJK = re.compile("[⺀-鿿　-〿＀-￯]")
TEXT_EXT = {".md", ".csv", ".json", ".geojson", ".inp", ".rpt", ".txt", ".tsv", ".xml"}


def routing_continuity(rpt: pathlib.Path) -> dict:
    """SWMM report 'Flow Routing Continuity' (CMS model: the first column is 10^6 ltr = 1000 m3)."""
    t = rpt.read_text(errors="ignore")
    m = re.search(r"Flow Routing Continuity(.*?)\n\s*\n\s*\n", t, re.S) or re.search(r"Flow Routing Continuity(.*?)\*{5,}", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        k = re.match(r"\s*([A-Za-z][A-Za-z /()%]+?)\s*\.{2,}\s*(-?[\d.]+)(?:\s+(-?[\d.]+))?", ln)
        if k:
            out[k.group(1).strip()] = float(k.group(3)) * 1000.0 if k.group(3) else float(k.group(2))
    return out


def node_flooding(rpt: pathlib.Path) -> dict:
    t = rpt.read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            out[q[0]] = float(q[5]) * 1000.0
    return out


def cp(src: pathlib.Path, dst: pathlib.Path, done: list, miss: list, move: bool = False) -> None:
    if not src.exists():
        miss.append(src.name); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(src, dst)
    done.append(dst)


def write_csv(path: pathlib.Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows(rows)


def write_xlsx(path: pathlib.Path, rows: list) -> bool:
    try:
        import openpyxl
    except ImportError:
        return False
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return True


def max_depth_tif(z, res: float, out: pathlib.Path) -> None:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    pd = os.path.join(os.path.dirname(__import__("importlib.util").util.find_spec("rasterio").origin), "proj_data")
    os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = pd
    xy = z["xy"].astype(float); hm = z["hmax"].astype(float)
    x0 = np.floor(xy[:, 0].min() / res) * res; y1 = np.ceil(xy[:, 1].max() / res) * res
    nx = int(np.ceil((xy[:, 0].max() - x0) / res)) + 1; ny = int(np.ceil((y1 - xy[:, 1].min()) / res)) + 1
    col = np.clip(((xy[:, 0] - x0) / res).astype(int), 0, nx - 1); row = np.clip(((y1 - xy[:, 1]) / res).astype(int), 0, ny - 1)
    idx = row * nx + col
    inside = np.zeros(nx * ny, bool); inside[idx] = True
    g = np.zeros(nx * ny, np.float32)
    np.maximum.at(g, idx, np.where(hm >= DRY, hm, 0.0).astype(np.float32))
    g[~inside] = -9999.0
    with rasterio.open(out, "w", driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs=CRS.from_epsg(32640),
                       transform=from_origin(x0, y1, res, res), nodata=-9999.0, compress="deflate") as ds:
        ds.write(g.reshape(ny, nx), 1)
        ds.set_band_description(1, "max water depth over the run, m (0 = dry, < 0.02 m recorded as 0; nodata = outside the domain)")


def scan_no_cjk(root: pathlib.Path) -> list[str]:
    """Scan every path and every readable content in the delivery for Chinese characters. Returns the offending items."""
    bad = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if CJK.search(rel):
            bad.append(f"path: {rel}")
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        try:
            if ext in TEXT_EXT:
                with open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                    for i, ln in enumerate(fh):
                        if CJK.search(ln):
                            bad.append(f"content: {rel}:{i + 1}"); break
            elif ext == ".xlsx":
                with zipfile.ZipFile(p) as z:
                    for n in z.namelist():
                        if n.endswith(".xml") and CJK.search(html.unescape(z.read(n).decode("utf-8", "replace"))):
                            bad.append(f"content: {rel}!{n}"); break
            elif ext == ".npz":
                with zipfile.ZipFile(p) as z:
                    if any(CJK.search(n) for n in z.namelist()):
                        bad.append(f"content: {rel} (array names)")
            elif ext == ".sww":
                from scipy.io import netcdf_file
                f = netcdf_file(str(p), "r", mmap=True)
                try:
                    items = [str(k) for k in f.dimensions] + [str(k) for k in f.variables] + [str(getattr(f, a)) for a in dir(f) if not a.startswith("_") and isinstance(getattr(f, a, None), (str, bytes))]
                    for it in items:
                        if CJK.search(it):
                            bad.append(f"content: {rel} (netCDF attribute)"); break
                finally:
                    f.close()
            elif ext in (".tif", ".tiff"):
                import rasterio
                with rasterio.open(p) as ds:
                    tags = [str(v) for v in ds.descriptions] + [f"{k}{v}" for k, v in ds.tags().items()]
                    if any(CJK.search(t) for t in tags):
                        bad.append(f"content: {rel} (GeoTIFF tags)")
        except Exception as e:                                         # an unreadable file must not pass silently
            bad.append(f"unreadable: {rel} ({type(e).__name__})")
    return bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--res", type=float, default=100.0)
    a = ap.parse_args()
    from scipy.spatial import cKDTree
    t0 = time.time()
    t = a.tag; reg = t.split("_")[0]; name = f"AF_{t}"
    host = os.environ.get("AD_HOST") or "unknown"
    root = pathlib.Path(os.environ.get("AD_RESULT_ROOT", "/media/pop-fs/flood_data/result"))
    cases = C2D / "ad100a_cases"
    meta_all = json.loads((cases / "hotspots_ad.json").read_text(encoding="utf-8"))
    RG = meta_all["regions"][reg]
    HS = [h for h in meta_all["hotspots"] if h["region"] == reg]
    inp = json.loads((C2D / f"af_inputs_{t}.json").read_text(encoding="utf-8"))
    rain, dur = inp["rain_mm"], inp["dur_h"]
    states = [s for s in ("L1", "L2") if (C2D / f"twoway_{name}_{s}.json").exists() and (C2D / f"{name}_{s}_2d.npz").exists()]
    if len(states) < 2:
        raise SystemExit(f"{name}: {[s for s in ('L1', 'L2') if s not in states]} not finished")
    DD = root / host / f"{RG['dir']}_{rain:g}mm{dur:g}h_{a.res:g}m_after"
    DD.mkdir(parents=True, exist_ok=True)
    print(f"{name}: {len(HS)} hotspots; delivery directory {DD}", flush=True)
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in states}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in states}
    META = {s: json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")) for s in states}
    z0 = Z[states[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); elev = z0["elev"].astype(float)
    tr = cKDTree(xy)
    # ---- hotspots: cells within 200 m of each point
    cells = {}
    for h in HS:
        idx = np.array(tr.query_ball_point((h["x"], h["y"]), HS_R), dtype=np.int64)
        d0, i0 = tr.query((h["x"], h["y"]))
        cells[h["hotspot_id"]] = (idx, int(i0), float(d0))
    res_hs = {s: {} for s in states}; zones = {}; deepest = {}; balance = {}
    for s in states:
        z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"]; th = np.asarray(z["t_h"], float)
        dt_min = float(np.median(np.diff(th)) * 60.0) if len(th) > 1 else 5.0
        for h in HS:
            idx, i0, d0 = cells[h["hotspot_id"]]
            if len(idx) == 0:
                idx = np.array([i0])
            ts = np.asarray(fr[:, idx], dtype=np.float32)                       # (frames, cells)
            top = ts.max(axis=1); vol = (ts.astype(np.float64) * area[idx]).sum(axis=1)
            wet = np.flatnonzero(top >= TH[0])
            res_hs[s][h["hotspot_id"]] = {
                "max_depth_200m": round(float(hm[idx].max()), 3), "nearest_cell_depth": round(float(hm[i0]), 3), "nearest_cell_dist_m": round(d0, 1),
                "area_ge_0p10_ha": round(float(area[idx][hm[idx] >= TH[0]].sum()) / 1e4, 3), "area_ge_0p30_ha": round(float(area[idx][hm[idx] >= TH[1]].sum()) / 1e4, 3),
                "peak_vol_m3": round(float(vol.max())), "t_peak_h": round(float(th[int(top.argmax())]), 2),
                "t_first_ge_0p10_h": round(float(th[wet[0]]), 2) if len(wet) else None, "duration_ge_0p10_min": round(len(wet) * dt_min) if len(wet) else 0,
                "n_cells_200m": int(len(idx))}
        allv = np.zeros(len(th))
        for i in range(len(th)):                                               # sum of depth x area over the domain, frame by frame
            allv[i] = float((np.asarray(fr[i], dtype=np.float64) * area).sum())
        ip = int(allv.argmax())
        zones[s] = {"max_depth_m": round(float(hm.max()), 3), "area_ge_0p10_ha": round(float(area[hm >= TH[0]].sum()) / 1e4, 2),
                    "area_ge_0p30_ha": round(float(area[hm >= TH[1]].sum()) / 1e4, 2), "peak_vol_m3": round(float(allv[ip])), "peak_vol_t_h": round(float(th[ip]), 2),
                    "domain_area_ha": round(float(area.sum()) / 1e4, 1), "n_cells": int(len(xy))}
        k = int(hm.argmax()); px, py = xy[k]
        near = min(HS, key=lambda h: (h["x"] - px) ** 2 + (h["y"] - py) ** 2) if HS else None
        deepest[s] = {"depth_m": round(float(hm[k]), 2), "x": round(float(px)), "y": round(float(py)), "ground_m": round(float(elev[k]), 2),
                      "near_hotspot": near["hotspot_id"] if near else None,
                      "near_dist_m": round(float(np.hypot(px - near["x"], py - near["y"]))) if near else None}
        rpt = C2D / f"{name}_{s}_2way.rpt"
        nf = node_flooding(rpt) if rpt.exists() else {}; inside = META[s]["nodes"]
        rc = routing_continuity(rpt) if rpt.exists() else {}; acc = TW[s]["acc_m3"]
        balance[s] = {"1D to 2D (manhole flooding handed to 2D), m3": round(acc["1to2"]), "2D to 1D (inflow through manholes / inlets), m3": round(acc["2to1"]),
                      "rain falling directly on 2D, m3": round(acc.get("rain", 0)), "taken into ponds / soakaways from 2D, m3": round(acc.get("pond", 0)),
                      f"retained in the 2D domain at {inp['sim_h']:g} h, m3": round(TW[s]["final_vol_m3"]), "boundary outflow (residual), m3": round(TW[s]["boundary_out_m3"]),
                      "1D: manhole flooding inside the domain (= handed to 2D), m3": round(sum(v for n, v in nf.items() if n in inside)),
                      "1D: flooding at nodes outside the domain (1D only, counted as loss), m3": round(sum(v for n, v in nf.items() if n not in inside)),
                      "1D: external inflow (net rain), m3": round(rc.get("External Inflow", 0)), "1D: outfall outflow, m3": round(rc.get("External Outflow", 0)),
                      "1D: continuity error, %": rc.get("Continuity Error (%)"), "wall_clock_h": round(TW[s]["wall_s"] / 3600, 2)}
        print(f"  {s}: domain max depth {zones[s]['max_depth_m']} m, >=0.30 m area {zones[s]['area_ge_0p30_ha']} ha; deepest within 200 m of a hotspot "
              f"{max([v['max_depth_200m'] for v in res_hs[s].values()] or [0])} m ({time.time() - t0:.0f} s)", flush=True)
    # ---- hotspot table + layer
    def flag(d):
        return "flooded (>=0.30 m)" if d >= TH[1] else ("shallow (0.10-0.30 m)" if d >= TH[0] else "no")
    head = ["hotspot", "catchment", "ADM no.", "area", "location", "criticality", "x (EPSG:32640)", "y (EPSG:32640)"]
    for s in states:
        head += [f"{s} max depth within 200 m (m)", f"{s} nearest cell depth (m)", f"{s} flooded area >=0.10 m (ha)", f"{s} flooded area >=0.30 m (ha)", f"{s} peak volume (m3)",
                 f"{s} time of max depth (h)", f"{s} first time depth >=0.10 m (h)", f"{s} duration depth >=0.10 m (min)", f"{s} flooded?"]
    head += ["L2 - L1 max depth within 200 m (m)"]
    rows = [head]; feats = []
    for h in sorted(HS, key=lambda q: (q["criticality"] != "Very High", q["criticality"] != "High", int(q["hotspot_id"].split("-")[1]) if q["hotspot_id"].split("-")[1].isdigit() else 0)):
        hid = h["hotspot_id"]; r = [hid, h["catchment"], h["adm"], h["area"], h["location"], h["criticality"], h["x"], h["y"]]; props = dict(hotspot_id=hid, catchment=h["catchment"], area=h["area"], location=h["location"], criticality=h["criticality"])
        for s in states:
            v = res_hs[s][hid]
            r += [v["max_depth_200m"], v["nearest_cell_depth"], v["area_ge_0p10_ha"], v["area_ge_0p30_ha"], v["peak_vol_m3"], v["t_peak_h"], v["t_first_ge_0p10_h"], v["duration_ge_0p10_min"], flag(v["max_depth_200m"])]
            props.update({f"{s}_max_depth_m": v["max_depth_200m"], f"{s}_nearest_depth_m": v["nearest_cell_depth"], f"{s}_area_ge_0p10_ha": v["area_ge_0p10_ha"], f"{s}_area_ge_0p30_ha": v["area_ge_0p30_ha"],
                          f"{s}_peak_vol_m3": v["peak_vol_m3"], f"{s}_t_peak_h": v["t_peak_h"], f"{s}_t_first_ge_0p10_h": v["t_first_ge_0p10_h"], f"{s}_dur_ge_0p10_min": v["duration_ge_0p10_min"], f"{s}_flooded": flag(v["max_depth_200m"])})
        dd = round(res_hs["L2"][hid]["max_depth_200m"] - res_hs["L1"][hid]["max_depth_200m"], 3); r.append(dd); props["L2_minus_L1_m"] = dd
        rows.append(r)
        from pyproj import Transformer
        lon, lat = Transformer.from_crs(32640, 4326, always_xy=True).transform(h["x"], h["y"])
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}})
    HSd = DD / "hotspots"; HSd.mkdir(parents=True, exist_ok=True)
    write_csv(HSd / "hotspot_results.csv", rows); x_ok = False          # no xlsx: openpyxl's default theme carries Chinese font names
    (HSd / "hotspot_layer.geojson").write_text(json.dumps({"type": "FeatureCollection", "name": f"{RG['feat']}_hotspots", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
                                                           "features": feats}, ensure_ascii=False), encoding="utf-8")
    # ---- summary
    srows = [["region", "metric"] + [SNAME[s] for s in states]]
    for met, lab in (("max_depth_m", "domain max depth (m)"), ("area_ge_0p10_ha", "flooded area >=0.10 m (ha)"), ("area_ge_0p30_ha", "flooded area >=0.30 m (ha)"), ("peak_vol_m3", "peak flood volume (m3)"),
                     ("peak_vol_t_h", "time of peak flood volume (h)"), ("domain_area_ha", "2D domain area (ha)")):
        srows.append([RG["feat"], lab] + [zones[s][met] for s in states])
    n_f = {s: sum(1 for v in res_hs[s].values() if v["max_depth_200m"] >= TH[1]) for s in states}
    n_s = {s: sum(1 for v in res_hs[s].values() if TH[0] <= v["max_depth_200m"] < TH[1]) for s in states}
    srows += [[RG["feat"], "hotspots in domain"] + [len(HS)] * len(states), [RG["feat"], "hotspots: flooded (>=0.30 m)"] + [n_f[s] for s in states], [RG["feat"], "hotspots: shallow (0.10-0.30 m)"] + [n_s[s] for s in states]]
    for s_lab, key in (("deepest cell depth (m)", "depth_m"), ("deepest cell: distance to nearest hotspot (m)", "near_dist_m")):
        srows.append([RG["feat"], s_lab] + [deepest[s][key] for s in states])
    srows.append([RG["feat"], "hotspot nearest to the deepest cell"] + [deepest[s]["near_hotspot"] for s in states])
    for k2 in balance[states[0]]:
        srows.append(["domain", f"water balance: {k2}"] + [balance[s][k2] for s in states])
    write_csv(DD / "statistics" / "summary.csv", srows)
    # ---- files
    done, miss = [], []
    for src, dst in ((f"{reg}_domain.geojson", "domain.geojson"), (f"{reg}_ponds.json", "storage_objects.json"), (f"{reg}_pumps.json", "pumps_built.json"),
                     ("pumps_not_built.csv", "pumps_not_built.csv"), ("data_usage_audit.csv", "data_usage_audit.csv")):
        cp(cases / src, DD / "common" / dst, done, miss)
    (DD / "common").mkdir(parents=True, exist_ok=True)
    (DD / "common" / "hotspots.json").write_text(json.dumps(HS, ensure_ascii=False, indent=0), encoding="utf-8")
    for s in states:
        d = DD / DIRS[s]
        cp(W / f"{name}_{s}.inp", d / "1D" / f"{name}_{s}.inp", done, miss)
        cp(C2D / f"{name}_{s}_2way.rpt", d / "1D" / f"{name}_{s}_1D_report.rpt", done, miss)
        cp(C2D / f"twoway_{name}_{s}.json", d / "2D" / f"{s}_coupled_run_record.json", done, miss)
        max_depth_tif(Z[s], a.res, d / "2D" / f"{name}_{s}_max_depth.tif"); done.append(d / "2D" / f"{name}_{s}_max_depth.tif")
        cp(C2D / "anuga_out" / f"{name}_{s}.sww", d / "2D" / f"{name}_{s}.sww", done, miss, move=True)
        cp(C2D / f"{name}_{s}_2d.npz", d / "2D" / f"{name}_{s}_2d.npz", done, miss)
    try:                                  # 页面「需要排 → 可以排 → 排完剩余」用：各态水深 ≥ 0.15 m 的积水量（tools/ponding_ge.py；失败不挡交付）
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from ponding_ge import write_ponding_ge
        write_ponding_ge(DD, {s: DD / DIRS[s] / "2D" / f"{name}_{s}_2d.npz" for s in states})
    except ImportError as e:
        print(f"ponding_ge 跳过：{e}")
    ponds = json.loads((cases / f"{reg}_ponds.json").read_text(encoding="utf-8")).get("ponds") or []
    n_est = sum(1 for p in ponds if p.get("estimated")); n_soak = sum(1 for p in ponds if str(p.get("source")) == "SOAKAWAY")
    lim_lines = [f"- {x}" for x in inp.get("pump_limits", [])] or ["- (none)"]
    (DD / "calculation_settings.md").write_text("\n".join([
        f"# {RG['title']} | {rain:g} mm / {dur:g} h | {a.res:g} m | calculation settings", "", "| Item | Setting |", "|---|---|",
        f"| Scenario | {rain:g} mm / {dur:g} h; design storm shape taken from the {dur:g} h design storm of the client Mussafah model, scaled to the depth; simulated {inp['sim_h']:g} h (duration + 4 h) |",
        f"| Domain | Abu Dhabi whole city, {inp['domain_km2']:,.1f} km2 (same domain polygon as the earlier whole-city runs); free outflow at the boundary |",
        f"| Hotspots | ADM hotspots inside the domain ({len(HS)}), used only to report results (common/hotspots.json) |",
        f"| 1D network | EPA SWMM 5.2, rebuilt only from the post-improvement data set (2026-10-25 file geodatabase): gravity pipes (PIPE_SUBTY = GravitLine) not marked Abandoned / Expired / Removed (the LEVEL field and Proposed status are not used to filter); "
        f"end points snapped within 0.5 m; blocks touching the domain taken whole ({inp['n_components']} blocks, {inp['n_nodes']:,} nodes of which {inp['n_nodes_in_domain']:,} in the domain), {inp['n_pipes']:,} pipes, {inp['n_outfalls']} outfalls (all free outfalls); "
        "dynamic wave, no ponding (water that leaves a manhole goes to 2D). Ground = DTM at the node (DTM on the server); invert from the data (already in the DTM datum), missing / implausible inverts replaced by ground minus 1.61 m |",
        f"| Runoff | SCS CN {inp['cn']:g}, Tc {inp['tc_min']:g} min (whole-city values; the data set has no catchment layer), net rain {inp['rain_excess_mm']} mm, injected at network nodes by service area (service radius {inp['R_served_m']} m); ground farther than that from a manhole gets the net rain directly on the 2D mesh |",
        f"| 2D | ANUGA, unstructured triangles, maximum cell area {a.res:g}^2/3 m2 (about {inp['n_tri_est']:,} cells), Manning n = 0.030 |",
        f"| Terrain | the DTM on the server averaged to {a.res:g} m blocks, then assigned to the triangles: small-scale relief (narrow channels, kerbs, small depressions) is smoothed away |",
        f"| Coupling | couple2way_v3, same-process two-way exchange every 60 s; manholes inside the domain exchange (cover orifice Cd 0.6, DN600; plus {inp.get('inlet_area_total_m2', 0):,.0f} m2 of inlet / catch-basin orifice area attached to {inp.get('n_inlet_nodes', 0):,} nodes, 0.10 m2 per inlet within 8 m of the node); flood water is spread over the 4 cells around the manhole |",
        f"| L1 | existing pipe network + {len(inp['pumps'])} pump groups that have flow data; no ponds, no soakaways |",
        f"| L2 | L1 + {len(ponds)} storage objects ({len(ponds) - n_soak} ponds, {n_soak} soakaways; {n_est} with estimated volume or depth); the network and pumps are the same as in L1 |",
        "| Pumps | fixed flow = sum over the station's pump units of the pump-curve flow at half the maximum head (PUMP_UNIT + PUMP_CURVE), or the station's FLOW_RATE (L/s, only when >= 10) when there is no curve; on / off at the given pump levels, otherwise 50% / 10% of the well depth; discharge to the far end of the pressure line, or out of the domain through a free outfall when the data has no discharge point. Stations without flow data are not built; movable (emergency) pumps are not used |",
        "| Ponds | storage with surface capture on the pond footprint (water on the footprint cells goes into the pond until full), flat storage curve; volume from the data where given, otherwise polygon area x the median plausible depth of the ponds, marked estimated (common/storage_objects.json); all ponds in the data are used in L2 whatever their LEVEL value |",
        "| Soakaways | L2 only: isolated storage L x W x D that takes surface water of its 2D cell until full; no infiltration |",
        f"| Hotspot metrics | cells within 200 m of the hotspot point: maximum depth, flooded area (>=0.10 / >=0.30 m), peak volume; on a {a.res:g} m mesh only a handful of cells fall within 200 m, so the nearest-cell depth is reported too |",
        "| Output | 2D frame every 5 minutes (in .sww and .npz); .sww and the max-depth GeoTIFF are in UTM 40N (EPSG:32640); shp + dat are converted from the .sww by the server script into the shp_dat directory next to result/ |",
        "", "## Limitations", "", *lim_lines, "- See common/pumps_not_built.csv, common/data_usage_audit.csv and common/storage_objects.json for the item-by-item list.", ""]), encoding="utf-8")
    lines = [f"# {RG['title']} | {rain:g} mm / {dur:g} h | L1 / L2 results ({a.res:g} m grid)", "",
             f"| State | Directory | Domain max depth | Area >=0.30 m | Peak flood volume | Hotspots ({len(HS)}): flooded / shallow | 1D manhole flooding (in domain) |", "|---|---|---|---|---|---|---|"]
    for s in states:
        lines.append(f"| {SNAME[s]} | `{DIRS[s]}/` | {zones[s]['max_depth_m']} m | {zones[s]['area_ge_0p30_ha']} ha | {zones[s]['peak_vol_m3']:,} m3 | {n_f[s]} / {n_s[s]} | {balance[s]['1D: manhole flooding inside the domain (= handed to 2D), m3']:,} m3 |")
    lines += ["", f"L2 = L1 + {len(ponds)} ponds / soakaways (network and pumps identical). Max-depth change L2 - L1 per hotspot is in hotspots/hotspot_results.csv.", "",
              "Directories: `hotspots/` (hotspot result table csv" + ("/xlsx" if x_ok else "") + ", hotspot layer geojson), `statistics/summary.csv`, `L1_existing_facilities/` and `L2_with_ponds/` (each with 1D and 2D: .sww, .npz, max-depth tif), `common/`, `calculation_settings.md`."]
    if miss:
        lines += ["", "**Missing files** (not present at post-processing time):", *[f"- `{m}`" for m in miss]]
    (DD / "00_README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = {"name": name, "tag": t, "region": reg, "title": RG["title"], "rain_mm": rain, "dur_h": dur, "sim_h": inp["sim_h"], "res_m": a.res, "states": states, "zones": zones, "deepest": deepest,
           "balance": balance, "hotspots": {s: res_hs[s] for s in states}, "n_hotspots": len(HS), "delivery": {"dir": str(DD), "n_files": len(done), "missing": miss},
           "sww": {s: str(DD / DIRS[s] / "2D" / f"{name}_{s}.sww") for s in states}, "wall_post_s": round(time.time() - t0)}
    (DD / "statistics" / "run_stats.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    # ---- no-Chinese scan over the whole delivery; the marker is written only when it is clean
    bad = scan_no_cjk(DD)
    if bad:
        print(f"  ! Chinese characters / unreadable files found in the delivery ({len(bad)}):", flush=True)
        for b in bad[:30]:
            print("    " + b, flush=True)
        raise SystemExit(3)
    print("  scan: no Chinese in any delivered path or content", flush=True)
    (C2D / f"af_stats_{t}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")      # marker, written last
    print(f"  -> {DD} ({len(done)} files" + (f", {len(miss)} missing" if miss else "") + f"); {time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
