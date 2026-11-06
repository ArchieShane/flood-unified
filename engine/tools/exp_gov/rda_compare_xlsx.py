"""RD-A 数据治理前后对比 Excel（给客户看的量化对比）。

  python tools/exp_gov/rda_compare_xlsx.py [治理后导出目录，默认 07_export_round11] [输出 xlsx]
输入（都只读）：
  08_area/RD-A_stats.json（area_stats.py）、09_maps/rda_maps.gpkg（map_rda_prep.py：原始管的连通、管底 − 地面）、
  <导出目录>/Stormwater_AD_reporting.gdb、governance_records.gdb、out/exp_gov/ledger/全市遗留问题清单_第N轮.xlsx、
  out/exp_gov/verify/compare.json（一二维验证）
工作表：
  1 总览对比：每类问题 治理前 / 治理后 / 变化 / 怎么解决 / 剩余原因和谁处理
  2 一二维验证：三场雨 × 各模型
  3 待处理（逐条）：RD-A 范围内还要客户核对或补的记录，带坐标
  4 治理动作（逐条）：RD-A 范围内做过的改动
  5 说明：口径、数据版本、来源
"""
import json
import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree

ROOT = "D:/阿布扎比/排水智能体"
W = "D:/swbrain_work/exp_gov"
EXP = sys.argv[1] if len(sys.argv) > 1 else "07_export_round11"
ROUND = int(EXP.rsplit("round", 1)[-1]) if "round" in EXP else 11
OUT = sys.argv[2] if len(sys.argv) > 2 else f"{W}/09_maps/RD-A_数据治理前后对比.xlsx"
TOL = 0.0021

S = json.load(open(f"{W}/08_area/RD-A_stats.json", encoding="utf-8"))
dom = gpd.read_file(f"{ROOT}/out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
D = dom.geometry.iloc[0]
M = f"{W}/09_maps/rda_maps.gpkg"
raw = gpd.read_file(M, layer="raw_pipes")                      # 范围内原始管：to_outfall、up_minus_dtm
dang_raw = gpd.read_file(M, layer="raw_dangling")

# 治理后：范围内的管、悬空端点
rg = f"{W}/{EXP}/Stormwater_AD_reporting.gdb"
bb = D.buffer(50).bounds
G = gpd.read_file(rg, layer="StormwaterLine", bbox=bb, columns=["assetid", "smasubnetworkname"])
G = G[G.intersects(D)]
pts = []
for lay in ("StormwaterDevice", "StormwaterJunction"):
    p = gpd.read_file(rg, layer=lay, bbox=bb, columns=[])
    pts.append(shapely.get_coordinates(p.geometry.values))
P = np.vstack(pts)
Ga = gpd.read_file(rg, layer="StormwaterLine", bbox=D.buffer(300).bounds, columns=[])
E = np.array([c for g in Ga.geometry.values for c in shapely.get_coordinates(g)[[0, -1]]])
te, tp = cKDTree(E), cKDTree(P)
Eg = np.array([c for g in G.geometry.values for c in shapely.get_coordinates(g)[[0, -1]]])
dang_gov = sum(1 for e in Eg if tp.query(e)[0] > TOL and len(te.query_ball_point(e, TOL)) <= 1)
traced = int((G.smasubnetworkname.notna() & (G.smasubnetworkname != "Unknown")).sum())

r = S["raw_dmt"]
gv = S["governed"]
iss = S["records"]["issues"]
datum_odd = sum(v for k, v in iss.items() if k.startswith("DATUM-ODD"))
sent = sum(v for k, v in iss.items() if k.startswith("E-SENT"))
rev = sum(v for k, v in iss.items() if k.startswith("REV-SLOPE"))
date_ph = sum(r[k]["installdate_le_1900"] for k in r)
zero_raw = sum(v.get("0", 0) for k in r for v in r[k]["elev"].values())
excl = sum(S["records"]["excluded"].values())
conf = sum(S["records"]["conflicts_aecom"].values())
u = raw.up_minus_dtm.dropna()
p100 = round(100 * ((u > 95) & (u < 105)).mean(), 1)
mats = r["PIPELINE"]["material"]
attr = gv["attr_rule_errors_300m"]["by_rule"]

C = {x["storm"] + x["model"]: x for x in json.load(open(f"{ROOT}/out/exp_gov/verify/compare.json", encoding="utf-8"))}
def mt(storm, m):
    with open(f"{ROOT}/out/exp_gov/verify/metrics_{storm}_{m}.json", encoding="utf-8") as f:
        return json.load(f)
def drain(storm, m):
    d = mt(storm, m)
    return round(100 * (d["peak_surface_m3"] - d["end_surface_m3"]) / d["peak_surface_m3"])

rows = [
    # 类别, 指标, 治理前, 治理后, 怎么解决, 剩余 / 谁处理
    ("管网连通", "管道总数（RD-A 范围内）", r["PIPELINE"]["n"], gv["StormwaterLine"]["n"],
     f"排除 {excl} 根废弃管；补入 AECOM 的 86 根", "—"),
    ("管网连通", "能连到出水口的管（治理前按几何判断；治理后按 Esri 子网追踪）",
     f"{int(raw.to_outfall.sum()):,}（{100 * raw.to_outfall.mean():.1f} %）", f"{traced:,}（{100 * traced / len(G):.1f} %）",
     "废弃管不装；按 AECOM 补缺；吸附管端；检查井拆成结构 + 流道", f"{len(G) - traced} 根未挂上子网（零星连接管），列清单请客户核对"),
    ("管网连通", "连不到出水口的管", int((raw.to_outfall == 0).sum()), len(G) - traced, "同上", "同上"),
    ("管网连通", "悬空管端（Esri 容差内不碰任何管和点）", len(dang_raw), dang_gov,
     "0.5 m 内吸附到井或管端；几毫米的缝接上", "剩下的离可接对象较远，列清单"),
    ("管网连通", "Esri 网络拓扑错误：数据本身的问题（范围外扩 300 m，第一轮装入后校验）", 5,
     0, "泵站段压力管直接接重力管 3 处 → 加连接点；出水口和拍门同点 2 处 → 拍门挪到管上 0.5 m", "无"),
    ("管网连通", "Esri 网络拓扑错误：我方融合 AECOM 时的实现错误（第一轮）", 404,
     0, "第一轮融合 AECOM 节点时只对照了 DMT 检查井、漏了雨水井，多建 72 口井压在雨水井上：点重叠 72 + 管不知道接哪个点 332；第二轮改正", "无（不是原始数据的问题，如实列出）"),
    ("管网连通", "属性规则错误（范围外扩 300 m）", "—", sum(attr.values()),
     "—", "1 处：泵站湿井的连接点规则不适用，已核实，不改数据"),
    ("高程", "高程是「实际 + 100 m」（管底 − 地面在 95–105 m 的管）", f"{p100} %", "0 %",
     "逐条和地形比对，统一换到地形基准", "—"),
    ("高程", "换算后仍和地形对不上的高程", "（换算前无法判断）", datum_odd, "保留换算后的值，登记", "客户核对这些高程"),
    ("高程", "哨兵值（−999 等）", sent, f"0（{sent} 条已置空，待补）", "置空，建模时按缺省规则补", "客户补正确高程"),
    ("高程", "用 0 冒充缺值的高程", zero_raw, f"0（{zero_raw} 条已置空）", "置空", "封堵端井盖本就无高程，其余请客户补"),
    ("记录", "状态「在用」但子类型「废弃」的管混在在用管里", excl, 0, "按状态规则不装，进排除清单", "这批管 AECOM 记为在用，需客户确认以谁为准"),
    ("记录", "倒坡管（起点管底低于终点）", rev, rev, "照原样保留，登记", "客户核对测量数据"),
    ("记录", "安装日期是占位值（1900 年以前）", date_ph, "@DATEPH", "照原样保留，登记（治理后数字少的部分是被排除的废弃管）", "客户补安装日期"),
    ("记录", "管材写法种类", len(mats), "统一编码", "按管材对照表统一", "—"),
    ("设施", "泵站", "1 个点：流量 0、泵数 0，没有湿井、泵曲线和压力管", "湿井 1 座 + 泵 3 台（带泵曲线）+ 压力管 9 段",
     "从 AECOM 模型补入", "客户确认 AECOM 的泵参数可用"),
    ("设施", "出水口", 1, 3, "AECOM 补 2 个", "—"),
    ("设施", "检查井的数据模型", f"{r['MANHOLECHAMBER']['n']} 口，每口只有一个点",
     f"{gv['StructureJunction']['n']} 口，每口拆成「井结构 + 井内流道」并建包含关系", "按 Esri Expanded 模型拆分；AECOM 补 25 口", "—"),
    ("与 AECOM 对账", "两份数据数值不一致（管底、井盖、管径等）", "8,350 处（中位差 100 m，基准不同）", f"{conf} 处（中位差 0.05–0.6 m）",
     "统一高程基准后逐条比对，以 DMT 为准，差异登记", "客户逐类确认以哪份为准"),
]
for storm, lab in (("D61", "61.34 mm / 1 h"), ("E26", "72 mm / 1 h"), ("E24", "82.2 mm / 1 h")):
    R_, G_, A_ = C[storm + "R"], C[storm + "G"], C[storm + "A"]
    rows += [
        (f"一二维验证（{lab}）", "出水口和泵站排出的水量（m³）", f"{R_['outfall_m3']:,}", f"{G_['outfall_m3']:,}",
         "高程校准 + 接通 + 泵站接入", f"AECOM 原模型 {A_['outfall_m3']:,}"),
        (f"一二维验证（{lab}）", "雨停 4 小时后排掉的积水比例", f"{drain(storm, 'R')} %", f"{drain(storm, 'G')} %", "同上", f"AECOM 原模型 {drain(storm, 'A')} %"),
        (f"一二维验证（{lab}）", "0.30 m 以上深积水面积（ha）", R_["ge030_ha"], G_["ge030_ha"], "同上", f"AECOM 原模型 {A_['ge030_ha']}"),
        (f"一二维验证（{lab}）", "模拟结束时地面剩余积水（m³）", f"{R_['surface_end_m3']:,}", f"{G_['surface_end_m3']:,}", "同上", f"AECOM 原模型 {A_['surface_end_m3']:,}"),
    ]
    M_ = C.get(storm + "M")                                  # 对照 ⑤：治理后管网 + AECOM 产流（2026-10-26）
    if M_:
        rows.append((f"一二维验证（{lab}）", "0.15 m 以上积水面积（ha）：治理后管网换上 AECOM 产流后", R_["ge015_ha"], M_["ge015_ha"],
                     "对照 ⑤：只换产流、管网不动，治理后管网和 AECOM 模型一致", f"AECOM 原模型 {A_['ge015_ha']}；治理后（我们的产流）{G_['ge015_ha']}"))
T1 = pd.DataFrame(rows, columns=["类别", "指标", "治理前", "治理后", "怎么解决", "剩余 / 需要谁做什么"])

# 2 一二维验证（全部模型）
V = pd.DataFrame([{"雨": x["storm_label"], "模型": x["model_label"], "进管水量 m³": x["runoff_m3"], "出口排出 m³": x["outfall_m3"],
                   "地面回流进管 m³": x["back_to_1d_m3"], "结束时地面存水 m³": x["surface_end_m3"], "≥0.15 m 面积 ha": x["ge015_ha"],
                   "≥0.30 m 面积 ha": x["ge030_ha"], "误报面积 ha": x["false_alarm_ha"], "热点命中": f"{x['hits']}/{x['n_hot']}"}
                  for x in json.load(open(f"{ROOT}/out/exp_gov/verify/compare.json", encoding="utf-8"))])

# 3 / 4 逐条（全市清单里 RD-A 范围内的）
L = f"{ROOT}/out/exp_gov/ledger/全市遗留问题清单_第{ROUND}轮.xlsx"
def clip(df):
    df = df.dropna(subset=["x", "y"])
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.x, df.y), crs=32640)
    return pd.DataFrame(g[g.within(D)].drop(columns="geometry"))
T3a = pd.read_excel(L, sheet_name="待处理（逐条）")
T3 = pd.concat([clip(T3a[T3a["问题码"] != "CONFLICT"]), T3a[T3a["问题码"] == "CONFLICT"]], ignore_index=True)   # 与 AECOM 的差异都属 RD-A，不按边界裁
ar = gv["attr_rule_errors_300m"]["items"]
T3 = pd.concat([T3, pd.DataFrame([{"类型": "属性规则错误：泵站湿井的连接点不在检查井结构里", "问题码": "ATTR", "资产编号": "SW_ABT_W33_PSS_69",
                                    "说明": a[1], "我们的处理": "规则不适用（湿井不是检查井），已核实，不改数据", "严重程度": "不影响模型",
                                    "需要谁做什么": "—", "状态": "已核实"} for a in ar])], ignore_index=True)
T4 = clip(pd.read_excel(L, sheet_name="治理动作记录（逐条）"))
T1.loc[T1["治理后"] == "@DATEPH", "治理后"] = int((T3["问题码"] == "DATE-PH").sum())
T3s = T3.groupby(["状态", "类型"]).size().rename("条数").reset_index().sort_values("条数", ascending=False)

note = pd.DataFrame({"项目": ["范围", "治理后数据版本", "治理前数据", "一二维验证", "「能连到出水口」的口径", "逐条清单", "生成程序"],
                     "说明": [f"RD-A：AECOM 子汇水区外包面，{S['domain_km2']} km²",
                              f"全市治理第 {ROUND} 轮导出（{EXP}）；RD-A 自第八轮起数据不变",
                              "DMT 全市雨水资产库原样（20261019/Stormwater）中 RD-A 范围内的部分",
                              "SWMM 5.2 + ANUGA 一二维双向耦合；雨取自 AECOM 雨量表（1 h）；治理前 = DMT 原样，治理后 = DMT 治理 + 融合 AECOM；另有两个对照：只治理 DMT、治理后管网 + AECOM 产流（见表 2）",
                              "治理前：原始数据要先做字段、编码和数据结构的转换才能装进 Esri 模板（例如检查井要拆成结构 + 流道、管径毫米要换值域），这里按管端、点在 2.1 mm 内重合判断几何上能否连到出水口；治理后：Esri 子网追踪",
                              "从全市遗留问题清单中取 RD-A 范围内的记录；每条带坐标，可在地图上定位",
                              "tools/exp_gov/rda_compare_xlsx.py"]})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    T1.to_excel(w, sheet_name="1 总览对比", index=False)
    V.to_excel(w, sheet_name="2 一二维验证", index=False)
    T3s.to_excel(w, sheet_name="3 待处理汇总", index=False)
    T3.to_excel(w, sheet_name="3 待处理（逐条）", index=False)
    T4.to_excel(w, sheet_name="4 治理动作（逐条）", index=False)
    note.to_excel(w, sheet_name="5 说明", index=False)

# 同一批逐条记录另存地图图层（ArcGIS / QGIS 直接加载，按「类型」分色）
gp = OUT.replace(".xlsx", "_逐条问题.gpkg")
if os.path.exists(gp):
    os.remove(gp)
for name, df in (("待处理", T3), ("治理动作", T4)):
    d = df.dropna(subset=["x", "y"])
    gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.x, d.y), crs=32640).to_file(gp, layer=name, driver="GPKG")
print("写出", gp, "（待处理无坐标的", int(T3.x.isna().sum()), "条只在 Excel 里）")

# 版式：表头加粗、底色，列宽，冻结首行；总览里治理前 / 治理后两列上色
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
wb = load_workbook(OUT)
for ws in wb.worksheets:
    ws.freeze_panes = "A2"
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for col in ws.columns:
        n = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
        ws.column_dimensions[col[0].column_letter].width = min(max(10, n * 1.6), 60)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
ws = wb["1 总览对比"]
for row in ws.iter_rows(min_row=2):
    row[2].fill = PatternFill("solid", fgColor="FCE4D6")
    row[3].fill = PatternFill("solid", fgColor="E2EFDA")
wb.save(OUT)
print("写出", OUT)
print(T1.to_string(index=False))
print("待处理（逐条）", len(T3), "；治理动作（逐条）", len(T4))
