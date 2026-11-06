"""两条路线（ArcGIS / 开源）的结果逐条比对。凡改了规则或替身，都要跑它，全部一致才算两条路线等价。

  python tools/oss_gov/cmp_paths.py <ArcGIS 的 gdb> <开源的 gdb> [输出 json]
  例：python tools/oss_gov/cmp_paths.py D:/swbrain_work/exp_gov/02_prep/prep.gdb D:/swbrain_work/oss_full/02_prep/prep.gdb

逐层：条数；按键（src_layer + src_oid + assetid，同键多条按出现顺序）对齐后，逐字段比属性、逐折点比坐标（容差 0.0002 m，
ArcGIS 写库按 0.0001 m 取精度）。不比 OBJECTID、GlobalID（随机生成）、Shape_Length / Shape_Area（派生）。
表（EXCLUDED / ISSUES / CONFLICTS）按整行多重集比较。
"""
from __future__ import annotations

import collections
import json
import sys

import numpy as np
import pandas as pd
import pyogrio
import shapely

TOL = 0.0002
SKIP = {"objectid", "globalid", "shape_length", "shape_area", "fid",
        # 报表库里由系统生成、和治理内容无关的字段
        "orig_fid", "created_user", "created_date", "last_edited_user", "last_edited_date", "validationstatus", "d_validationstatus",
        "featureglobalid", "fromglobalid", "toglobalid", "creationdate", "creator", "lastupdate", "updatedby", "lastupdatesubnetwork",
        "lastackexportsubnetwork", "subnetlineglobalid", "export_time"}


def key_cols(df):
    return [c for c in ("src_layer", "src_oid", "assetid") if c in df.columns]


def keyed(df):
    ks = key_cols(df)
    if not ks:
        k = pd.Series([("#", i) for i in range(len(df))], index=df.index)
    else:
        sv = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
        base = pd.Series(["|".join(sv(v) for v in r) for r in df[ks].itertuples(index=False)], index=df.index)
        k = base + "#" + base.groupby(base).cumcount().astype(str)
    return df.set_axis(k.values)


def norm(v):
    if v is None:
        return None
    if isinstance(v, float):
        if np.isnan(v):
            return None
        return round(v, 6)
    if isinstance(v, (pd.Timestamp,)):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, str):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def same_num(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)))
    return str(a) == str(b)


def geom_diff(ga, gb):
    """返回 None（一致）或差异说明"""
    if ga is None or gb is None:
        return None if (ga is None) == (gb is None) else "一边没有几何"
    ca, cb = shapely.get_coordinates(ga), shapely.get_coordinates(gb)
    if len(ca) != len(cb):                       # 折点数不同（如一边多一个重合 / 共线折点）：按形状差距判
        h = shapely.hausdorff_distance(ga, gb)
        return None if h <= TOL else f"折点数 {len(ca)} / {len(cb)}，形状最大差 {h:.4f} m"
    d = float(np.abs(ca - cb).max()) if len(ca) else 0.0
    return None if d <= TOL else f"坐标最大差 {d:.4f} m"


def cmp_layer(pa, pb, lay):
    A = pyogrio.read_dataframe(pa, layer=lay)
    B = pyogrio.read_dataframe(pb, layer=lay)
    out = {"layer": lay, "n_arcgis": len(A), "n_oss": len(B)}
    geo = "geometry" in A.columns and A.geometry.notna().any()
    if not key_cols(A) and not geo:                     # 表：按整行多重集
        cols = [c for c in A.columns if c.lower() not in SKIP and c in B.columns]
        ra = collections.Counter(tuple(norm(v) for v in r) for r in A[cols].itertuples(index=False))
        rb = collections.Counter(tuple(norm(v) for v in r) for r in B[cols].itertuples(index=False))
        out.update(only_arcgis=sum((ra - rb).values()), only_oss=sum((rb - ra).values()))
        out["same"] = out["only_arcgis"] == 0 and out["only_oss"] == 0 and len(A) == len(B)
        return out
    A, B = keyed(A), keyed(B)
    ka, kb = set(A.index), set(B.index)
    both = sorted(ka & kb)
    out.update(only_arcgis=len(ka - kb), only_oss=len(kb - ka), examples_only_arcgis=sorted(ka - kb)[:5], examples_only_oss=sorted(kb - ka)[:5])
    cols = [c for c in A.columns if c.lower() not in SKIP and c in B.columns and c != "geometry"]
    out["fields_only_arcgis"] = [c for c in A.columns if c not in B.columns and c.lower() not in SKIP]
    out["fields_only_oss"] = [c for c in B.columns if c not in A.columns and c.lower() not in SKIP]
    A2, B2 = A.loc[both], B.loc[both]
    bad = {}
    for c in cols:
        va, vb = [norm(v) for v in A2[c].tolist()], [norm(v) for v in B2[c].tolist()]
        diff = [k for k, x, y in zip(both, va, vb) if not same_num(x, y)]
        if diff:
            i = both.index(diff[0])
            bad[c] = {"n": len(diff), "example": [diff[0], va[i], vb[i]]}
    out["attr_diff"] = bad
    if geo:
        gd = [(k, geom_diff(x, y)) for k, x, y in zip(both, A2.geometry.values, B2.geometry.values)]
        gd = [(k, m) for k, m in gd if m]
        out["geom_diff"] = len(gd)
        out["geom_examples"] = gd[:5]
    out["same"] = (len(A) == len(B) and not out["only_arcgis"] and not out["only_oss"] and not bad and not out.get("geom_diff"))
    return out


def main(pa, pb, out=None):
    sys.stdout.reconfigure(encoding="utf-8")
    la = [n for n, _ in pyogrio.list_layers(pa)]
    lb = {n for n, _ in pyogrio.list_layers(pb)}
    res = []
    for lay in la:
        if lay not in lb:
            res.append({"layer": lay, "same": False, "missing_in_oss": True}); continue
        r = cmp_layer(pa, pb, lay)
        res.append(r)
        flag = "一致" if r["same"] else "不一致"
        extra = ""
        if not r["same"]:
            extra = (f"；只在 ArcGIS {r.get('only_arcgis')}、只在开源 {r.get('only_oss')}；属性不同 "
                     f"{ {k: v['n'] for k, v in r.get('attr_diff', {}).items()} }；几何不同 {r.get('geom_diff')}")
        print(f"  {lay:<16} ArcGIS {r.get('n_arcgis', '—'):>8} / 开源 {r.get('n_oss', '—'):>8}  {flag}{extra}")
    for lay in sorted(lb - set(la)):
        res.append({"layer": lay, "same": False, "missing_in_arcgis": True})
        print(f"  {lay:<16} 只在开源里有")
    n_ok = sum(1 for r in res if r.get("same"))
    print(f"共 {len(res)} 层，一致 {n_ok} 层")
    if out:
        json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    return res


if __name__ == "__main__":
    main(*sys.argv[1:4])
