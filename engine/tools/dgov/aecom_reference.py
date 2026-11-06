"""把 AECOM 自己的 RD-A 模拟结果整理成对比参照（没有实测数据时，唯一能拿来对比的东西）。

  python -m tools.dgov.aecom_reference --extent rda

两部分：
  1 热点结果表：RD-A_ADM-Simulation Summary.pdf → 每个热点 × 每场雨的全区积水量、管网排空时间、热点积水量、退水时间、主管排空时间
  2 逐管 D/R：9 个降雨文件夹的 Conduit.shp 输入相同、只有结果列 D/R 不同。**D/R 是导出那一刻的瞬时值（百分数），不是全过程最大值**
    （24 h 雨的中位数为 0；2024 年 1 h 中位 23.6%、2 h 却是 100%）。只留作记录，不能当对比参照
口径（比较时必须考虑）：AECOM 是一维计算，地面积水留在原地（ALLOW_PONDING YES）、之后回流管网，不进二维地面；
降雨总量 61.34 / 72 / 82.2 mm，雨型套 SCS II 型；PDF 里的全区积水量可能含 RD-B（模型库里有 RD-B），要核实。
产物：out/dgov/ref/<extent>_aecom_hotspots.csv、<extent>_aecom_pipes.gpkg
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from .common import OUT, lineage, source

ROW = re.compile(r"^(?P<hs>HS-\d+(?:, \d+)*|C-21A?)\s+(?P<storm>.+?)\s*\|\s*(?P<dur>\d+hr)\s+Existing\s+(?P<vol>[\d,\.]+)\s+(?P<empt>[\d\.]+)\s+(?P<loc>[\d,\.]+)\s+(?P<rec>[\d\.]+)\s+(?P<main>[\d\.]+)")
FOLDERS = {"10+20%-1hr": ("10yr+20%", "1hr"), "10+20%-2hr": ("10yr+20%", "2hr"), "10+20%-24": ("10yr+20%", "24hr"),
           "2024-1hr": ("2024", "1hr"), "2024-2hr": ("2024", "2hr"), "2024-24hr": ("2024", "24hr"),
           "2026-1hr": ("2026", "1hr"), "2026-2hr": ("2026", "2hr"), "2026-24hr": ("2026", "24hr")}


def storm_key(s):
    s = s.lower()
    return "10yr+20%" if "10yrs" in s else ("2026" if "2026" in s else ("2024" if "2024" in s else s))


def hotspots(pdf):
    import pypdf
    rows = []
    for p in pypdf.PdfReader(pdf).pages:
        for ln in (p.extract_text() or "").splitlines():
            m = ROW.match(ln.strip())
            if m:
                d = m.groupdict()
                rows.append({"hotspot": d["hs"].strip(), "storm": storm_key(d["storm"]), "duration": d["dur"],
                             "overall_flood_m3": float(d["vol"].replace(",", "")), "network_emptying_h": float(d["empt"]),
                             "hotspot_flood_m3": float(d["loc"].replace(",", "")), "flood_recession_h": float(d["rec"]),
                             "mainline_emptying_h": float(d["main"])})
    return pd.DataFrame(rows).drop_duplicates()


def pipes(base):
    import geopandas as gpd
    out = None
    for f, (st, du) in FOLDERS.items():
        g = gpd.read_file(base / f / "Conduit.shp", engine="pyogrio").set_crs(32640, allow_override=True)
        col = f"DR_snapshot_{st}_{du}"
        g = g[["LABEL", "D", "INV_STRT", "INV_STP", "D/R", "geometry"]].rename(columns={"D/R": col})
        g[col] = pd.to_numeric(g[col], errors="coerce")
        out = g if out is None else out.merge(g[["LABEL", col]], on="LABEL", how="outer")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.aecom_reference")
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    base = Path(source("aecom_rda")["abspath"])
    pdf = next(base.glob("*Simulation Summary.pdf"))
    dst = OUT / "ref"
    dst.mkdir(parents=True, exist_ok=True)
    H = hotspots(pdf)
    H.to_csv(dst / f"{a.extent}_aecom_hotspots.csv", index=False, encoding="utf-8-sig")
    P = pipes(base)
    gp = dst / f"{a.extent}_aecom_pipes.gpkg"
    if gp.exists():
        gp.unlink()
    P.to_file(gp, layer="pipes", driver="GPKG")
    dr = [c for c in P.columns if c.startswith("DR_snapshot_")]
    full = {c: round(float(P[c].median()), 1) for c in dr}
    lineage("aecom_reference", [pdf] + [base / f / "Conduit.shp" for f in FOLDERS], [dst / f"{a.extent}_aecom_hotspots.csv", gp], {"extent": a.extent},
            notes=f"热点结果 {len(H)} 行；逐管 {len(P)} 根 × {len(dr)} 场雨")
    print(f"热点结果 {len(H)} 行（{H.hotspot.nunique()} 处 × {H.groupby(['storm', 'duration']).ngroups} 场）")
    print("D/R 瞬时值中位数（%，不是最大值，不作参照）：", full)
    print(f"→ {dst}")


if __name__ == "__main__":
    main()
