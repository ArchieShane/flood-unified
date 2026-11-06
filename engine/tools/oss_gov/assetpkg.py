"""读 Esri 资产包（文件地理数据库）里的数据模型定义：类的字段、子类型（资产组）、每个资产组的资产类型值域、全部编码值域。

开源路线建模型时用（没有 ArcGIS 也能读）：定义都在系统表 GDB_Items 的 XML 里，GDAL OpenFileGDB 驱动加 LIST_ALL_TABLES=YES 就能读。

  from assetpkg import AssetPackage
  ap = AssetPackage("D:/swbrain_work/exp_gov/Expanded_local.gdb")
  ap.subtypes("StormwaterDevice")          → {0: "Unknown", 1: "Outfall", ...}
  ap.subtype_code("StormwaterDevice", "Outfall")
  ap.assettype_code("StormwaterDevice", "Outfall", "Non-Permit Outfall")
  ap.domain("Asset_State")                 → {code: name}
  ap.fields("StormwaterLine")              → [(name, esri 类型, 长度, 值域名)]
"""
from __future__ import annotations

import re
import warnings
from functools import lru_cache

import pyogrio

warnings.filterwarnings("ignore", message="Measured")


def _tag(x, t):
    m = re.search(rf"<{t}>(.*?)</{t}>", x, re.S)
    return m.group(1) if m else None


def _num(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        try:
            return float(s)
        except (TypeError, ValueError):
            return s


class AssetPackage:
    def __init__(self, gdb):
        self.gdb = str(gdb)
        df = pyogrio.read_dataframe(self.gdb, layer="GDB_Items", read_geometry=False, LIST_ALL_TABLES="YES")
        self._def = {n: d for n, d in zip(df.Name, df.Definition) if isinstance(d, str)}

    def _cls(self, name):
        x = self._def.get(name)
        if x is None:
            raise KeyError(f"资产包里没有类 {name}")
        return x

    @lru_cache(maxsize=None)
    def fields(self, name):
        x = self._cls(name)
        out = []
        for f in re.findall(r"<GPFieldInfoEx[^>]*>(.*?)</GPFieldInfoEx>", x, re.S):
            out.append((_tag(f, "Name"), (_tag(f, "FieldType") or "").replace("esriFieldType", ""), _num(_tag(f, "Length")), _tag(f, "DomainName")))
        return out

    @lru_cache(maxsize=None)
    def not_nullable_numeric(self, name):
        """不允许为空的数值字段（ArcGIS 装载时没给值、也没有缺省值的，填 0；如 StormwaterDevice.activevol）"""
        x = self._cls(name)
        out = []
        for f in re.findall(r"<GPFieldInfoEx[^>]*>(.*?)</GPFieldInfoEx>", x, re.S):
            n, t = _tag(f, "Name"), (_tag(f, "FieldType") or "")
            if _tag(f, "IsNullable") == "false" and t.replace("esriFieldType", "") in ("SmallInteger", "Integer", "Double", "Single", "BigInteger")                     and n.upper() not in ("OBJECTID", "ASSETGROUP", "ASSETTYPE"):
                out.append(n.lower())
        return out

    @lru_cache(maxsize=None)
    def field_defaults(self, name):
        """类一级的字段缺省值（字段定义里的 DefaultValueNumeric / DefaultValueString；子类型缺省值优先，见 defaults）"""
        x = self._cls(name)
        out = {}
        for f in re.findall(r"<GPFieldInfoEx[^>]*>(.*?)</GPFieldInfoEx>", x, re.S):
            n = _tag(f, "Name")
            v = _tag(f, "DefaultValueNumeric")
            if v is None:
                v = _tag(f, "DefaultValueString")
            if n and v is not None and n.upper() not in ("ASSETGROUP", "ASSETTYPE", "OBJECTID"):
                out[n.lower()] = _num(v)
        return out

    @lru_cache(maxsize=None)
    def _subtype_blocks(self, name):
        x = self._cls(name)
        blocks = {}
        for b in re.findall(r"<Subtype xsi:type='typens:Subtype'>(.*?)</Subtype>", x, re.S):
            code = int(_tag(b, "SubtypeCode"))
            doms = dict(re.findall(r"<FieldName>([^<]+)</FieldName><DomainName>([^<]+)</DomainName>", b))
            blocks[code] = (_tag(b, "SubtypeName"), {k.lower(): v for k, v in doms.items()})
        return blocks

    @lru_cache(maxsize=None)
    def defaults(self, name, ag_code):
        """某资产组下各字段的缺省值（新要素装进 UN 时，没给值的字段取它）"""
        x = self._cls(name)
        for blk in re.findall(r"<Subtype xsi:type='typens:Subtype'>(.*?)</Subtype>", x, re.S):
            if int(_tag(blk, "SubtypeCode")) == ag_code:
                return {f.lower(): _num(v) for f, v in re.findall(r"<FieldName>([^<]+)</FieldName>(?:<DomainName>[^<]*</DomainName>)?<DefaultValue[^>]*>([^<]*)</DefaultValue>", blk)}
        return {}

    def subtypes(self, name):
        return {c: n for c, (n, _) in self._subtype_blocks(name).items()}

    def subtype_code(self, name, ag_name):
        for c, n in self.subtypes(name).items():
            if n == ag_name:
                return c
        raise KeyError(f"{name} 没有资产组 {ag_name}")

    def field_domain(self, name, ag_code, field):
        """某资产组下某字段用的值域名（子类型可以给字段换值域，如 assettype）"""
        blk = self._subtype_blocks(name).get(ag_code)
        if blk and field.lower() in blk[1]:
            return blk[1][field.lower()]
        for f in self.fields(name):
            if f[0].lower() == field.lower():
                return f[3]
        return None

    @lru_cache(maxsize=None)
    def domain(self, dname):
        x = self._def.get(dname)
        if x is None:
            return {}
        return {_num(c): n for n, c in re.findall(r"<CodedValue[^>]*><Name>(.*?)</Name><Code[^>]*>(.*?)</Code></CodedValue>", x, re.S)}

    def assettype_code(self, name, ag_name, at_name):
        ag = self.subtype_code(name, ag_name)
        dom = self.domain(self.field_domain(name, ag, "assettype"))
        for c, n in dom.items():
            if n == at_name:
                return c
        raise KeyError(f"{name}/{ag_name} 没有资产类型 {at_name}")

    def describe(self, name, ag_code, field, value):
        """编码值 → 描述（UDMS 报表库的 d_<字段> 列）"""
        if field.lower() == "assetgroup":
            return self.subtypes(name).get(value)
        dn = self.field_domain(name, ag_code, field)
        return self.domain(dn).get(value) if dn else None
