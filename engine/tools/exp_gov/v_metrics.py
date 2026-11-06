"""RD-A 治理前后验证的指标（V-14…V-16，用户 2026-10-23 确认）。

  python tools/exp_gov/v_metrics.py --storm E24 --model A

V-14 热点命中：客户热点表里落在 RD-A 计算域 200 m 内的 11 个热点；热点 200 m 圈内二维最大水深 ≥ 0.15 m 算命中
V-15 误报面积：所有热点 200 m 圈以外、最大水深 ≥ 0.15 m 的面积（ha）
V-16 和 AECOM 对比（只当参照）：AECOM 摘要的热点溢流量、退水时间（out/dgov/ref/rda_aecom_hotspots.csv，1 h 那一行），
     对比本模型热点 200 m 圈内各井的一维溢流量合计（rpt 的 Node Flooding Summary）和二维退水时间（雨停后圈内最大水深退到 < 0.10 m）
产物：out/exp_gov/verify/metrics_<雨>_<模型>.json
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
OUT = ROOT / "out" / "exp_gov" / "verify"
AEC_STORM = {"E24": "2024", "E26": "2026", "D61": "10yr+20%"}
DEPTH, RADIUS, DRY = 0.15, 200.0, 0.10


def node_flooding(rpt):
    """rpt 的 Node Flooding Summary：节点 → 溢流总量（m³）"""
    out, on = {}, False
    for ln in open(rpt, encoding="utf-8", errors="ignore"):
        if "Node Flooding Summary" in ln:
            on = True; continue
        if on:
            if ln.strip().startswith("*****") and out:
                break
            p = ln.split()
            if len(p) >= 6 and re.match(r"^[\d.]+$", p[1] if len(p) > 1 else ""):
                try:
                    out[p[0]] = float(p[5]) * 1000.0            # 10^6 ltr → m³
                except ValueError:
                    pass
    return out


def coords(inp):
    out, on = {}, False
    for ln in open(inp, encoding="utf-8", errors="ignore"):
        s = ln.strip()
        if s.startswith("["):
            on = s == "[COORDINATES]"; continue
        if on and s and not s.startswith(";"):
            p = s.split()
            out[p[0]] = (float(p[1]), float(p[2]))
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", required=True); ap.add_argument("--model", required=True)
    a = ap.parse_args(argv)
    import geopandas as gpd
    name, st = f"VG_{a.storm}", a.model
    z = np.load(C2D / f"{name}_{st}_2d.npz")
    xy = z["xy"].astype(float); area = z["area"].astype(float); t = z["t_h"]
    hm = z["hmax"].astype(float); fr = z["frames"].astype(np.float32)
    dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    H = H[H.intersects(dom.geometry.iloc[0].buffer(RADIUS))].set_index("hotspot_id")
    tw = json.loads((C2D / f"twoway_{name}_{st}.json").read_text(encoding="utf-8"))
    fl = node_flooding(C2D / f"{name}_{st}_2way.rpt")
    co = coords(WORK / f"{name}_{st}.inp")
    in_any = np.zeros(len(xy), bool)
    hs = {}
    for h, r in H.iterrows():
        c = np.hypot(xy[:, 0] - r.geometry.x, xy[:, 1] - r.geometry.y) <= RADIUS
        in_any |= c
        dmax_t = fr[:, c].max(1) if c.any() else np.zeros(len(t))
        wet = np.flatnonzero((t >= 1.0) & (dmax_t >= DRY))
        rec = 0.0 if not len(wet) else (None if wet[-1] == len(t) - 1 else float(t[wet[-1] + 1] - 1.0))
        fv = sum(v for k, v in fl.items() if k in co and np.hypot(co[k][0] - r.geometry.x, co[k][1] - r.geometry.y) <= RADIUS)
        mx = float(hm[c].max()) if c.any() else 0.0
        hs[h] = {"max_m": round(mx, 2), "hit": bool(mx >= DEPTH), "ge015_ha": round(float(area[c & (hm >= DEPTH)].sum() / 1e4), 3),
                 "node_flood_m3": round(fv), "recede_h_after_rain": rec}
    hits = sum(v["hit"] for v in hs.values())
    res = {"storm": a.storm, "model": st, "hotspots_n": len(hs), "hits": hits, "hit_rate": round(hits / max(len(hs), 1), 3),
           "false_alarm_ha": round(float(area[(~in_any) & (hm >= DEPTH)].sum() / 1e4), 2),
           "domain_ge015_ha": round(float(area[hm >= DEPTH].sum() / 1e4), 2), "domain_ge030_ha": round(float(area[hm >= 0.30].sum() / 1e4), 2),
           "peak_surface_m3": round(float((fr * area).sum(1).max())), "end_surface_m3": round(float((fr[-1] * area).sum())),
           "flood_total_1d_m3": round(sum(fl.values())), "twoway": {k: v for k, v in tw.items() if not isinstance(v, (list, dict))}, "hotspots": hs}
    # V-16 AECOM 参照
    ref = []
    for r in csv.DictReader(open(ROOT / "out/dgov/ref/rda_aecom_hotspots.csv", encoding="utf-8-sig")):
        if r["storm"] != AEC_STORM[a.storm] or r["duration"] != "1hr":
            continue
        nums = re.findall(r"\d+", r["hotspot"])
        ids = [f"ADM-{n}" for n in nums]
        mine = [hs[i] for i in ids if i in hs]
        rec = [m["recede_h_after_rain"] for m in mine]
        ref.append({"aecom_hotspot": r["hotspot"], "matched": [i for i in ids if i in hs], "aecom_flood_m3": float(r["hotspot_flood_m3"]),
                    "aecom_recession_h": float(r["flood_recession_h"]), "model_node_flood_m3": sum(m["node_flood_m3"] for m in mine),
                    "model_recede_h": (None if any(x is None for x in rec) else max(rec)) if rec else None})
    res["aecom_reference"] = ref
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"metrics_{a.storm}_{st}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"{name} {st}：命中 {hits}/{len(hs)}，误报 {res['false_alarm_ha']} ha，全域 ≥0.15 m {res['domain_ge015_ha']} ha，一维溢流 {res['flood_total_1d_m3']:,} m³")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
