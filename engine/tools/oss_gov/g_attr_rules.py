"""开源路线：属性规则检查（资产包 B_AttributeRules 里启用的校验规则，同 ArcGIS 的 Evaluate Rules；不用 arcpy）。

  python tools/oss_gov/g_attr_rules.py <工作目录>

规则和参数直接读资产包（B_AttributeRules 的 check_parameters），按规则名实现：
  Invalid Geometry          几何为空 / 线不简单（自相交）
  Multipart                 线的部件数 > 1
  Self Closing Polyline     线首尾闭合
  Cutback Greater Than N°   线在某折点折回、夹角 < N°（参数 minimumangle）
  Duplicate Vertex          相邻折点距离 < 容差（参数 tolerance，单位换成米）
  Stacked Points            同一类里的点完全重合
  Stacked Devices and Junctions  设备和节点完全重合
  Unknown Subtype           资产组 = 0
  Invalid Subtype           资产组不在子类型里
  Invalid Domain Value      编码字段的值不在值域里（不查空值）
  Device - Check Pipe Connection  管连接点（Connection / Pipe Connection）没有装在检查井结构里（没有包含关联）
计算类规则（坡度、连接点生成等）是编辑时触发的，批量装载不执行，两条路线都不做。
输出 08_area/attr_errors.json（字段同 ArcGIS 路线的 ledger_dirty.py：RuleName、ErrorMessage、OriginClassName、OriginGlobalID、Severity、x、y）。
"""
import json
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pyogrio
import shapely

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assetpkg import AssetPackage  # noqa: E402

warnings.filterwarnings("ignore", message="Measured")
UNIT_M = {"foot_us": 1200 / 3937, "foot": 0.3048, "meter": 1.0, "kilometer": 1000.0, "": 1.0}
MSG = {"Invalid Geometry": "Feature geometry is not simple (self-intersecting)", "Self Closing Polyline": "Line/Polyline closes itself",
       "Cutback Greater Than 15 Degrees": "Cutbacks Error", "Device - Check Pipe Connection": "Error, Script error: Pipe Connection must be contained in a Vault",
       "Multipart": "Multipart", "Duplicate Vertex": "Duplicate vertex", "Stacked Points": "Stacked points",
       "Stacked Devices and Junctions": "Stacked devices and junctions", "Unknown Subtype": "Unknown subtype", "Invalid Subtype": "Invalid subtype",
       "Invalid Domain Value": "Invalid domain value", "Invalid Domain": "Invalid domain value"}


def params(s):
    try:
        it = json.loads(s)["propertySetItems"]
        return dict(zip(it[0::2], it[1::2]))
    except Exception:
        return {}


def cutbacks(g, min_deg):
    """折点处的夹角（两段方向之间的内角）< min_deg 的折点"""
    out = []
    for ls in getattr(g, "geoms", [g]):
        c = np.asarray(ls.coords)[:, :2]
        for i in range(1, len(c) - 1):
            a, b = c[i - 1] - c[i], c[i + 1] - c[i]
            na, nb = np.hypot(*a), np.hypot(*b)
            if na == 0 or nb == 0:
                continue
            ang = math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b) / (na * nb)))))
            if ang < min_deg:
                out.append(c[i])
    return out


def backtrack(g, tol=0.001):
    """同 ArcGIS 按 1 mm 容差判「几何不简单」：某折点离隔一段的前一段 ≤ 1 mm、且往回折（两段重叠）。
    GEOS 按精确坐标判，这种情况认为是简单的（SW_AMN_W15_01_PI_142133：末段 1.6 cm 折回，离前一段 0.78 mm）"""
    for ls in getattr(g, "geoms", [g]):
        c = np.asarray(ls.coords)[:, :2]
        for i in range(2, len(c)):
            a, b, p = c[i - 2], c[i - 1], c[i]
            ab, bp = b - a, p - b
            L = np.hypot(*ab)
            if L == 0 or float(ab @ bp) >= 0:
                continue
            t = float((p - a) @ ab) / (L * L)
            if 0 <= t <= 1 and abs(ab[0] * (p - a)[1] - ab[1] * (p - a)[0]) / L <= tol:
                return True
    return False


def main(work):
    sys.stdout.reconfigure(encoding="utf-8")
    rg = os.path.join(work, "07_export", "Stormwater_AD_reporting.gdb")
    ap = AssetPackage(os.path.join(work, "Expanded_local.gdb"))
    R = pyogrio.read_dataframe(os.path.join(work, "Expanded_local.gdb"), layer="B_AttributeRules", read_geometry=False)
    R = R[(R.type == "VALIDATION") & (R.is_enabled == 1)]
    have = {n for n, _ in pyogrio.list_layers(rg)}
    data = {c: pyogrio.read_dataframe(rg, layer=c) for c in R.in_table.unique() if c in have}
    assoc = pyogrio.read_dataframe(rg, layer="Associations", read_geometry=False) if "Associations" in have else pd.DataFrame()
    contained = set(assoc.loc[assoc.get("d_associationtype", pd.Series(dtype=str)) == "Containment", "toglobalid"]) if len(assoc) else set()
    errs = []

    def add(rule, cls, row, pt=None, sev=None):
        g = row.geometry
        p = pt if pt is not None else (shapely.get_coordinates(g)[0] if g is not None and not g.is_empty else (None, None))
        errs.append({"RuleName": rule, "ErrorMessage": MSG.get(rule, rule), "OriginClassName": cls, "OriginGlobalID": row.get("globalid"),
                     "Severity": sev, "x": float(p[0]) if p[0] is not None else None, "y": float(p[1]) if p[1] is not None else None})

    for r in R.itertuples():
        cls, name, P = r.in_table, r.name, params(r.check_parameters)
        df = data.get(cls)
        if df is None or not len(df):
            continue
        sev = int(r.severity) if r.severity == r.severity else None
        subs = json.loads(r.subtype) if isinstance(r.subtype, str) and r.subtype.startswith("[") else None
        if subs:
            codes = [ap.subtype_code(cls, s) for s in subs]
            df = df[df.assetgroup.isin(codes)]
        rows = [x._asdict() for x in df.itertuples(index=False)]
        geoms = df.geometry.values
        if name == "Invalid Geometry":
            bad = [i for i, g in enumerate(geoms) if g is None or g.is_empty or (g.geom_type.endswith("LineString") and (not g.is_simple or backtrack(g)))]
            for i in bad:
                add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name == "Multipart":
            for i in np.flatnonzero(shapely.get_num_geometries(geoms) > 1):
                add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name == "Self Closing Polyline":
            # 首尾坐标完全相同（第一版用 np.allclose，它缺省的相对容差在 270 万的坐标上约 27 m，误报 718 条；ArcGIS 是 1 条）
            for i in np.flatnonzero(shapely.is_closed(geoms) & (shapely.get_num_coordinates(geoms) > 2)):
                add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name.startswith("Cutback"):
            m = float(P.get("minimumangle", 15))
            for i, g in enumerate(geoms):
                for p in cutbacks(g, m)[:1]:
                    add(name, cls, pd.Series(rows[i]), pt=p, sev=sev)
        elif name == "Duplicate Vertex":
            tol = float(P.get("tolerance", 0)) * UNIT_M.get(P.get("unit", ""), 1.0)
            for i, g in enumerate(geoms):
                for ls in getattr(g, "geoms", [g]):
                    c = np.asarray(ls.coords)[:, :2]
                    d = np.hypot(*np.diff(c, axis=0).T) if len(c) > 1 else np.array([])
                    if (d < tol).any():
                        add(name, cls, pd.Series(rows[i]), pt=c[int(np.argmax(d < tol))], sev=sev)
                        break
        elif name in ("Stacked Points", "Stacked Devices and Junctions"):
            other = data.get(P.get("featurestocompare")) if name == "Stacked Devices and Junctions" else df
            if other is None:
                other = pyogrio.read_dataframe(rg, layer=P.get("featurestocompare")) if P.get("featurestocompare") in have else None
            if other is None or not len(other):
                continue
            key = lambda xy: (round(float(xy[0]), 4), round(float(xy[1]), 4))
            ko = pd.Series([key(p) for p in shapely.get_coordinates(other.geometry.values)])
            cnt = ko.value_counts()
            for i, p in enumerate(shapely.get_coordinates(geoms)):
                n = cnt.get(key(p), 0) - (1 if name == "Stacked Points" else 0)
                if n > 0:
                    add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name == "Unknown Subtype":
            for i in np.flatnonzero(df.assetgroup.values == 0):
                add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name == "Invalid Subtype":
            ok = set(ap.subtypes(cls))
            for i in np.flatnonzero(~df.assetgroup.isin(ok).values):
                add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name.startswith("Invalid Domain"):
            bad = np.zeros(len(df), bool)
            for f, ftype, _, dname in ap.fields(cls):          # 按（资产组, 字段）成批查，不逐行查
                fl = f.lower()
                if fl in ("assetgroup",) or fl not in df.columns:
                    continue
                col = df[fl]
                notnull = col.notna().values
                for ag in df.assetgroup.unique():
                    dn = ap.field_domain(cls, ag, fl)
                    dom = ap.domain(dn) if dn else {}
                    if not dom:
                        continue
                    m = (df.assetgroup.values == ag) & notnull
                    bad |= m & ~col.isin(list(dom)).values
            for i in np.flatnonzero(bad):
                add(name, cls, pd.Series(rows[i]), sev=sev)
        elif name == "Device - Check Pipe Connection":
            for i, row in enumerate(rows):
                if row.get("d_assettype") == "Pipe Connection" and row.get("globalid") not in contained:
                    add(name, cls, pd.Series(row), sev=sev)
    os.makedirs(os.path.join(work, "08_area"), exist_ok=True)
    json.dump(errs, open(os.path.join(work, "08_area", "attr_errors.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    print(pd.Series([e["RuleName"] for e in errs]).value_counts().to_dict() if errs else "没有属性规则错误", len(errs))


if __name__ == "__main__":
    main(sys.argv[1])
