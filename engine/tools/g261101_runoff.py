# -*- coding: utf-8 -*-
"""261101 新区域的产流子区表（替换 region_model.composite_subs，不改它）。
RD-A 的做法：AECOM 子汇水区成对（透水 + 不透水）→ 用 Makani 栅格数不透水比例拆开 → 复合 CN。
治理后 SUBCATCHMENT 已经是「透水 + 不透水合成一块」的子汇水区，带复合 CN（SCS_CN）、Tc、面积、出口节点（OUT_NODE），所以直接当产流子区用：
一块子汇水区 = 一个产流子区，CN / Tc 用数据里的值，不再拆分、不再重算（不做数据治理）。
用 install() 装进 region_model；run_region_g.py 启动时调用。
"""
from __future__ import annotations


def composite_subs(R, log=print):
    import geopandas as gpd
    import pandas as pd
    cat = gpd.read_file(R.p("catchments.gpkg"))
    df = pd.DataFrame({"label": cat.LABEL.astype(str), "node": cat.OUT_NODE.astype(str), "area_m2": cat.geometry.area.astype(float), "cn": cat.SCS_CN.astype(float),
                       "tc_min": cat.TC.astype(float), "part": "治理后合成"})
    st = {"n_client": len(cat), "n_plots": len(cat), "n_paired": 0, "n_single": len(cat), "area_client_ha": round(float(cat.A.sum()), 2), "area_ha": round(float(df.area_m2.sum() / 1e4), 2),
          "f_aw": None, "small_plots_fallback": 0, "makani_counts": {},
          "note": "治理后 SUBCATCHMENT 已是透水 + 不透水合成块，带复合 CN / Tc / 出口节点，直接用，不拆分、不用 Makani 重算不透水比例"}
    log(f"  子汇水区：治理后 {len(cat):,} 块，直接当产流子区（CN {df.cn.min():g}–{df.cn.max():g}，面积 {st['area_ha']} ha）")
    return df, st


def install():
    import region_model
    region_model.composite_subs = composite_subs
