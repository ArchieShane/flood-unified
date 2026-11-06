"""汇水区设施台账（2026-10-20，用户：按客户定义来，先做 8 个汇水区）。

级别按客户两张定义图（「Surface Water Management Levels」与「Resource Levels Definition」，用户 2026-10-20 发）：
  L1  Existing / Permanent：Pipelines、Pump Stations、Outfalls、Inlets、Dams、Wadis、Ditches、Geocells、Ponds
  L2  Pre-event / Temporary（雨前部署或建成的临时缓解措施），按交付线分：
        CAPEX         Ponds、Surface Pipelines、Spillways、Pump Stations、Geocells
        IDD           Pumps（表里写 temporary pumps）、Bunds、Ditches（表里另列 surface pipelines、spillways、pump stations、geocells）
        Municipality  Ponds、Surface Pipelines、Jersey Barriers、Sand Bags、Small Barriers、Bunds、Stationary / Stationed Pumps
  L3  During Event / Emergency：Tankers、Mobile Pumps、Cranes（雨中动态调度）
  待确认  证据不够定级的
  同一种设施可以在不同级：雨前固定位置预置的泵 = L2（Pumps / Stationed Pumps），雨中调度的移动泵 = L3。
  原始资料里没有一条写了 L2 的交付线，line 列只给候选。
每一行都带「状态证据」= 原始字段值或情景名原文，和出处（文件 / 图层 / 情景）。

来源与判读规则（按顺序）：
  1 客户 GDB PS_PUMP              计算域内（含 50 m）→ L1；STATUS / INFORMIGRATION / COMMENTS 原样记；备注写停用的标「L1（停用）」
  2 台账塘 console/data/ponds_l2.geojson（客户台账 SW_* 塘）→ L1；status / installed / data_source / project 原样记
  3 Parsons「Existing Storage」三个图层 → L1（文件夹名就是 Existing Storage）
  4 Parsons 改造后模型（26092026/*）的塘 → L2（Parsons 临时塘方案，报告 352059-PAR：2027 雨季前实施的开放空间临时塘）
  5 Parsons 改造后模型的泵：标签里带泵站号（RD09、RD-01、RD 05…）→ L1 现状泵站在模型里的表达（对 GDB）；
    PMP-<数字> → L2（Parsons 方案的泵，雨前固定预置在塘边，不是 L3 的雨中调度）；其余（如 RB-01 的 Pump1–5）→ 100 m 内有 GDB 泵站算 L1，否则待确认
  6 客户 SewerGEMS 工程（RD-A/C/F/G/I 的 .stsw.sqlite 情景树）：「Base Scenario / Base Model(s)」下 10 年 + 20% CC 1 h 那次运算的
    湿井 + 泵 = L1；情景名含 Temporary Intervention 或为 TIP 的运算里多出来的泵 = L2；不在情景树里的导出（情景已删）不纳入，列在摘要里
  6b Parsons 压力管（泵 → 塘的出水管）：按模型连成块，块里有 PMP-<数字> → L2 Surface Pipelines；只有现状站泵 → L1；两种都有 → 待确认。
     SewerGEMS 临时干预情景里多出来的管 → L2
  6c GDB 的其他 L1 资产（管线长度、出水口、雨水口、格栅沟、渗井、集水池）只计数，写进 summary.json；Dams / Wadis / Geocells 原始资料没有图层
  6d 客户热点表（ADM Stormwater Hotspot Analysis v4.4，主表「ADM Hotspot Inventory」；坐标用 exchange/hotspots/hotspots.json 里 KMZ 修正过的）：
     每个热点一行，记 Plan A 资源、IDD 计划（Planned / Category / Type / Stage / Owner / IDAS / 计划与实际进场完工日期 / Intervention status / Design Description）。
     热点的级别 = 它计划措施的级别：Type=Permanent → L1（建成后），Intermediate → L2，其余待确认；status 列按阶段和实际日期归类。
     顾问模型里的 L2 设施先拼成「一套措施」（unit 列）：Parsons = 压力管连通块 + 块里的泵 + 块的出水口 BNDYID 指向的塘；
     SewerGEMS = 临时干预情景里一台新泵 + 从它出水节点出去的新管。整套挂到成员里离「有 IDD 计划」热点最近的那个
     （塘 1000 m、泵和管 500 m 内），整套共用那个热点的 status；挂不上的 = 「只是方案（热点表里没有对应计划）」。
     挂接是按位置推断的（热点表没有设施编号），linked_hotspot 列写了距离、负责方、整套组成和可信度。
  7 result-017 L2 用过的塘：对上面的行（50 m），对不上的单列「待确认」
SewerGEMS .inp 坐标是英尺（×0.3048 = UTM 40N 米），流量 CFS；PUMP3 曲线 = 扬程（x）—流量（y）。

  python tools/facility_register.py [--r017 D:/swbrain_work/r017/result-017]
产物：out/facility_register/facilities.csv（utf-8-sig）、facilities.geojson（EPSG:4326）、summary.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import datapaths  # noqa: E402
from r017_l2_audit import inp_section  # noqa: E402

OUT = ROOT / "out" / "facility_register"
R017 = Path("D:/swbrain_work/r017/result-017")
sys.path.insert(0, str(ROOT))
from swbrain import client_data  # noqa: E402
PARSONS = Path("D:/阿布扎比/Parsons_Model Data Export")
FT, CFS = 0.3048, 0.0283168
NEAR_PS_M, MATCH_M, EDGE_M = 100.0, 50.0, 50.0

# 汇水区 → (result-017 目录名前缀 = 客户汇水区图层的计算域, 客户模型, 模型类型)
CATCH = {
    "MBZ (RD-9)": ("MBZ", "Musaffah_RD09", "parsons"),
    "RB-1": ("RB-1", "RB-01", "parsons"),
    "RB-3": ("RB-3", "RB3", "parsons"),
    "RD-A": ("RD-A", "RD-A", "sewergems"),
    "RD-C": ("RD-C", "RD-C", "sewergems"),
    "RD-F": ("RD-F", "RD-F", "sewergems"),
    "RD-G": ("RD-G", "RD-G", "sewergems"),
    "RD-I": ("RD-I", "RD-I", "sewergems"),
}
OOS = re.compile(r"out\s*of\s*service", re.I)        # 「the third pump unavailable」不算停用
STATION_LABEL = re.compile(r"RD[\s_\-]*\d", re.I)
TEMP_LABEL = re.compile(r"^PMP[\s\-]*\d+[A-Z]?(-X-\d+)?$", re.I)
TI_NAME = re.compile(r"temporary intervention|^TIP$", re.I)
BASE_NAME = re.compile(r"^base (scenario|models?)$", re.I)
RUN_1H = re.compile(r"10\s*yrs?\.?\s*\+\s*20\s*%\s*CC\s*-\s*1\s*hrs?$", re.I)


def gdf(rows, crs=32640):
    import geopandas as gpd
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def domain(r017, pre):
    import geopandas as gpd
    return gpd.read_file(Path(r017) / f"{pre}_61mm1h_30m" / "common" / "domain.geojson").to_crs(32640).union_all()


# ------------------------------------------------------------------ 参照图层（全市读一次）
def load_refs():
    import geopandas as gpd
    import pyogrio
    gdb = str(datapaths.get("gdb_dir"))
    ps = pyogrio.read_dataframe(gdb, layer="PS_PUMP").to_crs(32640)
    ledger = gpd.read_file(ROOT / "console" / "data" / "ponds_l2.geojson").to_crs(32640)
    ex = []
    for f in glob.glob(str(PARSONS / "Existing Storage" / "*" / "*.shp")):
        g = pyogrio.read_dataframe(f)
        g = g.set_crs(32640, allow_override=True) if g.crs is None else g.to_crs(32640)
        lab = next((c for c in g.columns if c.upper() in ("LABEL", "LB")), None)
        for i, r in g.iterrows():
            ex.append({"layer": Path(f).parent.name, "label": r[lab] if lab else str(i), "geometry": r.geometry})
    pp, pu, pr, of = [], [], [], {}
    for f in glob.glob(str(PARSONS / "26092026" / "*" / "*.shp")):
        b = os.path.basename(f).lower()
        if b.startswith("outfall") or b == "of.shp":           # 出水口的 BNDYID = 它排进的塘
            g = pyogrio.read_dataframe(f, read_geometry=False)
            lab = next((c for c in g.columns if c.upper() in ("LABEL", "LB")), None)
            if lab and "BNDYID" in g.columns:
                for l, t in zip(g[lab], g["BNDYID"]):
                    if t is not None and str(t).strip() not in ("", "nan", "None"):
                        of[(Path(f).parent.name, str(l).strip())] = str(t).strip()
            continue
        if ("pres" in b and "junction" not in b and "_jns" not in b) or "prpipe" in b:
            g = pyogrio.read_dataframe(f)
            g = g.set_crs(32640, allow_override=True) if g.crs is None else g
            s_col = _label_col(g, ("START_NDLB", "START_NODE", "START-NODE"))
            e_col = _label_col(g, ("STOP_NDLB", "STOP_NODE", "STOP-NODE"))
            if s_col and e_col:
                for _, r in g.iterrows():
                    pr.append({"model": Path(f).parent.name, "n1": str(r[s_col]).strip(), "n2": str(r[e_col]).strip(), "geometry": r.geometry})
            continue
        if not ("pond" in b or b.startswith("pump")):
            continue
        g = pyogrio.read_dataframe(f)
        g = g.set_crs(32640, allow_override=True) if g.crs is None else g
        lab = _find_label(g)
        for i, r in g.iterrows():
            rec = {"model": Path(f).parent.name, "label": str(r[lab]) if lab else f"#{i}", "geometry": r.geometry}
            if "pond" in b:
                area = r.geometry.area
                st = next((r[c] for c in ("MAXSTORAGE", "MAX_ST", "MAX_STORE", "MX_STORAGE", "MAX_STOR") if c in g.columns), None)
                pp.append({**rec, "area_m2": round(area), "model_max_storage_m3": None if st is None else round(float(st))})
            else:
                q = r["PUMP_FLOW"] if "PUMP_FLOW" in g.columns else None
                pu.append({**rec, "pump_flow_lps": None if q is None else float(q)})
    pr = gdf(pr)
    pr["comp"] = _components(pr)
    pp, pu = gdf(pp), gdf(pu)
    refs = {"ps": ps, "ledger": ledger, "ex": gdf(ex) if ex else None, "pp": pp, "pu": pu, "pr": pr, "gdb": gdb}
    refs.update(parsons_units(pp, pu, pr, of))
    return refs


def parsons_units(pp, pu, pr, of):
    """Parsons 方案按连接关系拼成「一套措施」：压力管连通块里有出水口，出水口 BNDYID 指向某个塘 → 块和塘同一套；
    几块排进同一个塘 → 合成一套；泵是块里的节点 → 跟块走。全市一次算好，跨汇水区的同一套措施结果一致。"""
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    ponds = {(m, l) for m, l in zip(pp.model, pp.label)}
    node_comp = {}
    for comp, g in pr.groupby("comp"):
        m = g.model.iloc[0]
        f(f"C|{comp}")
        for n in set(g.n1) | set(g.n2):
            node_comp[(m, n)] = comp
            t = of.get((m, n))
            if t and (m, t) in ponds:
                par[f(f"C|{comp}")] = f(f"P|{m}|{t}")
    unit_of = {}
    for m, l in ponds:
        unit_of[("pond", m, l)] = f(f"P|{m}|{l}")
    for comp in pr.comp.unique():
        unit_of[("comp", comp)] = f(f"C|{comp}")
    for m, l in zip(pu.model, pu.label):
        c = node_comp.get((m, l))
        unit_of[("pump", m, l)] = f(f"C|{c}") if c else f"S|{m}|{l}"
    members = {}                                           # 一套措施的成员：(几何, 是不是塘, 名字)
    for _, r in pp.iterrows():
        members.setdefault(unit_of[("pond", r.model, r.label)], []).append((r.geometry.representative_point(), True, f"塘 {r.label}"))
    for _, r in pu.iterrows():
        if TEMP_LABEL.match(r.label.strip()):
            members.setdefault(unit_of[("pump", r.model, r.label)], []).append((r.geometry, False, f"泵 {r.label}"))
    for comp, g in pr.groupby("comp"):
        members.setdefault(unit_of[("comp", comp)], []).append((g.geometry.union_all().centroid, False, "压力管 1 组"))
    return {"unit_of": unit_of, "unit_members": members}


def link_members(members, planned, parsons):
    """一套措施挂一个热点：成员里离「有计划」热点最近的那一对（塘 1000 m、泵和管 500 m 内）。返回 (status, 说明)"""
    best = None
    for geom, is_pond, _ in members:
        d = planned.geometry.distance(geom)
        i = d.idxmin()
        lim = LINK_M["pond"] if is_pond else LINK_M["other"]
        if d[i] <= lim and (best is None or d[i] < best[0]):
            best = (float(d[i]), i, is_pond)
    names = "、".join(sorted({n for _, _, n in members if n.startswith("塘")})) or "无塘"
    n_pump = sum(1 for _, _, n in members if n.startswith("泵"))
    n_pipe = sum(1 for _, _, n in members if n.startswith("压力管"))
    kit = f"整套：{names}，泵 {n_pump} 台，压力管 {n_pipe} 组"
    if best is None:
        dmin = min(float(planned.geometry.distance(g).min()) for g, _, _ in members)
        return "只是方案（热点表里没有对应计划）", f"{kit}；整套离最近的有计划热点 {dmin:.0f} m，超出挂接距离", None
    d, i, via_pond = best
    h = planned.loc[i]
    same = parsons and "parsons" in h.idd_owner.lower()
    has_pond = any(p for _, p, _ in members)
    pond_budget = has_pond and h.ponds_aed not in ("", "0", "0.0")
    conf = "强" if same and (pond_budget or not has_pond) else ("中" if same else "弱")
    txt = (f"[{conf}] {h.hotspot_id}（{d:.0f} m，按{'塘' if via_pond else '泵 / 管'}算；负责方 {h.idd_owner or '未填'}{'，与模型出处一致' if same else ''}"
           f"{'；热点预算里有塘 ' + h.ponds_aed + ' AED' if pond_budget else ''}；{h.idd_category or '-'} / {h.idd_type or '-'} / {h.idd_stage or '-'}）；{kit}")
    return hs_status(h), txt, h.hotspot_id


def _find_label(g):
    """标签列：LABEL / LB；没有的话找「多数值像 PMP- / PO- / RD 标签」的文本列（RB_02 的泵表列错位，标签在 INV_STRT 里）"""
    lab = next((c for c in g.columns if c.upper() in ("LABEL", "LB")), None)
    if lab:
        return lab
    for c in g.columns:
        if c != "geometry" and not str(g[c].dtype).startswith(("int", "float", "bool")):
            v = g[c].astype(str).str.strip()
            if v.str.match(r"^(PMP|PO|POND|RD|PUMP)", case=False).mean() > 0.5:
                return c
    return None


def _label_col(g, cands):
    """节点标签列：候选里第一列「多数不是纯数字」的（有的导出 STOP_NDLB 是编号、STOP_NODE 才是标签）"""
    for c in cands:
        if c in g.columns:
            v = g[c].astype(str).str.strip()
            if (~v.str.fullmatch(r"[\d.]+")).mean() > 0.5:
                return c
    return None


def _components(pr):
    """压力管按模型连成块（并查集），块号 = 模型#根节点"""
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for m, a, b in zip(pr.model, pr.n1, pr.n2):
        par[f((m, a))] = f((m, b))
    return [f"{m}#{f((m, a))[1]}" for m, a in zip(pr.model, pr.n1)]


def gdb_l1_counts(gdb, dom):
    """L1 其他资产只计数（GDB 图层，计算域内）：线状给 km，点状给个数"""
    import geopandas as gpd
    import pyogrio
    out = {}
    for lay, how in (("PIPELINE", "km"), ("OUTFALL", "n"), ("INLET", "n"), ("CATCHBASIN", "n"),
                     ("GRATEDCHANNELDRAINAGE", "km"), ("SOAKAWAY", "n"), ("COLLECTIVETANK", "n")):
        crs = pyogrio.read_info(gdb, layer=lay)["crs"]
        bb = tuple(gpd.GeoSeries([dom], crs=32640).to_crs(crs).total_bounds)
        g = pyogrio.read_dataframe(gdb, layer=lay, bbox=bb, columns=[]).to_crs(32640)
        g = g[g.geometry.intersects(dom)]
        out[lay] = round(float(g.geometry.clip(dom).length.sum()) / 1000, 1) if how == "km" else int(len(g))
    return out


HS_XLSX = "20260909 ADM Stormwater Hotspot Analysis v4.4.xlsx"
HS_COLS = {"criticality": "Criticality", "root_cause": "Root_Cause", "planA_pumps": "Pumps_Qty", "planA_pump12": "Diesel Pump 12 inch Qty",
           "planA_pump8": "Diesel Pump 8 inch Qty", "planA_pump6": "Diesel Pump 6 inch Qty", "planA_tankers": "Suction Tankers (NO'S)",
           "ponds_aed": "Detention_Ponds_AED", "barrier_aed": "Concrete_Barrier_AED", "sand_aed": "Sand_Barrier_AED", "chamber_aed": "Collection_Chamber_AED",
           "idd_planned": "IDD Intervention Planned", "idd_category": "IDD_Design_Category", "idd_type": "Type of Intervention",
           "idd_stage": "Project Stage", "idd_owner": "IDD_Owner_Name", "idd_idas": "Approval from IDAS secured (Yes/No)",
           "plan_mob": "Planned_IDD_Mobilization_Date", "plan_done": "Planned_IDD_Completion_Date",
           "act_mob": "Actual_Mobilization_Date", "act_done": "Actual_Completion_Date", "int_status": "Intervention status",
           "design": "Design Description", "cap_after": "Flood Volume Capacity after Intervention"}
LINK_M = {"pond": 1000.0, "other": 500.0}


def load_hotspots():
    """客户热点表 ADM 主表，按 # 取行；坐标取 hotspots.json（KMZ 修正，原表经纬度整体错一行）"""
    import openpyxl
    import pandas as pd
    from shapely.geometry import Point
    sys.path.insert(0, str(ROOT))
    from swbrain.hotspots import _sheet_frame
    import pyproj
    wb = openpyxl.load_workbook(Path(datapaths.get("hotspot_xlsx_dir")) / HS_XLSX, read_only=True, data_only=True)
    df, hdr = _sheet_frame(wb["ADM Hotspot Inventory"])
    df = df[pd.to_numeric(df["#"], errors="coerce").notna()].copy()
    df["hotspot_id"] = ["ADM-%d" % int(float(s)) for s in df["#"]]
    keep = {k: df[v] if v in df.columns else None for k, v in HS_COLS.items()}
    H = pd.DataFrame({"hotspot_id": df["hotspot_id"], "location": df["Hotspot_Location"],
                      **{k: (v.astype(str).str.strip().replace({"None": "", "nan": "", "NaT": ""}) if v is not None else "") for k, v in keep.items()}})
    H = H.fillna("").astype(str).replace({"None": "", "nan": "", "NaT": ""})
    ll = {h["hotspot_id"]: (h["lon"], h["lat"]) for h in json.loads((ROOT / "exchange" / "hotspots" / "hotspots.json").read_text(encoding="utf-8"))["hotspots"]
          if h.get("lon") is not None}
    tr = pyproj.Transformer.from_crs(4326, 32640, always_xy=True)
    H["geometry"] = [Point(*tr.transform(*ll[i])) if i in ll else None for i in H.hotspot_id]
    return gdf(H[H.geometry.notna()].to_dict("records"))


def hs_status(h):
    """热点计划措施的进展，按阶段和实际日期归类（原文另记在 evidence）"""
    st, pl = h["idd_stage"].lower(), h["idd_planned"].lower()
    if h["act_done"] or "hand over" in st:
        return "已完成"
    if "construction" in st or "work phase" in st:
        return "施工 / 报批施工中"
    if "design" in st or "concept" in st or "idas" in st:
        return "设计中（已列入计划）"
    if pl.startswith("assess"):
        return "评估中"
    if pl.startswith("yes"):
        return "已列入计划（阶段未填）"
    return "无 IDD 计划"


def hs_level(h):
    t = h["idd_type"].lower()
    if t.startswith("perm"):
        return "L1（Permanent，建成后）"
    if t.startswith("inter"):
        return "L2（Intermediate）"
    return "待确认" if h["idd_planned"] else "—"


def near_ps(refs, pt):
    d = refs["ps"].geometry.distance(pt)
    i = d.idxmin()
    return (refs["ps"].UNITID[i], refs["ps"].PUMP_STATION_ID[i], float(d[i])) if d[i] <= NEAR_PS_M else None


# ------------------------------------------------------------------ SewerGEMS
def sg_scenarios(base):
    sq = glob.glob(str(base / "**" / "*.stsw.sqlite"), recursive=True)
    if not sq:
        return {}
    c = sqlite3.connect(f"file:{sq[0]}?mode=ro", uri=True)
    cols = [r[1] for r in c.execute("pragma table_info(HMIModelingElement)")]
    lab = {a: b for a, b in c.execute(f'select "{cols[0]}", Label from HMIModelingElement')}
    return {str(s): {"name": (lab.get(s) or "").strip(), "parent": (lab.get(p) or "").strip() if p else ""}
            for s, p in c.execute("select ScenarioID, ParentID from HMIScenario where IsDeleted = 0")}


def sg_model(inp):
    xy = {r[0]: (float(r[1]) * FT, float(r[2]) * FT) for r in inp_section(inp, "COORDINATES")}
    curves = {}
    for r in inp_section(inp, "CURVES"):
        v = r[2:] if len(r) > 3 else r[1:]
        if len(r) > 3 and not r[1].replace(".", "").replace("-", "").isdigit():
            curves.setdefault(r[0], {"type": r[1].upper(), "pts": []})["pts"].append([float(x) for x in r[2:4]])
        else:
            curves.setdefault(r[0], {"type": "", "pts": []})["pts"].append([float(x) for x in v[:2]])
    pumps = {}
    for r in inp_section(inp, "PUMPS"):
        cv = curves.get(r[3], {"type": "", "pts": []})
        if cv["type"] == "PUMP3":                       # 扬程—流量：流量是第二列
            q = max(p[1] for p in cv["pts"]) if cv["pts"] else None
        elif cv["pts"]:                                 # PUMP1/2/4：流量也在第二列
            q = max(p[1] for p in cv["pts"])
        else:
            q = None
        pumps[r[0]] = {"inlet": r[1], "outlet": r[2], "curve": r[3], "curve_type": cv["type"],
                       "qmax_m3s": None if q is None else round(q * CFS, 3), "on_ft": r[5] if len(r) > 5 else None}
    storage = [r[0] for r in inp_section(inp, "STORAGE")]
    outs = [r[0] for r in inp_section(inp, "OUTFALLS")]
    conduits = {r[0]: (r[1], r[2], float(r[3])) for r in inp_section(inp, "CONDUITS")}
    return {"xy": xy, "pumps": pumps, "storage": storage, "outfalls": outs, "conduits": conduits}


def sewergems_rows(catch, base, refs, log):
    from shapely.geometry import Point
    sc = sg_scenarios(base)
    inps = {Path(p).stem.split("_")[-1]: p for p in glob.glob(str(base / "**" / "*.inp"), recursive=True)}
    base_runs = [s for s, v in sc.items() if BASE_NAME.match(v["parent"]) and RUN_1H.search(v["name"])]
    ti_runs = [s for s, v in sc.items() if TI_NAME.search(v["name"])]
    orphan = sorted(r for r in inps if r not in sc)
    info = {"scenarios": {s: v for s, v in sc.items() if s in inps}, "base_run": base_runs, "ti_runs": ti_runs,
            "orphan_runs_not_in_tree": orphan}
    rows = []
    if not base_runs or base_runs[0] not in inps:
        log(f"  {catch}：找不到 Base 下的 10 年 + 20% CC 1 h 运算")
        return rows, info
    B = sg_model(inps[base_runs[0]])
    bname = sc[base_runs[0]]["name"]
    by_wet = {}
    for k, p in B["pumps"].items():
        by_wet.setdefault(p["inlet"], []).append((k, p))
    for wet, ps in by_wet.items():
        pt = Point(B["xy"][wet]) if wet in B["xy"] else None
        hit = near_ps(refs, pt) if pt else None
        rows.append({"catchment": catch, "level": "L1", "kind": "泵站（客户模型）", "id": f"湿井 {wet}",
                     "detail": f"{len(ps)} 台；泵曲线最大流量 " + " / ".join(f"{p['qmax_m3s']}" for _, p in ps) + " m³/s",
                     "evidence": f"情景「{sc[base_runs[0]]['parent']} › {bname}」（运算 {base_runs[0]}）里有",
                     "source": f"SewerGEMS {catch} .inp {base_runs[0]}",
                     "gdb_match": f"{hit[0]}（站号 {hit[1]}，{hit[2]:.0f} m）" if hit else "100 m 内无 GDB 泵站",
                     "geometry": pt})
    for r in ti_runs:
        if r not in inps:
            continue
        T = sg_model(inps[r])
        new = {k: p for k, p in T["pumps"].items() if k not in B["pumps"]}
        new_out = [o for o in T["outfalls"] if o not in B["outfalls"]]
        newc = {k: v for k, v in T["conduits"].items() if k not in B["conduits"]}
        ev = f"情景「{sc[r]['name']}」（运算 {r}，父情景「{sc[r]['parent'] or '无'}」）"

        def pipe_row(cs, unit, label):
            pts = [T["xy"][n] for v in cs.values() for n in v[:2] if n in T["xy"]]
            return {"catchment": catch, "level": "L2", "kind": "临时压力管 / 新管（客户模型临时干预情景）", "id": label,
                    "detail": f"{len(cs)} 根，{sum(v[2] for v in cs.values()) * FT:,.0f} m；" + "；".join(f"{k} {v[0]}→{v[1]}" for k, v in list(cs.items())[:6]),
                    "evidence": ev + " 里有、Base 里没有", "source": f"SewerGEMS {catch} .inp {r}", "gdb_match": "", "unit": unit,
                    "geometry": Point(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None}
        for k, p in new.items():                          # 一台泵 + 从它出水节点出去的新管 = 一套
            unit = f"SG|{catch}|{r}|{k}"
            mine = {c: v for c, v in newc.items() if v[0] == p["outlet"]}
            outs = [v[1] for v in mine.values() if v[1] in new_out]
            pt = Point(T["xy"][p["inlet"]]) if p["inlet"] in T["xy"] else None
            rows.append({"catchment": catch, "level": "L2", "kind": "临时泵（客户模型临时干预情景）", "id": f"泵 {k}",
                         "detail": f"吸水井 {p['inlet']}；泵曲线最大流量 {p['qmax_m3s']} m³/s；出水到新出水口 {', '.join(outs) or '（不是新出水口）'}",
                         "evidence": ev, "source": f"SewerGEMS {catch} .inp {r}", "gdb_match": "", "unit": unit, "geometry": pt})
            if mine:
                rows.append(pipe_row(mine, unit, f"泵 {k} 的出水管"))
                newc = {c: v for c, v in newc.items() if c not in mine}
        if newc:
            rows.append(pipe_row(newc, f"SG|{catch}|{r}|其余", f"运算 {r} 其余新增管 {len(newc)} 根"))
        changed = [k for k in T["pumps"] if k in B["pumps"] and T["pumps"][k]["curve"] != B["pumps"][k]["curve"]]
        if changed:
            info.setdefault("ti_curve_changes", {})[r] = changed
    return rows, info


# ------------------------------------------------------------------ 主流程
def build(a):
    from shapely.geometry import Point
    refs = load_refs()
    refs["hs"] = load_hotspots()
    rows, summary = [], {}
    for catch, (pre, model, typ) in CATCH.items():
        dom = domain(a.r017, pre)
        domb = dom.buffer(EDGE_M)
        R = []
        # 1 GDB 泵站
        for _, s in refs["ps"][refs["ps"].geometry.within(domb)].iterrows():
            oos = bool(OOS.search(str(s.COMMENTS or "")))
            R.append({"catchment": catch, "level": "L1（停用）" if oos else "L1", "kind": "泵站（客户 GDB）",
                      "id": f"{s.UNITID}（站号 {s.PUMP_STATION_ID}）",
                      "detail": f"泵 {s.NO_OF_PUMPS} 台，TOTAL_CAPACITY {s.TOTAL_CAPACITY}；备注「{s.COMMENTS}」",
                      "evidence": f"STATUS={s.STATUS}，INFORMIGRATION={s.INFORMIGRATION}", "source": "GDB PS_PUMP",
                      "gdb_match": "", "geometry": s.geometry})
        # 2 台账塘
        for _, p in refs["ledger"][refs["ledger"].geometry.intersects(dom)].iterrows():
            R.append({"catchment": catch, "level": "L1", "kind": "塘（客户台账）", "id": p.unitid,
                      "detail": f"库容 {p.volume_m3} m³", "evidence": f"status={p.status}，installed={p.installed}，data_source={p.data_source}，project={p.project}",
                      "source": "console/data/ponds_l2.geojson", "gdb_match": "", "geometry": p.geometry.representative_point()})
        # 3 已建成塘图层
        if refs["ex"] is not None:
            for _, p in refs["ex"][refs["ex"].geometry.intersects(dom)].iterrows():
                R.append({"catchment": catch, "level": "L1", "kind": "塘（Parsons Existing Storage）", "id": f"{p.layer}:{p.label}",
                          "detail": f"面积 {p.geometry.area:,.0f} m²", "evidence": "图层在 Existing Storage 文件夹",
                          "source": f"Parsons_Model Data Export/Existing Storage/{p.layer}", "gdb_match": "", "geometry": p.geometry.representative_point()})
        # 4 Parsons 方案塘
        for _, p in refs["pp"][refs["pp"].geometry.intersects(dom)].iterrows():
            R.append({"catchment": catch, "level": "L2", "kind": "临时塘（Parsons 方案）", "id": f"{p.model}:{p.label}",
                      "detail": f"面积 {p.area_m2:,} m²（设计深 2 m）；模型那次运算最大蓄水 {p.model_max_storage_m3} m³",
                      "evidence": "只在 Parsons 改造后模型里，不在台账 / Existing Storage", "source": f"Parsons 26092026/{p.model}",
                      "gdb_match": "", "unit": refs["unit_of"][("pond", p.model, p.label)], "geometry": p.geometry.representative_point()})
        # 5 Parsons 泵
        for _, p in refs["pu"][refs["pu"].geometry.within(domb)].iterrows():
            hit = near_ps(refs, p.geometry)
            if STATION_LABEL.search(p.label):
                lv, kind, ev = "L1", "泵站（Parsons 模型里的现状站）", "标签带泵站号"
            elif TEMP_LABEL.match(p.label.strip()):
                lv, kind, ev = "L2", "临时泵（Parsons 方案，雨前预置）", "标签 PMP-<数字>；Parsons 方案里固定在塘边、雨前就位 → L2，不是 L3 的雨中调度移动泵"
            elif hit:
                lv, kind, ev = "L1", "泵站（Parsons 模型里的现状站）", "100 m 内有 GDB 泵站"
            else:
                lv, kind, ev = "待确认", "泵（Parsons 模型）", "标签不像临时泵，100 m 内也没有 GDB 泵站"
            R.append({"catchment": catch, "level": lv, "kind": kind, "id": f"{p.model}:{p.label}",
                      "detail": f"PUMP_FLOW {p.pump_flow_lps} L/s", "evidence": ev, "source": f"Parsons 26092026/{p.model}",
                      "gdb_match": f"{hit[0]}（站号 {hit[1]}，{hit[2]:.0f} m）" if hit else "",
                      "unit": refs["unit_of"][("pump", p.model, p.label)], "geometry": p.geometry})
        # 6b Parsons 压力管，按连通块
        P = refs["pr"][refs["pr"].geometry.intersects(domb)]
        for comp, g in P.groupby("comp"):
            ends = sorted(set(g.n1) | set(g.n2))
            temp = [e for e in ends if TEMP_LABEL.match(e)]
            stat = [e for e in ends if e.upper().startswith("PMP") and STATION_LABEL.search(e)]
            lv = "待确认" if temp and stat else ("L2" if temp else ("L1" if stat else "待确认"))
            R.append({"catchment": catch, "level": lv, "kind": "临时压力管（Parsons 方案）" if lv == "L2" else "压力管（Parsons 模型）",
                      "id": f"{g.model.iloc[0]}:压力管（{'/'.join((temp or stat or ends)[:4])}）",
                      "detail": f"{len(g)} 段，{g.geometry.length.sum():,.0f} m；端点 {', '.join(ends[:8])}{' …' if len(ends) > 8 else ''}",
                      "evidence": {"L2": "连着 PMP-<数字> 泵", "L1": "只连着现状站的泵"}.get(lv, "临时泵和现状站泵连在同一块，或两种都没有"),
                      "source": f"Parsons 26092026/{g.model.iloc[0]}", "gdb_match": "", "unit": refs["unit_of"][("comp", comp)],
                      "geometry": g.geometry.union_all().centroid})
        # 6 SewerGEMS
        info = {}
        if typ == "sewergems":
            sg, info = sewergems_rows(catch, client_data.model(model), refs, print)
            R += sg
        # 6d 热点表：热点行 + 把顾问模型的 L2 设施挂到有 IDD 计划的热点上
        HS = refs["hs"][refs["hs"].geometry.within(dom)]
        allhs = refs["hs"]                                   # 挂设施时用全市热点：设施和它对应的热点可能分在两个汇水区
        planned = allhs[allhs.idd_planned.str.lower().str.match(r"^(yes|assess)")]
        linked_hs, units = {}, {}
        for r in R:
            r.setdefault("status", "现状" if r["level"].startswith("L1") else "")
            if r["level"] == "L2" and r.get("unit"):
                units.setdefault(r["unit"], []).append(r)
        for u, rs in units.items():                       # 一套措施整体挂一个热点，成员共用状态
            if u in refs["unit_members"]:
                st, txt, hid = link_members(refs["unit_members"][u], planned, parsons=True)
            else:
                mem = [(r["geometry"], "塘" in r["kind"], "压力管 1 组" if "管" in r["kind"] else r["id"]) for r in rs if r["geometry"] is not None]
                st, txt, hid = link_members(mem, planned, parsons=False) if mem else ("待确认", "没有坐标", None)
            for r in rs:
                r["status"], r["linked_hotspot"] = st, txt
            if hid:
                linked_hs.setdefault(hid, []).extend(r["id"] for r in rs)
        for _, h in HS.iterrows():
            res = "；".join(f"{k} {h[k]}" for k in ("planA_pump12", "planA_pump8", "planA_pump6", "planA_tankers") if h[k] not in ("", "0", "0.0"))
            budget = "；".join(f"{n} {h[k]}" for n, k in (("塘", "ponds_aed"), ("混凝土挡墙", "barrier_aed"), ("沙袋", "sand_aed"), ("集水井", "chamber_aed"))
                              if h[k] not in ("", "0", "0.0"))
            n_link = len(linked_hs.get(h.hotspot_id, []))
            R.append({"catchment": catch, "level": hs_level(h), "kind": "热点（客户热点表）", "id": h.hotspot_id,
                      "detail": (f"Plan A：{res or '无泵 / 罐车'}；预算项：{budget or '无'}；IDD：{h.idd_planned or '未填'}"
                                 f" / {h.idd_category or '-'} / {h.idd_type or '-'}；措施描述：{h.design[:160] or '未填'}"
                                 + (f"；模型里挂上的设施 {n_link} 个" if n_link else ("；模型里没有对应设计" if h.idd_planned else ""))),
                      "evidence": (f"Project Stage={h.idd_stage or '-'}；Owner={h.idd_owner or '-'}；IDAS={h.idd_idas or '-'}；"
                                   f"计划 {h.plan_mob[:10] or '-'} → {h.plan_done[:10] or '-'}；实际 {h.act_mob[:10] or '-'} → {h.act_done[:10] or '-'}；"
                                   f"Intervention status={h.int_status or '-'}"),
                      "status": hs_status(h), "source": f"热点表 {HS_XLSX} · ADM Hotspot Inventory", "gdb_match": "", "geometry": h.geometry})
        # 7 result-017 L2 用过的塘
        inp = glob.glob(str(Path(a.r017) / f"{pre}_61mm1h_30m" / "L2_built_storage" / "1d" / "*.inp"))
        used = {r[0]: Point(float(r[1]), float(r[2])) for r in inp_section(inp[0], "COORDINATES") if r[0].startswith("PND_")} if inp else {}
        for k, pt in used.items():
            hits = [r for r in R if r["kind"] in POND_KINDS and r["geometry"] is not None
                    and _poly_near(refs, r, pt)]
            if hits:
                for r in hits:
                    r["r017_L2"] = (r.get("r017_L2", "") + " " + k).strip()
            else:
                R.append({"catchment": catch, "level": "待确认", "kind": "塘（result-017 用过、原始资料里找不到）", "id": k,
                          "detail": "", "evidence": "Parsons 塘、台账塘、Existing Storage 50 m 内都没有", "source": "result-017 L2 .inp",
                          "gdb_match": "", "r017_L2": k, "geometry": pt})
        lv = {}
        for r in R:
            lv[r["level"]] = lv.get(r["level"], 0) + 1
        summary[catch] = {"by_level": lv, "domain_km2": round(dom.area / 1e6, 2), "client_model": model,
                          "gdb_l1_other_assets": gdb_l1_counts(refs["gdb"], dom), **({"sewergems": info} if info else {})}
        print(f"{catch:12} {lv}")
        rows += R
    return rows, summary


# kind → (客户资产类别, L2 交付线候选)；交付线原始资料都没写
ASSET = {
    "泵站（客户 GDB）": ("Pump Stations (PS)", ""), "泵站（客户模型）": ("Pump Stations (PS)", ""),
    "泵站（Parsons 模型里的现状站）": ("Pump Stations (PS)", ""),
    "塘（客户台账）": ("Ponds", ""), "塘（Parsons Existing Storage）": ("Ponds", ""),
    "临时塘（Parsons 方案）": ("Ponds", "CAPEX 或 Municipality（两条线都有 Ponds），资料没写"),
    "临时泵（Parsons 方案，雨前预置）": ("Pumps / Stationed Pumps", "IDD（Pumps）或 Municipality（Stationed Pumps），资料没写"),
    "临时泵（客户模型临时干预情景）": ("Pumps / Stationed Pumps", "IDD（Pumps）或 Municipality（Stationed Pumps），资料没写"),
    "临时压力管（Parsons 方案）": ("Surface Pipelines", "CAPEX / IDD / Municipality 都列了，资料没写"),
    "临时压力管 / 新管（客户模型临时干预情景）": ("Surface Pipelines", "CAPEX / IDD / Municipality 都列了，资料没写"),
    "压力管（Parsons 模型）": ("Pipelines", ""), "泵（Parsons 模型）": ("Pumps", ""),
    "塘（result-017 用过、原始资料里找不到）": ("Ponds", ""),
    "热点（客户热点表）": ("（热点：计划措施见 detail）", ""),
}
POND_KINDS = {"塘（客户台账）": ("ledger", "unitid"), "塘（Parsons Existing Storage）": ("ex", None), "临时塘（Parsons 方案）": ("pp", None)}


def _poly_near(refs, row, pt):
    """台账塘 / Existing / Parsons 塘按面判 50 m（行里存的是代表点，回参照图层取面）"""
    lay = POND_KINDS[row["kind"]]
    G = refs[lay[0]]
    if lay[0] == "ledger":
        g = G[G.unitid == row["id"]].geometry
    elif lay[0] == "ex":
        layer, label = row["id"].split(":", 1)
        g = G[(G.layer == layer) & (G.label.astype(str) == label)].geometry
    else:
        model, label = row["id"].split(":", 1)
        g = G[(G.model == model) & (G.label == label)].geometry
    return bool(len(g)) and float(g.distance(pt).min()) <= MATCH_M


def main():
    import geopandas as gpd
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--r017", default=str(R017))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, summary = build(a)
    for r in rows:
        r["asset"], r["line"] = ASSET[r["kind"]]
    cols = ["catchment", "level", "status", "asset", "line", "kind", "id", "detail", "evidence", "linked_hotspot", "unit", "source", "gdb_match", "r017_L2"]
    G = gpd.GeoDataFrame(rows, geometry="geometry", crs=32640)
    for c in cols:
        if c not in G.columns:
            G[c] = ""
    G["x"] = G.geometry.x.round(1)
    G["y"] = G.geometry.y.round(1)
    G[cols + ["x", "y"]].fillna("").to_csv(OUT / "facilities.csv", index=False, encoding="utf-8-sig")
    G[cols + ["geometry"]].fillna("").to_crs(4326).to_file(OUT / "facilities.geojson", driver="GeoJSON")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT}（{len(G)} 行）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
