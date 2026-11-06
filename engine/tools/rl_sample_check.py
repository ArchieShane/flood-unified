# -*- coding: utf-8 -*-
"""简化试算模型的精度核对：完整模型 vs 简化模型，各热点 200 m 内一维溢流量（节点坐标取完整模型）。
用法：python tools/rl_sample_check.py --region RB-01 --name RLF_RB01_M61D1 --state L1"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
from c1_design_l2s4 import node_flood  # noqa: E402
from workdir import WORK  # noqa: E402


def coords(inp):
    t = pathlib.Path(inp).read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"\[COORDINATES\]\n(.*?)(?:\n\[|\Z)", t, re.S)
    return {q[0]: (float(q[1]), float(q[2])) for q in (ln.split() for ln in m.group(1).splitlines()) if len(q) >= 3 and not q[0].startswith(";")}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--region", required=True); ap.add_argument("--name", required=True); ap.add_argument("--state", default="L1")
    a = ap.parse_args(argv)
    import geopandas as gpd
    cfg = json.loads((HERE / "rl_regions" / f"{a.region}.json").read_text(encoding="utf-8"))
    C = coords(WORK / f"{a.name}_{a.state}.inp")
    full = node_flood(WORK / "bench" / f"smoke_{a.name}_{a.state}.rpt"); samp = node_flood(WORK / "bench" / f"smoke_{a.name}_{a.state}S.rpt")
    H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(32640)
    D = gpd.read_file(ROOT / cfg["gov_dir"] / "domain.geojson").to_crs(32640).geometry.iloc[0]
    H = H[H.within(D)]
    rows = []
    for h in H.itertuples():
        f = lambda F: sum(v["vol_m3"] for k, v in F.items() if k in C and math.hypot(C[k][0] - h.geometry.x, C[k][1] - h.geometry.y) <= 200)   # noqa: E731
        x, y = f(full), f(samp)
        rows.append({"hotspot": h.hotspot_id, "full_m3": round(x), "sample_m3": round(y), "diff_pct": round((y - x) / x * 100, 1) if x > 50 else None})
    tot = {"full_m3": round(sum(v["vol_m3"] for v in full.values())), "sample_m3": round(sum(v["vol_m3"] for v in samp.values()))}
    ok = [r for r in rows if r["diff_pct"] is not None]
    res = {"region": a.region, "rows": rows, "total": tot, "n_compared": len(ok), "within_15pct": sum(abs(r["diff_pct"]) <= 15 for r in ok),
           "median_abs_diff_pct": sorted(abs(r["diff_pct"]) for r in ok)[len(ok) // 2] if ok else None}
    out = ROOT / "out/l2s_rl" / cfg["key"] / "model" / f"sample_check_{a.name}_{a.state}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rows:
        print(r)
    print({k: v for k, v in res.items() if k != "rows"})


if __name__ == "__main__":
    main()
