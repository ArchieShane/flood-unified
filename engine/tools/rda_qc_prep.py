# -*- coding: utf-8 -*-
"""RD-A 数据质检的输入准备（2026-10-19）：客户 SewerGEMS 导出的情景 shp → tools/dataqc 认得的 GeoPackage + 计算域面。

· 9 个情景文件夹（10+20% / 2024 / 2026 × 1 / 2 / 24 h）的管网与子汇水区几何、属性完全相同（只差结果列），取 2026-1hr 一份。
· shp 没有 .prj；坐标与 5 m DTM 同为 UTM 40N（EPSG:32640），按它读。单位：标高 / 长度 m，管径 mm（与 inp 的英制不同）。
· 列名改成 dataqc 认的：管段 pipe_id / us_node / ds_node / diameter / us_invert / ds_invert / length；
  节点 node_id / invert / cover。编号用数字 ID（= inp 里的名字，全模型唯一），不用 LABEL：出水口与湿井的 LABEL 都叫「RD-A」，
  按 LABEL 接会把湿井和出水口当成一个点；原 LABEL 留在 label 列。湿井图层名带 tank（dataqc 按表名认节点种类，tank → 蓄水）。
· 计算域 = 3,901 块子汇水区的并集（客户模型自带的汇水范围）。
用法：python tools/rda_qc_prep.py --src "20261019/test/RD-A/RD-A/2026-1hr" --inp "20261019/test/RD-A/RD-A/Hydraulic Model/RD - A.stsw_1_51079.inp" --out out/dataqc/RD-A/input
"""
from __future__ import annotations

import argparse
import pathlib
import sys


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--inp", help="客户 inp（英制）：湿井池底取它 [STORAGE] 的 Invert，shp 里没有池底")
    a = ap.parse_args(argv)
    import geopandas as gpd
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def rd(name):
        for n in ([name] if isinstance(name, str) else name):
            p = src / f"{n}.shp"
            if p.exists():
                return gpd.read_file(p).set_crs(32640, allow_override=True)
        raise FileNotFoundError(name)

    gp = out / "rda_network.gpkg"
    gp.unlink(missing_ok=True)
    c = rd("Conduit")
    c = c.rename(columns={"ID": "pipe_id", "LABEL": "label", "START_NID": "us_node", "STOP_NID": "ds_node", "D": "diameter",
                          "INV_STRT": "us_invert", "INV_STP": "ds_invert", "LENGTH": "length", "MANN_N": "mann_n", "COND_SHAPE": "shape"})
    c[["pipe_id", "label", "us_node", "ds_node", "diameter", "us_invert", "ds_invert", "length", "mann_n", "shape", "geometry"]].to_file(gp, layer="Conduit")
    p = rd("Pressure Pipe").rename(columns={"ID": "pipe_id", "LABEL": "label", "START_NID": "us_node", "STOP_NID": "ds_node", "D": "diameter", "LENGTH": "length"})
    p[["pipe_id", "label", "START_NODE", "STOP_NODE", "us_node", "ds_node", "diameter", "length", "geometry"]].to_file(gp, layer="Pressure_Pipe")
    for name, layer in ((["Manhole", "Manholes"], "Manhole"), ("Outfall", "Outfall"), ("Pump", "Pump"), ("Wetwell", "Wetwell_tank")):
        n = rd(name).rename(columns={"ID": "node_id", "LABEL": "label", "ELV_INV": "invert", "ELV_GND": "cover"})
        if "invert" not in n:                       # 湿井 shp 没有池底：取 inp [STORAGE] 的 Invert（ft → m）；没给 inp 时按 地面 − NODE_H 估
            n["invert"] = n["cover"] - n["NODE_H"]
            if a.inp:
                import re
                t = pathlib.Path(a.inp).read_text(encoding="utf-8", errors="ignore")
                st = re.search(r"\[STORAGE\]\n(.*?)(?:\n\[|\Z)", t, re.S).group(1)
                inv = {r.split()[0]: float(r.split()[1]) * 0.3048 for r in st.splitlines() if r.strip() and not r.startswith(";")}
                n["invert"] = [inv.get(str(i), v) for i, v in zip(n["node_id"], n["invert"])]
        n[["node_id", "label", "invert", "cover", "geometry"]].to_file(gp, layer=layer)
    cat = rd("Catchment")
    cat.to_file(out / "rda_catchments.gpkg", layer="Catchment")
    dom = gpd.GeoDataFrame({"name": ["RD-A"]}, geometry=[cat.geometry.buffer(0).union_all()], crs=32640)
    dom.to_file(out / "rda_domain.geojson", driver="GeoJSON")
    print(f"管段 {len(c)}、压力管 {len(p)}、子汇水区 {len(cat)}；计算域 {dom.area.iloc[0] / 1e4:.1f} ha → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
