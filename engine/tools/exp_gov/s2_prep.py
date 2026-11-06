"""第 2 步（预处理）：把原样入库的 DMT 全市 11 层 + AECOM RD-A 7 层，按已确认的映射表变成「一类一个目标资产组」的预处理库，
供 UDMS 生成映射、建迁移工作区、装进 Expanded。

依据：docs/Expanded_本地化与字段映射_待确认_20261023.md（用户 2026-10-23 全部同意 + 第五节补问）。
  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s2_prep.py <工作目录>

输入 <工作目录>/src_dmt.gdb、src_aecom_rda.gdb（第 0 步原样入库）
输出 <工作目录>/02_prep/prep.gdb：
  MH_VAULT、MH_CHANNEL（T-1）  PIPE（T-2，tgt_ag 区分重力 / 穿孔 / 压力）  CATCH_BASIN（T-3、T-4、T-9、T-11，tgt_at 区分）
  END_CAP（T-5）  OUTFALL、FLAP_GATE（T-6）  PUMP（T-7 + AECOM 泵）  WETWELL（第五节 RD-A 泵站）
  BMP_POINT（T-9 GEOCELL、T-10 塘内点）  POND（T-10）
  不进管网的：MOVABLE_PUMP（T-8）、AECOM_CATCHMENT（第三节 4）
  EXCLUDED（S 表和完全重复，排除清单）、ISSUES（问题登记）、CONFLICTS（DMT 与 AECOM 不一致的值）
另写 02_prep/prep_log.json（每条规则命中多少条）。
几何一律写成 Z = 0（DMT 的 Z 不是高程，高程在属性里；Z 不一致会让 Utility Network 连不上）。
"""
import collections
import hashlib
import json
import math
import os
import sys
import time

import arcpy
import numpy as np
from scipy.spatial import cKDTree

arcpy.env.overwriteOutput = True
SR = arcpy.SpatialReference(32640)
SENT = {-999.0, -99.0, -111.11, 999.0, 2760.0, 2576.0, 10308.0}          # 2.2 字段表：哨兵值
ELEV_OK = (-20.0, 250.0)                                                 # 超出只登记，不改
EXCLUDE_STATUS = {"Expired": "S 表：过期记录不装", "Removed": "S 表：已拆除不装", "Abandoned": "S 表：废弃不装"}
OWNER = {"Public": 1, "Private": 2}
SPATIAL = {"AS BUILT": 1, "GPS": 4, "SITESURVEY": 4, "CONSSURVEY": 4}
MAT = {"GRP": 100, "UPVC": 20, "PVC": 20, "SOLID_UPVC": 20, "REINFORCED CONCRETE": 22, "REINFCONCR": 22, "CONCRETE": 7, "VITRIFIED CLAY": 26,
       "HDPI": 15, "HDPE": 15, "AC": 2, "DUCTILE IRON": 11, "DUCTILEIRN": 11, "GRC": 14, "GLASS REINFORCED EPOXY": 12, "STEEL": 25,
       "NDRC": 16, "OTHER": 16, "UNDEFINED": 0, "N/A": 0}
CB_MAT = {"CONCRETE": 101, "DUCT-IRON": 11, "N/A": 0, "UNDEFINED": 0}    # 第五节：雨水口材质
MH_TYPE = {"Manhole": 9, "JctChamber": 3, "ConManhole": 9, "Other": 0}
POND_AT = {"EVAPORATION POND": ("Retention Basin", 16), "Sedimentation Forebay": ("Sediment Trap", 13)}   # 其余 Detention Basin / 1
TOL_LINK = (0.5, 2.0)                                                    # integrate_rda.yaml
TOL_NODE = 0.5
RDA_PS = "SW_ABT_W33_PSS_69"                                             # 第五节：与 AECOM 湿井融合

COMMON = [("assetid", "TEXT", 64), ("assetstate", "SHORT"), ("ownedby", "SHORT"), ("spatialsource", "SHORT"), ("installdate", "DATE"),
          ("notes", "TEXT", 500), ("src_layer", "TEXT", 32), ("src_oid", "LONG"), ("src_compkey", "LONG"), ("src_status", "TEXT", 32),
          ("src_key", "TEXT", 48)]
# 第八轮（用户 2026-10-24 定）：DMT 原始数据里有内容的字段全部带进 Utility Network，字段名加前缀 dmt_，值为原样（治理前）。
# 只给「主记录」带；派生出来的要素（井内流道、拍门、生成接点、系统出流点、塘内 BMP 点）不重复带
RAW_CLASSES = ("MH_VAULT", "PIPE", "CATCH_BASIN", "END_CAP", "OUTFALL", "PUMP", "POND", "WETWELL", "BMP_POINT")
DMT_LAYERS = ("MANHOLECHAMBER", "PIPELINE", "INLET", "CATCHBASIN", "SOAKAWAY", "PETROLINTERCEPTOR", "SW_CAPPEDEND", "OUTFALL", "PUMP", "POND")
SCHEMA = {
    "MH_VAULT": ("POINT", [("manholetype", "SHORT"), ("invertelev", "DOUBLE"), ("rimelev", "DOUBLE"), ("depth", "DOUBLE"), ("src_aecom", "TEXT", 64)]),
    "MH_CHANNEL": ("POINT", [("elevation", "DOUBLE"), ("vault_assetid", "TEXT", 64)]),
    "PIPE": ("POLYLINE", [("tgt_ag", "TEXT", 32), ("diameter", "DOUBLE"), ("upelev", "DOUBLE"), ("downelev", "DOUBLE"), ("measuredlength", "DOUBLE"),
                          ("material", "SHORT"), ("src_material", "TEXT", 64), ("manningn", "DOUBLE"), ("src_aecom", "TEXT", 64)]),
    "CATCH_BASIN": ("POINT", [("tgt_at", "TEXT", 32), ("designtype", "SHORT"), ("classification", "SHORT"), ("elevation", "DOUBLE"),
                              ("coverelev", "DOUBLE"), ("material", "SHORT"), ("src_material", "TEXT", 64)]),
    "END_CAP": ("POINT", [("material", "SHORT"), ("src_material", "TEXT", 64)]),
    "OUTFALL": ("POINT", [("elevation", "DOUBLE"), ("diameter", "DOUBLE"), ("material", "SHORT"), ("src_material", "TEXT", 64),
                          ("boundarytype", "TEXT", 40), ("tailwater", "DOUBLE"), ("src_unittypede", "TEXT", 40), ("src_aecom", "TEXT", 64)]),
    "FLAP_GATE": ("POINT", [("outfall_assetid", "TEXT", 64)]),
    "PUMP": ("POINT", [("rate", "DOUBLE"), ("pumpcount", "SHORT"), ("pumpcurveid", "TEXT", 64), ("onelev", "DOUBLE"), ("offelev", "DOUBLE"),
                       ("elevation", "DOUBLE"), ("src_aecom", "TEXT", 64)]),
    "WETWELL": ("POINT", [("rimelev", "DOUBLE"), ("initelev", "DOUBLE"), ("maxelev", "DOUBLE"), ("xsection", "TEXT", 64), ("station_name", "TEXT", 64),
                          ("station_comments", "TEXT", 255), ("src_aecom", "TEXT", 64)]),
    "BMP_POINT": ("POINT", [("designtype", "SHORT"), ("pond_assetid", "TEXT", 64), ("placement", "TEXT", 64)]),
    "POND": ("POLYGON", [("tgt_at", "TEXT", 32), ("depth", "DOUBLE"), ("volume", "DOUBLE")]),
    "GEN_CONNECTION": ("POINT", [("lines_joined", "TEXT", 120)]),
    "SYSTEM_OUTFLOW": ("POINT", [("anchor_assetid", "TEXT", 64), ("anchor_layer", "TEXT", 32), ("anchor_x", "DOUBLE"), ("anchor_y", "DOUBLE")]),
}
SNAP_TOL = 0.5                                                                                     # 第五轮：悬空端点吸附距离（用户定，同 integrate 节点容差）
SO_OFFSET = 0.5                                                                                    # 第五轮：System Outflow 放在渗井 / 塘点旁 0.5 m
RANK = {"MANHOLECHAMBER": 0, "AECOM.Manholes": 0, "CATCHBASIN": 1, "INLET": 2, "SW_CAPPEDEND": 3}   # 第七节：同点只留一个的优先级
MM_TOL = 0.005                                                                                     # 第九轮（用户 2026-10-24 定「几毫米必须当接上」）：管端离点 / 别的管端 ≤ 5 mm 的，精确移到同一坐标
NO_AECOM = os.environ.get("EXP_NO_AECOM") == "1"   # 对照：只治理 DMT、不融合 AECOM（用户 2026-10-24 批准的对照算例；主线不设）
MM_EXACT = 0.0002                                                                                  # 小于这个距离算已经重合（坐标精度量级）
STACK_TOL = 0.0021                                                                                 # 同一位置：Utility Network XY 容差 0.001 m 的两倍（两点各自的容差圈相交）
FG_CUT = 0.5                                                                                       # 第七节：拍门放在接入管离出水口 0.5 m 处


def t(v):
    return v.strip() if isinstance(v, str) else v


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


class Prep:
    def __init__(self, work):
        self.work = work
        self.rda_ps = None
        import csv as _csv
        self.dtm = {}
        with open(os.path.join(work, "02_prep", "dtm_at_dmt.csv"), encoding="utf-8") as f:   # s2a_dtm_sample.py 的产物
            for r in _csv.DictReader(f):
                self.dtm[(r["layer"], r["oid"])] = float(r["dtm"]) if r["dtm"] not in ("", "nan") else None
        self.D = os.path.join(work, "src_dmt.gdb")
        self.A = os.path.join(work, "src_aecom_rda.gdb")
        self.out = os.path.join(work, "02_prep")
        os.makedirs(self.out, exist_ok=True)
        self.gdb = os.path.join(self.out, "prep.gdb")
        self.rows = collections.defaultdict(list)          # 目标类 → [(geometry, dict)]
        self.excluded, self.issues, self.conflicts = [], [], []
        self.stats = collections.Counter()
        self.zcount = collections.Counter()

    # ---------- 公共 ----------
    def read(self, gdb, fc, need=None):
        """need：只留这些字段（大层省内存）；_ahash 是除 OBJECTID_1 外全部属性的摘要，_ghash 是几何摘要，供判完全重复。"""
        flds = [f.name for f in arcpy.ListFields(os.path.join(gdb, fc)) if f.type not in ("OID", "Geometry") and not f.name.lower().startswith("shape")]
        base = ["STATUS", "UNITID", "OWNERSHIP", "DATA_SOURC", "ASSET_INST", "COMMENTS", "COMPKEY"]
        keepf = None if need is None else set(need) | set(base)
        out = []
        for r in arcpy.da.SearchCursor(os.path.join(gdb, fc), ["OID@", "SHAPE@"] + flds):
            vals = r[2:]
            d = {k: v for k, v in zip(flds, vals) if keepf is None or k in keepf}
            d["_oid"], d["_shape"] = r[0], r[1]
            d["_ahash"] = hashlib.md5(repr([v for k, v in zip(flds, vals) if k != "OBJECTID_1"]).encode("utf-8", "replace")).hexdigest()
            d["_ghash"] = hashlib.md5(r[1].WKB).hexdigest() if r[1] is not None else None
            out.append(d)
        return out

    def issue(self, layer, rec, code, desc):
        self.issues.append({"layer": layer, "src_oid": rec.get("_oid"), "assetid": rec.get("UNITID") or rec.get("LABEL"), "code": code, "desc": desc})
        self.stats[f"issue:{code}"] += 1

    def elev(self, layer, rec, field):
        v = num(rec.get(field))
        if v is None:
            return None
        if v == 0:
            self.stats[f"0→空:{layer}.{field}"] += 1
            return None
        if round(v, 2) in SENT:
            self.issue(layer, rec, "E-SENT", f"{field} = {v} 哨兵值，置空")
            return None
        v = self.datum(layer, rec, field, v)
        if not ELEV_OK[0] <= v <= ELEV_OK[1]:
            self.issue(layer, rec, "E-RANGE", f"{field} = {v} 超出 {ELEV_OK}，原样保留")
        return v

    def datum(self, layer, rec, field, v):
        """第六轮（用户 2026-10-24 定）：统一高程基准到 DTM（= AECOM 模型）基准。DMT 绝大多数高程 = 实际 + 100 m。
        井盖类：与 DTM 差 ±5 m 以内视为已是正常基准，不减；其余减 100 m。管底 / 井底类：在 DTM−25 m 到 DTM+5 m 之间视为正常，不减；其余减 100 m。
        减完仍不在上述范围的，登记 DATUM-ODD（值保留减后的）。"""
        key = (layer, f"{rec['_oid']}_s" if field == "INVERT_LEV" and layer == "PIPELINE" else
               f"{rec['_oid']}_e" if field == "INVERT_L_1" else str(rec["_oid"]))
        z = self.dtm.get(key)
        cover = field.startswith("COVER")
        ok = (lambda x: abs(x - z) <= 5) if cover else (lambda x: z - 25 <= x <= z + 5)
        if z is None or z != z:
            self.stats[f"基准:无 DTM，按 −100 处理:{layer}.{field}"] += 1
            return v - 100.0
        if ok(v):
            self.stats[f"基准:已是正常，不减:{layer}.{field}"] += 1
            return v
        w = v - 100.0
        self.stats[f"基准:−100 m:{layer}.{field}"] += 1
        if not ok(w):
            self.issue(layer, rec, "DATUM-ODD", f"{field} = {v}，减 100 m 后 {w:.2f}，DTM {z:.2f}，仍对不上")
        return w

    def common(self, layer, rec):
        st = t(rec.get("STATUS"))
        if st is None or st == "":
            self.issue(layer, rec, "S-NULL", "STATUS 为空，按在用装入（第五节）")
            state = 2
        elif st == "Closed":
            state = 0
        else:
            state = 2
            if st in ("Undefined", "FinalHover"):
                self.issue(layer, rec, "S-ODD", f"STATUS = {st}，按在用装入（S 表）")
        uid = t(rec.get("UNITID"))
        if not uid or uid == "0":
            self.issue(layer, rec, "ID-EMPTY", f"UNITID = {uid!r}，assetid 置空")
            uid = None
        return {"assetid": uid, "assetstate": state, "ownedby": OWNER.get(t(rec.get("OWNERSHIP"))), "spatialsource": SPATIAL.get(t(rec.get("DATA_SOURC")), 0),
                "installdate": rec.get("ASSET_INST"), "notes": t(rec.get("COMMENTS")) or None, "src_layer": layer, "src_oid": rec["_oid"],
                "src_compkey": rec.get("COMPKEY"), "src_status": st}

    def keep(self, layer, recs, extra_excl=None):
        """S 表排除 + 完全重复（几何和除 OBJECTID_1 外全部属性相同）只留一条；几何相同属性不同的都留并登记。"""
        out, seen, geo = [], {}, collections.defaultdict(list)
        for r in recs:
            st = t(r.get("STATUS"))
            why = EXCLUDE_STATUS.get(st) or (extra_excl(r) if extra_excl else None)
            if why:
                self.excluded.append({"layer": layer, "src_oid": r["_oid"], "assetid": r.get("UNITID"), "reason": why})
                self.stats[f"排除:{layer}:{why}"] += 1
                continue
            gk, ak = r["_ghash"], r["_ahash"]
            if (gk, ak) in seen:
                self.excluded.append({"layer": layer, "src_oid": r["_oid"], "assetid": r.get("UNITID"), "reason": f"完全重复，保留 OID {seen[(gk, ak)]}"})
                self.stats[f"排除:{layer}:完全重复"] += 1
                continue
            seen[(gk, ak)] = r["_oid"]
            geo[gk].append(r)
            out.append(r)
        for gk, rs in geo.items():
            if gk is not None and len(rs) > 1:
                for r in rs:
                    self.issue(layer, r, "DUP-GEOM", f"与 {len(rs) - 1} 条记录几何相同、属性不同，都装入")
        return out

    def add(self, cls, geom, attrs):
        self.rows[cls].append((geom, attrs))

    @staticmethod
    def xy(g):
        return (g.firstPoint.X, g.firstPoint.Y) if g.type == "point" else None

    def z0(self, g, cls):
        """几何改成 Z = 0；记有多少条原 Z 不为 0。"""
        if g is None:
            return None
        if g.type == "point":
            p = g.firstPoint
            if p.Z not in (None, 0) and not (isinstance(p.Z, float) and math.isnan(p.Z)):
                self.zcount[cls] += 1
            return arcpy.PointGeometry(arcpy.Point(p.X, p.Y, 0), SR, True)
        parts, nz = arcpy.Array(), False
        for part in g:
            a = arcpy.Array()
            for p in part:
                if p is None:                              # 面的内环分隔：另起一环
                    if a.count:
                        parts.add(a)
                    a = arcpy.Array()
                    continue
                if p.Z not in (None, 0) and not (isinstance(p.Z, float) and math.isnan(p.Z)):
                    nz = True
                a.add(arcpy.Point(p.X, p.Y, 0))
            if a.count:
                parts.add(a)
        if nz:
            self.zcount[cls] += 1
        return (arcpy.Polyline if g.type == "polyline" else arcpy.Polygon)(parts, SR, True)

    # ---------- 各层 ----------
    def manholes(self):
        L = "MANHOLECHAMBER"
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            dep = num(r.get("DEPTH"))
            if dep == 0:
                dep = None
            elif dep is not None and dep < 0:
                self.issue(L, r, "E-RANGE", f"DEPTH = {dep} 为负，原样保留")
            v = dict(c, manholetype=MH_TYPE.get(t(r.get("MH_CHAMBER")), 0), invertelev=self.elev(L, r, "INVERT_LEV"), rimelev=self.elev(L, r, "COVER_LEVE"), depth=dep)
            self.add("MH_VAULT", r["_shape"], v)
        self.stats["MH_VAULT"] = len(self.rows["MH_VAULT"])

    def pipes(self):
        L = "PIPELINE"

        def excl(r):
            return "S 表：PIPE_SUBTY = Abandoned 不装" if t(r.get("PIPE_SUBTY")) == "Abandoned" else None

        ids = collections.Counter()
        need = ["PIPE_SUBTY", "PIPE_TYPE", "PIPE_LENGT", "INVERT_LEV", "INVERT_L_1", "PIPE_MATER", "PIPE_DIAME"]
        for r in self.keep(L, self.read(self.D, L, need), excl):
            c = self.common(L, r)
            sub, typ = t(r.get("PIPE_SUBTY")), t(r.get("PIPE_TYPE"))
            if sub == "PressrLine":
                ag = "Force Pipe"
            elif sub == "LocSupLine" or typ == "Perforated":
                ag = "Perforated Pipe"
            else:
                ag = "Gravity Pipe"
                if typ not in ("Solid", "Solid Pipe"):
                    self.issue(L, r, "T-ODD", f"PIPE_TYPE = {typ!r}，按重力实管装入")
            if c["assetid"]:
                ids[c["assetid"]] += 1
                if ids[c["assetid"]] > 1:
                    self.issue(L, r, "DUP-ID", f"UNITID 重复，assetid 加后缀 #{ids[c['assetid']]}")
                    c["assetid"] = f"{c['assetid']}#{ids[c['assetid']]}"
            ml = num(r.get("PIPE_LENGT"))
            up, dn = self.elev(L, r, "INVERT_LEV"), self.elev(L, r, "INVERT_L_1")
            if up is not None and dn is not None and up < dn:
                self.issue(L, r, "REV-SLOPE", f"起点管底 {up} 低于终点 {dn}，不翻转（第三节 2）")
            if r["_shape"] is not None and r["_shape"].isMultipart:
                self.issue(L, r, "MULTIPART", "多部件管段，原样装入")
            if c["installdate"] is not None and c["installdate"].year <= 1900:
                self.stats["installdate≤1900"] += 1
            mt = t(r.get("PIPE_MATER"))
            mk = (mt or "").upper()
            code = 20 if mk.startswith("160 MM") else MAT.get(mk)
            if code is None and mt:
                self.issue(L, r, "M-ODD", f"PIPE_MATER = {mt!r} 不在 M 表，置 0")
                code = 0
            v = dict(c, tgt_ag=ag, diameter=num(r.get("PIPE_DIAME")), upelev=up, downelev=dn, measuredlength=None if not ml else ml,
                     material=code, src_material=mt)
            self.add("PIPE", r["_shape"], v)
        self.stats["PIPE"] = len(self.rows["PIPE"])

    def catch_basins(self):
        def dtype(r):
            for f in ("GULLY_TYPE", "UNITTYPE", "INLET_ENTR"):
                s = (t(r.get(f)) or "").upper()
                if s in ("", "N/A", "UNDEFINED", "OTHER", "1", "0", "NA"):
                    continue
                if "FLUSH" in s or "TOP" in s:
                    return 2
                if "KERB" in s or "SIDE" in s or "CURB" in s:
                    return 1
            return 0

        L = "INLET"
        for r in self.keep(L, self.read(self.D, L, ["GULLY_TYPE", "UNITTYPE", "INLET_ENTR", "MATERIAL", "INVERT_LEV", "COVER_LEVE"])):
            c = self.common(L, r)
            m = t(r.get("MATERIAL"))
            v = dict(c, tgt_at="Drop Inlet", designtype=dtype(r), elevation=self.elev(L, r, "INVERT_LEV"), coverelev=self.elev(L, r, "COVER_LEVE"),
                     material=CB_MAT.get((m or "").upper()), src_material=m)
            self.add("CATCH_BASIN", r["_shape"], v)
        L = "CATCHBASIN"
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            cl = {"Type 1": 1, "Type 2": 2}.get(t(r.get("UNITTYPE")), 0)
            v = dict(c, tgt_at="Drop Inlet", classification=cl, elevation=self.elev(L, r, "INVERT_LEV"), coverelev=self.elev(L, r, "COVER_LEVE"))
            self.add("CATCH_BASIN", r["_shape"], v)
        L = "SOAKAWAY"
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            if t(r.get("UNITTYPEDE")) == "GEOCELL":
                self.add("BMP_POINT", r["_shape"], dict(c, designtype=11, placement="DMT GEOCELL 原位"))
                continue
            v = dict(c, tgt_at="Infiltration", elevation=self.elev(L, r, "INVERT_LEV"), coverelev=self.elev(L, r, "COVER_LEVE"))
            self.add("CATCH_BASIN", r["_shape"], v)
        L = "PETROLINTERCEPTOR"
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            v = dict(c, tgt_at="Sediment Trap", elevation=self.elev(L, r, "INVERT_LEV"), coverelev=self.elev(L, r, "COVER_LEVE"))
            self.add("CATCH_BASIN", r["_shape"], v)
        self.stats["CATCH_BASIN"] = len(self.rows["CATCH_BASIN"])

    def end_caps(self):
        L = "SW_CAPPEDEND"
        ids = collections.Counter()
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            if c["assetid"]:
                ids[c["assetid"]] += 1
                if ids[c["assetid"]] > 1:
                    self.issue(L, r, "DUP-ID", f"UNITID 重复，assetid 加后缀 #{ids[c['assetid']]}")
                    c["assetid"] = f"{c['assetid']}#{ids[c['assetid']]}"
            m = t(r.get("MATERIAL"))
            self.add("END_CAP", r["_shape"], dict(c, material=MAT.get((m or "").upper(), 16 if m else None), src_material=m))
        self.stats["END_CAP"] = len(self.rows["END_CAP"])

    def outfalls(self):
        L = "OUTFALL"
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            d = t(r.get("PIPE_DIAME"))
            try:
                dia = float(d) if d not in (None, "") else None
                dia = None if dia == 0 else dia
            except ValueError:
                self.issue(L, r, "D-TEXT", f"PIPE_DIAME = {d!r} 不是单一数值，置空")
                dia = None
            m = t(r.get("PIPE_MATER"))
            ut = t(r.get("UNITTYPEDE"))
            v = dict(c, elevation=self.elev(L, r, "INVERT_LEV"), diameter=dia, material=MAT.get((m or "").upper()), src_material=m, src_unittypede=ut)
            self.add("OUTFALL", r["_shape"], v)
            if ut == "Outfall with Flap Gate":
                self.add("FLAP_GATE", r["_shape"], dict(c, assetid=(c["assetid"] + "-FG") if c["assetid"] else None, outfall_assetid=c["assetid"],
                                                       notes="由 OUTFALL.UNITTYPEDE = Outfall with Flap Gate 生成（T-6）"))
        self.stats["OUTFALL"], self.stats["FLAP_GATE"] = len(self.rows["OUTFALL"]), len(self.rows["FLAP_GATE"])

    def pumps(self):
        L = "PUMP"
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            fr = num(r.get("FLOW_RATE"))
            if not fr:
                fr = None
                self.stats["PUMP rate 0→空"] += 1
            if t(r.get("UNITID")) == RDA_PS and not NO_AECOM:
                self.rda_ps = (r, c)                       # 第五节：与 AECOM 湿井融合，不作 Pump
                continue
            self.add("PUMP", r["_shape"], dict(c, rate=fr, pumpcount=r.get("NO_OF_PUMP") or None))
        L = "MOVABLE_PUMP"
        self.movable = self.read(self.D, L)                # T-8：不进管网，原样另存

    def ponds(self):
        L = "POND"
        self.pond_recs = []
        for r in self.keep(L, self.read(self.D, L)):
            c = self.common(L, r)
            cm = t(r.get("COMMENTS")) or ""
            at = next((v for k, v in POND_AT.items() if k.upper() in cm.upper()), ("Detention Basin", 1))
            dep = num(r.get("DEPTH")) or None
            if dep is not None and dep > 20:
                self.issue(L, r, "E-RANGE", f"DEPTH = {dep} 明显错值，置空")
                dep = None
            v = dict(c, tgt_at=at[0], depth=dep, volume=num(r.get("VOLUME")) or None)
            self.add("POND", r["_shape"], v)
            self.pond_recs.append((r, c, at))

    def pond_points(self):
        """T-10：塘面内加一个 BMP 点，放在接入管末端（落在塘内或塘边 5 m 内的管端里，离塘边界最近的一个）；没有管端就放质心并登记。"""
        ends = []
        for g, a in self.rows["PIPE"]:
            if g is not None:
                ends += [(g.firstPoint, a), (g.lastPoint, a)]
        xy = np.array([(p.X, p.Y) for p, _ in ends])
        tree = cKDTree(xy)
        for r, c, at in self.pond_recs:
            poly = r["_shape"]
            buf = poly.buffer(5)
            ext = buf.extent
            cand = tree.query_ball_point([(ext.XMin + ext.XMax) / 2, (ext.YMin + ext.YMax) / 2], math.hypot(ext.width, ext.height) / 2)
            inside = [i for i in cand if buf.contains(arcpy.PointGeometry(ends[i][0], SR))]
            if inside:
                i = min(inside, key=lambda k: poly.boundary().distanceTo(arcpy.PointGeometry(ends[k][0], SR)))
                pt, how = arcpy.PointGeometry(ends[i][0], SR), f"接入管 {ends[i][1].get('assetid')} 末端（候选 {len(inside)} 个管端）"
                # 第十轮（用户 2026-10-25 定）：塘点让位。管端已有出水口 → 不放塘点；已有检查井 → 不放塘点（Expanded 没有检查井接系统出流的规则，
                # 这个系统终点是检查井，列入剩余问题）；已有封堵端 → 去掉封堵端（水是排进塘的），换成塘点
                p0 = ends[i][0]
                near = lambda cls: [j for j, (g, _) in enumerate(self.rows[cls]) if g is not None and math.hypot(g.firstPoint.X - p0.X, g.firstPoint.Y - p0.Y) <= STACK_TOL]
                if near("OUTFALL") or near("MH_VAULT"):
                    what = "出水口" if near("OUTFALL") else "检查井"
                    self.issue("POND", r, "POND-YIELD", f"塘的接入管末端已有{what}，不放塘点（第十轮）")
                    self.stats[f"塘点让位：已有{what}"] += 1
                    continue
                ec = near("END_CAP")
                if ec:
                    for j in ec:
                        a = self.rows["END_CAP"][j][1]
                        why = f"第十轮：塘 {c.get('assetid')} 的接入管末端，水排进塘、不是封死的，封堵端换成塘点"
                        self.excluded.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "reason": why})
                        self.issues.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "code": "POND-ENDCAP", "desc": why})
                    self.rows["END_CAP"] = [x for j, x in enumerate(self.rows["END_CAP"]) if j not in set(ec)]
                    self.stats["塘点让位：封堵端换成塘点"] += 1
            else:
                pt, how = arcpy.PointGeometry(poly.centroid, SR), "质心（塘内及 5 m 内没有管端）"
                self.issue("POND", r, "POND-NOPIPE", "塘内及边界 5 m 内没有管端，BMP 点放质心")
            self.add("BMP_POINT", pt, dict(c, assetid=(c["assetid"] + "-BMP") if c["assetid"] else None, designtype=at[1], pond_assetid=c["assetid"],
                                           placement=how[:64], notes="由 POND 生成（T-10）"))

    # ---------- AECOM RD-A 融合（integrate_rda.yaml 的规则） ----------
    def aecom(self):
        A = self.A
        if NO_AECOM:
            self.aecom_catch = self.read(A, "Catchment") if arcpy.Exists(os.path.join(A, "Catchment")) else []   # 只为原样另存；没有顾问模型时为空
            self.stats["对照：不融合 AECOM"] = 1
            return
        con = self.read(A, "Conduit")
        mh = self.read(A, "Manholes")
        ofl = self.read(A, "Outfall")
        pps = self.read(A, "Pressure_Pipe")
        pmp = self.read(A, "Pump")
        ww = self.read(A, "Wetwell")
        ext = arcpy.Describe(os.path.join(A, "Conduit")).extent
        box = (ext.XMin - 200, ext.YMin - 200, ext.XMax + 200, ext.YMax + 200)
        P = self.rows["PIPE"]
        idx = [i for i, (g, a) in enumerate(P) if g is not None and a["tgt_ag"] != "Force Pipe"
               and box[0] <= g.firstPoint.X <= box[2] and box[1] <= g.firstPoint.Y <= box[3]]
        eb = np.array([(P[i][0].firstPoint.X, P[i][0].firstPoint.Y, P[i][0].lastPoint.X, P[i][0].lastPoint.Y) for i in idx])
        ep = np.array([(r["_shape"].firstPoint.X, r["_shape"].firstPoint.Y, r["_shape"].lastPoint.X, r["_shape"].lastPoint.Y) for r in con])
        t0, t1 = cKDTree(eb[:, :2]), cKDTree(eb[:, 2:])
        used = set()
        n_match = n_rev = 0
        new_pipes = []
        for j, r in enumerate(con):
            e, hit, rev = ep[j], None, False
            for tol in TOL_LINK:
                a = set(t0.query_ball_point(e[:2], tol)) & set(t1.query_ball_point(e[2:], tol))
                b = set(t1.query_ball_point(e[:2], tol)) & set(t0.query_ball_point(e[2:], tol))
                if a:
                    hit = min(a)
                elif b:
                    hit, rev = min(b), True
                if hit is not None:
                    break
            lab = t(r.get("LABEL"))
            if hit is None:
                new_pipes.append(r)
                continue
            n_match += 1
            n_rev += rev
            g, v = P[idx[hit]]
            used.add(idx[hit])
            pv = {"diameter": num(r.get("D")), "upelev": num(r.get("INV_STP" if rev else "INV_STRT")), "downelev": num(r.get("INV_STRT" if rev else "INV_STP")),
                  "measuredlength": num(r.get("LENGTH"))}
            tol = {"diameter": 1, "upelev": 0.01, "downelev": 0.01, "measuredlength": 0.5}
            filled = []
            for f, x in pv.items():
                if x is None:
                    continue
                if v.get(f) is None:
                    v[f] = x
                    filled.append(f)
                elif abs(v[f] - x) > tol[f]:
                    self.conflicts.append({"class": "PIPE", "assetid": v["assetid"], "field": f, "DMT": v[f], "AECOM": x, "aecom_id": lab})
            v["manningn"] = num(r.get("MANN_N"))
            v["src_aecom"] = lab
            v["notes"] = "; ".join(x for x in (v.get("notes"), "AECOM 融合：曼宁系数" + ("，补 " + "、".join(filled) if filled else "") + ("，AECOM 方向相反" if rev else "")) if x)[:500]
        # 第七节补：AECOM 重力管对不上 DMT 重力 / 穿孔管的，再对 DMT 压力管（两端 0.5 / 2 m）。对上算同一根：
        # 保留 DMT 编号和几何，类型改重力管，曼宁和管底取 AECOM；AECOM 那根不另装；登记「DMT 与 AECOM 类型冲突」（用户 2026-10-23）
        fidx = [i for i, (g, a_) in enumerate(P) if g is not None and a_["tgt_ag"] == "Force Pipe"
                and box[0] <= g.firstPoint.X <= box[2] and box[1] <= g.firstPoint.Y <= box[3]]
        still = []
        if fidx:
            fb = np.array([(P[i][0].firstPoint.X, P[i][0].firstPoint.Y, P[i][0].lastPoint.X, P[i][0].lastPoint.Y) for i in fidx])
            f0, f1 = cKDTree(fb[:, :2]), cKDTree(fb[:, 2:])
            for r in new_pipes:
                g = r["_shape"]
                e = (g.firstPoint.X, g.firstPoint.Y, g.lastPoint.X, g.lastPoint.Y)
                hit, rev = None, False
                for tol in TOL_LINK:
                    a_ = set(f0.query_ball_point(e[:2], tol)) & set(f1.query_ball_point(e[2:], tol))
                    b_ = set(f1.query_ball_point(e[:2], tol)) & set(f0.query_ball_point(e[2:], tol))
                    if a_:
                        hit = min(a_)
                    elif b_:
                        hit, rev = min(b_), True
                    if hit is not None:
                        break
                if hit is None:
                    still.append(r)
                    continue
                v = P[fidx[hit]][1]
                lab = t(r.get("LABEL"))
                old = (v["tgt_ag"], v.get("upelev"), v.get("downelev"))
                v["tgt_ag"] = "Gravity Pipe"
                v["upelev"] = num(r.get("INV_STP" if rev else "INV_STRT"))
                v["downelev"] = num(r.get("INV_STRT" if rev else "INV_STP"))
                v["manningn"] = num(r.get("MANN_N"))
                v["src_aecom"] = lab
                v["notes"] = "; ".join(x for x in (v.get("notes"), f"第七节：DMT 记为压力管，AECOM {lab} 为重力管；按用户定算同一根、取 AECOM 类型 / 曼宁 / 管底（原管底 {old[1]} / {old[2]}）") if x)[:500]
                self.issues.append({"layer": "PIPELINE", "src_oid": v.get("src_oid"), "assetid": v.get("assetid"), "code": "TYPE-CONFLICT",
                                    "desc": f"DMT 压力管 / AECOM 重力管 {lab}，按 AECOM 作重力管"})
                self.stats["AECOM 重力管对上 DMT 压力管（按 AECOM 作重力管）"] += 1
            new_pipes = still
        self.stats["AECOM 重力管"] = len(con)
        self.stats["AECOM 对上 DMT 重力 / 穿孔管"] = n_match
        self.stats["AECOM 方向相反"] = n_rev
        for r in new_pipes:
            lab = t(r.get("LABEL"))
            self.add("PIPE", r["_shape"], {"assetid": f"AECOM:{lab}", "assetstate": 2, "spatialsource": 0, "src_layer": "AECOM.Conduit", "src_oid": r["_oid"],
                                           "tgt_ag": "Gravity Pipe", "diameter": num(r.get("D")), "upelev": num(r.get("INV_STRT")), "downelev": num(r.get("INV_STP")),
                                           "measuredlength": num(r.get("LENGTH")), "manningn": num(r.get("MANN_N")), "src_aecom": lab,
                                           "notes": "AECOM 模型（DMT 无对应，第三节 4）"})
        self.stats["AECOM 新增重力管"] = len(new_pipes)
        # 节点（integrate_rda.yaml：管端先认 DMT 节点，没有才用 AECOM 节点；DMT 节点含检查井和雨水井 / 雨水口）：
        # AECOM 检查井 0.5 m 内有 DMT 检查井或雨水井 / 雨水口 → 补空值、记冲突；否则若是新增管的端点 → 新增井
        # （2026-10-23 修正：第一版只查了 DMT 检查井，AECOM 节点落在 DMT 雨水井上时多建了一口井，UN 报点位重叠 72 处）
        V = self.rows["MH_VAULT"] + list(self.rows["CATCH_BASIN"])
        vt = cKDTree(np.array([(g.firstPoint.X, g.firstPoint.Y) for g, _ in V]))
        new_ends = cKDTree(np.array([(p.X, p.Y) for r in new_pipes for p in (r["_shape"].firstPoint, r["_shape"].lastPoint)])) if new_pipes else None
        n_fuse = n_new = 0
        for r in mh:
            p = r["_shape"].firstPoint
            d, i = vt.query((p.X, p.Y))
            lab = t(r.get("LABEL"))
            if d <= TOL_NODE:
                v = V[i][1]
                n_fuse += 1
                is_cb = i >= len(self.rows["MH_VAULT"])
                if is_cb:
                    self.stats["AECOM 井融合到 DMT 雨水井 / 雨水口"] += 1
                else:
                    v["src_aecom"] = lab
                for f, af in ((("coverelev", "ELV_GND"), ("elevation", "ELV_INV")) if is_cb else (("rimelev", "ELV_GND"), ("invertelev", "ELV_INV"))):
                    x = num(r.get(af))
                    if x is None or x == 0:
                        continue
                    if v.get(f) is None:
                        v[f] = x
                        v["notes"] = "; ".join(s for s in (v.get("notes"), f"AECOM 补 {f}") if s)[:500]
                    elif abs(v[f] - x) > 0.01:
                        self.conflicts.append({"class": "CATCH_BASIN" if is_cb else "MH_VAULT", "assetid": v["assetid"], "field": f, "DMT": v[f], "AECOM": x,
                                               "aecom_id": lab})
            elif new_ends is not None and new_ends.query((p.X, p.Y))[0] <= max(TOL_LINK):
                n_new += 1
                self.add("MH_VAULT", r["_shape"], {"assetid": f"AECOM:{lab}", "assetstate": 2, "spatialsource": 0, "src_layer": "AECOM.Manholes", "src_oid": r["_oid"],
                                                   "manholetype": 9, "rimelev": num(r.get("ELV_GND")), "invertelev": num(r.get("ELV_INV")) or None, "src_aecom": lab,
                                                   "notes": "AECOM 模型（DMT 无对应，AECOM 新增管的端点）"})
        self.stats["AECOM 井融合"], self.stats["AECOM 新增井"] = n_fuse, n_new
        # 出水口
        O = self.rows["OUTFALL"]
        ot = cKDTree(np.array([(g.firstPoint.X, g.firstPoint.Y) for g, _ in O]))
        for r in ofl:
            p = r["_shape"].firstPoint
            d, i = ot.query((p.X, p.Y))
            lab = t(r.get("LABEL"))
            bt, tw = t(r.get("BOUNDCOND")), num(r.get("TAILWATER"))
            if d <= TOL_NODE:
                v = O[i][1]
                v.update(boundarytype=bt, tailwater=tw if bt == "User Defined Tailwater" else None, src_aecom=lab)
                if v.get("elevation") is None and num(r.get("ELV_INV")) not in (None, 0):
                    v["elevation"] = num(r.get("ELV_INV"))
                self.stats["AECOM 出水口融合"] += 1
            else:
                self.add("OUTFALL", r["_shape"], {"assetid": f"AECOM:{lab}", "assetstate": 2, "spatialsource": 0, "src_layer": "AECOM.Outfall", "src_oid": r["_oid"],
                                                  "elevation": num(r.get("ELV_INV")), "boundarytype": bt, "tailwater": tw if bt == "User Defined Tailwater" else None,
                                                  "src_aecom": lab, "notes": f"AECOM 模型（DMT 最近出水口 {d:.1f} m，超出 {TOL_NODE} m）"})
                self.stats["AECOM 新增出水口"] += 1
        # 压力管、泵、湿井
        for r in pps:
            lab = t(r.get("LABEL"))
            self.add("PIPE", r["_shape"], {"assetid": f"AECOM:{lab}", "assetstate": 2, "spatialsource": 0, "src_layer": "AECOM.Pressure_Pipe", "src_oid": r["_oid"],
                                           "tgt_ag": "Force Pipe", "diameter": num(r.get("D")), "measuredlength": num(r.get("LENGTH")), "manningn": num(r.get("MANN_N")),
                                           "src_aecom": lab, "notes": f"AECOM 模型压力管 {t(r.get('START_NODE'))} → {t(r.get('STOP_NODE'))}"})
        for r in pmp:
            lab = t(r.get("LABEL"))
            self.add("PUMP", r["_shape"], {"assetid": f"AECOM:{lab}", "assetstate": 2, "spatialsource": 0, "src_layer": "AECOM.Pump", "src_oid": r["_oid"],
                                           "pumpcurveid": t(r.get("P_DEF")), "onelev": num(r.get("ONELEV")), "offelev": num(r.get("OFFELEV")),
                                           "elevation": num(r.get("ELV_INV")), "src_aecom": lab, "notes": f"AECOM 模型泵，泵站 {RDA_PS}"})
        r = ww[0]
        dr, dc = self.rda_ps
        lab = t(r.get("LABEL"))
        self.add("WETWELL", dr["_shape"], dict(dc, rimelev=num(r.get("ELV_GND")), initelev=num(r.get("INIT_ELEV")), maxelev=num(r.get("MAX_ELEV")),
                                               xsection=t(r.get("X_SECT")), station_name=t(dr.get("PUMP_STATI")), station_comments=t(dr.get("COMMENTS")),
                                               src_aecom=lab, notes=f"第五节：DMT 泵站 {RDA_PS} 与 AECOM 湿井 {lab} 融合，位置取 DMT"))
        self.stats["AECOM 压力管"], self.stats["AECOM 泵"] = len(pps), len(pmp)
        self.aecom_catch = self.read(A, "Catchment")

    # ---------- 第 8 步修正（用户 2026-10-23 定，见确认表第七节；Utility Network 首轮校验报的错误类） ----------
    def fix_stacked(self):
        """错误 25 点位重叠：同一位置（0.001 m）有多个检查井 / 雨水井 / 雨水口 / 封堵端时，按 检查井 > 雨水井 > 雨水口 > 封堵端 只留一个；
        同类留编号小的（DMT 优先于 AECOM 新增）。被去掉的进排除清单并登记，属性不合并。不在这四类里的点不动。"""
        # 第四轮改：「同一位置」按 Utility Network 的 XY 容差判（两点距离 ≤ STACK_TOL），不再按坐标取整到 0.001 m 判。
        # 取整会漏掉相距 0.001–0.002 m、跨过取整边界的点对（第三轮还剩 97 对，UN 照样报重叠和「规则不止一条」）
        items = []
        for cls in ("MH_VAULT", "CATCH_BASIN", "END_CAP"):
            for i, (g, a) in enumerate(self.rows[cls]):
                r = RANK.get(a.get("src_layer"))
                if g is not None and r is not None:
                    items.append(((g.firstPoint.X, g.firstPoint.Y), (r, str(a.get("src_layer", "")).startswith("AECOM"), a.get("src_oid") or 0, cls, i)))
        par = list(range(len(items)))

        def root(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        if items:
            # 第十一轮：同点阈值放到 5 mm（用户「几毫米当接上」）。两个雨水口相距 2.2 mm，UN 仍报重叠和歧义，挡住 SMI-0730 的 3,284 根管
            for i, j in cKDTree(np.array([xy for xy, _ in items])).query_pairs(MM_TOL):
                par[root(i)] = root(j)
        at = collections.defaultdict(list)
        for n, (_, v) in enumerate(items):
            at[root(n)].append(v)
        drop = collections.defaultdict(set)
        for k, lst in at.items():
            if len(lst) < 2:
                continue
            lst.sort()
            keep = lst[0]
            ka = self.rows[keep[3]][keep[4]][1]
            for r, _, oid, cls, i in lst[1:]:
                drop[cls].add(i)
                a = self.rows[cls][i][1]
                why = f"第七节：与 {ka.get('src_layer')} {ka.get('assetid')} 同点（错误 25），按优先级去掉"
                self.excluded.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "reason": why})
                self.issues.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "code": "STACK", "desc": why})
                self.stats[f"同点去掉:{a.get('src_layer')}"] += 1
        for cls, ids in drop.items():
            self.rows[cls] = [x for i, x in enumerate(self.rows[cls]) if i not in ids]

    def fix_flapgates(self):
        """错误 25（出水口 + 拍门同点）：把接入出水口的管在离出水口 0.5 m 处断开，拍门放在断点：管 → 拍门 → 0.5 m 短管 → 出水口。
        管长不到 1 m 的在中点断。没有管接到出水口的拍门放不上去：不建设备，登记。"""
        P = self.rows["PIPE"]
        ends = collections.defaultdict(list)
        for i, (g, a) in enumerate(P):
            if g is not None:
                ends[(round(g.lastPoint.X, 3), round(g.lastPoint.Y, 3))].append((i, "end"))
                ends[(round(g.firstPoint.X, 3), round(g.firstPoint.Y, 3))].append((i, "start"))
        new_fg, add_pipes = [], []
        for g, fa in self.rows["FLAP_GATE"]:
            k = (round(g.firstPoint.X, 3), round(g.firstPoint.Y, 3))
            hits = ends.get(k, [])
            if not hits:
                self.issues.append({"layer": "OUTFALL", "src_oid": fa.get("src_oid"), "assetid": fa.get("outfall_assetid"), "code": "FG-NOPIPE",
                                    "desc": "出水口没有接入管，拍门放不到管上，没建设备"})
                self.stats["拍门放不上（出水口没有管）"] += 1
                continue
            for n, (i, side) in enumerate(hits, start=1):
                pg, pa = P[i]
                L = pg.length
                cut = FG_CUT if L > 2 * FG_CUT else L / 2
                d_cut = L - cut if side == "end" else cut
                a_geom = pg.segmentAlongLine(0, d_cut)
                b_geom = pg.segmentAlongLine(d_cut, L)
                fpt = pg.positionAlongLine(d_cut)
                up, dn = pa.get("upelev"), pa.get("downelev")
                z_cut = up + (dn - up) * d_cut / L if up is not None and dn is not None and L > 0 else None
                main, short = (a_geom, b_geom) if side == "end" else (b_geom, a_geom)
                sa = dict(pa, assetid=(f"{pa['assetid']}-FGS" if pa.get("assetid") else None), measuredlength=round(cut, 3),
                          notes=f"由拍门拆分生成：原管 {pa.get('assetid')} 离出水口 {cut:.2f} m 的一段（第七节）")
                ma = dict(pa)
                if pa.get("measuredlength"):
                    ma["measuredlength"] = max(pa["measuredlength"] - cut, 0.01)
                if side == "end":
                    ma["downelev"], sa["upelev"] = z_cut, z_cut
                else:
                    ma["upelev"], sa["downelev"] = z_cut, z_cut
                ma["notes"] = "; ".join(x for x in (pa.get("notes"), f"第七节：离出水口 {cut:.2f} m 处断开放拍门") if x)[:500]
                P[i] = (main, ma)
                add_pipes.append((short, sa))
                new_fg.append((fpt, dict(fa, assetid=(f"{fa['assetid']}{n}" if len(hits) > 1 and fa.get("assetid") else fa.get("assetid")),
                                         notes=f"第七节：放在接入管 {pa.get('assetid')} 离出水口 {cut:.2f} m 处")))
                self.stats["拍门拆管"] += 1
        P += add_pipes
        self.rows["FLAP_GATE"] = new_fg

    def fix_line_junctions(self):
        """错误 10：不同资产组的管首尾直接相接（中间没有点）→ 接点加 Connection / Pipe Connection（编号 GEN-CN-n），管不动。"""
        key = lambda p: (round(p.X, 3), round(p.Y, 3))
        pxy = []
        for cls in ("MH_CHANNEL", "CATCH_BASIN", "END_CAP", "OUTFALL", "FLAP_GATE", "PUMP", "WETWELL", "BMP_POINT"):
            for g, _ in self.rows[cls]:
                if g is not None:
                    pxy.append((g.firstPoint.X, g.firstPoint.Y))
        ptree = cKDTree(np.array(pxy))                       # 第四轮改：接点上已有点（UN 容差内）就不再加连接点
        ag = collections.defaultdict(set)
        ids = collections.defaultdict(list)
        xy = {}
        for g, a in self.rows["PIPE"]:
            if g is None:
                continue
            for p in (g.firstPoint, g.lastPoint):
                k = key(p)
                ag[k].add(a["tgt_ag"])
                ids[k].append(a.get("assetid") or "?")
                xy[k] = p
        # 第十一轮：管端落在另一类管的中间折点上（连通策略 ANY_VERTEX，UN 认作相连，报错误 10，34 处）→ 也算「不同类管相接」，加连接点
        mids, mag = [], []
        for g, a in self.rows["PIPE"]:
            if g is None:
                continue
            for part in g:
                pp = [p for p in part if p is not None]
                for p in pp[1:-1]:
                    mids.append((p.X, p.Y))
                    mag.append(a["tgt_ag"])
        if mids:
            mt = cKDTree(np.array(mids))
            for k in list(ag):
                for j in mt.query_ball_point((xy[k].X, xy[k].Y), STACK_TOL):
                    if mag[j] not in ag[k]:
                        ag[k].add(mag[j])
                        ids[k].append("（中间折点）")
        n = 0
        for k, s_ in ag.items():
            if len(s_) > 1 and ptree.query((xy[k].X, xy[k].Y))[0] > STACK_TOL:
                n += 1
                self.add("GEN_CONNECTION", arcpy.PointGeometry(xy[k], SR), {"assetid": f"GEN-CN-{n}", "assetstate": 2, "spatialsource": 0,
                                                                          "src_layer": "GENERATED", "lines_joined": " / ".join(ids[k])[:120],
                                                                          "notes": f"第七节：{' 与 '.join(sorted(s_))}首尾相接，加连接点"})
        self.stats["GEN_CONNECTION"] = n

    def snap_aecom_ends(self):
        """对照（NO_AECOM）时没有 AECOM 管，这一步自然不动任何东西。
        integrate_rda.yaml：管端 0.5 m 内认同一个节点。AECOM 新增的重力管、压力管端点若离节点（检查井、雨水井 / 雨水口、湿井、泵、出水口）
        0.001–0.5 m，把端点移到节点上（2026-10-23 第三轮补：前两轮漏了，RD-A 湿井与 AECOM 压力管差 0.09 m 没接上）。"""
        pts = []
        for cls in ("MH_VAULT", "CATCH_BASIN", "WETWELL", "PUMP", "OUTFALL"):
            pts += [(g.firstPoint.X, g.firstPoint.Y) for g, _ in self.rows[cls] if g is not None]
        tr = cKDTree(np.array(pts))
        n = 0
        for i, (g, a_) in enumerate(self.rows["PIPE"]):
            if g is None or not str(a_.get("src_layer", "")).startswith("AECOM"):
                continue
            arr = [[p for p in part] for part in g]
            moved = False
            for pi, vi in ((0, 0), (-1, -1)):
                p = arr[pi][vi]
                d, j = tr.query((p.X, p.Y))
                if 0.001 < d <= TOL_NODE:
                    arr[pi][vi] = arcpy.Point(pts[j][0], pts[j][1])
                    moved = True
            if moved:
                self.rows["PIPE"][i] = (arcpy.Polyline(arcpy.Array([arcpy.Array(part) for part in arr]), SR), a_)
                n += 1
                a_["notes"] = "; ".join(x for x in (a_.get("notes"), "管端吸附到 0.5 m 内的节点（integrate_rda.yaml）") if x)[:500]
        self.stats["AECOM 管端吸附到节点"] = n

    def snap_mm(self):
        """第九轮（用户 2026-10-24 定）：几毫米的缝必须当接上。第十轮改成按簇做：
        5 mm 内的管端连成一簇（链式），簇内或簇旁 5 mm 内有点（检查井、雨水口等）就整簇移到最近的点上，否则移到簇里管端最多的那个位置。
        第九轮的做法逐个管端看，「旁边已有重合管端」就跳过，于是两对各自重合、彼此差 1 mm 的管端永远接不上（UN 报错误 10 共 46 处）。
        只动端点坐标，每条登记 SNAP-MM。"""
        pts = []
        for cls in ("MH_VAULT", "CATCH_BASIN", "END_CAP", "OUTFALL", "PUMP", "WETWELL", "BMP_POINT"):
            pts += [(g.firstPoint.X, g.firstPoint.Y) for g, _ in self.rows[cls] if g is not None]
        ptree = cKDTree(np.array(pts))
        P = self.rows["PIPE"]
        ends = []
        for i, (g, a_) in enumerate(P):
            if g is None:
                continue
            ends.append((i, 0, g.firstPoint.X, g.firstPoint.Y))
            ends.append((i, 1, g.lastPoint.X, g.lastPoint.Y))
        E = np.array([(x, y) for _, _, x, y in ends])
        par = list(range(len(ends)))

        def root(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        for a, b in cKDTree(E).query_pairs(MM_TOL):
            par[root(a)] = root(b)
        cl = collections.defaultdict(list)
        for n in range(len(ends)):
            cl[root(n)].append(n)
        moves = {}
        for idx in cl.values():
            d, j = ptree.query(E[idx])
            k = int(np.argmin(d))
            if d[k] <= MM_TOL:
                tgt, kind = pts[j[k]], "点"
            elif len(idx) > 1:
                cnt = [sum(np.hypot(*(E[m] - E[n])) <= MM_EXACT for m in idx) for n in idx]
                tgt, kind = tuple(E[idx[int(np.argmax(cnt))]]), "管端"
            else:
                continue
            dist = np.hypot(E[idx, 0] - tgt[0], E[idx, 1] - tgt[1])
            if dist.max() > 2 * MM_TOL:
                self.stats["毫米级吸附跳过（簇跨度超过 1 cm）"] += 1
                continue
            for n, dd in zip(idx, dist):
                if dd > MM_EXACT:
                    moves[n] = (tgt, float(dd), kind)
        by_pipe = collections.defaultdict(dict)
        for n, (xy, d, kind) in moves.items():
            i, side, x, y = ends[n]
            by_pipe[i][side] = (xy, d, kind, (x, y))
        for i, sides in by_pipe.items():
            g, a_ = P[i]
            arr = [[p for p in part if p is not None] for part in g]
            for side, (xy, d, kind, old) in sides.items():
                if side == 0:
                    arr[0][0] = arcpy.Point(xy[0], xy[1])
                else:
                    arr[-1][-1] = arcpy.Point(xy[0], xy[1])
            new = arcpy.Polyline(arcpy.Array([arcpy.Array(part) for part in arr]), SR)
            if new.pointCount < 2 or new.length < 0.001:
                self.stats["毫米级吸附跳过（会成零长度线）"] += len(sides)
                continue
            for side, (xy, d, kind, old) in sides.items():
                self.issues.append({"layer": a_.get("src_layer"), "src_oid": a_.get("src_oid"), "assetid": a_.get("assetid"), "code": "SNAP-MM",
                                    "desc": f"{'起点' if side == 0 else '终点'}离{kind} {d * 1000:.1f} mm，移到同一坐标"})
                self.stats[f"毫米级吸附到{kind}"] += 1
            P[i] = (new, a_)
            a_["notes"] = "; ".join(x for x in (a_.get("notes"), "第九轮：毫米级缝吸附（≤5 mm）") if x)[:500]

    def join_multipart(self):
        """第十一轮（用户 2026-10-25 定）：一根管在 DMT 里断成几截（多部件），截与截之间都在 0.5 m 内 → 首尾相连合成一截，编号不变，登记 MULTIPART-JOIN。
        UN 报错误 19（多部件），SW_ARB_RAH_C_PI_252715 两截差 11 cm，挡住 7,070 根管。截间距超过 0.5 m 的不动（仍登记 MULTIPART）。"""
        n = 0
        for i, (g, a) in enumerate(self.rows["PIPE"]):
            if g is None or not g.isMultipart:
                continue
            parts = [[p for p in part if p is not None] for part in g]
            chain = parts.pop(0)
            ok = True
            while parts:
                best = None
                for k, pt in enumerate(parts):
                    for rev in (False, True):
                        q = pt[::-1] if rev else pt
                        for at_end in (True, False):
                            e = chain[-1] if at_end else chain[0]
                            f = q[0] if at_end else q[-1]
                            d = math.hypot(e.X - f.X, e.Y - f.Y)
                            if best is None or d < best[0]:
                                best = (d, k, q, at_end)
                d, k, q, at_end = best
                if d > SNAP_TOL:
                    ok = False
                    break
                chain = chain + q if at_end else q + chain
                parts.pop(k)
            if not ok:
                continue
            self.rows["PIPE"][i] = (arcpy.Polyline(arcpy.Array([arcpy.Array(chain)]), SR), a)
            n += 1
            a["notes"] = "; ".join(x for x in (a.get("notes"), "第十一轮：断成几截的管连成一截") if x)[:500]
            self.issues.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "code": "MULTIPART-JOIN",
                                "desc": f"{g.partCount} 截，截间距 ≤ {SNAP_TOL} m，首尾相连合成一截"})
        self.stats["断成几截的管连成一截"] = n

    def snap_junction_to_point(self):
        """第十二轮（用户 2026-10-25 定）：几根管已在一点相互接上（UN 容差内重合），但这个接点上没有点，而 0.5 m 内有检查井或雨水口 / 雨水井
        → 整个接点（这里所有的管端）一起移到最近的那个检查井 / 雨水口上。和「悬空管端 0.5 m 内吸附到点」同一规则，只是一次移一组。
        全市第十一轮剩下的孤立系统里有 47 处（挡住 2,628 根管），多数相距几毫米到几厘米。每根管登记 SNAP-JCT。"""
        pts = [(g.firstPoint.X, g.firstPoint.Y) for g, _ in self.rows["MH_VAULT"] if g is not None]
        pts += [(g.firstPoint.X, g.firstPoint.Y) for g, a in self.rows["CATCH_BASIN"] if g is not None and a.get("src_layer") in ("INLET", "CATCHBASIN")]
        allp = []
        for cls in ("MH_VAULT", "CATCH_BASIN", "END_CAP", "OUTFALL", "PUMP", "WETWELL", "BMP_POINT"):
            allp += [(g.firstPoint.X, g.firstPoint.Y) for g, _ in self.rows[cls] if g is not None]
        ptree, atree = cKDTree(np.array(pts)), cKDTree(np.array(allp))
        P = self.rows["PIPE"]
        ends = []
        for i, (g, a_) in enumerate(P):
            if g is None:
                continue
            ends.append((i, 0, g.firstPoint.X, g.firstPoint.Y))
            ends.append((i, 1, g.lastPoint.X, g.lastPoint.Y))
        E = np.array([(x, y) for _, _, x, y in ends])
        par = list(range(len(ends)))

        def root(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        for a, b in cKDTree(E).query_pairs(STACK_TOL):
            par[root(a)] = root(b)
        cl = collections.defaultdict(list)
        for n in range(len(ends)):
            cl[root(n)].append(n)
        by_pipe = collections.defaultdict(dict)
        nj = 0
        for idx in cl.values():
            if len({ends[n][0] for n in idx}) < 2:
                continue                                   # 只处理「几根管的接点」；单个悬空管端前面已处理
            c = E[idx].mean(0)
            if atree.query(c)[0] <= STACK_TOL:
                continue                                   # 接点上已有点
            d, j = ptree.query(c)
            if d > SNAP_TOL:
                continue
            nj += 1
            for n in idx:
                by_pipe[ends[n][0]][ends[n][1]] = (pts[j], float(d))
        for i, sides in by_pipe.items():
            g, a_ = P[i]
            arr = [[p for p in part if p is not None] for part in g]
            for side, (xy, d) in sides.items():
                if side == 0:
                    arr[0][0] = arcpy.Point(*xy)
                else:
                    arr[-1][-1] = arcpy.Point(*xy)
            new = arcpy.Polyline(arcpy.Array([arcpy.Array(part) for part in arr]), SR)
            if new.pointCount < 2 or new.length < 0.01:
                self.stats["接点移到井上跳过（会成零长度线）"] += 1
                continue
            P[i] = (new, a_)
            for side, (xy, d) in sides.items():
                self.issues.append({"layer": a_.get("src_layer"), "src_oid": a_.get("src_oid"), "assetid": a_.get("assetid"), "code": "SNAP-JCT",
                                    "desc": f"{'起点' if side == 0 else '终点'}所在的管接点离检查井 / 雨水口 {d:.3f} m，整组移到井上"})
            a_["notes"] = "; ".join(x for x in (a_.get("notes"), "第十二轮：管接点移到 0.5 m 内的井上") if x)[:500]
        self.stats["管接点移到井上（处）"] = nj

    def snap_to_segment(self):
        """第十二轮（用户 2026-10-25 定）：管端接到别的管的中段（典型是支管接干管，数字化时没在干管上打折点）。
        Expanded 的线是 ANY_VERTEX 连通：管端落在别的管的中段、那里没有折点，就不算相接。
          a) 管端离别的管中段 ≤ 5 mm（「几毫米当接上」）：不动管端，在那根管上、管端的位置加一个折点；
          b) 悬空管端（UN 容差内不碰点、不碰别的管端）离别的管中段 5 mm–0.5 m：管端移到那根管上最近的位置，并在那里加折点。
        已经落在那根管折点上的（≤ 2.1 mm）不算。不同类型的管，后面「不同类管相接加连接点」会在这个折点上加连接点。每条登记 SNAP-SEG。"""
        pts = []
        for cls in ("MH_VAULT", "CATCH_BASIN", "END_CAP", "OUTFALL", "PUMP", "WETWELL", "BMP_POINT"):
            pts += [(g.firstPoint.X, g.firstPoint.Y) for g, _ in self.rows[cls] if g is not None]
        ptree = cKDTree(np.array(pts))
        P = self.rows["PIPE"]
        ends, seg = [], []
        for i, (g, a_) in enumerate(P):
            if g is None or g.isMultipart:
                continue
            vs = [p for p in g.getPart(0) if p is not None]
            ends.append((i, 0, vs[0].X, vs[0].Y))
            ends.append((i, 1, vs[-1].X, vs[-1].Y))
            for k in range(len(vs) - 1):
                seg.append((i, k, vs[k].X, vs[k].Y, vs[k + 1].X, vs[k + 1].Y))
        E = np.array([(x, y) for _, _, x, y in ends])
        etree = cKDTree(E)
        S = np.array([s_[2:] for s_ in seg])
        CELL = 20.0
        grid = collections.defaultdict(list)
        lo = np.floor(np.minimum(S[:, :2], S[:, 2:]) / CELL - SNAP_TOL / CELL).astype(int)
        hi = np.floor(np.maximum(S[:, :2], S[:, 2:]) / CELL + SNAP_TOL / CELL).astype(int)
        for n in range(len(S)):
            for cx in range(lo[n, 0], hi[n, 0] + 1):
                for cy in range(lo[n, 1], hi[n, 1] + 1):
                    grid[(cx, cy)].append(n)
        inserts = collections.defaultdict(list)          # 管 → [(段号, 段内比例, 点)]
        moves = {}
        nA = nB = 0
        for m, (i, side, x, y) in enumerate(ends):
            cand = grid.get((int(np.floor(x / CELL)), int(np.floor(y / CELL))), [])
            if not cand:
                continue
            cs = np.array([c for c in cand if seg[c][0] != i])
            if not len(cs):
                continue
            ax, ay, bx, by = S[cs, 0], S[cs, 1], S[cs, 2], S[cs, 3]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = np.clip(((x - ax) * dx + (y - ay) * dy) / np.where(L2 > 0, L2, 1), 0, 1)
            qx, qy = ax + t * dx, ay + t * dy
            d = np.hypot(qx - x, qy - y)
            k = int(np.argmin(d))
            if d[k] > SNAP_TOL:
                continue
            c = cs[k]
            # 落在那根管的折点上（段的两端）就不是「中段」
            if min(np.hypot(ax[k] - qx[k], ay[k] - qy[k]), np.hypot(bx[k] - qx[k], by[k] - qy[k])) <= STACK_TOL:
                continue
            if d[k] <= MM_TOL:
                inserts[seg[c][0]].append((seg[c][1], float(t[k]), (x, y)))
                nA += 1
                kind = f"离 {P[seg[c][0]][1].get('assetid')} 中段 {d[k] * 1000:.1f} mm，在那根管上加折点"
            else:
                dangling = ptree.query((x, y))[0] > STACK_TOL and not [j for j in etree.query_ball_point((x, y), STACK_TOL) if ends[j][0] != i]
                if not dangling:
                    continue
                q = (float(qx[k]), float(qy[k]))
                inserts[seg[c][0]].append((seg[c][1], float(t[k]), q))
                moves[m] = q
                nB += 1
                kind = f"悬空，离 {P[seg[c][0]][1].get('assetid')} 中段 {d[k]:.3f} m，移过去并加折点"
            a_ = P[i][1]
            self.issues.append({"layer": a_.get("src_layer"), "src_oid": a_.get("src_oid"), "assetid": a_.get("assetid"), "code": "SNAP-SEG",
                                "desc": f"{'起点' if side == 0 else '终点'}{kind}"})
        # 先挪管端，再给目标管加折点（同一根管可能既被接、又挪自己的端点）
        for m, q in moves.items():
            i, side, _, _ = ends[m]
            g, a_ = P[i]
            vs = [p for p in g.getPart(0) if p is not None]
            if side == 0:
                vs[0] = arcpy.Point(*q)
            else:
                vs[-1] = arcpy.Point(*q)
            new = arcpy.Polyline(arcpy.Array(vs), SR)
            if new.pointCount < 2 or new.length < 0.01:
                self.stats["接到中段跳过（会成零长度线）"] += 1
                continue
            P[i] = (new, a_)
            a_["notes"] = "; ".join(x for x in (a_.get("notes"), "第十二轮：悬空管端接到别的管中段") if x)[:500]
        for i, lst in inserts.items():
            g, a_ = P[i]
            vs = [p for p in g.getPart(0) if p is not None]
            add = collections.defaultdict(list)
            for k, t, q in lst:
                add[k].append((t, q))
            out = []
            for k, v in enumerate(vs):
                out.append(v)
                for t, q in sorted(add.get(k, [])):
                    out.append(arcpy.Point(*q))
            new = arcpy.Polyline(arcpy.Array(out), SR)
            if new.pointCount < 2 or new.length < 0.01:
                self.stats["加折点跳过（几何无效）"] += 1
                continue
            P[i] = (new, a_)
            a_["notes"] = "; ".join(x for x in (a_.get("notes"), f"第十二轮：加了 {len(lst)} 个折点供支管接入") if x)[:500]
        self.stats["管端离别的管中段 ≤5 mm：加折点"] = nA
        self.stats["悬空管端接到别的管中段（≤0.5 m）"] = nB
        self.stats["加了折点的管"] = len(inserts)

    def stack_soak_inlet(self):
        """第十轮（用户 2026-10-25 定）：渗井和雨水口同点（UN 容差内）→ 留渗井、去掉雨水口，进排除清单并登记。
        渗井是这个系统真正的终点；雨水井（CATCHBASIN）和渗井同点的没定规则，不动，只计数。"""
        S = [(g.firstPoint.X, g.firstPoint.Y) for g, a in self.rows["CATCH_BASIN"] if g is not None and a.get("src_layer") == "SOAKAWAY"]
        if not S:
            return
        st = cKDTree(np.array(S))
        drop = set()
        for i, (g, a) in enumerate(self.rows["CATCH_BASIN"]):
            if g is None or a.get("src_layer") not in ("INLET", "CATCHBASIN"):
                continue
            if st.query((g.firstPoint.X, g.firstPoint.Y))[0] <= MM_TOL:
                if a.get("src_layer") == "CATCHBASIN":
                    self.stats["雨水井与渗井同点（未定规则，未动）"] += 1
                    continue
                drop.add(i)
                why = "第十轮：与渗井同点（错误 9 / 25），按用户定留渗井、去掉雨水口"
                self.excluded.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "reason": why})
                self.issues.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "code": "STACK-SOAK", "desc": why})
        self.rows["CATCH_BASIN"] = [x for i, x in enumerate(self.rows["CATCH_BASIN"]) if i not in drop]
        self.stats["渗井与雨水口同点：去掉雨水口"] = len(drop)

    def force_at_inlet(self):
        """第十轮（用户 2026-10-25 定）：DMT 压力管的端点直接接在雨水口（Drop Inlet）上 → 这一根改按重力管，登记 FORCE-INLET（列入客户确认）。
        雨水口只收地面水、靠重力流走，多半是 DMT 把重力管标成了压力管。AECOM 的压力管不动。"""
        I = [(g.firstPoint.X, g.firstPoint.Y) for g, a in self.rows["CATCH_BASIN"] if g is not None and a.get("tgt_at") == "Drop Inlet"]
        it = cKDTree(np.array(I))
        n = 0
        for g, a in self.rows["PIPE"]:
            if g is None or a.get("tgt_ag") != "Force Pipe" or str(a.get("src_layer", "")).startswith("AECOM"):
                continue
            if min(it.query((p.X, p.Y))[0] for p in (g.firstPoint, g.lastPoint)) <= STACK_TOL:
                a["tgt_ag"] = "Gravity Pipe"
                n += 1
                a["notes"] = "; ".join(x for x in (a.get("notes"), "第十轮：DMT 记为压力管、端点接雨水口，按重力管处理（待客户确认）") if x)[:500]
                self.issues.append({"layer": a.get("src_layer"), "src_oid": a.get("src_oid"), "assetid": a.get("assetid"), "code": "FORCE-INLET",
                                    "desc": "DMT 记为压力管，但端点直接接雨水口；按重力管处理，请客户确认"})
        self.stats["压力管接雨水口→按重力管"] = n

    def dedup_vertices(self):
        """第十轮：同一根管上相邻两个折点相距 ≤ 5 mm（UN 报错误 21，重复折点；Pro 修复几何按 0.001 m 判，2 mm 的修不掉）→ 去掉后一个中间折点，端点不动。"""
        n = 0
        for i, (g, a) in enumerate(self.rows["PIPE"]):
            if g is None:
                continue
            parts, changed = [], False
            for part in g:
                pts_ = [p for p in part if p is not None]
                keep = [pts_[0]]
                for k, p in enumerate(pts_[1:], 1):
                    last = k == len(pts_) - 1
                    if math.hypot(p.X - keep[-1].X, p.Y - keep[-1].Y) <= MM_TOL:
                        if last and len(keep) > 1:
                            keep[-1] = p                     # 末端不动：去掉它前面那个中间折点
                            changed = True
                            continue
                        if not last:
                            changed = True
                            continue
                    keep.append(p)
                parts.append(keep)
            if changed and all(len(x) >= 2 for x in parts):
                self.rows["PIPE"][i] = (arcpy.Polyline(arcpy.Array([arcpy.Array(x) for x in parts]), SR), a)
                n += 1
        self.stats["去掉 5 mm 内的重复折点（管数）"] = n

    def snap_dangling(self):
        """第五轮（用户 2026-10-23 定）：悬空管端吸附。管端在 Utility Network 容差内既不碰任何点、也不碰别的管端 = 悬空。
        先找 0.5 m 内最近的点（检查井、雨水井 / 雨水口、封堵端、出水口、泵、湿井、BMP 点），移过去；
        没有点的，再找 0.5 m 内别的管的端点，移到它的位置（被对上的端点就不再移动）。只动端点坐标，每条记旧坐标、新坐标、距离。"""
        pts = []
        for cls in ("MH_VAULT", "CATCH_BASIN", "END_CAP", "OUTFALL", "PUMP", "WETWELL", "BMP_POINT"):
            pts += [(g.firstPoint.X, g.firstPoint.Y) for g, _ in self.rows[cls] if g is not None]
        ptree = cKDTree(np.array(pts))
        P = self.rows["PIPE"]
        ends = []
        for i, (g, a_) in enumerate(P):
            if g is None:
                continue
            ends.append((i, 0, g.firstPoint.X, g.firstPoint.Y))
            ends.append((i, 1, g.lastPoint.X, g.lastPoint.Y))
        E = np.array([(x, y) for _, _, x, y in ends])
        etree = cKDTree(E)
        dangling = []
        for n, (i, side, x, y) in enumerate(ends):
            if ptree.query((x, y))[0] <= STACK_TOL:
                continue
            near = [j for j in etree.query_ball_point((x, y), STACK_TOL) if ends[j][0] != i]
            if near:
                continue
            dangling.append(n)
        moves = {}
        fixed = set()
        dset = set(dangling)
        for n in dangling:
            # 第十一轮：5 mm 内有不悬空的别的管端（那里本来就是接点）→ 接到那个接点，不去 0.5 m 内的点。
            # 第十轮先吸点，RBW8 一根管被吸到 7 mm 外的点上，和原来 2 mm 外的接点分开了（5 根管掉出子网）
            i, side, x, y = ends[n]
            mates = [j for j in etree.query_ball_point((x, y), MM_TOL) if ends[j][0] != i and j not in dset]
            if mates:
                j = min(mates, key=lambda j: np.hypot(ends[j][2] - x, ends[j][3] - y))
                moves[n] = ((ends[j][2], ends[j][3]), float(np.hypot(ends[j][2] - x, ends[j][3] - y)), "管端")
                fixed.add(j)
                continue
            d, j = ptree.query((x, y))
            if d <= SNAP_TOL:
                moves[n] = (pts[j], d, "点")
                fixed.add(n)
        for n in dangling:
            if n in moves:
                continue
            i, side, x, y = ends[n]
            best = None
            for j in etree.query_ball_point((x, y), SNAP_TOL):
                if ends[j][0] == i or j in moves:
                    continue
                d = float(np.hypot(ends[j][2] - x, ends[j][3] - y))
                if best is None or d < best[1]:
                    best = (j, d)
            if best is not None:
                j, d = best
                moves[n] = ((ends[j][2], ends[j][3]), d, "管端")
                fixed.add(j)
        by_pipe = collections.defaultdict(dict)
        for n, (xy, d, kind) in moves.items():
            i, side, x, y = ends[n]
            by_pipe[i][side] = (xy, d, kind, (x, y))
        for i, sides in by_pipe.items():
            g, a_ = P[i]
            arr = [[p for p in part if p is not None] for part in g]
            for side, (xy, d, kind, old) in sides.items():
                if side == 0:
                    arr[0][0] = arcpy.Point(xy[0], xy[1])
                else:
                    arr[-1][-1] = arcpy.Point(xy[0], xy[1])
            new = arcpy.Polyline(arcpy.Array([arcpy.Array(part) for part in arr]), SR)
            if new.pointCount < 2 or new.length < 0.01:
                # 两端被吸到同一点等情况：吸附后成了零长度线，不吸附这根管，登记
                self.issues.append({"layer": a_.get("src_layer"), "src_oid": a_.get("src_oid"), "assetid": a_.get("assetid"), "code": "SNAP-SKIP",
                                    "desc": "两端都悬空，吸附后会成零长度线，不吸附"})
                self.stats["悬空端点不吸附（会成零长度线）"] += len(sides)
                continue
            for side, (xy, d, kind, old) in sides.items():
                self.issues.append({"layer": a_.get("src_layer"), "src_oid": a_.get("src_oid"), "assetid": a_.get("assetid"), "code": "SNAP",
                                    "desc": f"{'起点' if side == 0 else '终点'}悬空，移到 {d:.3f} m 外的{kind}：({old[0]:.3f}, {old[1]:.3f}) → ({xy[0]:.3f}, {xy[1]:.3f})"})
                self.stats[f"悬空端点吸附到{kind}"] += 1
            P[i] = (new, a_)
            a_["notes"] = "; ".join(x for x in (a_.get("notes"), "第五轮：悬空端点吸附（≤0.5 m）") if x)[:500]
        self.stats["悬空端点（吸附前）"] = len(dangling)
        self.stats["悬空端点吸附后仍悬空"] = len(dangling) - len(moves)

    def add_system_outflows(self):
        """第五轮（用户 2026-10-23 定）：没有出水口、终点是渗井 / 塘 / Geocell 的系统，在每个渗井（Catch Basin / Infiltration）和 BMP 点旁
        0.5 m 加一个 System Outflow（编号 GEN-SO-n），作子网控制器；部署后用节点—节点连通关联把它和渗井 / BMP 点连起来（s4b）。"""
        key = lambda p: (round(p.X, 3), round(p.Y, 3))
        par = {}

        def root(x):
            while par.setdefault(x, x) != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        other = collections.defaultdict(list)
        # 第十四轮：UN 的线—线连通要求「至少有一条线在这里是端点」（或这里有点）；两条线只在各自中间折点上交叉不连通。
        # 第十三轮把所有中间折点都当连通点，误判部分内陆系统「已有出水口」而没加系统出流点
        hubs = set()
        for g, _ in self.rows["PIPE"]:
            if g is not None:
                hubs.add(key(g.firstPoint)); hubs.add(key(g.lastPoint))
        for cls in ("MH_CHANNEL", "CATCH_BASIN", "END_CAP", "OUTFALL", "FLAP_GATE", "PUMP", "WETWELL", "BMP_POINT", "GEN_CONNECTION"):
            hubs |= {key(g.firstPoint) for g, _ in self.rows[cls] if g is not None}
        for g, a_ in self.rows["PIPE"]:
            if g is None:
                continue
            a, b = key(g.firstPoint), key(g.lastPoint)
            par[root(a)] = root(b)
            for part in g:
                for p in part:
                    if p is not None and key(p) in hubs:
                        par[root(key(p))] = root(a)
            other[a].append((g.lastPoint.X, g.lastPoint.Y))
            other[b].append((g.firstPoint.X, g.firstPoint.Y))
        has_of = {root(key(g.firstPoint)) for g, _ in self.rows["OUTFALL"] if g is not None and key(g.firstPoint) in par}
        sinks = [(g, a_, "CATCH_BASIN") for g, a_ in self.rows["CATCH_BASIN"] if a_.get("tgt_at") == "Infiltration"]
        sinks += [(g, a_, "BMP_POINT") for g, a_ in self.rows["BMP_POINT"]]
        n = 0
        systems = set()
        for g, a_, cls in sinks:
            k = key(g.firstPoint)
            if k not in par or root(k) in has_of:
                continue
            x, y = g.firstPoint.X, g.firstPoint.Y
            ox = np.array(other[k])
            v = np.array([x, y]) - ox.mean(0)                      # 背离接入管的方向
            norm = float(np.hypot(*v))
            v = v / norm if norm > 1e-6 else np.array([1.0, 0.0])
            n += 1
            systems.add(root(k))
            pt = arcpy.PointGeometry(arcpy.Point(x + v[0] * SO_OFFSET, y + v[1] * SO_OFFSET), SR)
            self.add("SYSTEM_OUTFLOW", pt, {"assetid": f"GEN-SO-{n}", "assetstate": 2, "spatialsource": 0, "src_layer": "GENERATED",
                                            "anchor_assetid": a_.get("assetid"), "anchor_layer": a_.get("src_layer"), "anchor_x": x, "anchor_y": y,
                                            "notes": f"第五轮：无出水口系统的终点（{a_.get('src_layer')} {a_.get('assetid')}）旁加的系统出流点，作子网控制器"})
        self.stats["SYSTEM_OUTFLOW"] = n
        self.stats["SYSTEM_OUTFLOW 覆盖的系统"] = len(systems)

    def channels(self):
        for g, v in self.rows["MH_VAULT"]:
            self.add("MH_CHANNEL", g, {k: v.get(k) for k in ("assetstate", "ownedby", "spatialsource", "installdate", "src_layer", "src_oid", "src_compkey", "src_status")}
                     | {"assetid": (v["assetid"] + "-CH") if v.get("assetid") else None, "elevation": v.get("invertelev"), "vault_assetid": v.get("assetid"),
                        "notes": "由检查井生成的井内流道（T-1）"})
        self.stats["MH_CHANNEL"] = len(self.rows["MH_CHANNEL"])

    # ---------- 写库 ----------
    def write(self):
        if arcpy.Exists(self.gdb):
            arcpy.management.Delete(self.gdb)
        arcpy.management.CreateFileGDB(self.out, "prep.gdb")
        for cls, (geom, extra) in SCHEMA.items():
            fc = os.path.join(self.gdb, cls)
            arcpy.management.CreateFeatureclass(self.gdb, cls, geom, spatial_reference=SR, has_z="ENABLED")
            flds = COMMON + extra
            arcpy.management.AddFields(fc, [[f[0], f[1], None, f[2] if len(f) > 2 else None] for f in flds])
            names = [f[0] for f in flds]
            with arcpy.da.InsertCursor(fc, ["SHAPE@"] + names) as cur:
                for g, a in self.rows[cls]:
                    lay = a.get("src_layer")
                    primary = cls in RAW_CLASSES and lay in DMT_LAYERS and not (cls == "BMP_POINT" and lay == "POND")
                    a["src_key"] = f"{lay}:{a.get('src_oid')}" if primary else None
                    cur.insertRow([self.z0(g, cls)] + [a.get(n) for n in names])
            arcpy.management.AddGlobalIDs(fc)
            print(f"  {cls}: {len(self.rows[cls])}", flush=True)
        self.join_raw()
        # 不进管网的
        for name, recs, geom in (("MOVABLE_PUMP", self.movable, "POINT"), ("AECOM_CATCHMENT", self.aecom_catch, "POLYGON")):
            src = os.path.join(self.D if name == "MOVABLE_PUMP" else self.A, "MOVABLE_PUMP" if name == "MOVABLE_PUMP" else "Catchment")
            if not arcpy.Exists(src):
                continue
            arcpy.conversion.ExportFeatures(src, os.path.join(self.gdb, name))
            print(f"  {name}: {len(recs)}（原样另存，不进管网）", flush=True)
        for name, recs, cols in (("EXCLUDED", self.excluded, [("layer", "TEXT", 40), ("src_oid", "LONG"), ("assetid", "TEXT", 64), ("reason", "TEXT", 120)]),
                                 ("ISSUES", self.issues, [("layer", "TEXT", 40), ("src_oid", "LONG"), ("assetid", "TEXT", 64), ("code", "TEXT", 20), ("desc_", "TEXT", 255)]),
                                 ("CONFLICTS", self.conflicts, [("cls", "TEXT", 20), ("assetid", "TEXT", 64), ("field", "TEXT", 20), ("DMT", "DOUBLE"),
                                                                ("AECOM", "DOUBLE"), ("aecom_id", "TEXT", 64)])):
            tb = os.path.join(self.gdb, name)
            arcpy.management.CreateTable(self.gdb, name)
            arcpy.management.AddFields(tb, [[c[0], c[1], None, c[2] if len(c) > 2 else None] for c in cols])
            names = [c[0] for c in cols]
            with arcpy.da.InsertCursor(tb, names) as cur:
                for x in recs:
                    cur.insertRow([x.get({"desc_": "desc", "cls": "class"}.get(n, n)) for n in names])
            print(f"  {name}: {len(recs)}", flush=True)

    def join_raw(self):
        """第八轮：把 DMT 原始字段（该层里有内容的，见 00_profile/profile.json）原样带进预处理库，字段名 dmt_<原名小写>。
        做法：每个目标类建一张原始属性表（各源图层合并，键 src_key = 图层:记录号；同名不同类型的字段加图层后缀），再按 src_key 关联进来。"""
        prof = json.load(open(os.path.join(self.work, "00_profile", "profile.json"), encoding="utf-8"))["layers"]
        TY = {"String": "TEXT", "Double": "DOUBLE", "Single": "FLOAT", "Integer": "LONG", "SmallInteger": "SHORT", "Date": "DATE", "BigInteger": "LONG"}
        raw_gdb = os.path.join(self.out, "raw_fields.gdb")
        if arcpy.Exists(raw_gdb):
            arcpy.management.Delete(raw_gdb)
        arcpy.management.CreateFileGDB(self.out, "raw_fields.gdb")
        summary = {}
        for cls in RAW_CLASSES:
            layers = sorted({a.get("src_layer") for _, a in self.rows[cls] if a.get("src_key")})
            if not layers:
                continue
            fdefs, plan = {}, {}
            for lay in layers:
                pf = prof[f"src_dmt/{lay}"]["fields"]
                src_fields = {f.name: f for f in arcpy.ListFields(os.path.join(self.D, lay))}
                keep = [n for n, x in pf.items() if n != "OBJECTID_1" and (x["filled"] - x.get("blank", 0)) > 0 and n in src_fields]
                plan[lay] = []
                for n in keep:
                    f = src_fields[n]
                    t_ = TY.get(f.type, "TEXT")
                    name = "dmt_" + n.lower()
                    if name in fdefs and fdefs[name][0] != t_:
                        name = f"{name}_{lay[:4].lower()}"              # 同名不同类型：加图层后缀
                    fdefs.setdefault(name, (t_, f.length if t_ == "TEXT" else None))
                    plan[lay].append((n, name))
            tb = os.path.join(raw_gdb, f"RAW_{cls}")
            arcpy.management.CreateTable(raw_gdb, f"RAW_{cls}")
            arcpy.management.AddFields(tb, [["src_key", "TEXT", None, 48]] + [[n, t_, None, ln] for n, (t_, ln) in fdefs.items()])
            allf = ["src_key"] + list(fdefs)
            n_rows = 0
            with arcpy.da.InsertCursor(tb, allf) as cur:
                for lay in layers:
                    src = [x for x, _ in plan[lay]]
                    dst = [y for _, y in plan[lay]]
                    pos = [allf.index(y) for y in dst]
                    for r in arcpy.da.SearchCursor(os.path.join(self.D, lay), ["OID@"] + src):
                        row = [None] * len(allf)
                        row[0] = f"{lay}:{r[0]}"
                        for p_, v in zip(pos, r[1:]):
                            row[p_] = v
                        cur.insertRow(row)
                        n_rows += 1
            arcpy.management.AddIndex(tb, ["src_key"], "ix_key")
            arcpy.management.JoinField(os.path.join(self.gdb, cls), "src_key", tb, "src_key", list(fdefs))
            summary[cls] = {"layers": layers, "fields": len(fdefs), "raw_rows": n_rows}
            print(f"  原始字段 → {cls}：{len(fdefs)} 个（{', '.join(layers)}）", flush=True)
        self.stats["dmt_原始字段"] = summary

    def run(self):
        t0 = time.time()
        # 第十轮：悬空吸附（0.5 m，可吸到检查井）先于毫米级吸附——第九轮毫米级先做，两根管先互相接上、不再算悬空，
        # 没被吸到 2.5 cm 外的检查井，SMA-0099 断开 120 根管。塘点放到吸附之后，免得管端被挪走、塘点落空
        for step in (self.manholes, self.pipes, self.catch_basins, self.end_caps, self.outfalls, self.pumps, self.ponds, self.aecom,
                     self.join_multipart, self.fix_stacked, self.stack_soak_inlet, self.snap_aecom_ends, self.snap_dangling, self.snap_mm, self.snap_junction_to_point, self.snap_to_segment, self.pond_points,
                     self.force_at_inlet, self.dedup_vertices, self.channels, self.fix_flapgates, self.fix_line_junctions,
                     self.add_system_outflows):
            step()
            print(f"{step.__name__} 完成 {round(time.time() - t0)} s", flush=True)
        self.write()
        # 第七节：几何修复（错误 19 / 20 / 21）。Pro「修复几何」：去重复折点等；多部件不拆
        r = arcpy.management.RepairGeometry(os.path.join(self.gdb, "PIPE"), "KEEP_NULL")
        self.stats["RepairGeometry"] = r.getMessages()[-800:]
        self.stats["z_nonzero"] = dict(self.zcount)
        self.stats["issues_by_code"] = dict(collections.Counter(x["code"] for x in self.issues))
        self.stats["excluded_by_reason"] = dict(collections.Counter(f"{x['layer']}:{x['reason'].split('，')[0]}" for x in self.excluded))
        self.stats["seconds"] = round(time.time() - t0)
        json.dump(self.stats, open(os.path.join(self.out, "prep_log.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print(json.dumps(self.stats, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    Prep(sys.argv[1]).run()
