"""开源替身：只实现治理链用到的那一小部分 arcpy 接口，底层是 pyogrio（GDAL）+ shapely。

用途：没有 ArcGIS 时，同一份规则代码（tools/exp_gov/s2_prep.py 等）照样能跑——两条路线共用一套规则，改规则只改一处。
用法：python tools/oss_gov/run_shim.py tools/exp_gov/s2_prep.py <工作目录>
（run_shim 把本目录放到 sys.path 最前面，脚本里的 `import arcpy` 就拿到这个替身；脚本跑完把内存里的数据集一次写盘。）

写盘：文件地理数据库（GDAL OpenFileGDB 驱动；坐标精度和 ArcGIS 缺省一样是 0.0001 m）。
约定：只实现用到的；用到没实现的接口直接报错（NotImplementedError），不悄悄返回假结果。
"""
from __future__ import annotations

import math
import os
import shutil
import uuid
from types import SimpleNamespace

import numpy as np
import pandas as pd
import shapely
from shapely import ops as _ops
from shapely.geometry import LineString, MultiLineString, MultiPolygon
from shapely.geometry import Point as _SPoint
from shapely.geometry import Polygon as _SPolygon

env = SimpleNamespace(overwriteOutput=True, workspace=None)
ENGINE = "oss"                          # 让脚本能分辨跑在哪条路线上（真 arcpy 没有这个属性）


# ------------------------------------------------------------------ 几何
class Point:
    def __init__(self, X=0.0, Y=0.0, Z=None, M=None, ID=None):
        self.X, self.Y, self.Z, self.M = float(X), float(Y), (None if Z is None else float(Z)), M

    def __repr__(self):
        return f"Point({self.X}, {self.Y}, {self.Z})"


class Array:
    def __init__(self, items=None):
        self._a = list(items) if items is not None else []

    def add(self, x):
        self._a.append(x)

    append = add

    @property
    def count(self):
        return len(self._a)

    def __iter__(self):
        return iter(self._a)

    def __len__(self):
        return len(self._a)

    def __getitem__(self, i):
        return self._a[i]


class SpatialReference:
    def __init__(self, code=32640):
        self.factoryCode = code
        self.name = "WGS_1984_UTM_Zone_40N" if code == 32640 else f"EPSG:{code}"


class Extent:
    def __init__(self, XMin, YMin, XMax, YMax):
        self.XMin, self.YMin, self.XMax, self.YMax = XMin, YMin, XMax, YMax
        self.width, self.height = XMax - XMin, YMax - YMin


def _pt(p, has_z=True):
    """has_z=False：同 arcpy，新建几何没声明三维时丢掉 Z（第一版替身保留了 Z，Z 不为 0 的统计因此多数了 35 根管）"""
    if isinstance(p, Point):
        return p if has_z or p.Z is None else Point(p.X, p.Y)
    p = list(p)
    return Point(p[0], p[1], p[2] if len(p) > 2 and has_z else None)


def _parts_of(inp, has_z=True):
    """Array（点）或 Array（Array（点））→ [[Point, ...], ...]；None 分隔（面的内环）原样保留"""
    items = list(inp)
    if items and all(isinstance(x, (Array, list, tuple)) and not (isinstance(x, (list, tuple)) and len(x) in (2, 3)
                                                              and all(isinstance(v, (int, float, np.floating)) for v in x))
                     for x in items if x is not None):
        return [[(None if p is None else _pt(p, has_z)) for p in part] for part in items]
    return [[(None if p is None else _pt(p, has_z)) for p in items]]


def _despike(ring, tol=0.001):
    """同 arcpy 新建面时的简化：去掉折回的尖角——某折点离前后两点连线 ≤ 1 mm（容差）、走向又折回（前后两段方向相反）。
    RD-110 塘 37：一个 0.27 m 长、宽不到 0.2 mm 的尖角，GEOS 认为合法，ArcGIS 去掉了"""
    pts = list(ring)
    closed = len(pts) > 3 and pts[0] == pts[-1]
    if closed:
        pts = pts[:-1]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            ax, ay, bx, by, cx, cy = a[0], a[1], b[0], b[1], c[0], c[1]
            L = math.hypot(cx - ax, cy - ay)
            h = abs((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / L if L > 0 else math.hypot(bx - ax, by - ay)
            back = (bx - ax) * (cx - bx) + (by - ay) * (cy - by) < 0
            if h <= tol and back:
                pts.pop(i); changed = True
                break
    return pts + [pts[0]] if closed else pts


def _dedup(pts):
    """同 arcpy：新建线时，坐标精度（0.0001 m）内完全重合的相邻折点只留一个
    （证据：第一版替身「去掉 5 mm 内的重复折点」比 ArcGIS 多数 25 根管，而两边最终几何逐点一致）"""
    out = []
    for p in pts:
        if out and round(p.X, 4) == round(out[-1].X, 4) and round(p.Y, 4) == round(out[-1].Y, 4):
            continue
        out.append(p)
    return out


class Geometry:
    type = None
    spatialReference = SpatialReference()

    def _shp(self):
        raise NotImplementedError

    @property
    def length(self):
        return float(self._shp().length)

    @property
    def area(self):
        return float(self._shp().area)

    @property
    def extent(self):
        b = self._shp().bounds
        return Extent(*b)

    def distanceTo(self, other):
        return float(self._shp().distance(other._shp()))

    def buffer(self, d):
        return _from_shapely(self._shp().buffer(d))

    def contains(self, other, relation=None):
        return bool(self._shp().covers(other._shp()))

    def within(self, other, relation=None):
        return bool(other._shp().covers(self._shp()))

    @property
    def WKT(self):
        return self._shp().wkt

    @property
    def WKB(self):
        """判重复几何用（s0 探查、s2_prep 的 _ghash）。同 arcpy 只含平面坐标：
        第 0 步探查核对过，按三维判 INLET 重复几何 2,115 处，ArcGIS 是 2,117（差的那 2 处只有 Z 不同）"""
        return shapely.to_wkb(self._shp())

    def _shp3(self):
        return self._shp()


class PointGeometry(Geometry):
    type = "point"

    def __init__(self, inp, spatial_reference=None, has_z=False, has_m=False):
        self._p = _pt(inp, has_z)

    def _shp(self):
        return _SPoint(self._p.X, self._p.Y)

    def _shp3(self):
        return _SPoint(self._p.X, self._p.Y, self._p.Z or 0.0)

    @property
    def firstPoint(self):
        return self._p

    lastPoint = firstPoint
    centroid = firstPoint
    trueCentroid = firstPoint
    isMultipart = False
    partCount = 1
    pointCount = 1

    def __iter__(self):
        return iter([Array([self._p])])


class Polyline(Geometry):
    type = "polyline"

    def __init__(self, inp, spatial_reference=None, has_z=False, has_m=False, _raw=False):
        # _raw：从库里读出来的线保持原样（arcpy 读出的几何不去重合折点，只有新建时才去）
        self._parts = [(lambda x: x if _raw else _dedup(x))([p for p in part if p is not None]) for part in _parts_of(inp, has_z)]
        self._parts = [p for p in self._parts if p]

    def _shp(self):
        ls = [LineString([(p.X, p.Y) for p in part]) for part in self._parts if len(part) >= 2]
        if not ls:
            return LineString()
        return ls[0] if len(ls) == 1 else MultiLineString(ls)

    def _shp3(self):
        ls = [LineString([(p.X, p.Y, p.Z or 0.0) for p in part]) for part in self._parts if len(part) >= 2]
        if not ls:
            return LineString()
        return ls[0] if len(ls) == 1 else MultiLineString(ls)

    @property
    def firstPoint(self):
        return self._parts[0][0] if self._parts else None

    @property
    def lastPoint(self):
        return self._parts[-1][-1] if self._parts else None

    def getPart(self, i=None):
        if i is None:
            return Array([Array(p) for p in self._parts])
        return Array(self._parts[i])

    def __iter__(self):
        return iter([Array(p) for p in self._parts])

    @property
    def isMultipart(self):
        return len(self._parts) > 1

    @property
    def partCount(self):
        return len(self._parts)

    @property
    def pointCount(self):
        return sum(len(p) for p in self._parts)

    def _single(self):
        g = self._shp()
        if isinstance(g, MultiLineString):
            g = _ops.linemerge(g)
            if isinstance(g, MultiLineString):
                raise NotImplementedError("多部件线上沿线取点 / 取段")
        return g

    def positionAlongLine(self, value, use_percentage=False):
        g = self._single()
        p = g.interpolate(value, normalized=use_percentage)
        return PointGeometry(Point(p.x, p.y))

    def segmentAlongLine(self, start, end, use_percentage=False):
        g = self._single()
        s = _ops.substring(g, start, end, normalized=use_percentage)
        if s.geom_type == "Point":
            return Polyline(Array([Point(s.x, s.y), Point(s.x, s.y)]))
        return Polyline(Array([Point(x, y) for x, y in s.coords]))


class Polygon(Geometry):
    type = "polygon"

    def __init__(self, inp, spatial_reference=None, has_z=False, has_m=False):
        self._parts = _parts_of(inp, has_z)             # 每个部件：外环点…[None, 内环点…]

    def _shp(self):
        polys = []
        for part in self._parts:
            rings, cur = [], []
            for p in part:
                if p is None:
                    if cur:
                        rings.append(cur); cur = []
                else:
                    cur.append((p.X, p.Y))
            if cur:
                rings.append(cur)
            rings = [_despike(r) for r in rings]
            if rings and len(rings[0]) >= 3:
                polys.append(_SPolygon(rings[0], [r for r in rings[1:] if len(r) >= 3]))
        if not polys:
            return _SPolygon()
        g = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        if not g.is_valid:                      # 同 arcpy 新建面时的简化：去掉毛刺（走出去又折回来的边）、自相交（RD-110 塘 37 有 0.27 m 的毛刺）
            v = shapely.make_valid(g)
            ps = [x for x in getattr(v, "geoms", [v]) if x.geom_type in ("Polygon", "MultiPolygon")]
            ps = [q for x in ps for q in getattr(x, "geoms", [x])]
            g = ps[0] if len(ps) == 1 else MultiPolygon(ps)
        return g

    @property
    def firstPoint(self):
        return next(p for p in self._parts[0] if p is not None)

    @property
    def centroid(self):
        g = self._shp()
        c = g.centroid if g.covers(g.centroid) else g.representative_point()      # 同 arcpy：在面内取质心，否则取标注点
        return Point(c.x, c.y)

    def boundary(self):
        return _from_shapely(self._shp().boundary)

    def __iter__(self):
        return iter([Array(p) for p in self._parts])

    @property
    def isMultipart(self):
        return len(self._parts) > 1

    @property
    def partCount(self):
        return len(self._parts)

    @property
    def pointCount(self):
        return sum(1 for part in self._parts for p in part if p is not None)


def _from_shapely(g):
    if g is None or g.is_empty:
        return None
    t = g.geom_type
    c3 = lambda xy: Point(*xy)
    if t == "Point":
        return PointGeometry(Point(*g.coords[0]), None, True)
    if t == "MultiPoint":
        return PointGeometry(Point(*g.geoms[0].coords[0]), None, True)
    if t in ("LineString", "LinearRing"):
        return Polyline(Array([c3(xy) for xy in g.coords]), None, True, _raw=True)
    if t == "MultiLineString":
        return Polyline(Array([Array([c3(xy) for xy in ls.coords]) for ls in g.geoms]), None, True, _raw=True)
    if t in ("Polygon", "MultiPolygon"):
        parts = []
        for pg in getattr(g, "geoms", [g]):
            part = [c3(xy) for xy in pg.exterior.coords]
            for r in pg.interiors:
                part.append(None); part += [c3(xy) for xy in r.coords]
            parts.append(Array(part))
        return Polygon(Array(parts), None, True)
    raise NotImplementedError(t)


def _to_shapely(g):
    return None if g is None else g._shp()


# ------------------------------------------------------------------ 数据集（内存登记，脚本结束一次写盘）
_REG: dict = {}          # 规范化路径 → {"gdb", "name", "geom", "fields": [(name, type, length)], "rows": [...] 或 "df"}
_ORDER: list = []


def _abs(p):
    """相对名（如 "CATCHBASIN"）按 env.workspace 解析，同 arcpy"""
    p = str(p)
    if not os.path.isabs(p) and env.workspace and not os.path.exists(p):
        return os.path.join(env.workspace, p)
    return p


def _norm(p):
    return os.path.normcase(os.path.normpath(_abs(p)))


def _split(path):
    p = os.path.normpath(str(path))
    gdb, name = os.path.split(p)
    return gdb, name


def _is_gdb_layer(path):
    gdb, name = _split(path)
    return gdb.lower().endswith((".gdb", ".gpkg", ".geodatabase"))


def _read(path):
    """读一个数据集 → (DataFrame, geometry list or None, 字段类型表)"""
    path = _abs(path)
    k = _norm(path)
    if k in _REG:
        d = _REG[k]
        _materialize(d)
        return d["df"], d["geom"]
    import pyogrio
    if _is_gdb_layer(path):
        gdb, name = _split(path)
        df = pyogrio.read_dataframe(gdb, layer=name, fid_as_index=True)
    else:
        df = pyogrio.read_dataframe(str(path), fid_as_index=True)
    return df, None


def _materialize(d):
    if "df" in d and (d.get("_frozen") or d.get("_n") == len(d["rows"])):
        return                                  # 已经是表（复制来的、加过字段的），不要再用空的行列表覆盖
    names = [f[0] for f in d["fields"]]
    cols = {n: [r[i] for r in d["rows"]] for i, n in enumerate(names)}
    df = pd.DataFrame(cols, index=pd.RangeIndex(1, len(d["rows"]) + 1))
    if d["geom"]:
        import geopandas as gpd
        df = gpd.GeoDataFrame(df, geometry=[_to_shapely(r[-1]) for r in d["rows"]], crs=32640)
    d["df"], d["_n"] = df, len(d["rows"])


_GDBS: set = set()        # 本次运行新建、还没写盘的库


def Exists(path):
    path = _abs(path)
    k = _norm(path)
    if k in _REG or k in _GDBS or any(x.startswith(k + os.sep) for x in _REG):
        return True
    if os.path.exists(path):
        return True
    if _is_gdb_layer(path):
        gdb, name = _split(path)
        if os.path.exists(gdb):
            import pyogrio
            return name.lower() in {n.lower() for n, _ in pyogrio.list_layers(gdb)}
    return False


class _Field:
    def __init__(self, name, type_, length=None):
        self.name, self.type, self.length = name, type_, length or (255 if type_ == "String" else None)


_DT = {"Int16": "SmallInteger", "Int32": "Integer", "Int64": "BigInteger",
       "object": "String", "str": "String", "string": "String", "float64": "Double", "float32": "Single", "int32": "Integer",
       "int16": "SmallInteger", "int64": "BigInteger", "datetime64[ms]": "Date", "datetime64[ns]": "Date", "bool": "SmallInteger"}
_TY = {"TEXT": "String", "DOUBLE": "Double", "FLOAT": "Single", "LONG": "Integer", "SHORT": "SmallInteger", "DATE": "Date", "GUID": "Guid"}


def ListFields(path):
    path = _abs(path)
    k = _norm(path)
    if k in _REG:
        d = _REG[k]
        return [_Field("OBJECTID", "OID")] + [_Field(n, _TY.get(t, "String"), ln) for n, t, ln in d["fields"]] + \
               ([_Field("Shape", "Geometry")] if d["geom"] else [])
    import pyogrio
    gdb, name = _split(path) if _is_gdb_layer(path) else (str(path), None)
    info = pyogrio.read_info(gdb, layer=name)
    out = [_Field("OBJECTID", "OID")]
    for n, t in zip(info["fields"], info["dtypes"]):
        out.append(_Field(n, _DT.get(str(t), "String")))
    if info.get("geometry_type"):
        out.append(_Field("Shape", "Geometry"))
    return out


class _Desc:
    def __init__(self, path):
        df, _ = _read(path)
        b = df.total_bounds if hasattr(df, "total_bounds") else (0, 0, 0, 0)
        self.extent = Extent(*b)
        self.spatialReference = SpatialReference(32640)
        crs = getattr(df, "crs", None)
        if crs is None and str(path).lower().endswith(".shp"):
            self.spatialReference.name = "Unknown"                 # 同 arcpy：没有 .prj 的 shapefile
        gt = df.geometry.geom_type.dropna() if "geometry" in getattr(df, "columns", []) else []
        self.shapeType = {"Point": "Point", "MultiPoint": "Multipoint", "LineString": "Polyline", "MultiLineString": "Polyline",
                          "Polygon": "Polygon", "MultiPolygon": "Polygon"}.get(gt.iloc[0] if len(gt) else None)


def Describe(path):
    return _Desc(path)


def ListFeatureClasses(wild_card=None, feature_type=None):
    ws = env.workspace
    names = [d["name"] for k, d in _REG.items() if _norm(d["gdb"]) == _norm(ws) and d["geom"]]
    if not names and ws and os.path.exists(ws):
        import pyogrio
        names = [n for n, g in pyogrio.list_layers(ws) if g]
    return names


def ListTables(wild_card=None):
    ws = env.workspace
    return [d["name"] for k, d in _REG.items() if _norm(d["gdb"]) == _norm(ws) and not d["geom"]]


def ValidateTableName(name, workspace=None):
    import re
    s = re.sub(r"\W", "_", name)
    return ("T" + s) if s[:1].isdigit() else s


def _null(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else float(v)
    return v


class _da:
    class SearchCursor:
        def __init__(self, path, fields, where_clause=None, spatial_reference=None, explode_to_points=False, sql_clause=None):
            if where_clause:
                raise NotImplementedError("SearchCursor where_clause")
            df, _ = _read(path)
            self.fields = fields
            cols = []
            geo = getattr(df, "geometry", None) if "geometry" in getattr(df, "columns", []) else None
            for f in fields:
                if f in ("OID@",):
                    cols.append(list(df.index))
                elif f in ("SHAPE@",):
                    cols.append([_from_shapely(g) if g is not None else None for g in (geo if geo is not None else [None] * len(df))])
                elif f in ("SHAPE@WKB",):
                    cols.append([shapely.to_wkb(shapely.force_2d(g)) if g is not None else None for g in (geo if geo is not None else [None] * len(df))])
                elif f in ("SHAPE@XY",):
                    cols.append([(g.x, g.y) if g is not None else None for g in geo])
                else:
                    if f not in df.columns:
                        m = {c.lower(): c for c in df.columns}
                        f = m[f.lower()]
                    cols.append([_null(v) for v in df[f].tolist()])
            self._rows = list(zip(*cols)) if cols else []

        def __iter__(self):
            return iter(self._rows)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class InsertCursor:
        def __init__(self, path, fields):
            k = _norm(path)
            if k not in _REG:
                raise NotImplementedError(f"InsertCursor 只支持本次运行新建的数据集：{path}")
            self.d = _REG[k]
            names = [f[0] for f in self.d["fields"]]
            self.pos = []
            self.shape_at = None
            for i, f in enumerate(fields):
                if f == "SHAPE@":
                    self.shape_at = i
                else:
                    self.pos.append((i, names.index(f)))
            self.n = len(names)

        def insertRow(self, row):
            r = [None] * self.n + ([None] if self.d["geom"] else [])
            for i, j in self.pos:
                r[j] = row[i]
            if self.d["geom"]:
                r[-1] = row[self.shape_at] if self.shape_at is not None else None
            self.d["rows"].append(r)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False


da = _da


class _Result:
    def __init__(self, msg=""):
        self._m = msg

    def getMessages(self, *a):
        return self._m

    def __getitem__(self, i):
        return self._m


class _mgmt:
    @staticmethod
    def CreateFileGDB(folder, name, *a, **k):
        p = os.path.join(folder, name if name.endswith(".gdb") else name + ".gdb")
        _mgmt.Delete(p)
        _GDBS.add(_norm(p))
        return _Result(p)

    @staticmethod
    def Delete(path, *a, **k):
        k_ = _norm(path)
        for kk in [x for x in _REG if x == k_ or x.startswith(k_ + os.sep)]:
            _REG.pop(kk); _ORDER.remove(kk)
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        return _Result()

    @staticmethod
    def CreateFeatureclass(gdb, name, geometry_type, template=None, has_m=None, has_z=None, spatial_reference=None, *a, **k):
        p = os.path.join(gdb, name)
        _REG[_norm(p)] = {"gdb": gdb, "name": name, "geom": geometry_type.upper(), "fields": [], "rows": []}
        _ORDER.append(_norm(p))
        return _Result(p)

    @staticmethod
    def CreateTable(gdb, name, *a, **k):
        p = os.path.join(gdb, name)
        _REG[_norm(p)] = {"gdb": gdb, "name": name, "geom": None, "fields": [], "rows": []}
        _ORDER.append(_norm(p))
        return _Result(p)

    @staticmethod
    def AddFields(path, defs, *a, **k):
        d = _REG[_norm(path)]
        if d["rows"]:
            raise NotImplementedError("有数据以后再加字段")
        for f in defs:
            d["fields"].append((f[0], f[1], f[3] if len(f) > 3 else None))
        return _Result()

    @staticmethod
    def AddGlobalIDs(path, *a, **k):
        d = _REG[_norm(path)]
        _materialize(d)
        d["df"]["GlobalID"] = ["{" + str(uuid.uuid4()).upper() + "}" for _ in range(len(d["df"]))]
        d["fields"].append(("GlobalID", "GUID", 38))
        d["_frozen"] = True
        return _Result()

    @staticmethod
    def GetCount(path, *a, **k):
        df, _ = _read(path)
        return _Result(str(len(df)))

    @staticmethod
    def DefineProjection(*a, **k):                  # 开源路线一律按 EPSG:32640 写库
        return _Result()

    @staticmethod
    def AddIndex(*a, **k):
        return _Result()

    @staticmethod
    def JoinField(in_data, in_field, join_table, join_field, fields=None, *a, **k):
        d = _REG[_norm(in_data)]
        _materialize(d)
        t, _ = _read(join_table)
        t = t[[join_field] + list(fields or [c for c in t.columns if c != join_field])].drop_duplicates(join_field)
        idx = d["df"].index
        m = d["df"].merge(t, how="left", left_on=in_field, right_on=join_field, suffixes=("", "_1"))
        if join_field != in_field and join_field in m:
            m = m.drop(columns=[join_field])
        m.index = idx
        d["df"] = m
        tt = _REG.get(_norm(join_table))
        tdefs = {f[0]: f for f in (tt["fields"] if tt else [])}
        for c in (fields or []):
            d["fields"].append(tdefs.get(c, (c, "TEXT", None)))
        d["_frozen"] = True
        return _Result()

    @staticmethod
    def RepairGeometry(path, delete_null="DELETE_NULL", *a, **k):
        """同 Pro「修复几何」对线的处理：去掉连续重复折点（按 0.0001 m 坐标精度）、空部件；不拆多部件"""
        d = _REG[_norm(path)]
        _materialize(d)
        n = 0
        out = []
        for g in d["df"].geometry:
            if g is None or g.is_empty:
                out.append(g); continue
            parts = []
            for ls in getattr(g, "geoms", [g]):
                c = np.round(np.asarray(ls.coords)[:, :2], 4)
                keep = np.r_[True, np.any(np.diff(c, axis=0) != 0, axis=1)]
                c = c[keep]
                if len(c) >= 2:
                    parts.append(LineString(c))
            ng = None if not parts else (parts[0] if len(parts) == 1 else MultiLineString(parts))
            if ng is None or not ng.equals_exact(g, 1e-9):
                n += 1
            out.append(ng)
        d["df"] = d["df"].set_geometry(out)
        d["_frozen"] = True
        return _Result(f"开源替身 RepairGeometry：改了 {n} 条（去重复折点、空部件）")


management = _mgmt


def _dbf_fields(shp):
    """shapefile 的 dbf 字段定义：[(原名, 类型字母, 宽度, 小数位)]"""
    import struct
    with open(os.path.splitext(shp)[0] + ".dbf", "rb") as f:
        f.read(32)
        out = []
        while True:
            d = f.read(32)
            if not d or d[0] == 0x0D:
                break
            out.append((d[:11].split(b"\0")[0].decode("latin1"), chr(d[11]), d[16], d[17]))
    return out


def _dbf_raw(shp, want):
    """按原样读 dbf 里指定字段（不经 GDAL）：{字段名: [原始字节串, …]}，跳过已删除记录（同 GDAL 的行序）"""
    import struct
    base = os.path.splitext(shp)[0]
    with open(base + ".dbf", "rb") as f:
        b = f.read()
    n, hl, rl = struct.unpack("<I", b[4:8])[0], struct.unpack("<H", b[8:10])[0], struct.unpack("<H", b[10:12])[0]
    flds, off, pos = [], 1, 32
    while b[pos] != 0x0D:
        d = b[pos:pos + 32]
        flds.append((d[:11].split(bytes(1))[0].decode("latin1"), d[16], off))
        off += d[16]
        pos += 32
    at = {nm: (o, w) for nm, w, o in flds if nm in want}
    out = {nm: [] for nm in at}
    for i in range(n):
        r = b[hl + i * rl: hl + (i + 1) * rl]
        if not r or r[:1] == b"*":
            continue
        for nm, (o, w) in at.items():
            out[nm].append(r[o:o + w])
    return out


def _enc(shp):
    c = os.path.splitext(shp)[0] + ".cpg"
    if os.path.exists(c):
        e = open(c, encoding="latin1").read().strip()
        return {"UTF-8": "utf-8", "UTF8": "utf-8"}.get(e.upper(), e)
    return "cp1252"


def _atof(raw):
    """同 C 的 atof：取开头能解析成数的部分（'-1.#IND000' → -1.0）；空白 / 星号 → 空值"""
    import re as _re
    m = _re.match(rb"\s*([-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)", raw)
    return float(m.group(1)) if m else None


def _shp_like_arcgis(src, df):
    """同 arcpy 把 shapefile 导进文件地理数据库：
    - 字段名：非字母数字下划线换成 _（Q_(MAX) → Q__MAX_）；已有 OBJECTID 字段改名 OBJECTID_1（库里的 OID 占了这个名字）
    - 类型：C 文本、D 日期、L 短整型、F 双精度；N 有小数 → 双精度，没小数：宽 ≤ 4 短整型、≤ 10 长整型、更宽双精度
      （核对过：DMT 的 N(5,0)、N(10,0) → 长整型，AECOM 的 N(11,0) → 双精度）
    - 空文本读成一个空格（ArcGIS 读 dBase III 的空白文本是 ' '，GDAL 读成空值）"""
    import re as _re
    ren, types = {}, {}
    for n, t, w, d in _dbf_fields(src):
        v = _re.sub(r"[^0-9A-Za-z_]", "_", n)
        if v.upper() == "OBJECTID":
            v = v + "_1"
        ren[n] = v
        types[v] = ("TEXT" if t == "C" else "DATE" if t == "D" else "SHORT" if t == "L" else "DOUBLE" if t == "F" or d > 0
                    else "SHORT" if w <= 4 else "LONG" if w <= 10 else "DOUBLE")
    raw = _dbf_raw(src, {n for n, t, w, d in _dbf_fields(src) if t in "CNF"})
    enc = _enc(src)
    df = df.rename(columns=ren)
    for n, vals in raw.items():
        c = ren[n]
        if len(vals) != len(df):
            continue
        if types[c] == "TEXT":            # 只去末尾空格，开头的空格保留；全空白读成一个空格（同 ArcGIS）
            df[c] = [(v.decode(enc, errors="replace").rstrip(" ") or " ") for v in vals]     # 只去空格：末尾的回车换行、制表符 ArcGIS 保留
        else:                             # 数：GDAL 读不出的（如 '-1.#IND'）按 atof 取开头的数
            cur = pd.to_numeric(df[c], errors="coerce")
            fix = [None if not np.isnan(x) else _atof(v) for x, v in zip(cur.astype(float), vals)]
            df[c] = [x if f is None else f for x, f in zip(cur.astype(float), fix)]
    for c, ty in types.items():
        if c not in df.columns:
            continue
        if ty == "TEXT":
            df[c] = df[c].where(df[c].notna(), " ")
        elif ty in ("SHORT", "LONG"):
            v = df[c]
            if v.dtype == object:
                v = v.map(lambda x: None if x is None else (1 if str(x).upper() in ("T", "Y", "TRUE", "1") else 0))
            df[c] = pd.to_numeric(v, errors="coerce").astype("Int16" if ty == "SHORT" else "Int32")
        elif ty == "DOUBLE":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df, types


class _conv:
    @staticmethod
    def ExportFeatures(src, dst, *a, **k):
        df, _ = _read(src)
        stypes, grid = None, None
        if str(src).lower().endswith(".shp"):
            df, stypes = _shp_like_arcgis(_abs(src), df)
            if not os.path.exists(os.path.splitext(_abs(src))[0] + ".prj") and "geometry" in df.columns:
                import sys as _sys
                _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                from fgdb import grid_unknown_sr
                grid = grid_unknown_sr(df.total_bounds)              # 同 ArcGIS：没有 .prj 的数据用按范围算的细网格
        gdb, name = _split(dst)
        geom = None
        if "geometry" in df.columns and len(df):
            gt = df.geometry.geom_type.dropna()
            geom = {"Point": "POINT", "MultiPoint": "POINT", "LineString": "POLYLINE", "MultiLineString": "POLYLINE",
                    "Polygon": "POLYGON", "MultiPolygon": "POLYGON"}.get(gt.iloc[0] if len(gt) else "Point", "POINT")
        ty = lambda dt: {"float64": "DOUBLE", "float32": "FLOAT", "int32": "LONG", "int64": "LONG", "int16": "SHORT"}.get(str(dt),
                                                                                                         "DATE" if "datetime" in str(dt) else "TEXT")
        if "geometry" in df.columns and getattr(df, "crs", None) is None:
            df = df.set_crs(32640, allow_override=True)
        _REG[_norm(dst)] = {"gdb": gdb, "name": name, "geom": geom,
                            "fields": [(c, (stypes or {}).get(c) or ty(df[c].dtype), None) for c in df.columns if c != "geometry"],
                            "rows": [], "df": df.copy(), "_frozen": True, "grid": grid}
        _ORDER.append(_norm(dst))
        return _Result(dst)


conversion = _conv


# ------------------------------------------------------------------ 写盘
_PD = {"TEXT": "object", "GUID": "object", "DOUBLE": "float64", "FLOAT": "float64", "LONG": "Int32", "SHORT": "Int16", "DATE": "datetime64[ms]"}


def flush(verbose=True):
    """把本次运行登记的数据集写成文件地理数据库（OpenFileGDB）。同一个 .gdb 里多层依次追加。"""
    import pyogrio
    for k in list(_ORDER):
        d = _REG[k]
        _materialize(d)
        df = d["df"]
        for n, t, _ in d["fields"]:                       # 一律按字段定义的类型写（整数字段不能变成 1.0 这样的浮点，和 ArcGIS 对不上）
            if n in df.columns and t in _PD:
                try:
                    if t in ("LONG", "SHORT") and df[n].dtype == object:
                        df[n] = pd.to_numeric(df[n], errors="coerce")
                    df[n] = df[n].astype(_PD[t])
                except (TypeError, ValueError):
                    pass
        os.makedirs(os.path.dirname(d["gdb"]) or ".", exist_ok=True)
        kw = {}
        if d["geom"]:
            gt = {"POINT": "Point", "POLYLINE": "MultiLineString", "POLYGON": "MultiPolygon"}[d["geom"]]
            kw = {"geometry_type": gt, "promote_to_multi": d["geom"] != "POINT"}
            out = df
        else:
            out = pd.DataFrame(df.drop(columns=[c for c in ("geometry",) if c in df.columns]))
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from fgdb import write as _write                      # 坐标网格和 ArcGIS 一致（tools/oss_gov/fgdb.py）
        _write(out, d["gdb"], d["name"], grid=d.get("grid"), **kw)
        if verbose:
            print(f"  [开源替身] 写出 {d['name']}：{len(df):,} 条 → {d['gdb']}", flush=True)
    _REG.clear(); _ORDER.clear()
