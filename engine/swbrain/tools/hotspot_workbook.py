"""三道防线热点尺寸反推工作簿（按 DMT WA-726 四卷手册重做）。

原 D:/世界模型/Stormwater model v2.xlsx 是别人 2026-09-18 用脚本生成的规划级表：每个热点 × 5/10/25/50/100 年，
推理公式算峰值流量 → 与管网能力比 → 三角形过程线估积水体积 → 塘缺口 → 移动泵/罐车数。它用的是迪拜 IDF 和迪拜的 Cf 频率系数，
阿布扎比手册里都没有。本脚本按 WA-726-1（设计）、-3（运维）、-4（规范）重做：
  · IDF 换 B 区表 3-6 / 3-7；Cf 去掉，换表 3-3 历时倍数；设计重现期按表 3-2 分设施；
  · 积水体积按 3.6 节：梯形过程线（升降各 tc），对 ≥ tc 的各档历时都算一遍取最大，不加倍数；塘容 +5%（4.2）；
  · 第三道：到场目标 2 小时（WA-726-4 §17000 1.1.1）、移动泵与罐车型号和日租单价来自 WA-726-3 附录 L Bill D；
  · 应急分级用 WA-726-3 表 11-1 / 附录 H-1。
热点行由本系统 run 结果生成（out/state），标「系统生成」；公式全部保留在 Excel 里，客户可以自己改输入。

用法：python -m swbrain.tools.hotspot_workbook --out "D:/世界模型/Stormwater model v3 (DMT WA-726).xlsx" --top 20
"""
from __future__ import annotations
import argparse
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule, FormulaRule

ROOT = Path(__file__).resolve().parents[2]
BLUE = Font(color="FF0000FF"); BOLD = Font(bold=True); HEAD = Font(bold=True, color="FFFFFFFF")
YELLOW = PatternFill("solid", fgColor="FFFFFF00"); GREY = PatternFill("solid", fgColor="FFDCE6F1"); ORANGE = PatternFill("solid", fgColor="FFFCE4D6"); HEADFILL = PatternFill("solid", fgColor="FF1F4E78")
WRAP = Alignment(wrap_text=True, vertical="top"); CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
THIN = Side(style="thin", color="FFBFBFBF"); BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

RPS = [5, 10, 25, 50, 100]
DUR = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440]
IDF_I = {2: [48.39, 29.54, 22.13, 13.51, 8.25, 5.04, 3.77, 2.30, 1.41, 0.86], 5: [113.18, 68.48, 51.04, 30.88, 18.68, 11.30, 8.43, 5.10, 3.08, 1.87],
         10: [127.64, 77.34, 57.70, 34.96, 21.18, 12.84, 9.57, 5.80, 3.52, 2.13], 25: [189.05, 113.38, 84.07, 50.42, 30.24, 18.13, 13.45, 8.06, 4.84, 2.90],
         50: [277.48, 161.98, 118.22, 69.01, 40.28, 23.51, 17.16, 10.02, 5.85, 3.41], 100: [325.17, 189.81, 138.54, 80.87, 47.21, 27.56, 20.11, 11.74, 6.85, 4.00]}
IDF_EQ = {2: (8.248, 0.712), 5: (18.685, 0.725), 10: (21.183, 0.723), 15: (22.327, 0.695), 25: (30.237, 0.738), 50: (40.284, 0.777), 100: (47.206, 0.777)}
CLASSES = [  # 类别, 设计重现期, 校核重现期, 历时倍数 M, 说明（表 3-2 / 3-3）
    ("underpass", 50, 100, 1, "下穿通道、地下车库坡道、主干路涵洞（表 3-2：1/50，校核 1/100；表 3-3 优先级 1）"),
    ("airport", 25, 50, 1, "机场、海港、次要道路涵洞（表 3-2：1/25，校核 1/50；优先级 1）"),
    ("major_road", 10, 0, 1, "主干路、快速路、干线、宫殿、关键区（表 3-2：1/10；优先级 1）"),
    ("critical_facility", 10, 0, 2, "医院、学校、地铁站周边（表 3-2「其他关键区，经部门批准」按 1/10；假设 M=2）"),
    ("high_density", 5, 0, 2, "高密度区，不透水面 ≥ 50%（1/5；优先级 2，M=2）"),
    ("low_density", 5, 0, 3, "低密度区，不透水面 ≤ 50%（1/5；优先级 3，M=3）"),
    ("industrial", 5, 0, 5, "工业区（1/5；优先级 4，M=5；若不透水 >50% 按低密度区）"),
    ("open_space", 5, 0, 12, "公园、空地、不致淹房的低频使用区（1/5；优先级 5，M=12）"),
    ("general", 5, 0, 2, "一般管网服务区（1/5；按高密度区取 M=2）"),
]
RUNOFF_C = [("沥青路面 Asphaltic", 0.90), ("混凝土 Concrete", 0.90), ("屋面 Roofs", 0.85), ("联锁砖 Interlocking", 0.78), ("裸地/差覆盖 Poor ground cover", 0.20),
            ("灌溉绿地 >70% irrigated grass", 0.15), ("未接管围墙地块 Walled plot not connected", 0.20)]
PUMPS = [  # 型号（WA-726-3 附录 L D2.01.077–081：柴油自吸泵，含 8 m 吸管 + 20 m 排管）, 日租 AED, 排量 m3/h（假设，按典型样本）
    ("柴油自吸泵 65 mm", 220, 40), ("柴油自吸泵 80 mm", 265, 70), ("柴油自吸泵 100 mm", 310, 120), ("柴油自吸泵 150 mm", 355, 300), ("柴油自吸泵 200 mm", 445, 500),
]
TANKERS = [  # 型号（附录 L D2.02.001–006，「日」= 10 小时班）, 日租 AED, 容量 m3（1 US gal = 3.785 L）
    ("吸污罐车 ≤3,500 gal", 850, 13.2), ("吸污罐车 3,500–4,500 gal", 1000, 17.0), ("吸污罐车 4,500–5,500 gal", 1225, 20.8), ("吸污罐车 5,500–7,500 gal", 1550, 28.4),
    ("吸污罐车 7,500–10,000 gal", 2000, 37.9), ("联合吸污/高压疏通车 ≥8,000 L", 5500, 8.0),
]
EMERGENCY = [
    ("I 级 · 优先级 C", "路面积水，路面/巡检班组处置不了，需应急土建班组；对公众与财产无风险", "工头到场→网络工程师评估→宣布 I 级→工头留守到解除"),
    ("II 级 · 优先级 B", "VIP 地点 6 小时内可恢复；室内积水；可能引起媒体关注；应急服务受扰；管网隔离 ≤6 小时；支路交通受阻；对公众/财产有风险", "网络工程师到场→高级网络工程师评估并宣布→高级工程师现场指挥→通知项目经理/顾问"),
    ("III 级 · 优先级 A", "VIP 地点 6 小时内不能恢复；暴雨致地基沉降损坏管网；多处外部积水影响多处物业；主干路/高速交通受阻；多座泵站失电；对公众/财产高风险", "高级工程师到场→项目经理评估并宣布→项目经理现场指挥→联合部门设控制室，必要时调动应急服务"),
]


def _style_header(ws, row, ncol):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c); cell.font = HEAD; cell.fill = HEADFILL; cell.alignment = CENTER; cell.border = BOX


def _box(ws, r0, r1, c0, c1):
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            ws.cell(row=r, column=c).border = BOX


# ---------------------------------------------------------------- 热点：从系统状态表生成
TUNNEL_WORDS = ("نفق", "tunnel", "underpass", "Tunnel", "Underpass")


def _vertices(cfg: dict) -> pd.DataFrame:
    """管网顶点坐标（32640 米制 + 经纬度）。首次从 pipe 库读并缓存到 out/state/vertices.parquet。"""
    cache = ROOT / "out" / "state" / "vertices.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    from swbrain import db, extract
    with db.connect_source(cfg, "pipe") as pc:
        v = extract.load_vertices(pc, cfg)
    keep = [c for c in ("id", "x", "y", "lon", "lat", "node_table") if c in v.columns]
    v = v[keep].copy(); v.to_parquet(cache, index=False)
    return v


def _edge_index(es: pd.DataFrame, vertices: pd.DataFrame):
    """在用管段的中点坐标（米制），用于按位置找热点附近的管段。"""
    vt = vertices.set_index("id")
    act = es[es["is_active"]].copy()
    act["mx"] = (act["u"].map(vt["x"]).values + act["v"].map(vt["x"]).values) / 2.0
    act["my"] = (act["u"].map(vt["y"]).values + act["v"].map(vt["y"]).values) / 2.0
    act = act.dropna(subset=["mx", "my"]).reset_index(drop=True)
    return act


def hotspots_from_client(hs_path: Path, regions: list[str] | None, run_note: str, radius_m: float = 500.0, es: pd.DataFrame | None = None, cs: pd.DataFrame | None = None) -> pd.DataFrame:
    """客户热点清单（swbrain.hotspots/1）→ 工作簿热点行。水力输入（A、tc、Qcap、塘容、类别）按位置匹配本系统 500 m 内最近的汇水区终点；
    匹配不到（或艾因/Dhafra 没有我们的管网数据）就留空，Scenario_Calc 对应行显示缺输入。客户字段（重要度、有无管网、根因、预案 A、IDD）原样带入。"""
    from scipy.spatial import cKDTree
    from swbrain import hotspots as hsm
    hs = hsm.load(hs_path)
    if regions:
        hs = hs[hs["region"].isin(regions)]
    hs = hs.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    st = ROOT / "out" / "state"
    # 同一次运行里传进来的 es/cs 优先（run 写 parquet 在调度之后，读文件会拿到上一轮的状态）
    es = es if es is not None else pd.read_parquet(st / "es.parquet"); cs = cs if cs is not None else pd.read_parquet(st / "cs.parquet"); fac = pd.read_parquet(st / "facilities.parquet").dropna(subset=["lon", "lat"])
    act = es[es["is_active"]]; into = act[act["v"] == act["terminal"]]
    trunk = into.sort_values("q_full", ascending=False).groupby("terminal").first(); tcmax = into.groupby("terminal")["tc_min"].max()
    c = cs.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    k = np.cos(np.radians(24.4)) * 111000
    xy = lambda d: np.c_[d["lon"].values * k, d["lat"].values * 111000]
    d, j = cKDTree(xy(c)).query(xy(hs))
    # 管段级匹配：热点 edge_radius 内的在用管段里取上游汇水面积最大的那段（主干），比「最近终点」更能代表该低点的来水
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    from pyproj import Transformer
    tr32 = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    hx, hy = tr32.transform(hs["lon"].values, hs["lat"].values)
    eidx = _edge_index(es, _vertices(cfg)); etree = cKDTree(np.c_[eidx["mx"].values, eidx["my"].values])
    edge_radius = 150.0
    near_edges = etree.query_ball_point(np.c_[hx, hy], r=edge_radius)
    csi = cs.set_index("terminal")
    ftree = cKDTree(xy(fac)); crit_kind = fac["kind"].isin(["hospital", "school", "metro", "tunnel"]).values
    ncrit = [int(crit_kind[ftree.query_ball_point(q, r=300)].sum()) for q in xy(hs)]
    CZ = {"Very High": "极高", "High": "高", "Medium": "中", "Low": "低"}
    RZ = {"capacity": "管网能力不足/需升级", "no_network": "无排水管网", "topography": "地形与道路工程", "other": "其他"}
    rows = []
    for i, h in hs.iterrows():
        name = f"{h['area']} · {h['location']}"
        is_tun = any(w in str(h["location"]) for w in TUNNEL_WORDS)
        cand = near_edges[i] if h["region"] == "ADM" else []
        egid = None
        matched = (d[i] <= radius_m) and (h["region"] == "ADM")
        if len(cand):
            e = eidx.iloc[cand]; e = e.loc[e["area_m2"].idxmax()]
            egid = int(e["gid"])
            dist_e = float(np.hypot(e["mx"] - hx[i], e["my"] - hy[i]))
            cls = "underpass" if is_tun else (str(e["design_class"]) if isinstance(e["design_class"], str) else "general")
            area_ha = round(float(e["area_m2"]) / 1e4, 1); tc = round(float(e["tc_min"]), 1) if pd.notna(e["tc_min"]) else None
            qcap = round(float(e["q_full"]), 3)
            t = int(e["terminal"]) if pd.notna(e["terminal"]) else None
            pond = round(float(csi.loc[t, "storage_m3"]), 0) if (t in csi.index and pd.notna(csi.loc[t, "storage_m3"])) else 0.0
            tk = str(csi.loc[t, "terminal_kind"]) if t in csi.index else ""
            note = f"匹配 {edge_radius:.0f} m 内主干管段 {e['unitid']}（DN{int(e['pipe_diameter']) if pd.notna(e['pipe_diameter']) else '?'}，{dist_e:.0f} m，上游 {area_ha} ha，终点 {tk}）"
            cval = round(float(e["runoff_c"]), 2) if ("runoff_c" in e.index and pd.notna(e["runoff_c"])) else 0.85
            risk_out = {"出水口": 0, "泵站": 1, "渗井": 2}.get(tk, 3)
            matched = True
        elif matched:
            t = int(c.loc[j[i], "terminal"]); tr = trunk.loc[t] if t in trunk.index else None
            cls = "underpass" if is_tun else (str(tr["design_class"]) if tr is not None and isinstance(tr["design_class"], str) else "general")
            area_ha = round(float(c.loc[j[i], "area_m2"]) / 1e4, 1); tc = round(float(tcmax.get(t, 15.0)), 1)
            qcap = round(float(tr["q_full"]), 3) if tr is not None else None
            pond = round(float(c.loc[j[i], "storage_m3"]), 0) if pd.notna(c.loc[j[i], "storage_m3"]) else 0.0
            note = f"匹配汇水区终点 {c.loc[j[i], 'terminal_unitid'] or t}（{c.loc[j[i], 'terminal_kind']}，{d[i]:.0f} m）" + ("，疑似漏登终点，数值待核" if bool(c.loc[j[i], "suspect_missing_outlet"]) else "")
            cval = round(float(tr["runoff_c"]), 2) if (tr is not None and "runoff_c" in tr.index and pd.notna(tr["runoff_c"])) else 0.85
            risk_out = {"出水口": 0, "泵站": 1, "渗井": 2}.get(str(c.loc[j[i], "terminal_kind"]), 3)
        else:
            cls = "underpass" if is_tun else "general"; area_ha = tc = qcap = pond = None; cval = None
            note = "非阿布扎比市，无本系统管网数据" if h["region"] != "ADM" else f"500 m 内无本系统汇水区终点（最近 {d[i]:.0f} m）"
            risk_out = 3 if h["network_available"] is False else 2
        crit = h["criticality"]; cw = {"Very High": 3, "High": 2, "Medium": 1, "Low": 0}.get(crit, 1)
        risk_imp = {"underpass": 3, "major_road": 3, "high_density": 3, "critical_facility": 2, "industrial": 2, "general": 2, "low_density": 1, "open_space": 0, "airport": 2}.get(cls, 2)
        idd = h.get("idd_planned_flag")
        idd_txt = ("已计划：" + " · ".join(str(x) for x in (h.get("idd_stage"), h.get("idd_owner"), h.get("idd_completion")) if x)) if idd == "yes" else ("评估中" if idd == "assessing" else ("无" if idd == "no" else ""))
        rows.append({"id": h["hotspot_id"], "edge_gid": egid, "name": name, "class": cls, "area_ha": area_ha, "c": cval if matched else None, "tc_min": tc, "qcap": qcap, "pond_m3": pond,
                     "risk_low": cw, "risk_hist": cw, "risk_out": risk_out, "risk_crit": 3 if ncrit[i] >= 3 else 2 if ncrit[i] == 2 else 1 if ncrit[i] == 1 else 0, "risk_imp": risk_imp,
                     "lon": round(float(h["lon"]), 5), "lat": round(float(h["lat"]), 5), "source": run_note,
                     "region": h["region"], "crit_zh": CZ.get(crit, crit), "net": "是" if h["network_available"] is True else ("否" if h["network_available"] is False else ""),
                     "root_zh": RZ.get(h.get("root_cause"), h.get("root_cause")), "planA_pumps": h.get("planA_pumps"), "planA_tankers": h.get("planA_tankers"), "idd": idd_txt, "match": note})
    out = pd.DataFrame(rows)
    tp = ROOT / "out" / "state" / "hotspot_terrain.parquet"   # 5 m DTM 填洼（run 或 python -m swbrain.terrain --hotspots 生成）
    if tp.exists():
        ht = pd.read_parquet(tp)[["hotspot_id", "dtm_z", "depth_max_m", "pond_volume_m3", "spill_z", "terrain_note_zh", "risk_low"]]
        ht = ht.rename(columns={"hotspot_id": "id", "risk_low": "risk_low_dtm", "terrain_note_zh": "terrain_note"})
        out = out.merge(ht, on="id", how="left")
        has = out["risk_low_dtm"].notna()
        out.loc[has, "risk_low"] = out.loc[has, "risk_low_dtm"].astype(int)
        out = out.drop(columns=["risk_low_dtm"])
    return out


def hotspots_from_state(top: int, run_note: str) -> pd.DataFrame:
    st = ROOT / "out" / "state"
    es = pd.read_parquet(st / "es.parquet"); cs = pd.read_parquet(st / "cs.parquet"); zs = pd.read_parquet(st / "zs.parquet")
    fac = pd.read_parquet(st / "facilities.parquet").dropna(subset=["lon", "lat"])
    act = es[es["is_active"]]
    into = act[act["v"] == act["terminal"]]
    trunk = into.sort_values("q_full", ascending=False).groupby("terminal").first()
    tcmax = into.groupby("terminal")["tc_min"].max()
    c = cs[~cs["suspect_missing_outlet"].fillna(False).astype(bool)].copy()
    # 只取推理公式适用范围内的汇水区（WA-726-1 3.3：A < 80 ha 或 tc < 30 min），面积 ≥ 2 ha；大汇水区要拆分或用 SCS，不进这张表
    c["tc_t"] = c["terminal"].map(tcmax); c["cls_t"] = c["terminal"].map(trunk["design_class"]).fillna("general")
    c = c[(c["area_m2"] >= 2e4) & ((c["area_m2"] < 80e4) | (c["tc_t"] < 30))]
    wcls = {"underpass": 4.0, "airport": 3.0, "major_road": 2.0, "critical_facility": 2.0, "high_density": 1.2, "industrial": 1.0, "general": 1.0, "low_density": 0.8, "open_space": 0.5}
    c["score"] = c["cls_t"].map(wcls).fillna(1.0) * (0.5 + c["over_share_常规"].fillna(0)) * np.log1p(c["n_inlets"].fillna(0))
    must = c[c["cls_t"].isin(["underpass", "airport", "critical_facility"])]
    rest = c[~c.index.isin(must.index) & (c["over_km_常规"] > 0)].sort_values("score", ascending=False)
    c = pd.concat([must.sort_values("score", ascending=False), rest]).head(top).reset_index(drop=True)
    from scipy.spatial import cKDTree
    fx = np.c_[fac["lon"].values * np.cos(np.radians(24.4)) * 111000, fac["lat"].values * 111000]
    tree = cKDTree(fx); crit = fac["kind"].isin(["hospital", "school", "metro", "tunnel"]).values
    zb = zs.set_index("zone_name")["blockage_per_100km"]
    rows = []
    for i, r in c.iterrows():
        t = int(r["terminal"]); tr = trunk.loc[t] if t in trunk.index else None
        q = np.c_[[r["lon"] * np.cos(np.radians(24.4)) * 111000], [r["lat"] * 111000]]
        near = tree.query_ball_point(q[0], r=300); ncrit = int(crit[near].sum()) if len(near) else 0
        cls = str(tr["design_class"]) if tr is not None and isinstance(tr["design_class"], str) else "general"
        low = float(r["cover_p10_rel"]) if pd.notna(r["cover_p10_rel"]) else np.nan
        risk_low = 3 if (pd.notna(low) and low < 2.0) else 2 if (pd.notna(low) and low < 3.0) else 1
        bl = float(zb.get(r["zone_name"], np.nan)); risk_hist = 3 if bl > 3 else 2 if bl > 1 else 1 if bl > 0 else 0
        risk_out = {"出水口": 0, "泵站": 1, "渗井": 2}.get(str(r["terminal_kind"]), 3)
        risk_crit = 3 if ncrit >= 3 else 2 if ncrit == 2 else 1 if ncrit == 1 else 0
        risk_imp = {"underpass": 3, "major_road": 3, "high_density": 3, "critical_facility": 2, "industrial": 2, "general": 2, "low_density": 1, "open_space": 0, "airport": 2}.get(cls, 2)
        rows.append({"id": f"H{i+1:02d}", "name": f"{r['zone_name'] or '未分区'} · 终点 {r['terminal_unitid'] or t}（{r['terminal_kind']}）",
                     "class": cls, "area_ha": round(float(r["area_m2"]) / 1e4, 1), "c": 0.85,
                     "tc_min": round(float(tcmax.get(t, 15.0)), 1), "qcap": round(float(tr["q_full"]) if tr is not None else 0.0, 3),
                     "pond_m3": round(float(r["storage_m3"]) if pd.notna(r["storage_m3"]) else 0.0, 0),
                     "risk_low": risk_low, "risk_hist": risk_hist, "risk_out": risk_out, "risk_crit": risk_crit, "risk_imp": risk_imp,
                     "lon": round(float(r["lon"]), 5), "lat": round(float(r["lat"]), 5), "source": run_note})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 工作簿
def build(out: Path, hotspots: pd.DataFrame, run_note: str):
    wb = Workbook(); wb.calculation.fullCalcOnLoad = True
    # ---- Instructions
    ws = wb.active; ws.title = "Instructions"
    ws.column_dimensions["B"].width = 26; ws.column_dimensions["C"].width = 110
    ws["B2"] = "阿布扎比雨水系统 · 三道防线热点尺寸反推模型（按 DMT WA-726 手册，v3）"; ws["B2"].font = Font(bold=True, size=14)
    ws["B3"] = "每个热点 × 5/10/25/50/100 年暴雨：推理公式峰值流量 → 与管网能力比 → 梯形过程线积水体积 → 调蓄缺口 → 移动泵与罐车需求与日租成本。规划级筛查，不是设计。"
    rows = [
        ("依据", "WA-726-1 设计手册第三版 2022-04：表 3-2 分设施设计重现期、表 3-3 历时倍数、3.2.3 汇流时间、表 3-6/3-7 B 区 IDF、3.3.2 推理公式 Q=2.77×10⁻³·C·i·A、3.6 洪量梯形过程线、4.2 调蓄 +5% 沉沙、表 3-8 径流系数。"
                "WA-726-3 运维手册：表 11-1 与附录 H-1 应急分级、11.6 到场目标 2 小时、附录 L 日工单价（移动泵 D2.01.073–081、罐车 D2.02.001–006）。WA-726-4 规范第 17 分部 §17000 1.1.1：目标响应时间 2 小时。"),
        ("与 v2 的区别", "v2 用迪拜市政 IDF 和迪拜的 Cf 频率系数，阿布扎比手册没有 Cf，用的是按用地优先级的历时倍数；v2 积水体积用 0.5×超出流量×历时的三角形，手册 3.6 要求梯形过程线并对多个历时取最大；v2 的泵 300 m³/h、罐车 15 m³、2 小时排干是假设，v3 换成手册附录 L 的型号与单价，到场 2 小时是手册规定；v2 六个热点是虚构示例，v3 的热点由「会思考的排水系统」run 结果生成。"),
        ("1. Hotspots", "热点登记表。默认装入客户热点清单（DMT 各市政区 Stormwater Hotspot Analysis，Origen Batch 1）全部热点，右侧带客户原字段（区域、重要度、有无管网、根因、预案 A 泵/罐车、IDD 干预）。"
                        "汇水面积、汇流时间、现有管网能力、现有塘容、类别由「会思考的排水系统」按位置匹配 500 m 内最近汇水区终点填入（匹配说明列写明终点与距离）；艾因、Dhafra 没有管网数据的行留空，Scenario_Calc 对应行显示缺输入。"
                        "最右 5 列是 5 m DTM 地形（客户提供）：热点处高程、洼地最大深度、灌到溢出的填洼体积与溢出高程、说明；有 DTM 的行「低点/高程风险」按洼深 ≥0.5/0.3/0.15 m 记 3/2/1。"
                        "Scenario_Calc 末两列把缺口与填洼体积比较：缺口超过洼地能蓄的量就会漫到邻区。"
                        "蓝字为输入，可直接覆盖：类别（下拉，决定设计重现期、校核重现期、历时倍数）、面积、径流系数、汇流时间、管网能力、塘容、五项 0–3 风险打分。"),
        ("2. IDF_ZoneB", "表 3-6 雨强/雨深与表 3-7 公式系数（B 区 = 阿布扎比市区、Dhafra、艾因非山区）。艾因东北山区（A 区）不适用。"),
        ("3. DesignStandard", "表 3-2 类别 → 设计重现期、校核重现期、历时倍数 M；表 3-8 径流系数；表 11-1 / 附录 H-1 应急分级。"),
        ("4. Assumptions", "黄底为假设：沉沙余量、入流时间、到场目标、移动泵与罐车型号（附录 L 单价是官方的，排量与容量按典型样本估）、罐车周转时间。"),
        ("5. Scenario_Calc", "引擎。设计历时 = M×tc → 雨强 i=α·t^(−β) → Q → 超出流量；洪量按 3.6：对 ≥tc 的每档 IDF 历时算梯形超出体积取最大（不加倍数）→ ×1.05 → 缺口 → 泵数、罐车数、日租成本。设计/校核重现期行有标记。"),
        ("6. Fleet_Summary", "按重现期汇总；另有「按法定标准」行（每个热点只取自己的设计重现期）与「校核」行（下穿 100 年、机场 50 年）。"),
        ("颜色", "蓝字 = 输入；黄底 = 假设，用前核对；黑字 = 公式，不要覆盖。"),
        ("限制", "推理公式只适用汇水 <80 ha 或 tc <30 min（3.3），超范围的热点表内标红，应用 SCS/水动力模型；渗井类方案受 4.12 限制；结果供排序与量级判断，工程设计须经部门批准的水力模型。"),
    ]
    for i, (k, v) in enumerate(rows):
        ws.cell(row=5 + i, column=2, value=k).font = BOLD; c = ws.cell(row=5 + i, column=3, value=v); c.alignment = WRAP
        ws.row_dimensions[5 + i].height = 15 * max(2, len(v) // 95 + 1)
    ws.cell(row=5 + len(rows) + 1, column=2, value="生成").font = BOLD; ws.cell(row=5 + len(rows) + 1, column=3, value=run_note)

    # ---- IDF_ZoneB
    ws = wb.create_sheet("IDF_ZoneB")
    ws["B2"] = "B 区 IDF（WA-726-1 表 3-6）与公式系数（表 3-7）：i = α · t^(−β)，t 为小时"; ws["B2"].font = Font(bold=True, size=12)
    ws["A4"] = "重现期(年) \\ 历时(min)"; ws["A4"].font = BOLD
    for j, d in enumerate(DUR): ws.cell(row=4, column=2 + j, value=d)
    _style_header(ws, 4, 1 + len(DUR))
    for i, rp in enumerate([2, 5, 10, 25, 50, 100]):
        ws.cell(row=5 + i, column=1, value=rp)
        for j, v in enumerate(IDF_I[rp]): ws.cell(row=5 + i, column=2 + j, value=v)
    ws["A12"] = "雨深 mm = 雨强 × 历时/60"; ws["A12"].font = BOLD
    for i, rp in enumerate([2, 5, 10, 25, 50, 100]):
        ws.cell(row=13 + i, column=1, value=rp)
        for j, d in enumerate(DUR): ws.cell(row=13 + i, column=2 + j, value=f"=ROUND({L(2+j)}{5+i}*{d}/60,2)")
    ws["A20"] = "重现期(年)"; ws["B20"] = "α"; ws["C20"] = "β"; _style_header(ws, 20, 3)
    for i, rp in enumerate([2, 5, 10, 15, 25, 50, 100]):
        ws.cell(row=21 + i, column=1, value=rp); ws.cell(row=21 + i, column=2, value=IDF_EQ[rp][0]); ws.cell(row=21 + i, column=3, value=IDF_EQ[rp][1])
    ws["E20"] = "查表用：α = INDEX($B$21:$B$27, MATCH(rp,$A$21:$A$27,0))，β 同理；历时下限 5 分钟（表格最短档）"; ws["E20"].alignment = WRAP
    ws.column_dimensions["A"].width = 22
    for j in range(len(DUR) + 2): ws.column_dimensions[L(2 + j)].width = 9

    # ---- DesignStandard
    ws = wb.create_sheet("DesignStandard")
    ws["B2"] = "设计标准（WA-726-1 表 3-2 设计重现期 × 表 3-3 历时倍数）"; ws["B2"].font = Font(bold=True, size=12)
    hdr = ["类别 class", "设计重现期 (yr)", "校核重现期 (yr, 0=无)", "历时倍数 M", "依据与说明"]
    for j, h in enumerate(hdr): ws.cell(row=4, column=1 + j, value=h)
    _style_header(ws, 4, len(hdr))
    for i, (cls, rp, chk, m, note) in enumerate(CLASSES):
        for j, v in enumerate((cls, rp, chk, m, note)): ws.cell(row=5 + i, column=1 + j, value=v)
        ws.cell(row=5 + i, column=5).alignment = WRAP
    _box(ws, 4, 4 + len(CLASSES), 1, len(hdr))
    r0 = 6 + len(CLASSES)
    ws.cell(row=r0, column=2, value="径流系数（表 3-8，按地表类型面积加权得复合 C）").font = BOLD
    ws.cell(row=r0 + 1, column=1, value="地表类型"); ws.cell(row=r0 + 1, column=2, value="C"); _style_header(ws, r0 + 1, 2)
    for i, (k, v) in enumerate(RUNOFF_C):
        ws.cell(row=r0 + 2 + i, column=1, value=k); ws.cell(row=r0 + 2 + i, column=2, value=v)
    r1 = r0 + 3 + len(RUNOFF_C)
    ws.cell(row=r1, column=2, value="内涝应急分级（WA-726-3 表 11-1 / 附录 H-1）；到场目标 2 小时（11.6；WA-726-4 §17000 1.1.1）；应急动员 KPI 30 分钟（5.7.1）").font = BOLD
    for j, h in enumerate(["级别", "宣布条件", "响应链"]): ws.cell(row=r1 + 1, column=1 + j, value=h)
    _style_header(ws, r1 + 1, 3)
    for i, (a, b, c) in enumerate(EMERGENCY):
        for j, v in enumerate((a, b, c)): cell = ws.cell(row=r1 + 2 + i, column=1 + j, value=v); cell.alignment = WRAP
        ws.row_dimensions[r1 + 2 + i].height = 60
    ws.column_dimensions["A"].width = 20; ws.column_dimensions["B"].width = 60; ws.column_dimensions["C"].width = 60; ws.column_dimensions["D"].width = 12; ws.column_dimensions["E"].width = 70

    # ---- Assumptions
    ws = wb.create_sheet("Assumptions")
    ws["B2"] = "全局假设（黄底请核对）"; ws["B2"].font = Font(bold=True, size=12)
    for j, h in enumerate(["参数", "值", "依据"]): ws.cell(row=4, column=2 + j, value=h)
    _style_header(ws, 4, 4); ws.cell(row=4, column=1, value="")
    A = [
        ("沉沙余量系数", 1.05, "WA-726-1 4.2：塘/离线池容积比所需调蓄量大 5%"),
        ("入流时间最小值 (min)", 10, "WA-726-1 3.2.3：不透水汇水区入流时间至少 10 分钟（热点 tc 已含）"),
        ("到场/处置目标 (h)", 2, "WA-726-3 11.6 与 WA-726-4 §17000 1.1.1：目标响应时间 2 小时到场。此处也用作缺口排除时限（假设）"),
        ("推理公式常数", 0.00277, "WA-726-1 3.3.2：Q(m³/s) = 2.77×10⁻³ · C · i(mm/h) · A(ha)"),
        ("选用移动泵型号", PUMPS[3][0], "下拉选择；排量为典型样本估计（假设），日租取 WA-726-3 附录 L D2.01.077–081"),
        ("选用罐车型号", TANKERS[2][0], "下拉选择；容量按加仑换算，日租取附录 L D2.02.001–006（10 小时班）"),
        ("罐车单趟周转 (min)", 45, "假设：装载+往返+卸载；影响一个班次内一辆罐车能运几趟"),
        ("罐车班次 (h)", 10, "附录 L：罐车「日」= 10 小时班"),
        ("每热点最多同时布泵 (台)", 3, "假设：受场地与吸水位置限制"),
    ]
    for i, (k, v, s) in enumerate(A):
        ws.cell(row=5 + i, column=2, value=k); c = ws.cell(row=5 + i, column=3, value=v); c.fill = YELLOW; ws.cell(row=5 + i, column=4, value=s).alignment = WRAP
    r0 = 6 + len(A)
    ws.cell(row=r0, column=2, value="移动泵型号表（附录 L Bill D，柴油自吸泵含 8 m 吸管 + 20 m 排管）").font = BOLD
    for j, h in enumerate(["型号", "日租 AED/日", "排量 m³/h（假设）"]): ws.cell(row=r0 + 1, column=2 + j, value=h)
    _style_header(ws, r0 + 1, 4)
    for i, (k, rate, cap) in enumerate(PUMPS):
        ws.cell(row=r0 + 2 + i, column=2, value=k); ws.cell(row=r0 + 2 + i, column=3, value=rate); c = ws.cell(row=r0 + 2 + i, column=4, value=cap); c.fill = YELLOW
    p0, p1 = r0 + 2, r0 + 1 + len(PUMPS)
    r1 = p1 + 2
    ws.cell(row=r1, column=2, value="罐车型号表（附录 L D2.02，「日」= 10 小时班，含司机油料）").font = BOLD
    for j, h in enumerate(["型号", "日租 AED/日", "容量 m³"]): ws.cell(row=r1 + 1, column=2 + j, value=h)
    _style_header(ws, r1 + 1, 4)
    for i, (k, rate, cap) in enumerate(TANKERS):
        ws.cell(row=r1 + 2 + i, column=2, value=k); ws.cell(row=r1 + 2 + i, column=3, value=rate); ws.cell(row=r1 + 2 + i, column=4, value=cap)
    t0, t1 = r1 + 2, r1 + 1 + len(TANKERS)
    # 选中型号的参数（公式）
    r2 = t1 + 2
    ws.cell(row=r2, column=2, value="选中泵排量 m³/h"); ws.cell(row=r2, column=3, value=f"=INDEX($D${p0}:$D${p1},MATCH($C$9,$B${p0}:$B${p1},0))")
    ws.cell(row=r2 + 1, column=2, value="选中泵日租 AED"); ws.cell(row=r2 + 1, column=3, value=f"=INDEX($C${p0}:$C${p1},MATCH($C$9,$B${p0}:$B${p1},0))")
    ws.cell(row=r2 + 2, column=2, value="选中罐车容量 m³"); ws.cell(row=r2 + 2, column=3, value=f"=INDEX($D${t0}:$D${t1},MATCH($C$10,$B${t0}:$B${t1},0))")
    ws.cell(row=r2 + 3, column=2, value="选中罐车日租 AED"); ws.cell(row=r2 + 3, column=3, value=f"=INDEX($C${t0}:$C${t1},MATCH($C$10,$B${t0}:$B${t1},0))")
    ws.cell(row=r2 + 4, column=2, value="一辆罐车每班可运趟数"); ws.cell(row=r2 + 4, column=3, value="=INT($C$12*60/$C$11)")
    dv_p = DataValidation(type="list", formula1=f"=$B${p0}:$B${p1}", allow_blank=False); ws.add_data_validation(dv_p); dv_p.add("C9")
    dv_t = DataValidation(type="list", formula1=f"=$B${t0}:$B${t1}", allow_blank=False); ws.add_data_validation(dv_t); dv_t.add("C10")
    ws.column_dimensions["B"].width = 34; ws.column_dimensions["C"].width = 16; ws.column_dimensions["D"].width = 90
    SEL_PUMP_CAP, SEL_PUMP_RATE, SEL_TANK_CAP, SEL_TANK_RATE, TRIPS = f"Assumptions!$C${r2}", f"Assumptions!$C${r2+1}", f"Assumptions!$C${r2+2}", f"Assumptions!$C${r2+3}", f"Assumptions!$C${r2+4}"
    SED, TARGET_H, KRAT, MAXPUMPS = "Assumptions!$C$5", "Assumptions!$C$7", "Assumptions!$C$8", "Assumptions!$C$13"

    # ---- Hotspots
    ws = wb.create_sheet("Hotspots")
    ws["B2"] = "热点登记表"; ws["B2"].font = Font(bold=True, size=12)
    ws["B3"] = "蓝字 = 输入。类别按 DesignStandard 下拉；系统生成行来源见「来源」列，可覆盖。风险打分 0–3；风险分与排名为公式。汇水 >80 ha 或 tc >30 min 的行标红：推理公式仅作筛查。"
    hdr = ["热点\nID", "位置 / 说明", "类别\n(表 3-2)", "汇水面积\nA (ha)", "径流系数\nC", "汇流时间\ntc (min)", "现有管网能力\nQcap (m³/s)", "现有塘容\n(m³)",
           "设计重现期\n(yr)", "校核重现期\n(yr)", "历时倍数\nM", "低点/高程\n风险 0-3", "历史内涝\n风险 0-3", "离出水口\n风险 0-3", "重点单位\n风险 0-3", "不透水\n风险 0-3", "风险分", "排名", "推理公式\n适用?", "经度", "纬度", "来源",
           "区域", "客户\n重要度", "客户:\n有管网", "客户:根因", "预案A\n泵", "预案A\n罐车", "IDD 干预", "水力输入匹配说明",
           "DTM 高程\n(m)", "地形洼深\n(m)", "填洼体积\n(m³)", "溢出高程\n(m)", "地形说明（5 m DTM）"]
    for j, h in enumerate(hdr): ws.cell(row=5, column=1 + j, value=h)
    _style_header(ws, 5, len(hdr)); ws.row_dimensions[5].height = 44
    NMAX = max(40, len(hotspots) + 5); H0, H1 = 6, 5 + NMAX
    cls_range = f"DesignStandard!$A$5:$A${4+len(CLASSES)}"
    for k in range(NMAX):
        r = H0 + k
        if k < len(hotspots):
            h = hotspots.iloc[k]
            vals = [h["id"], h["name"], h["class"], h["area_ha"], h["c"], h["tc_min"], h["qcap"], h["pond_m3"], None, None, None,
                    int(h["risk_low"]), int(h["risk_hist"]), int(h["risk_out"]), int(h["risk_crit"]), int(h["risk_imp"]), None, None, None, h["lon"], h["lat"], h["source"],
                    h.get("region"), h.get("crit_zh"), h.get("net"), h.get("root_zh"), h.get("planA_pumps"), h.get("planA_tankers"), h.get("idd"), h.get("match"),
                    h.get("dtm_z"), h.get("depth_max_m"), h.get("pond_volume_m3"), h.get("spill_z"), h.get("terrain_note")]
            vals = [None if (isinstance(v, float) and np.isnan(v)) else v for v in vals]
            for j, v in enumerate(vals):
                if v is not None: ws.cell(row=r, column=1 + j, value=v)
            for j in (1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 15, 16): ws.cell(row=r, column=j).font = BLUE
        ws.cell(row=r, column=9, value=f'=IF($C{r}="","",INDEX(DesignStandard!$B$5:$B${4+len(CLASSES)},MATCH($C{r},{cls_range},0)))')
        ws.cell(row=r, column=10, value=f'=IF($C{r}="","",INDEX(DesignStandard!$C$5:$C${4+len(CLASSES)},MATCH($C{r},{cls_range},0)))')
        ws.cell(row=r, column=11, value=f'=IF($C{r}="","",INDEX(DesignStandard!$D$5:$D${4+len(CLASSES)},MATCH($C{r},{cls_range},0)))')
        ws.cell(row=r, column=17, value=f'=IF($A{r}="","",SUM(L{r}:P{r}))')
        ws.cell(row=r, column=18, value=f'=IF($A{r}="","",RANK(Q{r},$Q${H0}:$Q${H1}))')
        ws.cell(row=r, column=19, value=f'=IF($A{r}="","",IF(NOT(ISNUMBER(D{r})),"缺输入：无匹配汇水区",IF(OR(D{r}<80,F{r}<30),"是","否：A≥80 ha 且 tc≥30 min，须 SCS/水动力模型")))')
    dv_c = DataValidation(type="list", formula1=cls_range, allow_blank=True); ws.add_data_validation(dv_c); dv_c.add(f"C{H0}:C{H1}")
    ws.conditional_formatting.add(f"S{H0}:S{H1}", FormulaRule(formula=[f'LEFT($S{H0},1)="否"'], fill=PatternFill("solid", fgColor="FFF4CCCC")))
    _box(ws, 5, H1, 1, len(hdr))
    widths = [12, 46, 15, 10, 8, 10, 13, 10, 10, 10, 8, 9, 9, 9, 9, 9, 8, 6, 22, 10, 10, 30, 7, 9, 8, 18, 8, 8, 40, 46, 9, 9, 11, 9, 36]
    for j, w in enumerate(widths): ws.column_dimensions[L(1 + j)].width = w
    ws.freeze_panes = "C6"

    # ---- Scenario_Calc
    ws = wb.create_sheet("Scenario_Calc")
    ws["B2"] = "情景计算：每个热点 × 5/10/25/50/100 年。峰值流量按表 3-3 历时倍数；积水体积按 3.6 梯形过程线多历时取最大（不加倍数）；三道防线资源按 Assumptions 选型"; ws["B2"].font = Font(bold=True, size=12)
    ws["B3"] = "黑字全是公式。设计重现期 = 该热点表 3-2 的标准；校核重现期 = 下穿 100 年 / 机场 50 年。"
    hdr = ["热点ID", "重现期\n(yr)", "热点名称", "类别", "是否设计\n重现期", "是否校核\n重现期", "tc (min)", "M", "设计历时\nM·tc (min)", "α", "β", "雨强 i\n(mm/h)", "A (ha)", "C", "峰值流量 Q\n(m³/s)", "Qcap\n(m³/s)", "超出流量\n(m³/s)", "能力比\nQ/Qcap"]
    vol_hdr = ["V@tc\n(m³)"] + [f"V@{d}min\n(m³)" for d in DUR]
    hdr2 = ["洪量 max\n(m³)", "沉沙系数", "所需调蓄\n(m³)", "现有塘容\n(m³)", "缺口\n(m³)", "处置时限\n(h)", "需排量\n(m³/h)", "选中泵\n排量", "需泵台数", "超出布泵\n上限?", "罐车趟数", "需罐车\n(辆/班)", "泵日租\nAED/日", "罐车日租\nAED/日", "待命日成本\nAED/日", "区域", "地形填洼\n(m³)", "超出地形\n可蓄 (m³)"]
    allh = hdr + vol_hdr + hdr2
    for j, h in enumerate(allh): ws.cell(row=5, column=1 + j, value=h)
    _style_header(ws, 5, len(allh)); ws.row_dimensions[5].height = 44
    S0 = 6; nrows = NMAX * len(RPS); S1 = S0 + nrows - 1
    HR = f"Hotspots!$A${H0}:$A${H1}"
    def hcol(col, r):   # INDEX 引用空格会得 0，先判空再取值，缺输入的行才能真正留空
        ix = f'INDEX(Hotspots!${col}${H0}:${col}${H1},MATCH($A{r},{HR},0))'
        return f'IFERROR(IF({ix}="","",{ix}),"")'
    cV0 = len(hdr) + 1  # 第一个 V 列
    for k in range(NMAX):
        for q, rp in enumerate(RPS):
            r = S0 + k * len(RPS) + q
            ws.cell(row=r, column=1, value=f'=IF(Hotspots!$A${H0+k}="","",Hotspots!$A${H0+k})')
            ws.cell(row=r, column=2, value=rp)
            ws.cell(row=r, column=3, value=f'=IF($A{r}="","",{hcol("B", r)})')
            ws.cell(row=r, column=4, value=f'=IF($A{r}="","",{hcol("C", r)})')
            ws.cell(row=r, column=5, value=f'=IF($A{r}="","",IF({hcol("I", r)}=$B{r},"设计",""))')
            ws.cell(row=r, column=6, value=f'=IF($A{r}="","",IF({hcol("J", r)}=$B{r},"校核",""))')
            ws.cell(row=r, column=7, value=f'=IF($A{r}="","",{hcol("F", r)})')
            ws.cell(row=r, column=8, value=f'=IF($A{r}="","",{hcol("K", r)})')
            OK = f'AND(ISNUMBER(G{r}),ISNUMBER(M{r}),ISNUMBER(N{r}),ISNUMBER(P{r}))'
            ws.cell(row=r, column=9, value=f'=IF(OR($A{r}="",NOT(ISNUMBER(G{r}))),"",MAX(5,H{r}*G{r}))')
            ws.cell(row=r, column=10, value=f'=INDEX(IDF_ZoneB!$B$21:$B$27,MATCH($B{r},IDF_ZoneB!$A$21:$A$27,0))')
            ws.cell(row=r, column=11, value=f'=INDEX(IDF_ZoneB!$C$21:$C$27,MATCH($B{r},IDF_ZoneB!$A$21:$A$27,0))')
            ws.cell(row=r, column=12, value=f'=IF(OR($A{r}="",I{r}=""),"",J{r}*(I{r}/60)^(-K{r}))')
            ws.cell(row=r, column=13, value=f'=IF($A{r}="","",{hcol("D", r)})')
            ws.cell(row=r, column=14, value=f'=IF($A{r}="","",{hcol("E", r)})')
            ws.cell(row=r, column=15, value=f'=IF(OR($A{r}="",NOT({OK})),"",{KRAT}*N{r}*L{r}*M{r})')
            ws.cell(row=r, column=16, value=f'=IF($A{r}="","",{hcol("G", r)})')
            ws.cell(row=r, column=17, value=f'=IF(OR($A{r}="",NOT({OK})),"",MAX(0,O{r}-P{r}))')
            ws.cell(row=r, column=18, value=f'=IF(OR($A{r}="",NOT({OK}),P{r}=0),"",O{r}/P{r})')
            # 洪量（3.6）：对历时 = tc 及每档 ≥ tc 的 IDF 历时（不加倍数）算梯形超出体积，取最大
            iT = f"J{r}*(MAX(5,G{r})/60)^(-K{r})"; QT = f"({KRAT}*N{r}*{iT}*M{r})"
            ws.cell(row=r, column=cV0, value=f'=IF(OR($A{r}="",NOT({OK})),"",IF({QT}>P{r},({QT}-P{r})*(G{r}*({QT}-P{r})/{QT})*60,0))')
            for j, d in enumerate(DUR):
                c = cV0 + 1 + j
                iD = f"J{r}*(MAX(5,{d})/60)^(-K{r})"; QD = f"({KRAT}*N{r}*{iD}*M{r})"
                ws.cell(row=r, column=c, value=f'=IF(OR($A{r}="",NOT({OK}),{d}<G{r}),"",IF({QD}>P{r},({QD}-P{r})*(({d}-G{r})+G{r}*({QD}-P{r})/{QD})*60,0))')
            cA = cV0 + len(DUR) + 1  # 洪量 max
            ws.cell(row=r, column=cA, value=f'=IF(OR($A{r}="",NOT({OK})),"",MAX(0,MAX({L(cV0)}{r}:{L(cV0+len(DUR))}{r})))')
            ws.cell(row=r, column=cA + 1, value=f"={SED}")
            ws.cell(row=r, column=cA + 2, value=f'=IF(OR($A{r}="",{L(cA)}{r}=""),"",{L(cA)}{r}*{L(cA+1)}{r})')
            ws.cell(row=r, column=cA + 3, value=f'=IF($A{r}="","",{hcol("H", r)})')
            ws.cell(row=r, column=cA + 4, value=f'=IF(OR($A{r}="",{L(cA+2)}{r}=""),"",MAX(0,{L(cA+2)}{r}-N({L(cA+3)}{r})))')
            ws.cell(row=r, column=cA + 5, value=f"={TARGET_H}")
            ws.cell(row=r, column=cA + 6, value=f'=IF(OR($A{r}="",{L(cA+4)}{r}=""),"",{L(cA+4)}{r}/{L(cA+5)}{r})')
            ws.cell(row=r, column=cA + 7, value=f"={SEL_PUMP_CAP}")
            ws.cell(row=r, column=cA + 8, value=f'=IF(OR($A{r}="",{L(cA+6)}{r}=""),"",IF({L(cA+6)}{r}=0,0,ROUNDUP({L(cA+6)}{r}/{L(cA+7)}{r},0)))')
            ws.cell(row=r, column=cA + 9, value=f'=IF(OR($A{r}="",{L(cA+8)}{r}=""),"",IF({L(cA+8)}{r}>{MAXPUMPS},"是：超 "&{MAXPUMPS}&" 台，需罐车/封路",""))')
            ws.cell(row=r, column=cA + 10, value=f'=IF(OR($A{r}="",{L(cA+4)}{r}=""),"",IF({L(cA+4)}{r}=0,0,ROUNDUP({L(cA+4)}{r}/{SEL_TANK_CAP},0)))')
            ws.cell(row=r, column=cA + 11, value=f'=IF(OR($A{r}="",{L(cA+10)}{r}=""),"",IF({L(cA+10)}{r}=0,0,ROUNDUP({L(cA+10)}{r}/{TRIPS},0)))')
            ws.cell(row=r, column=cA + 12, value=f'=IF(OR($A{r}="",{L(cA+8)}{r}=""),"",{L(cA+8)}{r}*{SEL_PUMP_RATE})')
            ws.cell(row=r, column=cA + 13, value=f'=IF(OR($A{r}="",{L(cA+11)}{r}=""),"",{L(cA+11)}{r}*{SEL_TANK_RATE})')
            ws.cell(row=r, column=cA + 14, value=f'=IF(OR($A{r}="",{L(cA+12)}{r}=""),"",{L(cA+12)}{r}+{L(cA+13)}{r})')
            ws.cell(row=r, column=cA + 15, value=f'=IF($A{r}="","",{hcol("W", r)})')
            # 地形：缺口与「灌到溢出」的填洼体积比；缺口超过洼地能蓄的量就会漫到邻区（Hotspots 第 33 列 = 填洼体积）
            ws.cell(row=r, column=cA + 16, value=f'=IF($A{r}="","",{hcol(L(33), r)})')
            ws.cell(row=r, column=cA + 17, value=f'=IF(OR($A{r}="",{L(cA+4)}{r}="",NOT(ISNUMBER({L(cA+16)}{r}))),"",MAX(0,{L(cA+4)}{r}-{L(cA+16)}{r}))')
    ws.conditional_formatting.add(f"E{S0}:E{S1}", CellIsRule(operator="equal", formula=['"设计"'], fill=GREY))
    ws.conditional_formatting.add(f"F{S0}:F{S1}", CellIsRule(operator="equal", formula=['"校核"'], fill=ORANGE))
    for j in range(len(allh)): ws.column_dimensions[L(1 + j)].width = 11
    ws.column_dimensions["C"].width = 40; ws.column_dimensions["D"].width = 14
    ws.freeze_panes = "E6"
    COL = {name: L(1 + allh.index(name)) for name in allh}

    # ---- Fleet_Summary
    ws = wb.create_sheet("Fleet_Summary")
    ws["B2"] = "控制室待命汇总：每档重现期全网需要备多少（WA-726-3 11.9 应急设备、11.10 雨季准备）"; ws["B2"].font = Font(bold=True, size=12)
    ws["B3"] = "「按法定标准」行：每个热点只算它自己的设计重现期（表 3-2）；「校核」行：只算下穿 100 年、机场 50 年。日成本按附录 L 单价。"
    hdr = ["口径", "峰值流量合计\n(m³/s)", "超出流量合计\n(m³/s)", "所需调蓄合计\n(m³)", "现有塘容合计\n(m³)", "缺口合计\n(m³)", "需泵台数", "需罐车\n(辆/班)", "待命日成本\nAED/日", "缺口热点数"]
    for j, h in enumerate(hdr): ws.cell(row=5, column=1 + j, value=h)
    _style_header(ws, 5, len(hdr)); ws.row_dimensions[5].height = 40
    rng = lambda c: f"Scenario_Calc!${c}${S0}:${c}${S1}"
    B = rng("B"); E = rng("E"); F = rng("F")
    cols = [COL["峰值流量 Q\n(m³/s)"], COL["超出流量\n(m³/s)"], COL["所需调蓄\n(m³)"], COL["现有塘容\n(m³)"], COL["缺口\n(m³)"], COL["需泵台数"], COL["需罐车\n(辆/班)"], COL["待命日成本\nAED/日"]]
    for i, rp in enumerate(RPS):
        r = 6 + i; ws.cell(row=r, column=1, value=f"{rp} 年一遇（全部热点）")
        for j, c in enumerate(cols): ws.cell(row=r, column=2 + j, value=f'=SUMIFS({rng(c)},{B},$B{r}&"")' if False else f"=SUMIFS({rng(c)},{B},{rp})")
        ws.cell(row=r, column=10, value=f'=COUNTIFS({B},{rp},{rng(COL["缺口\n(m³)"])},">0")')
    r = 6 + len(RPS); ws.cell(row=r, column=1, value="按法定标准（各热点自身设计重现期）").font = BOLD
    for j, c in enumerate(cols): ws.cell(row=r, column=2 + j, value=f'=SUMIFS({rng(c)},{E},"设计")')
    ws.cell(row=r, column=10, value=f'=COUNTIFS({E},"设计",{rng(COL["缺口\n(m³)"])},">0")')
    r += 1; ws.cell(row=r, column=1, value="校核（下穿 100 年 / 机场 50 年）").font = BOLD
    for j, c in enumerate(cols): ws.cell(row=r, column=2 + j, value=f'=SUMIFS({rng(c)},{F},"校核")')
    ws.cell(row=r, column=10, value=f'=COUNTIFS({F},"校核",{rng(COL["缺口\n(m³)"])},">0")')
    if "区域" in COL and "region" in hotspots.columns:
        RG = rng(COL["区域"])
        for reg in ("ADM", "AAM", "DRM"):
            if not (hotspots["region"] == reg).any():
                continue
            r += 1; ws.cell(row=r, column=1, value=f"按法定标准 · {reg}（有水力输入的热点）")
            for j, c in enumerate(cols): ws.cell(row=r, column=2 + j, value=f'=SUMIFS({rng(c)},{E},"设计",{RG},"{reg}")')
            ws.cell(row=r, column=10, value=f'=COUNTIFS({E},"设计",{RG},"{reg}",{rng(COL["缺口\n(m³)"])},">0")')
    _box(ws, 5, r, 1, len(hdr))
    ws.cell(row=r + 2, column=1, value="读法：某档重现期一行 = 那场雨全网同时需要的移动泵与罐车，是控制室雨季前要能调集的量；「按法定标准」行才是与手册标准对应的义务。泵数受每热点布泵上限约束时，缺口靠罐车与封路疏散。").alignment = WRAP
    ws.merge_cells(start_row=r + 2, start_column=1, end_row=r + 2, end_column=len(hdr)); ws.row_dimensions[r + 2].height = 40
    ws.column_dimensions["A"].width = 34
    for j in range(1, len(hdr)): ws.column_dimensions[L(1 + j)].width = 14

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"D:/世界模型/Stormwater model v3 (DMT WA-726).xlsx")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--copy", default=r"D:/阿布扎比/Stormwater model v3 (DMT WA-726).xlsx")
    ap.add_argument("--source", choices=["client", "system"], default=None, help="client = 客户热点清单（缺省，文件存在时）；system = 本系统汇水区")
    ap.add_argument("--regions", default="ADM,AAM,DRM", help="client 模式下取哪些区，逗号分隔")
    ap.add_argument("--hotspots", default=None, help="客户热点契约文件，缺省取 config [hotspots].file")
    a = ap.parse_args(argv)
    import json, tomllib
    S = json.loads((ROOT / "out" / "summary.json").read_text(encoding="utf-8"))
    AREA_NOTE = S.get("假设", {}).get("汇水面积来源") or "汇水面积按进水口×600 m² 估，C=0.85"
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    hs_path = Path(a.hotspots) if a.hotspots else ROOT / cfg["hotspots"]["file"]
    source = a.source or ("client" if hs_path.exists() else "system")
    if source == "client":
        note = f"客户热点清单（Origen Batch 1，2026-09-09）· 水力输入由会思考的排水系统 {S.get('生成时间')} 按位置匹配（{AREA_NOTE}）"
        hs = hotspots_from_client(hs_path, [x for x in a.regions.split(",") if x], note)
    else:
        note = f"系统生成 · 会思考的排水系统 {S.get('生成时间')} · 规划级（{AREA_NOTE}）"
        hs = hotspots_from_state(a.top, note)
    out = build(Path(a.out), hs, note)
    print(f"热点 {len(hs)} 个 → {out}")
    print(hs[["id", "class", "area_ha", "tc_min", "qcap", "pond_m3"]].head(12).to_string(index=False))
    if "region" in hs.columns:
        print("按区域：", hs["region"].value_counts().to_dict(), "；有水力输入：", int(hs["area_ha"].notna().sum()), "；类别：", hs["class"].value_counts().to_dict())
    if a.copy:
        import shutil; shutil.copy(out, a.copy); print("copy →", a.copy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
