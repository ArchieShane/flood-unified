# -*- coding: utf-8 -*-
"""区域依赖数据打包：把公共数据源（全市 GDB / DTM / Makani，见 config/data_sources.toml）按区域裁出来 → data/regions/<区域>/（小、可提交）。

分层：
  config/data_sources.toml     公共数据源（大，不进 git）：全市 GDB、DTM、Makani、Mussafah inp 的位置，按环境分段
  config/model_rules.toml      公共规则
  data/common/                 公共且小的数据：rain_mussafah.json（雨型，所有区域共用）
  data/regions/<区域>/         区域专属（region.toml + 裁出来的数据，可提交）

区域产出：network.gpkg、dtm_5m.tif、catchments.gpkg、aecom_manholes.gpkg、catchment_outlets.csv、makani.gpkg、aecom_elements.json、
          reference_F/、domain.geojson、manifest.json。换区域：复制 data/regions/RDA/region.toml 改一改，再跑本脚本。

用法：
  MAKANI_PASSWORD=*** python tools/region_pack.py --region RDA [--skip-makani]     # 只密码走环境变量，不写进任何文件
  python tools/region_pack.py --common                                              # 重新生成 data/common/（雨型）
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
import data_sources as DS  # noqa: E402

CONF: dict = {}          # 公共数据源（data_sources.sources()）
REGION: dict = {}        # region.toml
MANIFEST: dict = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "files": {}}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def note(out: Path, rel: str, source: str, **info):
    p = out / rel
    MANIFEST["files"][rel] = {"source": source, "bytes": p.stat().st_size if p.is_file() else None, "sha256": sha(p) if p.is_file() else None, **info}
    print(f"  ✓ {rel}  {info}")


def pack_network(out: Path, dom, work: Path):
    import geopandas as gpd
    import pyogrio
    from scipy.spatial import cKDTree
    import gdb_fix
    fixed = work / "fixed.gdb"
    print("修 GDB 工作副本 …")
    gdb_fix.fix_gdb(CONF["gdb"], fixed)
    bb = tuple(dom.bounds)
    marg = float(REGION["network"].get("margin_m", 60))
    ext = (bb[0] - marg, bb[1] - marg, bb[2] + marg, bb[3] + marg)
    lines = pyogrio.read_dataframe(str(fixed), layer="StormwaterLine")   # 全市（27 万条），为了做连通追踪
    # 连通追踪：从 RD-A 出水口出发，端点吸附（0.01 m）后无向遍历
    def ends(g):
        parts = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
        a, b = parts[0].coords[0], parts[-1].coords[-1]
        return (round(a[0], 2), round(a[1], 2)), (round(b[0], 2), round(b[1], 2))
    E = [ends(g) for g in lines.geometry]
    node: dict = {}
    for i, (a, b) in enumerate(E):
        node.setdefault(a, []).append(i); node.setdefault(b, []).append(i)
    dev = pyogrio.read_dataframe(str(fixed), layer="StormwaterDevice", bbox=ext)
    of = dev[dev.assetid.isin(REGION["network"]["outfalls"])]
    keys = list(node); tr = cKDTree(np.array(keys))
    seeds = set()
    for g in of.geometry:
        d, i = tr.query((g.x, g.y))
        if d < 2:
            seeds.add(keys[i])
    seen, stack, conn = set(), list(seeds), set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for i in node[n]:
            if i not in conn:
                conn.add(i); a, b = E[i]; stack.append(b if a == n else a)
    lines["connected"] = [i in conn for i in range(len(lines))]
    sub = lines[lines.geometry.intersects(dom.buffer(marg)) | lines["connected"]].copy()
    ub = sub.total_bounds
    ext = (min(ext[0], ub[0] - marg), min(ext[1], ub[1] - marg), max(ext[2], ub[2] + marg), max(ext[3], ub[3] + marg))
    gp = out / "network.gpkg"
    if gp.exists():
        gp.unlink()
    sub.to_file(gp, layer="lines", driver="GPKG")
    n = {"lines": len(sub), "lines_connected": int(sub.connected.sum())}
    for lay, name in (("StormwaterDevice", "devices"), ("StructureJunction", "structure_junctions"), ("StormwaterJunction", "junctions")):
        df = pyogrio.read_dataframe(str(fixed), layer=lay, bbox=ext)
        df = df[df.geometry.intersects(dom.buffer(marg)) | df.geometry.intersects(sub[sub.connected].unary_union.buffer(1.0))]
        df.to_file(gp, layer=name, driver="GPKG"); n[name] = len(df)
    note(out, "network.gpkg", CONF["gdb"], **n, outfalls=of.assetid.tolist(), extent=[round(x, 1) for x in ext])
    shutil.rmtree(work / "fixed.gdb", ignore_errors=True)
    return ext


def pack_dtm(out: Path, ext):
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(CONF["dtm"]) as r:
        w = from_bounds(*ext, transform=r.transform).round_offsets().round_lengths()
        a = r.read(1, window=w)
        tf = r.window_transform(w)
        prof = dict(driver="GTiff", height=a.shape[0], width=a.shape[1], count=1, dtype="float32", crs=r.crs, transform=tf, nodata=r.nodata, compress="deflate", predictor=3)
    with rasterio.open(out / "dtm_5m.tif", "w", **prof) as d:
        d.write(a, 1)
    valid = a != prof["nodata"]
    note(out, "dtm_5m.tif", CONF["dtm"], shape=list(a.shape), res_m=5, crs="EPSG:32640", nodata_cells=int((~valid).sum()),
         z_min=round(float(a[valid].min()), 2), z_max=round(float(a[valid].max()), 2))


def pack_catchments(out: Path):
    import geopandas as gpd
    c = gpd.read_file(REGION["inputs"]["catchments"]).set_crs(32640, allow_override=True)
    c.to_file(out / "catchments.gpkg", layer="catchments", driver="GPKG")
    m = gpd.read_file(REGION["inputs"]["manholes"]).set_crs(32640, allow_override=True)
    m.to_file(out / "aecom_manholes.gpkg", layer="manholes", driver="GPKG")
    note(out, "catchments.gpkg", REGION["inputs"]["catchments"], n=len(c), note="源 shp 没有 .prj，按 UTM 40N（范围吻合）；成对地块（透水 + 不透水两块几何相同）未去重，由 rda_catchments 处理")
    note(out, "aecom_manholes.gpkg", REGION["inputs"]["manholes"], n=len(m), note="LABEL = 子汇水区 OUT_NODE，ID = OUT_NODEID")
    # 出口 → GDB 节点：AECOM 井坐标找最近的 GDB 设备 / 结构井
    import pandas as pd
    from scipy.spatial import cKDTree
    pts = []
    for lay in ("devices", "structure_junctions"):
        g = gpd.read_file(out / "network.gpkg", layer=lay)
        pts.append(pd.DataFrame({"assetid": g["assetid"], "layer": lay, "x": g.geometry.x, "y": g.geometry.y}))
    P = pd.concat(pts, ignore_index=True)
    tr = cKDTree(P[["x", "y"]].values)
    mm = m[m["LABEL"].astype(str).isin(set(c["OUT_NODE"].astype(str)))]
    d, i = tr.query(np.c_[mm.geometry.x, mm.geometry.y])
    res = pd.DataFrame({"out_node": mm["LABEL"].astype(str).values, "aecom_id": mm["ID"].values, "assetid": P.assetid.values[i], "layer": P.layer.values[i], "dist_m": np.round(d, 3)})
    far_m = float(REGION.get("catchments", {}).get("far_outlet_m", 5.0))
    res["flag"] = np.where(res.dist_m > far_m, "far", np.where(res.dist_m > 1.0, "check", "ok"))   # far = GDB 里没有这口 AECOM 井，按最近节点挂接，需核对
    res.to_csv(out / "catchment_outlets.csv", index=False)
    note(out, "catchment_outlets.csv", "AECOM 井坐标 → 最近 GDB 节点", n=len(res), dist_median=float(res.dist_m.median()), dist_max=float(res.dist_m.max()),
         n_over_1m=int((res.dist_m > 1).sum()), n_over_5m=int((res.dist_m > 5).sum()), n_far=int((res.flag == 'far').sum()), distinct_nodes=int(res.assetid.nunique()))


def pack_makani(out: Path, dom, skip: bool):
    if skip:
        print("  - 跳过 Makani"); return
    import geopandas as gpd
    import psycopg
    from shapely import wkb
    pw = os.environ.get(CONF["makani"].get("password_env", "MAKANI_PASSWORD"))
    if not pw:
        raise SystemExit(f"需要环境变量 {CONF['makani'].get('password_env', 'MAKANI_PASSWORD')}（密码不写进文件）")
    host, port = os.environ.get("MAKANI_HOST", CONF["makani_host"]), os.environ.get("MAKANI_PORT", str(CONF["makani_port"]))
    x0, y0, x1, y1 = dom.buffer(150).bounds
    from pyproj import Transformer
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)
    gp = out / "makani.gpkg"
    if gp.exists():
        gp.unlink()
    counts = {}
    with psycopg.connect(host=host, port=port, user=CONF["makani"]["user"], password=pw, dbname=CONF["makani"]["dbname"], connect_timeout=15) as conn, conn.cursor() as cur:
        for t, s in CONF['makani']['tables'].items():
            env = f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if s == 4326 else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"
            cols = "".join(f", {c}" for c in CONF['makani'].get('extra_columns', {}).get(t, []))
            try:
                cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)){cols} from public.{t} where shape && {env} and not st_isempty(shape)")
                rows = cur.fetchall()
            except Exception as e:  # noqa: BLE001
                conn.rollback(); counts[t] = f"读不到：{str(e).splitlines()[0][:60]}"; continue
            geoms = [(wkb.loads(bytes(r[0])), r[1:]) for r in rows if r[0]]
            geoms = [(g, a) for g, a in geoms if g.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") and not g.is_empty]
            counts[t] = len(geoms)
            if geoms:
                data = {c: [a[i] for _, a in geoms] for i, c in enumerate(CONF['makani'].get('extra_columns', {}).get(t, []))}
                gpd.GeoDataFrame(data, geometry=[g for g, _ in geoms], crs=32640).to_file(gp, layer=t, driver="GPKG")
    note(out, "makani.gpkg", f"Makani {host}:{port}/{CONF['makani']['dbname']}（只读）", counts=counts, extent=[round(v, 1) for v in (x0, y0, x1, y1)])


def pack_makani_points(out: Path, dom, skip: bool):
    """Makani 的点资产（雨水口 inlet、雨水井 catchbasin）→ makani_points.gpkg。GDB 的 INL / CPD 设备就是从这两张表来的（unitid 一致），
    AECOM 的雨水口清单是它们的子集；格栅尺寸字段库里多为 N/A，不能当数据用。"""
    if skip:
        print("  - 跳过 Makani 点资产"); return
    import geopandas as gpd
    import psycopg
    from shapely import wkb
    pw = os.environ.get(CONF["makani"].get("password_env", "MAKANI_PASSWORD"))
    if not pw:
        raise SystemExit("需要环境变量 MAKANI_PASSWORD")
    host, port = os.environ.get("MAKANI_HOST", CONF["makani_host"]), os.environ.get("MAKANI_PORT", str(CONF["makani_port"]))
    x0, y0, x1, y1 = dom.buffer(300).bounds
    gp = out / "makani_points.gpkg"
    if gp.exists():
        gp.unlink()
    counts = {}
    with psycopg.connect(host=host, port=port, user=CONF["makani"]["user"], password=pw, dbname=CONF["makani"]["dbname"], connect_timeout=15) as conn, conn.cursor() as cur:
        for t, srid in CONF["makani"]["points"].items():
            cols = CONF["makani"]["point_columns"][t]
            cur.execute(f"select st_asbinary(st_transform(shape,32640)), {', '.join(cols)} from public.{t} "
                        f"where shape && st_transform(st_makeenvelope({x0},{y0},{x1},{y1},32640),{srid}) and not st_isempty(shape)")
            rows = cur.fetchall()
            gpd.GeoDataFrame({c: [r[i + 1] for r in rows] for i, c in enumerate(cols)}, geometry=[wkb.loads(bytes(r[0])) for r in rows], crs=32640).to_file(gp, layer=t, driver="GPKG")
            counts[t] = len(rows)
    note(out, "makani_points.gpkg", f"Makani {host}:{port}/{CONF['makani']['dbname']} 的 inlet / catchbasin（只读）", counts=counts, extent=[round(v, 1) for v in (x0, y0, x1, y1)],
         note="grating_type / clear_opening / inlet_size 库里基本是 N/A 或 0，格栅尺寸没有数据，规则文件里的 0.635 m 见 model_rules.toml")


def pack_rain(out: Path):
    """公共数据：所有区域共用的雨型，放 data/common/。"""
    import runoff as R
    rs = R.rain_series(Path(CONF["mussafah_inp"]))
    d = {"source": CONF["mussafah_inp"], "series": {k: {"t_h": [round(float(x), 5) for x in t], "cum_mm": [round(float(x), 4) for x in v]} for k, (t, v) in rs.items()},
         "scenario_pick": {"1h": "10_years_CC_1_hour-10yr_TS", "2h": "10_years_with_CC_2_hours-10yr_TS", "24h": "10_years_with_CC-10yr_TS",
                           "note": "9 种场景 {33,61,96} mm × {1,2,24} h 取对应历时的「含 CC」曲线按雨量等比缩放；24 h 的 with_CC / 2024_April_critical / 2026 三条形状完全相同"}}
    (out / "rain_mussafah.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    note(out, "rain_mussafah.json", CONF["mussafah_inp"], n_series=len(rs))


def pack_aecom_elements(out: Path):
    txt = Path(REGION["inputs"]["aecom_inp"]).read_text(encoding="utf-8", errors="replace")
    def sec(name):
        m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", txt, re.S | re.M)
        return [l for l in (m.group(1).splitlines() if m else []) if l.strip() and not l.startswith(";")]
    pump_curves = {l.split()[0] for l in sec("PUMPS")}
    d = {"source": Path(REGION["inputs"]["aecom_inp"]).name, "pumps": sec("PUMPS"), "storage": sec("STORAGE"), "outfalls": sec("OUTFALLS"),
         "curves": [l for l in sec("CURVES") if l.split()[0].startswith(("PC_", "SW_ABT_W33_PSS_69"))],
         "note": "GDB 里泵站 SW_ABT_W33_PSS_69 只有额定值（3 台、510 L/s、6.1 m），没有泵曲线和启停水深；这里的值沿用 AECOM"}
    (out / "aecom_elements.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    note(out, "aecom_elements.json", "F 系列 L1F inp", pumps=len(d["pumps"]), curves=len(d["curves"]), outfalls=len(d["outfalls"]))


def pack_exchange(out: Path):
    """AECOM 雨水口（没有 Catchbasin.shp 时）：取 F 系列输入登记 pilot 里的交换节点类型（inlet / overflow），它就是用 AECOM Catchbasin 提取出来的结果；
    与本区域 GDB 节点按 assetid 对上，对不上的标 not_in_gdb。"""
    import geopandas as gpd
    import pandas as pd
    ref = REGION["inputs"].get("exchange_from")
    if not ref:
        print("  - 没有 exchange_from，跳过")
        return
    x = json.loads((Path(REGION["inputs"]["reference_dir"]) / ref).read_text(encoding="utf-8"))["xchg_type"]
    ids = {}
    for lay in ("devices", "structure_junctions"):
        for a in gpd.read_file(out / "network.gpkg", layer=lay)["assetid"]:
            ids.setdefault(a, lay)
    mk = {}
    mp = out / "makani_points.gpkg"
    if mp.exists():
        for t in ("inlet", "catchbasin"):
            for u in gpd.read_file(mp, layer=t)["unitid"]:
                mk.setdefault(u, t)
    rows = [{"node": k, "type": v, "layer": ids.get(k, ""), "in_gdb": k in ids, "makani_table": mk.get(k, "")} for k, v in x.items()]
    df = pd.DataFrame(rows)
    df.to_csv(out / "aecom_exchange_nodes.csv", index=False)
    inl = df[df.type == "inlet"]
    gdb_inl = {a for a, l in ids.items() if l == "devices" and re.search(r"_(INL|CPD)_", a)}
    note(out, "aecom_exchange_nodes.csv", f"{ref} 的 xchg_type（F 系列用 AECOM Catchbasin 提取的结果）", n_inlet=len(inl), n_inlet_in_gdb=int(inl.in_gdb.sum()),
         n_overflow=int((df.type == "overflow").sum()), n_overflow_in_gdb=int(df[df.type == "overflow"].in_gdb.sum()),
         n_inlet_in_makani=int((inl.makani_table != "").sum()), gdb_inl_cpd_not_aecom=len(gdb_inl - set(inl.node)), note="雨水口以 AECOM 为准：交换节点里 inlet = 这里的 inlet；GDB 里不在 AECOM 清单的 INL / CPD 不当雨水口（只溢流）")


def pack_dem_edits(out: Path):
    """区域专属的地形编辑（深坑填平等）：取参考算例 pilot 里的 dem_edits（[{id, polygon, crest_m}]）→ dem_edits.json。没有参考就不写（该区域没有地形编辑）。"""
    ref = REGION["inputs"].get("dem_edits_from")
    if not ref:
        print("  - 没有 dem_edits_from，跳过（该区域不做地形编辑）")
        return
    eds = json.loads((Path(REGION["inputs"]["reference_dir"]) / ref).read_text(encoding="utf-8")).get("dem_edits") or []
    (out / "dem_edits.json").write_text(json.dumps(eds, ensure_ascii=False), encoding="utf-8")
    note(out, "dem_edits.json", f"{ref} 的 dem_edits", n=len(eds), ids=[e.get("id") for e in eds], note="深坑按周边地面填平（规则 twod.dem_edits.deep_pit_rule）；换区域需要另行提供")


def pack_reference(out: Path):
    ref = out / "reference_F"
    ref.mkdir(exist_ok=True)
    for f in REGION["inputs"].get("reference_files", []):
        shutil.copyfile(Path(REGION["inputs"]["reference_dir"]) / f, ref / f)
        note(out, f"reference_F/{f}", str(Path(REGION["inputs"]["reference_dir"]) / f))


def main() -> int:
    global CONF, REGION
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", help="data/regions/<区域>")
    ap.add_argument("--common", action="store_true", help="只生成 data/common/")
    ap.add_argument("--skip-makani", action="store_true")
    a = ap.parse_args()
    CONF = DS.sources()
    if a.common:
        out = ROOT / "data" / "common"; out.mkdir(parents=True, exist_ok=True)
        pack_rain(out)
        (out / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    if not a.region:
        ap.error("需要 --region 或 --common")
    import geopandas as gpd
    out = ROOT / "data" / "regions" / a.region
    REGION = tomllib.loads((out / "region.toml").read_text(encoding="utf-8"))
    dom = gpd.read_file(out / REGION["region"]["domain"]).to_crs(32640).geometry.union_all()
    with tempfile.TemporaryDirectory() as w:
        ext = pack_network(out, dom, Path(w))
    pack_dtm(out, ext)
    pack_catchments(out)
    pack_makani(out, dom, a.skip_makani)
    pack_makani_points(out, dom, a.skip_makani)
    pack_aecom_elements(out)
    pack_reference(out)
    pack_dem_edits(out)
    pack_exchange(out)
    (out / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    print("manifest.json 已写")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
