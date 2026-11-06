# -*- coding: utf-8 -*-
"""一二维计算的计算域（范围面）：从哪儿来、怎么校验、怎么落成一个面文件交给建模。

计算域可以是任意范围面（2026-10 用户定）：最常用的是行政区、汇水区，以及 C1 这种跨多个汇水区的工程范围。
C1 是工程项目编码，不是汇水区编码。四种来源：

  admin    行政区（makani 库 public.udm_district；第一次用时缓存到 out/domains/admin_districts.geojson）
  catch    汇水区（客户 22 个水力模型的汇水区单元，out/units_from_models/model_units.geojson，与系统里的汇水区同一来源）
  draw     自己画（管理端地图上画的多边形，GeoJSON，经纬度）
  upload   上传面图层（shp 压缩包 / geojson / gpkg / kml，要带坐标系）

可以多选（几个行政区 / 几个汇水区一起算），合并成一个面；外扩、多块处理、填洞的规则同 tools/build_c1.py load_domain。
每次提交都把最终的面写成 out/domains/<运行名>.geojson（EPSG:4326），任务只引用这个文件 —— 来源库以后变了，已提交的任务不受影响。
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import re
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIR = ROOT / "out" / "domains"
UPLOADS = DIR / "uploads"
ADMIN_CACHE = DIR / "admin_districts.geojson"
CATCH_FILE = ROOT / "out" / "units_from_models" / "model_units.geojson"
MAX_UPLOAD = 6 * 1024 * 1024
SOURCES = {"admin": "行政区", "catch": "汇水区", "draw": "自己画", "upload": "上传面图层"}


class DomainError(ValueError):
    pass


def _gpd():
    import geopandas as gpd
    return gpd


# ---------------------------------------------------------------- 备选清单（下拉用）
def admin_layer(cfg: dict | None = None, refresh: bool = False):
    """行政区面（EPSG:4326）：有缓存用缓存，没有就从 makani 库读一次并缓存。库连不上又没有缓存 → DomainError。"""
    gpd = _gpd()
    if ADMIN_CACHE.exists() and not refresh:
        return gpd.read_file(ADMIN_CACHE)
    from . import db
    cfg = cfg or db.load_config(None)
    try:
        with db.connect_source(cfg, "makani", timeout_ms=60_000) as c:
            rows = c.execute("select districtid, nameenglish, municipalityname, st_asgeojson(st_transform(shape, 4326), 6) "
                             "from public.udm_district where shape is not null").fetchall()
    except Exception as ex:
        raise DomainError(f"行政区：makani 库连不上，本机也没有缓存（{type(ex).__name__}: {str(ex)[:120]}）。"
                          f"可以先用汇水区 / 自己画 / 上传，或把行政区面文件放到 {ADMIN_CACHE}") from ex
    from shapely.geometry import shape
    g = gpd.GeoDataFrame({"id": [str(r[0]) for r in rows], "name": [r[1] or str(r[0]) for r in rows], "group": [r[2] or "" for r in rows]},
                         geometry=[shape(json.loads(r[3])) for r in rows], crs=4326)
    DIR.mkdir(parents=True, exist_ok=True)
    g.to_file(ADMIN_CACHE, driver="GeoJSON")
    return g


def catch_layer():
    """汇水区单元（客户 22 个模型，EPSG:4326）：id = unit_id，name，group = 所属模型。"""
    gpd = _gpd()
    if not CATCH_FILE.exists():
        raise DomainError(f"汇水区：没有 {CATCH_FILE.relative_to(ROOT)}（先跑 c1_pipeline run prep:units）")
    g = gpd.read_file(CATCH_FILE).to_crs(4326)
    return gpd.GeoDataFrame({"id": g["unit_id"].astype(str), "name": g["name"].astype(str), "group": g["model"].astype(str),
                             "level": g.get("level", "")}, geometry=g.geometry, crs=4326)


def options(kind: str, cfg: dict | None = None) -> dict:
    """某个来源的备选（id、名称、分组、面积 km²），不带几何（几何走 shapes()）。"""
    g = admin_layer(cfg) if kind == "admin" else catch_layer() if kind == "catch" else None
    if g is None:
        raise DomainError(f"来源 {kind} 没有备选清单")
    a = g.to_crs(32640).area / 1e6
    items = [{"id": i, "name": n, "group": grp, "km2": round(float(k), 2)} for i, n, grp, k in zip(g["id"], g["name"], g["group"], a)]
    return {"kind": kind, "label": SOURCES[kind], "items": sorted(items, key=lambda x: (x["group"], x["name"]))}


def shapes(kind: str, ids: list[str] | None = None, cfg: dict | None = None, simplify_deg: float = 0.0003) -> dict:
    """备选的几何（GeoJSON，经纬度，简化过，给地图显示）。"""
    g = admin_layer(cfg) if kind == "admin" else catch_layer()
    if ids:
        g = g[g["id"].isin([str(i) for i in ids])]
    g = g.copy(); g["geometry"] = g.geometry.simplify(simplify_deg, preserve_topology=True)
    return json.loads(g[["id", "name", "group", "geometry"]].to_json(drop_id=True))


# ---------------------------------------------------------------- 上传
def save_upload(name: str, data: bytes) -> dict:
    """存一个上传的面图层，返回 {upload_id, layers, fields, n, crs}。shp 要打成 zip（.shp/.shx/.dbf/.prj 一起）。"""
    if len(data) > MAX_UPLOAD:
        raise DomainError(f"文件 {len(data) / 1e6:.1f} MB，超过上限 {MAX_UPLOAD / 1e6:.0f} MB")
    ext = pathlib.Path(name or "").suffix.lower()
    if ext not in (".zip", ".geojson", ".json", ".gpkg", ".kml"):
        raise DomainError("只收 .zip（shapefile 打包）/ .geojson / .gpkg / .kml")
    uid = hashlib.sha1(data).hexdigest()[:16]
    d = UPLOADS / uid; d.mkdir(parents=True, exist_ok=True)
    if ext == ".zip":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.namelist():
                p = pathlib.PurePosixPath(m)
                if m.endswith("/") or p.is_absolute() or ".." in p.parts:
                    continue
                if p.suffix.lower() in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson", ".json", ".gpkg", ".kml"):
                    (d / p.name).write_bytes(z.read(m))
        files = sorted(d.glob("*.shp")) or sorted(d.glob("*.gpkg")) or sorted(d.glob("*.geojson")) or sorted(d.glob("*.kml"))
        if not files:
            raise DomainError("压缩包里没有 .shp / .gpkg / .geojson / .kml")
        main = files[0]
    else:
        main = d / ("upload" + ext); main.write_bytes(data)
    info = inspect_file(main)
    (d / "meta.json").write_text(json.dumps({"name": name, "file": main.name, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), **info}, ensure_ascii=False), encoding="utf-8")
    return {"upload_id": uid, "file": main.name, **info}


def upload_path(uid: str) -> pathlib.Path:
    if not re.fullmatch(r"[0-9a-f]{16}", uid or ""):
        raise DomainError(f"上传编号不对：{uid}")
    m = UPLOADS / uid / "meta.json"
    if not m.exists():
        raise DomainError(f"没有这个上传：{uid}")
    return UPLOADS / uid / json.loads(m.read_text(encoding="utf-8"))["file"]


def inspect_file(path: pathlib.Path, layer: str | None = None) -> dict:
    import pyogrio
    layers = [str(x[0]) for x in pyogrio.list_layers(path)]
    g = _gpd().read_file(path, layer=layer or (layers[0] if layers else None))
    if g.empty:
        raise DomainError("图层里没有要素")
    if not g.geom_type.isin(["Polygon", "MultiPolygon"]).any():
        raise DomainError(f"图层里没有面（是 {', '.join(sorted(set(g.geom_type)))}）")
    fields = [c for c in g.columns if c != "geometry"]
    sample = {c: sorted({str(v) for v in g[c].dropna().head(200)})[:30] for c in fields[:20]}
    return {"layers": layers, "fields": fields, "values": sample, "n": int(len(g)), "crs": str(g.crs) if g.crs else None}


# ---------------------------------------------------------------- 解析：规格 → 一个面
def resolve(spec: dict, cfg: dict | None = None):
    """计算域规格 → (shapely Polygon @ EPSG:32640, 说明文字)。

    spec = {source: admin|catch|draw|upload,
            ids: [...]                       admin / catch：选中的 id（可多选）
            geojson: {...}                   draw：画的面（Feature / FeatureCollection / Geometry，经纬度）
            upload_id, layer, field, values  upload：可选按字段取值筛（values 多选）
            buffer_m, multi: error|largest}"""
    gpd = _gpd()
    src = spec.get("source")
    if src not in SOURCES:
        raise DomainError(f"计算域来源只能是 {list(SOURCES)}")
    if src in ("admin", "catch"):
        ids = [str(i) for i in (spec.get("ids") or [])]
        if not ids:
            raise DomainError(f"{SOURCES[src]}：至少选一个")
        g = admin_layer(cfg) if src == "admin" else catch_layer()
        g = g[g["id"].isin(ids)]
        if g.empty:
            raise DomainError(f"{SOURCES[src]}：选的 {ids} 一个都没找到")
        what = f"{SOURCES[src]} " + "、".join(g["name"].astype(str))
    elif src == "draw":
        gj = spec.get("geojson")
        if not gj:
            raise DomainError("自己画：没有收到画的面")
        if isinstance(gj, str):
            gj = json.loads(gj)
        feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj if gj.get("type") == "Feature" else {"type": "Feature", "geometry": gj, "properties": {}}]
        g = gpd.GeoDataFrame.from_features(feats, crs=4326)
        what = f"自己画的范围（{len(g)} 个面）"
    else:
        p = upload_path(spec.get("upload_id") or "")
        g = gpd.read_file(p, layer=spec.get("layer") or None)
        if g.crs is None:
            raise DomainError("上传的图层没有坐标系（shp 要带 .prj）")
        f, vals = spec.get("field"), [str(v) for v in (spec.get("values") or [])]
        if f and vals:
            if f not in g.columns:
                raise DomainError(f"上传的图层没有字段 {f}")
            g = g[g[f].astype(str).isin(vals)]
            if g.empty:
                raise DomainError(f"上传的图层里 {f} ∈ {vals} 没有要素")
        what = f"上传的 {json.loads((p.parent / 'meta.json').read_text(encoding='utf-8'))['name']}" + (f"（{f} = {'、'.join(vals)}）" if f and vals else "")
    g = g[g.geom_type.isin(["Polygon", "MultiPolygon"])]
    if g.empty:
        raise DomainError("选中的要素里没有面")
    if g.crs.is_geographic and (abs(g.total_bounds[[0, 2]]).max() > 180 or abs(g.total_bounds[[1, 3]]).max() > 90):
        raise DomainError("标的是经纬度但坐标是投影坐标，坐标系标错了")
    return finish(g.to_crs(32640).geometry.make_valid().union_all(), float(spec.get("buffer_m") or 0), spec.get("multi") or "error", what)


def finish(D, buffer_m: float = 0.0, multi: str = "error", what: str = ""):
    """外扩 → 去碎片 → 多块按 multi 处理 → 填洞 → 面积下限。返回 (Polygon, 说明, 提示[])。"""
    from shapely.geometry import Polygon
    notes = []
    if buffer_m:
        D = D.buffer(buffer_m)
    parts = sorted([p for p in getattr(D, "geoms", [D]) if p.geom_type == "Polygon"], key=lambda p: -p.area)
    if not parts:
        raise DomainError("合并后不是面")
    tot = sum(p.area for p in parts)
    tiny = [p for p in parts if p.area < 0.01 * tot]
    parts = [p for p in parts if p.area >= 0.01 * tot]
    if tiny:
        notes.append(f"丢掉 {len(tiny)} 块碎片（共 {sum(p.area for p in tiny) / 1e6:.3f} km²，各小于总面积 1%）")
    if len(parts) > 1:
        desc = "、".join(f"{p.area / 1e6:.2f}" for p in parts)
        if multi != "largest":
            raise DomainError(f"范围是 {len(parts)} 块不相连的面（{desc} km²），二维只能算一块：加大外扩让它们连上，或选「只留最大块」，或分成几次算")
        notes.append(f"{len(parts)} 块不相连（{desc} km²），只留最大块")
    D = parts[0]
    if D.interiors:
        notes.append(f"填上 {len(D.interiors)} 个内部的洞（{(Polygon(D.exterior).area - D.area) / 1e6:.3f} km²）")
        D = Polygon(D.exterior)
    if D.area < 0.1e6:
        raise DomainError(f"范围只有 {D.area / 1e6:.4f} km²，太小（坐标系对吗？）")
    return D, what + (f"，外扩 {buffer_m:g} m" if buffer_m else ""), notes


def write(D, run: str, what: str) -> pathlib.Path:
    """把最终的面写成 out/domains/<运行名>.geojson（EPSG:4326），返回路径。"""
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{run}.geojson"
    _gpd().GeoDataFrame({"name": [run], "source": [what], "area_km2": [round(D.area / 1e6, 2)]}, geometry=[D], crs=32640).to_crs(4326).to_file(p, driver="GeoJSON")
    return p


def as_geojson(D) -> dict:
    """面 → GeoJSON（经纬度，简化到约 5 m，给地图预览）。"""
    g = _gpd().GeoSeries([D.simplify(5.0)], crs=32640).to_crs(4326)
    return json.loads(g.to_json(drop_id=True))
