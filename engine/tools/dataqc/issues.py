# -*- coding: utf-8 -*-
"""把全部问题写成 GeoPackage：每类问题一层，带位置、检查项、程度、整改途径、整改建议。

与 2026-09-27 的 生成问题图层.py 同一套字段。编号不唯一的要素无法唯一定位，同号要素都列出并在 id_note 注明。
几何：管段项取整条线，节点项取点，管端项取那一端，C7 取交点。坐标系跟源数据。
"""
from __future__ import annotations

import os
import re

import numpy as np
import shapely

from . import thresholds as T

FIELDS = ["check_code", "check_name", "severity", "route", "recommendation", "feature_id", "source_layer", "detail",
      "other_id", "value_m", "id_note"]


def export_gpkg(out_gpkg: str, net, net_result, dtm=None, thr=None, log=print) -> int:
    """返回写出的层数。"""
    import geopandas as gpd
    import pandas as pd

    thr = thr or T.Thresholds()
    txt = __import__("json").load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "texts_zh.json"), encoding="utf-8"))
    C = net_result["checks"]
    P, N = net.P, net.N
    links_geom = {i: g for i, g in zip(P["id"], net.P_geom)}
    link_layer = {i: l for i, l in zip(P["id"], P["layer"])}
    node_geom = {}
    for i, g, l in zip(N["id"], net.N_geom, N["layer"]):
        node_geom.setdefault(i, []).append((l, g))
    target_path = out_gpkg if os.path.exists(out_gpkg) else out_gpkg
    if os.path.exists(out_gpkg):
        os.remove(out_gpkg)      # 重跑要干净的一份
    n_layers = 0
    written = []

    def row(code, fid, geometry, other="", value=None, id_note="", layer=None):
        return {"check_code": code, "check_name": txt[code]["name"], "severity": {"crit": "影响建模", "warn": "需处理", "info": "提示"}[T.severity(code)],
                "route": {"desk": "桌面修正", "supply": "补交资料", "survey": "外业复测", "std": "对照设计标准", "none": "不需整改"}[T.route_of(code)],
                "recommendation": txt[code]["fix"], "feature_id": fid, "source_layer": layer, "detail": "",
                "other_id": other, "value_m": value, "id_note": id_note, "geometry": geometry}

    def write_layer(code, rows):
        nonlocal n_layers
        if not rows:
            return
        name = "%s_%s" % (code, re.sub(r"[^a-z0-9]+", "_", txt[code]["name"].lower())[:40])
        g = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=net.crs)
        g.to_file(out_gpkg, layer=name, driver="GPKG")
        n_layers += 1
        written.append({"layer": name, "code": code, "rows": len(rows)})

    for code, v in C.items():
        if not v["count"] or not v["ids"]:
            continue
        kind = T.shape_of(code)
        rows = []
        for s in v["ids"]:
            detail = other_id = ""
            fid = s
            if kind == "管端":
                m = re.match(r"(.*)\((起|止)\)$", s)
                fid, end = (m.group(1), m.group(2)) if m else (s, "起")
                g = links_geom.get(fid)
                if g is None:
                    continue
                x, y = (shapely.get_coordinates(g)[0] if end == "起" else shapely.get_coordinates(g)[-1])[:2]
                rows.append(row(code, fid, shapely.Point(x, y), layer=link_layer.get(fid)))
            elif code == "C7":
                a, b = s.split("×", 1) if "×" in s else (s, "")
                ga, gb = links_geom.get(a), links_geom.get(b)
                if ga is None or gb is None:
                    continue
                it = shapely.intersection(ga, gb)
                c = shapely.get_coordinates(it)
                if len(c) == 0:          # 几何上不相交（容差内算了相交）：取两条线最近点
                    d = shapely.shortest_line(ga, gb)
                    c = shapely.get_coordinates(d)[-1:] if d is not None else np.zeros((0, 2))
                if len(c) == 0:
                    continue
                p = shapely.Point(c[0])
                rows.append(row(code, a, p, other=b, layer=link_layer.get(a)))
            elif kind == "节点":
                for layer_name, g in node_geom.get(fid, ()):
                    rows.append(row(code, fid, g, layer=layer_name))
            else:
                g = links_geom.get(fid)
                if g is None:
                    continue
                rows.append(row(code, fid, g, layer=link_layer.get(fid)))
        write_layer(code, rows)
    # DTM：低洼与深坑（点）
    if dtm:
        rows = [row("R12", "L%d" % (i + 1), shapely.Point(a["x"], a["y"]), value=a["min"], layer=os.path.basename(dtm["meta"]["name"]))
                for i, a in enumerate(dtm["lows"])]
        write_layer("R12", rows)
        pit_rows = [row("R10", "P%d" % (i + 1), shapely.Point(x, y), value=v, layer=os.path.basename(dtm["meta"]["name"]))
              for i, (x, y, v) in enumerate(dtm.get("pitPts", []))]
        write_layer("R10", pit_rows)
    log("问题图层：%d 层，共 %s 条" % (n_layers, format(sum(s["rows"] for s in written), ",")))
    return n_layers
