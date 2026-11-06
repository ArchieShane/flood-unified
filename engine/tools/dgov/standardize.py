"""1 标准化层：按映射配置把一份原始数据转成标准结构（数据标准 v0.1 第 4 章），裁到一个范围。

  python -m tools.dgov.standardize --source dmt_stormwater --extent rda
  python -m tools.dgov.standardize --source aecom_rda --extent rda

只做「搬」：改字段名、换单位、去基准偏移、把占位值认成缺失、给每个值标来源和置信度。不修错、不补缺、不合并——
那是清洗层、融合层、加工层的事。映射里没写的字段不进标准结构；运算结果列（FLOW_MAX、HGL…）一律不进。
产物：out/dgov/std/<source>__<extent>.gpkg（node / link / pump / storage / catchment 图层 + pump_curve 表）、
      out/dgov/std/<source>__<extent>.log.json（每层每个字段：缺失数、按原值兜底数、值域）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import STD_VERSION
from .common import CONF, OUT, ROOT, lineage, load, raw_manifest, source, write_json
from .schema import TABLES, fields

UN_TYPE = {"gravity": "Gravity Pipe", "force": "Force Pipe", "perforated": "Perforated Pipe", "abandoned": "Unknown",
           "local_supply": "Unknown", "culvert": "Pipe Culvert", "channel": "Ditch Channel"}


def extent(name):
    import geopandas as gpd
    e = load("extents.yaml")["extents"][name]
    s = source(e["from_source"])
    g = gpd.read_file(s["abspath"] / e["layer"]).set_crs(e["crs"], allow_override=True)
    return g.geometry.make_valid().union_all().buffer(e["buffer_m"]), e


def _sha(man, rel):
    by = {f["path"]: f.get("sha256", "") for f in man["files"]}
    stem = rel[:-4]
    return f"shp:{by.get(rel, '')[:16]}/dbf:{by.get(stem + '.dbf', '')[:16]}"


def convert(df, spec, m, default_conf, log_f):
    """一个字段的值和置信度。"""
    n = len(df)
    if "const" in spec:
        return pd.Series([spec["const"]] * n, index=df.index), pd.Series([default_conf] * n, index=df.index)
    col = spec["from"]
    if col not in df.columns:
        log_f["missing_column"] = col
        return pd.Series([None] * n, index=df.index), pd.Series(["missing"] * n, index=df.index)
    s = df[col]
    flag = pd.Series([default_conf] * n, index=df.index, dtype=object)
    if spec.get("date"):
        t = s.astype(str).str[:10]
        bad = s.isna() | t.isin(m.get("null_dates", [])) | t.isin(["NaT", "None", "nan"])
        v = t.where(~bad, None)
    elif "map" in spec:
        txt = s.astype(str).str.strip()
        v = txt.map({str(k): x for k, x in spec["map"].items()})
        unk = v.isna() & ~txt.isin(m.get("null_text", []))
        log_f["unmapped"] = dict(txt[unk].value_counts().head(10))
    elif not pd.api.types.is_numeric_dtype(s):           # 文本（pandas 新版的字符串类型不是 object，要按「不是数值」判）
        txt = s.astype(str).str.strip()
        v = txt.where(~(s.isna() | txt.isin(m.get("null_text", []))), None)
    else:
        raw = pd.to_numeric(s, errors="coerce").astype(float)
        if "null_if" in spec:
            raw = raw.where(~raw.isin([float(x) for x in spec["null_if"]]))
        v = raw * spec.get("scale", 1.0) + spec.get("offset", 0.0)
        if "plausible" in spec:
            lo, hi = spec["plausible"]
            out = v.notna() & ((v < lo) | (v > hi))
            raw_ok = out & (raw >= lo) & (raw <= hi)
            v = v.where(~out)
            v[raw_ok] = raw[raw_ok]
            flag[raw_ok] = "D"
            log_f["fallback_raw"] = int(raw_ok.sum())
            log_f["implausible"] = int((out & ~raw_ok).sum())
        if v.notna().any():
            log_f["range"] = [round(float(v.min()), 3), round(float(v.max()), 3)]
    flag[pd.isna(v)] = "missing"
    log_f["missing"] = int(pd.isna(v).sum())
    return v, flag


def standardize(sid, ext_name, log=print):
    import geopandas as gpd
    import pyogrio
    s = source(sid)
    man = raw_manifest(sid)
    m = load(f"map_{sid}.yaml")
    ext, e = extent(ext_name)
    base = s["abspath"] / m.get("folder", "")
    out_rows = {k: [] for k in ("node", "link", "pump", "storage", "catchment")}
    LOG = {"source": sid, "extent": ext_name, "extent_def": e, "std_version": STD_VERSION, "layers": {}}
    for lname, L in m["layers"].items():
        rel = (Path(m.get("folder", "")) / (lname if lname.lower().endswith(".shp") else lname + ".shp")).as_posix()
        path = s["abspath"] / rel
        g = pyogrio.read_dataframe(path, bbox=tuple(ext.bounds))
        g = g.set_crs(m["crs"], allow_override=True) if g.crs is None else g.to_crs(m["crs"])
        n_box = len(g)
        g = g[g.geometry.intersects(ext)].copy()
        tgt = L["target"]
        lf = {"rows_in_bbox": n_box, "rows_in_extent": len(g), "target": tgt, "fields": {}}
        idf = g[m["id_field"]].astype(str).str.strip() if m["id_field"] in g.columns else pd.Series([""] * len(g), index=g.index)
        noid = idf.isin(m.get("null_text", []) + ["", "None", "nan"])
        idf[noid] = ["OID%s" % x for x in (g["OBJECTID"][noid] if "OBJECTID" in g.columns else g.index[noid])]
        lf["no_id"] = int(noid.sum())
        lf["dup_id"] = int(idf.duplicated(keep=False).sum())
        res = pd.DataFrame(index=g.index)
        layer_key = Path(lname).stem.replace(" ", "_")
        res["asset_id"] = s["prefix"] + ":" + layer_key + ":" + idf
        res["src_id"] = idf
        res["src_dataset"] = sid
        res["src_layer"] = rel
        res["src_sha256"] = _sha(man, rel)
        res["std_version"] = STD_VERSION
        conf = {}
        # 种类
        k = L["kind"]
        if "const" in k:
            kind = pd.Series([k["const"]] * len(g), index=g.index)
        else:
            kind = g[k["field"]].astype(str).map(k["map"]).fillna("unknown")
            if "refine" in k:
                r = g[k["refine"]["field"]].astype(str).map(k["refine"]["map"])
                kind = r.where(r.notna() & (kind == "gravity"), kind)
        if tgt != "catchment":
            res[tgt + "_kind"] = kind
        # 字段
        for f, spec in L["fields"].items():
            lf["fields"][f] = {}
            v, fl = convert(g, spec, m, s["default_conf"], lf["fields"][f])
            res[f] = v
            conf[f] = fl
        for f, c in (L.get("conf_override") or {}).items():
            conf[f] = conf[f].where(conf[f] == "missing", c)
        # Esri UN 分类
        ucls, ugrp, utyp = L["un"]
        res["un_class"], res["un_asset_group"] = ucls, ugrp
        if utyp == "by_kind":
            res["un_asset_type"] = kind.map(UN_TYPE).fillna("Unknown")
        elif utyp == "by_flap":
            res["un_asset_type"] = np.where(res.get("flap", pd.Series(index=g.index, dtype=object)) == "yes", "Flap Gate", "Pipe Outlet")
        else:
            res["un_asset_type"] = utyp
        if tgt == "link":
            res["length_geom"] = g.geometry.length.round(3)
        if tgt == "catchment":
            res["area_geom_m2"] = g.geometry.area.round(1)
        res["conf"] = pd.DataFrame(conf).apply(lambda r: json.dumps({k: v for k, v in r.items()}, ensure_ascii=False), axis=1) if conf else "{}"
        res["geometry"] = g.geometry.force_2d() if hasattr(g.geometry, "force_2d") else g.geometry
        out_rows[tgt].append(res)
        LOG["layers"][rel] = lf
        log(f"  {rel:40} → {tgt:9} {len(g):>7} 行（框内 {n_box}）；无编号 {lf['no_id']}，编号重复 {lf['dup_id']}")
    dst = OUT / "std" / f"{sid}__{ext_name}.gpkg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    for tgt, parts in out_rows.items():
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True)
        cols = [c for c in fields(tgt) if c in df.columns] + [c for c in df.columns if c not in fields(tgt) and c != "geometry"]
        gdf = gpd.GeoDataFrame(df[cols], geometry=df["geometry"], crs=m["crs"])
        gdf.to_file(dst, layer=tgt, driver="GPKG")
    if "pump_curves" in m:
        pc = pump_curves(s, m, dst, LOG, log)
        if pc is not None:
            pyogrio.write_dataframe(pc, dst, layer="pump_curve", driver="GPKG")
    write_json(dst.with_suffix(".log.json"), LOG)
    lineage("standardize", [OUT / "raw" / sid / "manifest.json", CONF / f"map_{sid}.yaml", CONF / "extents.yaml"], [dst, dst.with_suffix(".log.json")],
            {"source": sid, "extent": ext_name})
    log(f"→ {dst}")
    return dst


def pump_curves(s, m, dst, LOG, log):
    """AECOM：泵曲线从 Base 情景的 SWMM 导出取（英尺、CFS → 米、m³/s）；再把标准化后的泵挂上曲线号、吸水湿井、最大流量。"""
    import geopandas as gpd
    import pyogrio
    sys.path.insert(0, str(ROOT / "tools"))
    from r017_l2_audit import inp_section
    pc = m["pump_curves"]
    inp = next(s["abspath"].rglob(f"*_{pc['run']}.inp"))
    ft, cfs = pc["ft"], pc["cfs"]
    pumps = {r[0]: r for r in inp_section(inp, "PUMPS")}
    rows, cur = [], {}
    for r in inp_section(inp, "CURVES"):
        name = r[0]
        if len(r) == 4:
            cur[name] = r[1].upper()
            pts = r[2:4]
        else:
            pts = r[1:3]
        rows.append({"curve_id": name, "curve_type": cur.get(name, ""), "x": float(pts[0]) * ft, "y_m3s": float(pts[1]) * cfs})
    used = {p[3] for p in pumps.values()}
    df = pd.DataFrame([r for r in rows if r["curve_id"] in used])
    df["seq"] = df.groupby("curve_id").cumcount()
    rel = inp.relative_to(s["abspath"]).as_posix()
    man = raw_manifest(s["id"])
    df["src_dataset"], df["src_layer"], df["std_version"] = s["id"], rel, STD_VERSION
    df["src_sha256"] = next((f.get("sha256", "") for f in man["files"] if f["path"] == rel), "")[:16]
    # 泵：shapefile 的 ID = inp 里的泵名
    shp = s["abspath"] / m["folder"]
    sp = pyogrio.read_dataframe(shp / "Pump.shp", read_geometry=False)
    wl = pyogrio.read_dataframe(shp / "Wetwell.shp", read_geometry=False)
    wmap = {str(i): str(lb) for i, lb in zip(wl["ID"], wl["LABEL"])}
    by_label = {str(lb): str(i) for i, lb in zip(sp["ID"], sp["LABEL"])}
    P = gpd.read_file(dst, layer="pump")
    for c in ("curve_id", "wetwell_src"):
        P[c] = P[c].astype(object) if c in P.columns else None
    P["flow_lps"] = P["flow_lps"].astype(float) if "flow_lps" in P.columns else np.nan
    for i, r in P.iterrows():
        pid = by_label.get(r["src_id"])
        if pid in pumps:
            cid = pumps[pid][3]
            P.at[i, "curve_id"] = cid
            P.at[i, "wetwell_src"] = wmap.get(pumps[pid][1], pumps[pid][1])
            q = df.loc[df.curve_id == cid, "y_m3s"]
            P.at[i, "flow_lps"] = round(float(q.max()) * 1000, 1) if len(q) else None
            c = json.loads(r["conf"])
            c.update({"curve_id": "C", "wetwell_src": "C", "flow_lps": "C"})
            P.at[i, "conf"] = json.dumps(c, ensure_ascii=False)
    P.to_file(dst, layer="pump", driver="GPKG")
    LOG["pump_curves"] = {"inp": rel, "curves": int(df.curve_id.nunique()), "points": len(df),
                          "note": "flow_lps = 曲线最大流量（零扬程那一点），不是额定流量"}
    log(f"  泵曲线 {df.curve_id.nunique()} 条（{rel}）")
    return df[[c for c in TABLES["pump_curve"] if c in df.columns]]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.standardize")
    ap.add_argument("--source", required=True)
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    standardize(a.source, a.extent)


if __name__ == "__main__":
    main()
