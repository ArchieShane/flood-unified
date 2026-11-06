"""Tatica / DMT 管网能力问询（2026-10-19）：从 result-017 现有文件算不需要重跑的数。

  python tools/tatica_capacity.py <result-017 目录> <输出 json>

每个模型（L1；Al Riyadh 另算 L2）：
  - 复核 Tatica 对 .inp/.rpt 的读数（管数、长度、DN300 占比、满管体积、出口、连续性、出口峰值、受限管段占比）
  - 一维入流拆分：净雨注入（[INFLOWS] 比例 × TS_RUNOFF）与二维回流（耦合日志 2to1）
  - 服务面积：[INFLOWS] 比例之和（ha；TS_RUNOFF 单位 m³/s/ha），全模型 / 计算域内
  - 蓄水：管道满管体积 + 井（满深 × SWMM 最小井面积 1.167 m²）+ 蓄水节点
  - 出口满管能力（入出口管 Manning）与模拟峰值；泵站曲线最大流量；与其他模型共用的出口
  - 热点：最近的域内检查井、出井最大管径
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.prepared import prep

MIN_SURFAREA_M2 = 12.566 * 0.0929  # SWMM 5.2 默认 MIN_SURFAREA 12.566 ft²

MODELS = {  # 目录名 → (Tatica 表里的模型名, inp 前缀)
    "Al_Riyadh_ponds_61mm1h_30m": ("Al_Riyadh", "AD_ALR"),
    "MBZ_61mm1h_30m": ("MBZ", "AD_MBZ"),
    "MI-8_61mm1h_30m": ("MI-8", "AD_MI8"),
    "Qasr_Al_Bahr-1_61mm1h_30m": ("Qasr_Al_Bahr-1", "AD_QAB1"),
    "RB-1_61mm1h_30m": ("RB-1", "AD_RB1"),
    "RB-3_61mm1h_30m": ("RB-3", "AD_RB3"),
    "RBE1-OF1_61mm1h_30m": ("RBE1-OF1", "AD_RBE1"),
    "RD-14_61mm1h_30m": ("RD-14", "AD_RD14"),
    "RD-2_61mm1h_30m": ("RD-2", "AD_RD2"),
    "RD-A_61mm1h_30m": ("RD-A", "AD_RDA"),
    "RD-C_61mm1h_30m": ("RD-C", "AD_RDC"),
    "RD-F_61mm1h_30m": ("RD-F", "AD_RDF"),
    "RD-G_61mm1h_30m": ("RD-G", "AD_RDG"),
    "RD-I_61mm1h_30m": ("RD-I", "AD_RDI"),
    "YAS_OUTFALL_61mm1h_30m": ("YAS_OUTFALL", "AD_YASO"),
    "AlAin_Model_61mm1h_250m": ("Al Ain", "AA_ALAIN"),
}


def read_sections(path):
    sec, cur = {}, None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            if s.startswith("["):
                cur = s.strip("[]").upper()
                sec.setdefault(cur, [])
                continue
            if cur:
                sec[cur].append(s.split())
    return sec


def ts_volume_per_unit(rows, name):
    pts = []
    for r in rows:
        if r[0] != name:
            continue
        t, q = r[-2], float(r[-1])
        h, m = t.split(":")[:2]
        day = 0
        if len(r) == 4:  # 带日期：跨日按出现顺序累加
            day = r[1]
        pts.append((day, float(h) + float(m) / 60, q))
    days = sorted({p[0] for p in pts}, key=lambda d: pts.index(next(p for p in pts if p[0] == d)))
    t = np.array([days.index(p[0]) * 24 + p[1] for p in pts]) * 3600
    q = np.array([p[2] for p in pts])
    return float(np.trapezoid(q, t))


def rpt_block(lines, title):
    i = next(k for k, l in enumerate(lines) if l.strip() == title)
    out, dashes = [], 0
    for l in lines[i + 1:]:
        if l.strip() and set(l.strip()) == {"-"}:
            dashes += 1
            if dashes == 4:
                break
            continue
        if dashes >= 2:  # 第 3 条横线后是 System 合计行
            if not l.strip():
                if out:
                    break
                continue
            out.append(l.split())
    return out


def rpt_continuity(lines):
    i = next(k for k, l in enumerate(lines) if "Flow Routing Continuity" in l)
    d = {}
    for l in lines[i:i + 20]:
        m = re.match(r"\s+([A-Za-z ]+?)\s*\.+\s+([-\d.]+)\s+([-\d.]+)", l)
        if m:
            d[m.group(1).strip()] = float(m.group(3)) * 1000  # 10^6 ltr → m³
        if "Continuity Error" in l:
            d["Continuity Error (%)"] = float(l.split()[-1])
            break
    return d


def manning_full(d, n, s):
    if not math.isfinite(s) or s <= 0:
        return None
    a = math.pi * d * d / 4
    return a * (d / 4) ** (2 / 3) * math.sqrt(s) / n


def analyse(state_dir, prefix, state, domain):
    inp = next((state_dir / "1d").glob(f"{prefix}_M61H1_{state}.inp"))
    rpt = next((state_dir / "1d").glob(f"{prefix}_M61H1_{state}_1d_report.rpt"))
    log = json.loads(next((state_dir / "2d").glob(f"{state}_coupled_run_log.json")).read_text(encoding="utf-8"))
    S = read_sections(inp)
    coords = {r[0]: (float(r[1]), float(r[2])) for r in S.get("COORDINATES", [])}
    junc = {r[0]: (float(r[1]), float(r[2])) for r in S.get("JUNCTIONS", [])}  # invert, max depth
    outf = {r[0]: (float(r[1]), r[2]) for r in S.get("OUTFALLS", [])}
    stor = {r[0]: r for r in S.get("STORAGE", [])}
    xs = {r[0]: r for r in S.get("XSECTIONS", [])}
    cond = S.get("CONDUITS", [])
    dia = {}
    for r in cond:
        x = xs[r[0]]
        dia[r[0]] = float(x[2]) if x[1] == "CIRCULAR" else None
    shapes = {xs[r[0]][1] for r in cond}
    L = np.array([float(r[3]) for r in cond])
    D = np.array([dia[r[0]] or np.nan for r in cond])
    pipe_vol = float(np.nansum(math.pi / 4 * D ** 2 * L))
    man_vol = sum(md * MIN_SURFAREA_M2 for _, md in junc.values() if math.isfinite(md))
    n_nan_junc = sum(1 for v, md in junc.values() if not (math.isfinite(v) and math.isfinite(md)))
    n_nan_cond = sum(1 for r in cond if not (math.isfinite(float(r[5])) and math.isfinite(float(r[6]))))

    # 入流：净雨注入
    inflows = S.get("INFLOWS", [])
    ts_name = {r[2] for r in inflows}
    assert len(ts_name) == 1, ts_name
    per_ha = ts_volume_per_unit(S["TIMESERIES"], ts_name.pop())
    scale = {r[0]: float(r[5]) for r in inflows}
    served_ha = sum(scale.values())
    in_dom = {k for k, (x, y) in coords.items() if domain.contains(Point(x, y))}
    served_dom_ha = sum(v for k, v in scale.items() if k in in_dom)
    rain_in = served_ha * per_ha

    lines = rpt.read_text(encoding="utf-8", errors="ignore").splitlines()
    cont = rpt_continuity(lines)
    olf = {r[0]: {"max_cms": float(r[3]), "vol_m3": float(r[4]) * 1000} for r in rpt_block(lines, "Outfall Loading Summary") if r[0] != "System"}
    sys_row = next(r for r in rpt_block(lines, "Outfall Loading Summary") if r[0] == "System")
    sur = rpt_block(lines, "Conduit Surcharge Summary")
    hcl = np.array([float(r[5]) for r in sur])
    n_lim = int((hcl > 0).sum())
    pumps = []
    curves = {}
    for r in S.get("CURVES", []):
        vals = r[2:] if len(r) % 2 == 0 and not r[1].replace(".", "").isdigit() else r[1:]
        curves.setdefault(r[0], []).extend(float(v) for v in vals[1::2])
    pump_rows = {r[0]: r for r in rpt_block(lines, "Pumping Summary")} if any(l.strip() == "Pumping Summary" for l in lines) else {}
    for r in S.get("PUMPS", []):
        pumps.append({"pump": r[0], "from": r[1], "to": r[2], "to_outfall": r[2] in outf,
                      "curve_max_cms": max(curves.get(r[3], [0])),
                      "sim_max_cms": float(pump_rows[r[0]][4]) if r[0] in pump_rows else None})

    # 出口满管能力：所有进入出口的管段
    outcap = {}
    for r in cond:
        if r[2] in outf:
            n, up, dn = float(r[4]), float(r[5]), float(r[6])
            s = (up - dn) / float(r[3])
            q = manning_full(dia[r[0]], n, s) if dia[r[0]] else None
            o = outcap.setdefault(r[2], {"invert_m": outf[r[2]][0], "type": outf[r[2]][1], "pipes": []})
            o["pipes"].append({"conduit": r[0], "d_m": dia[r[0]], "slope": round(s, 5), "q_full_cms": q})
    for k, o in outcap.items():
        qs = [p["q_full_cms"] for p in o["pipes"] if p["q_full_cms"]]
        o["q_full_cms"] = sum(qs) if qs else None
        o["flat_or_adverse"] = any(p["slope"] <= 0 for p in o["pipes"])
        o.update(olf.get(k, {}))
        o["pump_discharge"] = k.startswith("X_")
    nets = {"nodes_in_domain": len(in_dom & set(junc)), "nodes": len(junc)}
    return {
        "inp": inp.name, "shapes": sorted(shapes), "pipes": len(cond), "length_km": float(L.sum() / 1000),
        "share_dn300": float(np.mean(np.isclose(D, 0.3))), "n_ge_1m": int(np.sum(D >= 1.0)), "max_d_m": float(np.nanmax(D)),
        "pipe_vol_m3": pipe_vol, "manhole_vol_m3": man_vol, "nan_junctions": n_nan_junc, "nan_conduits": n_nan_cond, "n_storage": len(stor),
        "outfalls": len(outf), "outfall_types": sorted({v[1] for v in outf.values()}), "pumps_in_model": len(pumps),
        "rpt": cont, "peak_outfall_system_cms": float(sys_row[3]), "share_capacity_limited": n_lim / len(cond), "share_limited_over_1h": float((hcl > 1).sum() / len(cond)), "share_limited_over_3h": float((hcl > 3).sum() / len(cond)),
        "rain_injected_m3": rain_in, "coupled_2to1_m3": log["acc_m3"]["2to1"], "coupled_1to2_m3": log["acc_m3"]["1to2"],
        "rain_on_2d_m3": log["acc_m3"]["rain"], "net_rain_mm": per_ha / 10,
        "served_km2": served_ha / 100, "served_in_domain_km2": served_dom_ha / 100, **nets,
        "outfall_detail": outcap, "pumps": pumps,
        "_coords": coords, "_junc": junc, "_cond": [(r[0], r[1], dia[r[0]]) for r in cond], "_in_dom": in_dom,
    }


def nearest_manholes(res, hotspots):
    names = [k for k in res["_junc"] if k in res["_in_dom"] and k in res["_coords"]]
    xy = np.array([res["_coords"][k] for k in names])
    out_d = {}
    for c, frm, d in res["_cond"]:
        if d and d > out_d.get(frm, 0):
            out_d[frm] = d
    rows = []
    for h in hotspots:
        dd = np.hypot(xy[:, 0] - h["x"], xy[:, 1] - h["y"])
        i = int(np.argmin(dd))
        within = int(np.sum(dd <= 200))
        rows.append({"hotspot": h["hotspot_id"], "node": names[i], "dist_m": float(dd[i]),
                     "out_d_mm": round(out_d.get(names[i], 0) * 1000) or None, "manholes_within_200m": within})
    return rows


def main(root, out):
    root = Path(root)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    result, outfall_models = {}, {}
    for folder, (name, prefix) in MODELS.items():
        mdir = root / folder
        gj = json.loads((mdir / "common" / "domain.geojson").read_text(encoding="utf-8"))
        from shapely.ops import transform
        dom = transform(lambda x, y, z=None: to_utm.transform(x, y), shape(gj["features"][0]["geometry"]))
        dprep = prep(dom)
        hs = json.loads((mdir / "common" / "hotspots.json").read_text(encoding="utf-8"))
        states = ["L1", "L2"] if name == "Al_Riyadh" else ["L1"]
        for st in states:
            sd = mdir / ("L1_existing_network" if st == "L1" else "L2_built_storage")
            r = analyse(sd, prefix, st, dprep)
            r["domain_km2"] = dom.area / 1e6
            if st == "L1":
                r["hotspots"] = nearest_manholes(r, hs)
                for o in r["outfall_detail"]:
                    outfall_models.setdefault(o, []).append(name)
            for k in [k for k in r if k.startswith("_")]:
                r.pop(k)
            result[f"{name}|{st}"] = r
            print(f"{name} {st}: pipes {r['pipes']}, served {r['served_km2']:.2f} km² (domain {r['served_in_domain_km2']:.2f}), "
                  f"storage {r['pipe_vol_m3'] + r['manhole_vol_m3']:,.0f} m³, rain in {r['rain_injected_m3']:,.0f}, 2to1 {r['coupled_2to1_m3']:,.0f}, "
                  f"ext inflow {r['rpt'].get('External Inflow', 0):,.0f}", flush=True)
    for k, r in result.items():
        for o, v in r["outfall_detail"].items():
            v["also_in"] = [m for m in outfall_models.get(o, []) if m != k.split("|")[0]]
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:3])
