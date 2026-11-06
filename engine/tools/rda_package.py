# -*- coding: utf-8 -*-
"""RD-A 优化成果交付包（2026-10-24，用户：「先帮我输出目前最新的优化结果：设施情况、内涝降低多少（量化）、降雨场景背景；表格、文档、图片」）。

内容（当前方案 = OPTCS2：L2+suggested v3 + 7 座地面泵站）：
  RD-A_L2S成果_设施台账与效果.xlsx   降雨场景、内涝效果（全域 / 热点）、L2+suggested 设施台账（含造价、可建性、待办）、地面取水口、现状与计划设施、造价汇总、字段说明
  图/                              设施分布（L1、L2、L2+planned、L2+suggested 各一张 + 四态对比）、最大水深对比（已算的雨）、效果对比图
  RD-A_L2S成果说明.md / .docx      总体说明（由 tools/md_to_docx_cn.py 转 Word）
数据：out/l2opt/RDA_96C/（候选、合规、剪枝、地面泵站、eval2d_<雨>_ex.json）、console/data/flood2d_rda_<雨>/rda_stats.json（设施坐标、雨型）、c2d/*_2d.npz
判据统计按剔除 ADM-80 岸边 DTM 填 0 区的口径（各态同一口径）。当前方案还没算完的雨，表里写「待补」。
用法：python tools/rda_package.py [--out out/交付/RDA_L2S_v3_<日期>]
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import subprocess
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
OPT = ROOT / "out" / "l2opt" / "RDA_96C"
STORMS = ["S33D1", "S33D2", "S33D24", "S61D1", "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]
HS = ["ADM-75", "ADM-76", "ADM-80", "ADM-161", "ADM-376", "ADM-377", "ADM-378", "ADM-379", "ADM-380", "ADM-382", "ADM-383"]
CUR = "OPTCS2"
ST = ["L1", "L2", "L2P", CUR]
LAB = {"L1": "L1 现状", "L2": "L2（+预置移动泵）", "L2P": "L2+planned（+客户计划的泵）", CUR: "L2+suggested（优化方案）"}
SHORT = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", CUR: "L2+suggested"}
COL = {"L1": "#334155", "L2": "#0e7490", "L2P": "#b45309", CUR: "#15803d"}
KIND = {"tank_gravity": "重力进水地下调蓄池", "tank_pumped": "井边泵站 + 地下调蓄池", "ps_cluster": "泵站 → 新建排海口", "surface_ps": "地面泵站 → 新建排海口"}
BASIS = {"tank_gravity": "池体 350 USD/m³、重力管、排空泵：占位单价",
         "tank_pumped": "压力管按 WA-726 第三卷 p349 单价；池体、泵站、湿井：占位",
         "ps_cluster": "压力管、排海口按 WA-726 第三卷 p349 单价；泵站、湿井、重力连接管：占位",
         "surface_ps": "压力管、排海口按 WA-726 第三卷 p349 单价；泵站、湿井、取水井：占位"}


L3F = ROOT / "out/rda/l3_handover.json"      # tools/rda_l3.py 的输出
TLF = ROOT / "out/rda/threelines.json"       # tools/rda_threelines.py 的输出（L3 调度求解器实际派单）
TL_FIELDS = [("Island A13 / ADM 全部驻地", "L3 资源：仅 Island 片区驻地 A13（现状，各片区各管各的）/ ADM 五个驻地集中调度（上限，未扣其他片区同时要用的资源）"),
             ("L3 按时 / 超时 / 未派", "第三道防线调度求解器（swbrain.dispatch2：滚动 MILP、路网行程、退水重演，目标「平衡」）派单后，在「雨停 + 处置时限」内退干 / 派了但超时 / 没派上"),
             ("超出 L3 → 回流 L2", "L3 单点能力（3 台 12 寸泵 / 6 辆罐车）赶不上时限的点，交回 L2 下一轮优化"),
             ("费用 AED", "调度求解器的台班、动员、人工、燃油、车辆费用（模拟费率）")]
L3_FIELDS = [("处置时限", "雨停后多久要退干：300 m 内重点单位（医院 120、隧道 / 地铁 / 急救 180、学校 240、其他 360 min）与 200 m 内客户热点重要度（Very High 120、High 240、Medium 600 min）取小"),
             ("自然退干", "不派泵时，二维结果里该片积水量降到 50 m³ 以下的时刻；二维视界之后按末帧水量不再退（偏保守）"),
             ("L3 手段", "1.5 km 内有出水口 / 泵站 / 渗井终点 → 12 英寸移动泵（700 m³/h，预案 A），每点 ≤3 台；否则罐车（17 m³）≤6 辆"),
             ("到位", "积水量首超 50 m³ 后 120 min（预案首次到场目标 trt_min）"),
             ("判定", "自然退干达标 / L3 可承接 / L3 可承接（关键点，建议 L2：隧道 / 医院 / 急救或 Very High 且深 ≥0.30 m）/ 超出 L3 → 应由 L2 解决"),
             ("台班费", "仅租用台班（12 英寸泵 90 AED/h、罐车 250 AED/h），未含购置、待命、人员")]


def sname(s):
    mm, h = s[1:].split("D"); return f"{mm} mm / {h} h"


def npass(E, st):
    return sum(all(v["pass"].values()) for v in E[st]["hotspots"].values()) if st in E else None


def pct(a, b):
    return None if a is None or b in (None, 0) else round((b - a) / b * 100, 1)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out" / "交付" / f"RDA_L2S_v3_{datetime.date.today():%Y%m%d}"))
    a = ap.parse_args(argv)
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    from matplotlib.patches import Polygon as MPoly
    from pyproj import Transformer
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
    out = pathlib.Path(a.out); fig_dir = out / "图"; fig_dir.mkdir(parents=True, exist_ok=True)
    T = Transformer.from_crs(32640, 4326, always_xy=True)

    CAND = {c["id"]: c for c in json.loads((OPT / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    COMP = json.loads((OPT / "compliance.json").read_text(encoding="utf-8"))
    V = json.loads((OPT / "variant_S96D1_OPTC.json").read_text(encoding="utf-8"))
    SU = json.loads((OPT / "sumps_OPTCS2.json").read_text(encoding="utf-8"))
    PR = json.loads((OPT / "prune.json").read_text(encoding="utf-8"))
    EV = {s: (json.loads((OPT / f"eval2d_{s}_ex.json").read_text(encoding="utf-8"))["states"] if (OPT / f"eval2d_{s}_ex.json").exists() else {}) for s in STORMS}
    RS = {s: json.loads((ROOT / "console" / "data" / f"flood2d_rda_{s.lower()}" / "rda_stats.json").read_text(encoding="utf-8")) for s in STORMS}
    done = [s for s in STORMS if CUR in EV[s]]
    print(f"当前方案已有二维结果的雨：{done}")

    # ================= 表 1 降雨场景
    rain = []
    for s in STORMS:
        h = RS[s]["hyeto"]
        rain.append({"场景": sname(s), "雨量 mm": h["rain_mm"], "历时 h": h["dur_h"], "5 分钟峰值雨强 mm/h": h["peak_mmph"], "峰值时刻 h": h["peak_t_h"],
                     "净雨 mm（面积加权）": h["excess_mm"], "模拟时长 h（雨后再算 10 h）": h["sim_h"], "雨型来源": h["source"],
                     "L1 / L2 / L2+planned": "已算", "L2+suggested": "已算" if s in done else "计算中（待补）"})
    # ================= 表 2 全域效果
    dom = []
    for s in STORMS:
        E = EV[s]
        for st in ST:
            if st not in E:
                dom.append({"场景": sname(s), "状态": LAB[st], "全域 ≥0.30 m 积水面积 ha": None, "峰值积水量 m³": None, "模拟末地面留存 m³": None,
                            "四项全过的热点（/11）": None, "比 L1 ≥0.30 m 面积减少 %": None, "比 L2+planned 减少 %": None, "备注": "计算中（待补）"}); continue
            d = E[st]["domain"]
            dom.append({"场景": sname(s), "状态": LAB[st], "全域 ≥0.30 m 积水面积 ha": d["ge030_ha"], "峰值积水量 m³": d["peak_vol_m3"], "模拟末地面留存 m³": d["end_vol_m3"],
                        "四项全过的热点（/11）": npass(E, st),
                        "比 L1 ≥0.30 m 面积减少 %": pct(d["ge030_ha"], E["L1"]["domain"]["ge030_ha"]) if st != "L1" else None,
                        "比 L2+planned 减少 %": pct(d["ge030_ha"], E["L2P"]["domain"]["ge030_ha"]) if st not in ("L1", "L2", "L2P") else None,
                        "备注": ""})
    # ================= 表 3 热点效果
    hsr = []
    for s in STORMS:
        E = EV[s]
        for h in HS:
            for st in ST:
                if st not in E:
                    continue
                r = E[st]["hotspots"][h]; bad = "".join(k for k, v in r["pass"].items() if not v)
                hsr.append({"场景": sname(s), "热点": h, "状态": SHORT[st], "200 m 内 ≥0.30 m 面积 ha": r["ge030_ha"], "≥0.15 m 面积×时间 ha·h": r["ge015_ha_h"],
                            "最大水深 m": r["max_m"], "雨停后退到 0.10 m 用时 h": "到模拟结束未退" if r["recede_h_after_rain"] is None else round(r["recede_h_after_rain"], 2),
                            "四项全过": "是" if not bad else "否", "未过的判据": bad or ""})
    # ================= 表 4 L2+suggested 设施台账
    led = []
    for m in sorted(V["measures"], key=lambda m: (m["hotspot"], m["kind"], m["id"])):
        c = CAND[m["id"]]; k = m["kind"]
        lo, la = T.transform(c.get("px", c.get("ps_x")), c.get("py", c.get("ps_y")))
        items = COMP.get(m["id"], [])
        olo = ola = None
        if c.get("of_xy"):
            olo, ola = T.transform(*c["of_xy"])
        led.append({"编号": m["id"], "类型": KIND[k], "服务热点": c["hotspot"], "经度": round(lo, 6), "纬度": round(la, 6),
                    "截流井": "、".join(c.get("wells") or [c.get("well")]), "截流井溢流量 m³（96 mm 两场取大）": c.get("overflow_m3"),
                    "池容 m³": c.get("pond_vol_m3"), "有效水深 m": c.get("pond_depth_m"), "池底高程 m": c.get("z_floor"), "开挖深度 m": c.get("excavation_m"),
                    "泵台数": c.get("n_pumps"), "设计流量 m³/s": c.get("q_m3s"), "单泵流量 m³/s": c.get("pump_each_m3s"), "泵站场地 m²": c.get("ps_site_m2"),
                    "管长 m": c.get("pipe_m"), "管径 m": c.get("main_d_m") or c.get("pipe_d_m"), "并联根数": c.get("n_mains"), "峰值流速 m/s": c.get("v_ms"),
                    "排口岸点": c.get("outfall_site"), "排口经度": round(olo, 6) if olo else None, "排口纬度": round(ola, 6) if ola else None,
                    "用地（Makani 占比）": "、".join(f"{kk} {vv:.0%}" for kk, vv in (c.get("land") or {}).items() if vv) or None,
                    "占位造价 USD": round(m["cost_usd"]), "造价依据": BASIS[k], "可建性": c.get("status"),
                    "需审批 / 许可": "；".join(i["zh"] for i in items if i["result"] == "review"),
                    "缺数据判不了": "；".join(i["zh"] for i in items if i["result"] == "unknown"),
                    "合规检查项数": len(items)})
    for x in SU["stations"]:
        lo, la = T.transform(x["ps_x"], x["ps_y"]); olo, ola = T.transform(*x["of_xy"]); items = SU["compliance"].get(x["id"], [])
        led.append({"编号": x["id"], "类型": KIND["surface_ps"], "服务热点": x["hotspot"], "经度": round(lo, 6), "纬度": round(la, 6),
                    "截流井": f"地面取水口 {len(x['intakes'])} 个（见「地面取水口」表）", "泵台数": x["n_pumps"], "设计流量 m³/s": x["q_m3s"], "单泵流量 m³/s": x["pump_each_m3s"],
                    "泵站场地 m²": x["ps_site_m2"], "管长 m": x["pipe_m"], "管径 m": x["main_d_m"], "并联根数": x["n_mains"], "峰值流速 m/s": x["v_ms"],
                    "排口岸点": x["outfall_site"], "排口经度": round(olo, 6), "排口纬度": round(ola, 6),
                    "占位造价 USD": x["cost_usd"], "造价依据": BASIS["surface_ps"], "可建性": x["status"],
                    "需审批 / 许可": "；".join(i["zh"] for i in items if i["result"] == "review"),
                    "缺数据判不了": "；".join(i["zh"] for i in items if i["result"] == "unknown"), "合规检查项数": len(items)})
    intake = []
    for x in SU["stations"]:
        for j, it in enumerate(x["intakes"]):
            lo, la = T.transform(it["x"], it["y"])
            intake.append({"取水口": f"{x['id']}_{j}", "所属地面泵站": x["id"], "服务热点": x["hotspot"], "类别": it["class"], "经度": round(lo, 6), "纬度": round(la, 6),
                           "坑底高程 m": it["z"], "取水流量 m³/s": it["q_m3s"], "到泵站重力管 m": it["link_m"], "说明": it["why"] or it.get("tune", "")})
    # 现状与计划设施
    F = RS["S96D1"]["facilities"]
    exist = []
    for st, kname in (("L1", "现状泵站"), ("L2", "预置移动泵（热点表 Plan A）"), ("L2P", "客户计划的泵（情景 51159）")):
        for f in F.get(st, []):
            exist.append({"状态": LAB[st], "编号": f["id"], "类型": kname, "流量 L/s": f.get("q_lps"), "纬度": f["ll"][0], "经度": f["ll"][1],
                          "排到（纬度, 经度）": f"{f['to_ll'][0]}, {f['to_ll'][1]}" if f.get("to_ll") else "", "说明": f["p"].get("why", ""), "造价": "不计（现状或客户计划）"})
    # 造价汇总
    byk = collections.defaultdict(lambda: [0, 0.0])
    for r in led:
        k = next(kk for kk, vv in KIND.items() if vv == r["类型"]); byk[k][0] += 1; byk[k][1] += r["占位造价 USD"]
    tot = sum(v[1] for v in byk.values())
    cost = [{"设施类型": KIND[k], "数量": byk[k][0], "占位造价 百万美元": round(byk[k][1] / 1e6, 2), "占比 %": round(byk[k][1] / tot * 100, 1), "造价依据": BASIS[k]}
            for k in ("tank_gravity", "tank_pumped", "ps_cluster", "surface_ps") if k in byk]
    cost.append({"设施类型": "合计", "数量": sum(v[0] for v in byk.values()), "占位造价 百万美元": round(tot / 1e6, 2), "占比 %": 100.0,
                 "造价依据": "占位造价只用于方案比较，不是预算；未含运维与更换（NPV）"})
    n_of = len({r["排口岸点"] for r in led if r.get("排口岸点")})
    fields = [{"表": "内涝效果_全域", "字段": "全域 ≥0.30 m 积水面积", "含义": "计算域内全程最大水深 ≥0.30 m 的面积（不含塘 / 地下池占地，剔除 ADM-80 岸边 DTM 填 0 区）"},
              {"表": "内涝效果_全域", "字段": "峰值积水量", "含义": "地面积水体积的最大值"},
              {"表": "内涝效果_全域", "字段": "四项全过的热点", "含义": "11 个客户热点中，判据 A–D 全部通过的个数"},
              {"表": "内涝效果_热点", "字段": "判据 A", "含义": "热点 200 m 内 ≥0.30 m 积水面积 = 0（容差 0.005 ha）"},
              {"表": "内涝效果_热点", "字段": "判据 B", "含义": "≥0.15 m 积水的面积×时间 ≤ L2+planned 同值的 10 %"},
              {"表": "内涝效果_热点", "字段": "判据 C", "含义": "雨停后 6 h 内热点 200 m 内最大水深退到 0.10 m 以下"},
              {"表": "内涝效果_热点", "字段": "判据 D", "含义": "全域比 L2+planned 变深 ≥5 cm 的面积 ≤ 0.05 ha"},
              {"表": "设施台账", "字段": "可建性", "含义": "三层约束（WA-726 手册条文、领域知识、Makani 空间数据）逐条判定：有条件可建 = 无不通过项，但有需审批 / 许可或缺数据的项"},
              {"表": "设施台账", "字段": "占位造价", "含义": "压力管、排海口按 WA-726 第三卷附录 L 单价；池体、泵站、湿井、重力管、取水井为占位单价"}]
    L3 = json.loads(L3F.read_text(encoding="utf-8")) if L3F.exists() else None
    l3s, l3r = [], []
    if L3:
        for r in L3["summary"]:
            if r.get("n"):
                l3s.append({"场景": sname(r["storm"]), "状态": SHORT[r["state"]], "残余积水点": r["n"], "峰值积水量合计 m³": r["vol_peak_m3"], "自然退干达标": r["n_nat_ok"],
                            "L3 可承接": r["n_l3"], "其中关键点（建议 L2）": r["n_l3_key"], "超出 L3 → 需 L2": r["n_beyond"], "超出 L3 的积水量 m³": r["vol_beyond_m3"],
                            "需 12 英寸泵 台": r["pumps_12in"], "需罐车 辆": r["tankers"], "泵 / 车台时": r["unit_hours"], "抽排量 m³": r["pumped_m3"], "台班费 AED（仅租用）": r["cost_aed"]})
        for r in L3["rows"]:
            l3r.append({"场景": sname(r["storm"]), "状态": SHORT[r["state"]], "积水点": r["id"], "经度": r["lon"], "纬度": r["lat"], "200 m 内客户热点": r["hotspots_200m"],
                        "热点重要度": r["criticality"], "300 m 内重点单位": r["top_kind"], "最大水深 m": r["max_depth_m"], "面积 m²": r["area_m2"], "峰值积水量 m³": r["vol_peak_m3"],
                        "峰值时刻 h": r["t_peak_h"], "处置时限 min（雨停起）": r["target_min"], "退干期限 h（从降雨开始）": r["deadline_h"], "自然退干 h": r["t_dry_nat_h"] if r["t_dry_nat_h"] is not None else "视界内未退干",
                        "期限时剩余 m³": r["residual_at_deadline_m3"], "最近排放去处 m": r["discharge_m"], "L3 手段": r["l3_means"], "台 / 辆": r["l3_units"], "到位 h": r["arrive_h"],
                        "L3 后退干 h": r["t_dry_l3_h"], "抽排量 m³": r["pumped_m3"], "台时": r["unit_hours"], "台班费 AED": r["cost_aed"], "判定": r["verdict"]})
        fields += [{"表": "L3承接_汇总 / 清单", "字段": k, "含义": v} for k, v in L3_FIELDS]
    # 全寿命成本（tools/rda_npv.py → out/rda/npv.json，DEC-10 只进报告）
    NPVJ = json.loads((ROOT / "out/rda/npv.json").read_text(encoding="utf-8")) if (ROOT / "out/rda/npv.json").exists() else None
    npvr, npvp = [], []
    if NPVJ:
        D10 = NPVJ["basis"]["dec10"]; r0 = str(D10["discount_rate"]); rs = [str(x) for x in D10["discount_sensitivity"]]
        for k, v in NPVJ["schemes"].items():
            n0 = v["npv"][r0]
            npvr.append({"方案": v["label"], "建设费 M USD": round(n0["capex"] / 1e6, 2), "每年运维 M USD": round(n0["om_per_year"] / 1e6, 2), "每年电费 USD": n0["energy_per_year"],
                         "运维现值 M USD": round(n0["pv_om"] / 1e6, 2), "更换现值 M USD": round(n0["pv_replace"] / 1e6, 2),
                         **{f"NPV {float(r) * 100:g}% M USD": round(v["npv"][r]["npv"] / 1e6, 1) for r in [rs[0], r0, rs[1]]}})
        cur = NPVJ["schemes"].get("OPTCS2") or next(iter(NPVJ["schemes"].values())); ptot = sum(cur["parts"].values())
        npvp = [{"造价分项（当前方案）": NPVJ["basis"]["parts"][k], "M USD": round(v / 1e6, 2), "占比 %": round(v / ptot * 100, 1)} for k, v in cur["parts"].items()]
    TLJ = json.loads(TLF.read_text(encoding="utf-8")) if TLF.exists() else []
    tlr = []
    for r in TLJ:
        if r["state"] not in SHORT or r["storm"] not in STORMS:          # 本说明只讲当前方案（96 mm 档）的 9 场雨；61 mm 推荐方案、AECOM 雨型另有交付（2026-10-27）
            continue
        lv = r.get("levels") or {}
        row = {"场景": sname(r["storm"]), "防线": SHORT[r["state"]], "残余积水点": r.get("n", 0), "关键": lv.get("关键", 0), "高": lv.get("高", 0), "中": lv.get("中", 0),
               "无暴露（只监测）": r.get("n_noexpo", 0), "超出 L3 → 回流 L2": r.get("n_beyond", 0)}
        for p, pz in (("island", "Island A13"), ("adm", "ADM 全部驻地")):
            c = r.get(f"{p}_counts") or {}
            row.update({f"{pz}：自然退干": c.get("自然退干", 0), f"{pz}：L3 按时": c.get("L3 按时退干", 0), f"{pz}：L3 超时": c.get("L3 已派、超时", 0), f"{pz}：L3 未派": c.get("L3 未派", 0),
                        f"{pz}：泵 台": r.get(f"{p}_pumps", 0), f"{pz}：罐车 辆": r.get(f"{p}_tankers", 0), f"{pz}：费用 AED": r.get(f"{p}_cost_aed", 0)})
        tlr.append(row)
    if tlr:
        fields += [{"表": "三道防线联动", "字段": k, "含义": v} for k, v in TL_FIELDS]
    xlsx = out / "RD-A_L2S成果_设施台账与效果.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as W:
        for name, rows in (("降雨场景", rain), ("内涝效果_全域", dom), ("内涝效果_热点", hsr), ("设施台账_L2S", led), ("地面取水口", intake),
                           ("现状与计划设施", exist), ("造价汇总", cost)) + ((("全寿命成本_NPV", npvr + [{}] + npvp),) if npvr else ()) + ((("L3承接_汇总", l3s), ("L3承接清单", l3r)) if L3 else ()) + ((("三道防线联动", tlr),) if tlr else ()) + (("字段说明", fields),):
            df = pd.DataFrame(rows); df.to_excel(W, sheet_name=name, index=False)
            ws = W.sheets[name]; ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, 1):
                w = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) * 1.6
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(8, w), 60)
    print(f"→ {xlsx}")

    # ================= 图
    domg = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(4326)
    cond = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_network.gpkg", layer="Conduit").to_crs(4326)
    H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(4326); H = H[H.hotspot_id.isin(HS)]
    asp = 1 / np.cos(np.radians(24.46))

    def base(ax, title):
        cond.plot(ax=ax, color="#c4ccd2", lw=.45); domg.boundary.plot(ax=ax, color="#333", lw=1, ls="--")
        H.plot(ax=ax, color="red", markersize=36, marker="*", zorder=6)
        for r in H.itertuples():
            ax.annotate(r.hotspot_id, (r.geometry.x, r.geometry.y), xytext=(3, 3), textcoords="offset points", fontsize=6.5, color="#b91c1c")
        ax.set_title(title, fontsize=11); ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel(""); ax.set_aspect(asp)

    def draw(ax, st):
        chain = {"L1": ["L1"], "L2": ["L1", "L2"], "L2P": ["L1", "L2P"], CUR: ["L1", "L2P", CUR]}[st]
        cnt = collections.Counter()
        for s_ in chain:
            for f in F.get(s_, []):
                faded = s_ != st
                k = f["kind"]; cnt[(s_, k)] += 1
                if f.get("poly") and k in ("tank", "sump", "pond"):
                    ax.add_patch(MPoly([(p[1], p[0]) for p in f["poly"]], closed=True, fc="#0f766e" if k == "tank" else "#db2777", ec="none", alpha=.6 if k == "tank" else .35))
                if f.get("line"):
                    xs = [p[1] for p in f["line"]]; ys = [p[0] for p in f["line"]]; ax.plot(xs, ys, color="#15803d", lw=1.2, alpha=.8)
                    continue
                if not f.get("ll"):
                    continue
                y, x = f["ll"]
                if f.get("to_ll"):
                    ax.plot([x, f["to_ll"][1]], [y, f["to_ll"][0]], color="#6b7280" if faded else {"conv": "#7c3aed", "sump": "#db2777", "mobile": "#0891b2"}.get(k, "#334155"),
                            lw=.6, ls="--", alpha=.7)
                mk = {"ps_l1": ("s", "#334155", 9), "mobile": ("^", "#0891b2", 7), "ps_planned": ("s", "#c2410c", 9), "conv": ("D", "#7c3aed", 6), "sump": ("o", "#db2777", 4)}.get(k)
                if mk:
                    ax.plot(x, y, marker=mk[0], color="#9ca3af" if faded else mk[1], ms=mk[2], mec="white", mew=.6, zorder=5)
        return cnt
    LEG = {"ps_l1": ("s", "#334155", "现状泵站"), "mobile": ("^", "#0891b2", "预置移动泵"), "ps_planned": ("s", "#c2410c", "客户计划的泵"),
           "tank": ("s", "#0f766e", "地下调蓄池"), "conv": ("D", "#7c3aed", "泵站（→ 排海口 / 地下池）"), "sump": ("o", "#db2777", "地面取水口")}
    for st in ST:
        fig, ax = plt.subplots(figsize=(10, 6.4)); base(ax, f"设施分布 · {LAB[st]}")
        cnt = draw(ax, st)
        hand = []
        for (s_, k), n in sorted(cnt.items()):
            if k in LEG:
                mk, c, lab = LEG[k]
                hand.append(plt.Line2D([], [], marker=mk, color="#9ca3af" if s_ != st else c, ls="", ms=7, label=f"{lab}（{n}）" + ("（前一级，灰）" if s_ != st else "")))
        if st == CUR:
            hand.append(plt.Line2D([], [], marker="s", color="#db2777", alpha=.35, ls="", ms=10, label="地面取水口的取水范围（洼坑积水片）"))
            hand.append(plt.Line2D([], [], color="#15803d", lw=1.4, label="重力管（井 → 地下池）"))
            hand.append(plt.Line2D([], [], color="#7c3aed", lw=.8, ls="--", label=f"压力管走向示意（新建排海口 {n_of} 处岸点）"))
        hand.append(plt.Line2D([], [], marker="*", color="red", ls="", ms=9, label="客户热点"))
        ax.legend(handles=hand, loc="lower left", fontsize=7.5, framealpha=.9)
        fig.savefig(fig_dir / f"设施分布_{SHORT[st].replace('+', '加')}.png", dpi=160, bbox_inches="tight", facecolor="white"); plt.close(fig)
    fig, axs = plt.subplots(2, 2, figsize=(15, 10))
    for ax, st in zip(axs.ravel(), ST):
        base(ax, LAB[st]); draw(ax, st)
    fig.suptitle("RD-A 设施分布：L1 → L2 → L2+planned → L2+suggested（灰 = 前一级已有的设施）", fontsize=13)
    fig.savefig(fig_dir / "设施分布_四态对比.png", dpi=150, bbox_inches="tight", facecolor="white"); plt.close(fig)
    # 最大水深
    Hu = H.to_crs(32640)
    for s in done:
        fig, axs = plt.subplots(1, 4, figsize=(20, 5.2))
        for ax, st in zip(axs, ST):
            z = np.load(C2D / f"RDA_{s}_{st}_2d.npz"); xy = z["xy"]; hm = z["hmax"]; m = hm >= 0.10
            sc = ax.scatter(xy[m, 0], xy[m, 1], c=np.clip(hm[m], 0, 1), s=1.4, cmap="Blues", vmin=0, vmax=1, marker="s")
            E = EV[s][st]
            ax.set_title(f"{SHORT[st]}\n≥0.30 m {E['domain']['ge030_ha']} ha · 热点全过 {npass(EV[s], st)}/11", fontsize=10)
            Hu.plot(ax=ax, color="red", markersize=14, marker="*"); ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect("equal")
        fig.colorbar(sc, ax=axs, fraction=.012, label="全程最大水深 m（≥0.10 m 才画）")
        fig.suptitle(f"最大水深对比 · {sname(s)}", fontsize=13)
        fig.savefig(fig_dir / f"最大水深对比_{s[1:3]}mm_{s.split('D')[1]}h.png", dpi=150, bbox_inches="tight", facecolor="white"); plt.close(fig)
    # 效果对比
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.4))
    x = np.arange(len(STORMS)); w = .2
    for i, st in enumerate(ST):
        ys = [EV[s][st]["domain"]["ge030_ha"] if st in EV[s] else np.nan for s in STORMS]
        ps = [npass(EV[s], st) if st in EV[s] else np.nan for s in STORMS]
        axs[0].bar(x + (i - 1.5) * w, ys, w, color=COL[st], label=SHORT[st]); axs[1].bar(x + (i - 1.5) * w, ps, w, color=COL[st], label=SHORT[st])
    for ax, yl in zip(axs, ("全域 ≥0.30 m 积水面积 ha", "四项全过的热点（/11）")):
        ax.set_xticks(x); ax.set_xticklabels([sname(s).replace(" mm / ", "/") for s in STORMS], rotation=30, fontsize=8); ax.set_ylabel(yl); ax.grid(axis="y", alpha=.3)
    axs[1].set_ylim(0, 11.5); axs[0].legend(fontsize=8)
    fig.suptitle("各降雨场景的内涝程度（L2+suggested 缺柱 = 计算中）", fontsize=12)
    fig.savefig(fig_dir / "效果对比_9场雨.png", dpi=150, bbox_inches="tight", facecolor="white"); plt.close(fig)
    if L3:
        VC = {"自然退干达标，不需 L3": "#16a34a", "L3 可承接": "#2563eb", "L3 可承接（关键点，建议 L2 兜住）": "#f59e0b", "超出 L3 → 应由 L2 解决": "#dc2626"}
        for s in [x for x in ("S96D1", "S96D24") if x in done]:
            fig, axs = plt.subplots(1, 2, figsize=(15, 4.9))
            for ax, st in zip(axs, ("L2P", CUR)):
                base(ax, f"{sname(s)} · {SHORT[st]}")
                rr = [r for r in L3["rows"] if r["storm"] == s and r["state"] == st]
                for v, c in VC.items():
                    q = [r for r in rr if r["verdict"] == v]
                    if q:
                        ax.scatter([r["lon"] for r in q], [r["lat"] for r in q], s=[max(12, min(400, r["vol_peak_m3"] / 40)) for r in q], c=c, alpha=.75, edgecolors="k", linewidths=.4, zorder=7, label=f"{v}（{len(q)}）")
                ax.legend(fontsize=7.5, loc="lower left")
            fig.suptitle(f"残余积水点能否交给 L3（圆大小 = 峰值积水量）", fontsize=12)
            fig.savefig(fig_dir / f"L3承接_{s[1:3]}mm_{s.split('D')[1]}h.png", dpi=150, bbox_inches="tight", facecolor="white"); plt.close(fig)
    print(f"→ {fig_dir}")

    # ================= 文档
    def fmtp(v):
        return "—" if v is None else f"{v:.0f} %"
    eff = ""
    for s in done:
        E = EV[s]; d = {st: E[st]["domain"] for st in ST}
        eff += (f"| {sname(s)} | {d['L1']['ge030_ha']} | {d['L2P']['ge030_ha']} | **{d[CUR]['ge030_ha']}** | {fmtp(pct(d[CUR]['ge030_ha'], d['L1']['ge030_ha']))} | "
                f"{fmtp(pct(d[CUR]['ge030_ha'], d['L2P']['ge030_ha']))} | {d['L2P']['peak_vol_m3']:,} → {d[CUR]['peak_vol_m3']:,}（{fmtp(pct(d[CUR]['peak_vol_m3'], d['L2P']['peak_vol_m3']))}） | "
                f"{npass(E, 'L1')} / {npass(E, 'L2P')} / **{npass(E, CUR)}** |\n")
    sev = ""
    for s in STORMS:
        E = EV[s]
        sev += f"| {sname(s)} | {RS[s]['hyeto']['peak_mmph']} | " + " | ".join(f"{E[st]['domain']['ge030_ha']}（{npass(E, st)}/11）" if st in E else "计算中" for st in ST) + " |\n"
    hsd = ""
    for s in done:
        E = EV[s]
        for h in HS:
            r0, r1 = E["L2P"]["hotspots"][h], E[CUR]["hotspots"][h]; bad = "".join(k for k, v in r1["pass"].items() if not v)
            hsd += (f"| {sname(s)} | {h} | {r0['max_m']} → {r1['max_m']} | {r0['ge030_ha']} → {r1['ge030_ha']} | {r0['ge015_ha_h']} → {r1['ge015_ha_h']} | "
                    f"{'✓' if not bad else '未过 ' + bad} |\n")
    costt = "".join(f"| {r['设施类型']} | {r['数量']} | {r['占位造价 百万美元']} | {r['占比 %']} |\n" for r in cost)
    pend = collections.Counter()
    for r in led:
        for t_ in filter(None, (r.get("需审批 / 许可") or "").split("；")):
            pend[t_] += 1
    pendt = "".join(f"| {k} | {n} |\n" for k, n in pend.most_common(10))
    n_todo = len([s for s in STORMS if s not in done])
    l3t = "".join(f"| {r['场景']} | {r['状态']} | {r['残余积水点']} | {r['自然退干达标']} | {r['L3 可承接']}（{r['其中关键点（建议 L2）']}） | {r['超出 L3 → 需 L2']} | {r['超出 L3 的积水量 m³']:,} | {r['需 12 英寸泵 台']} / {r['需罐车 辆']} |\n"
                  for r in l3s if r["场景"].startswith("96"))
    # 客户历史积水点：当前方案下每个热点的结论（与系统 RD-A 页「三道防线联动」② 同一口径）
    hst = ""
    HSINFO = {}
    l3doc = {}
    for s in ("S96D1", "S96D24"):
        f = ROOT / "console/data" / f"flood2d_rda_{s.lower()}" / "l3.json"
        if f.exists() and s in done:
            l3doc[s] = json.loads(f.read_text(encoding="utf-8")); HSINFO = l3doc[s].get("hotspots") or HSINFO

    def hs_verdict(s, h):
        S = l3doc[s]["states"].get(CUR); X = (S or {}).get("dispatch", {}).get("island")
        if not S or not X:
            return "—"
        ok = all(EV[s][CUR]["hotspots"][h]["pass"].values())
        ids = S["hs_links"].get(h, [])
        rs = [X["points"][i]["result"] for i in ids if i in X["points"]]
        if ok:
            return "已达标"
        if not ids:
            return "只在雨峰短时积水，雨后很快退掉"
        if all(r in ("自然退干", "只监测") for r in rs):
            return "雨后自然退干，L3 不需出动"
        if all(r in ("自然退干", "只监测", "L3 按时退干") for r in rs):
            return "L3 按时兜底"
        return "**L3 兜不住 → 回流 L2**"
    for h, hi in HSINFO.items():
        pa = hi.get("planA") or {}
        hst += (f"| {h} | {hi.get('criticality')} | " + " | ".join(hs_verdict(s, h) if s in l3doc else "—" for s in ("S96D1", "S96D24"))
                + f" | 泵 {pa.get('pumps') if pa.get('pumps') is not None else '—'} 台 · 罐车 {pa.get('tankers') or 0} 辆 |\n")
    tlt = "".join(f"| {r['场景']} | {r['防线']} | {r['残余积水点']} | {r['关键']} / {r['高']} / {r['中']} / {r['无暴露（只监测）']} | {r['Island A13：自然退干']} | {r['Island A13：L3 按时']} / {r['Island A13：L3 超时']} / {r['Island A13：L3 未派']} | "
                  f"{r['Island A13：泵 台']} / {r['Island A13：罐车 辆']} | {r['ADM 全部驻地：L3 按时']} / {r['ADM 全部驻地：L3 超时']} / {r['ADM 全部驻地：L3 未派']} | "
                  f"{r['ADM 全部驻地：泵 台']} / {r['ADM 全部驻地：罐车 辆']} | {r['超出 L3 → 回流 L2']} |\n" for r in tlr if r["场景"].startswith("96"))
    npvt = "".join(f"| {r['方案']} | {r['建设费 M USD']} | {r['每年运维 M USD']} | " + " | ".join(str(v) for k, v in r.items() if k.startswith("NPV")) + " |\n" for r in npvr)
    md = DOC.format(npvt=npvt or "| — | — | — | — | — | — |\n", hst=hst or "| — | — | — | — | — |\n", tlt=tlt or "| — | — | — | — | — | — | — | — | — | — |\n",l3t=l3t or "| — | — | — | — | — | — | — | — |\n", date=datetime.date.today().isoformat(), n=len(led), tot=tot / 1e6, n_of=n_of, done="、".join(sname(s) for s in done), n_todo=n_todo,
                    status=("9 场雨都已完成当前方案的一二维计算。" if not n_todo else f"**中间版**：9 场雨中已有 {len(done)} 场完成当前方案的一二维计算，其余 {n_todo} 场正在计算，表中标「计算中」，完成后更新。"),
                    eff=eff, sev=sev, hsd=hsd, costt=costt, pendt=pendt, pr0=PR["n_ref"], pr1=PR["n_final"], prc0=PR["cost_ref"] / 1e6, prc1=PR["cost_final"] / 1e6,
                    ntank=sum(1 for r in led if "调蓄池" in r["类型"]), nps=sum(1 for r in led if r["类型"].startswith("泵站") or r["类型"].startswith("井边")),
                    nsps=sum(1 for r in led if r["类型"].startswith("地面")), nint=len(intake), vol=sum(r.get("池容 m³") or 0 for r in led))
    mdp = out / "RD-A_L2S成果说明.md"; mdp.write_text(md, encoding="utf-8")
    subprocess.run([sys.executable, str(HERE / "md_to_docx_cn.py"), str(mdp), str(mdp.with_suffix(".docx"))], check=True)
    print(f"→ {mdp.with_suffix('.docx')}")
    return 0


DOC = """# RD-A 汇水区 L2+suggested 优化成果说明（中间版）

> {date} · 当前方案：L2+suggested v3 + 7 座地面泵站（内部编号 OPTCS2）
> 本包：说明文档（本文件）、`RD-A_L2S成果_设施台账与效果.xlsx`、`图/` 文件夹、`RDA_61mm1h_全套成果/`（61 mm / 1 h 一场的全套计算结果）。
> {status}
>
> **重要：当前方案只按 96 mm 优化。** 它是用 96 mm / 1 h 和 96 mm / 24 h 两场雨优化定下的（{n} 项措施），其余各场（含 61 mm / 1 h）是把同一套方案原样放进去做的一二维验证，
> 不是针对那场雨的最优方案。各状态的计算结果都是真实计算结果。按 AECOM 口径（AECOM「10 年 + 20%」1 h 设计雨 60.7 mm）的 61 mm / 1 h 专门优化 2026-10-26 已开始计算，结果另行交付。

## 一、背景

### 1. 范围与模型

- **范围**：RD-A 汇水区，计算域 3.15 km²，客户热点 11 个（ADM-75、76、80、161、376、377、378、379、380、382、383）。
- **模型**：一维用客户 AECOM SewerGEMS 模型（inp 51079，2,157 口井、2,160 根管、现有 RD-A 泵站），换算公制后用 SWMM 5.2 计算；二维用 ANUGA，客户 5 m DTM、15 m 网格；一二维每 60 s 交换一次。
- **产流**：每块地只算一次面积，按 Makani 测得的不透水比例拆成两部分分别计算（纠正了客户模型面积重复计算的问题）。
- **模型未率定**：客户没有提供观测数据。方案之间的相对差别可信，积水的绝对深度和范围待率定。

### 2. 降雨场景

共 9 场设计暴雨：雨量 33 / 61 / 96 mm × 历时 1 / 2 / 24 h。雨型取客户 Mussafah 模型同历时的「10 年一遇含气候变化」雨型，按雨量等比缩放；雨停后再算 10 h，用来看积水退得多快。出水口尾水固定 1.3 m（WA-726 表 4-5 设计高潮位）。各场的峰值雨强等见 Excel「降雨场景」表。

**这里的雨型不是 AECOM RD-A 模型的雨型。** 同样 61 mm / 1 h，本包的雨（Mussafah 雨型放大）5 分钟峰值约 352 mm/h，AECOM 模型的 1 h 设计雨峰值约 147 mm/h，本包的雨更尖、更严。
「10 年一遇 + 20 % 气候变化」的雨量，AECOM 与 WA-726 手册口径不同：

| 历时 | AECOM RD-A 模型 | WA-726 表 3-6（B 区 × 1.2） |
|---|---|---|
| 1 h | 60.7 mm | 25.4 mm |
| 2 h | 60.7 mm | 30.8 mm |
| 24 h | 61.3 mm | 61.3 mm |

AECOM 把 24 h 的雨量压进 1 h 和 2 h，所以按 AECOM 口径，61 mm / 1 h 就是 10 年 + 20 % 设计雨；按手册口径，它已超过 100 年一遇（1 h 100 年一遇 + 20 % 为 56.6 mm）。用哪个口径作设计标准，待与客户确认。

### 3. 对比的状态

| 状态 | 内容 |
|---|---|
| L1 现状 | 客户模型原样，含现有 RD-A 泵站（3 台泵） |
| L2 | L1 + 热点表 Plan A 的 15 台预置移动泵 |
| L2+planned | L1 + AECOM 模型情景「Temporary Intervention」中列入计划的 2 台泵（AECOM 只按 10 年 + 20 %、24 h、61.3 mm 验算过这个情景；热点表里 AECOM 的永久工程客户未提供设计，未计入） |
| **L2+suggested** | L2+planned + 本次优化的 {n} 项措施（本文的「当前方案」） |

### 4. 怎么评价「内涝降低了多少」

- **全域**：≥0.30 m 积水面积、峰值积水量。
- **热点**（11 个，各取 200 m 范围）：四条判据——A 无 ≥0.30 m 积水；B ≥0.15 m 积水的面积×时间不超过 L2+planned 的 10 %；C 雨停后 6 h 内退到 0.10 m 以下；D 全域没有明显变深。
- 统计时剔除了 ADM-80 岸边约 9.7 ha 的 DTM 缺测区（高程填 0 造成的假积水），各状态同一口径。

## 二、优化方案：设施情况

### 1. 怎么得到的

候选措施先过三层约束，再优化：

1. **三层约束**：WA-726 手册条文（821 条）、领域知识（15 组规则，如重力进水、道路红线、供电、泵站场地、排口岸段）、Makani 空间数据（建筑、道路、管线走廊、变电站、地块等 15 个图层）。不通过的候选直接剔除。
2. **一维优化**：用 96 mm / 1 h 和 96 mm / 24 h 两场雨（只用这两场），贪心选 20 轮，再剪枝和替换：{pr0} → {pr1} 项，{prc0:.1f} → {prc1:.1f} M USD。其余雨量下的结果都是这套方案的验证，不是按那场雨重新优化。
3. **补地面泵站**：二维里热点附近的封闭洼坑管网收不进去，配 7 座地面泵站，同样过可建性检查。

### 2. 设施组成

- **地下调蓄池 {ntank} 座**（总池容 {vol:,.0f} m³），其中 3 座由井边泵站提水，其余重力进水；
- **泵站 {nps} 座**（含井边泵站），压力管沿道路走，排到新建排海口；
- **地面泵站 {nsps} 座**，共 {nint} 个地面取水口；
- **新建排海口 {n_of} 处岸点**。

逐项的位置、规模、去向、用地、造价和可建性见 Excel「设施台账_L2S」和「地面取水口」表；分布见 `图/设施分布_*.png`。

### 3. 造价（占位）

| 设施类型 | 数量 | 占位造价（百万美元） | 占比 % |
|---|---|---|---|
{costt}
占位造价只用于比较方案，不是预算：压力管、排海口用 WA-726 第三卷附录 L 单价，其余为占位单价。同一岸点的排口在造价里分别计算，实际可合建。

**全寿命成本（NPV，按 DEC-10 占位参数，百万美元）**：

| 方案 | 建设费 | 每年运维 | NPV 3 % | NPV 5 % | NPV 8 % |
|---|---|---|---|---|---|
{npvt}
口径：
- 评估期 60 年。
- 更换：机电 15 年一换，土建和排海口 50 年一换，管道 60 年内不换；不计残值。
- 年运维（占建设费）：机电 4 %，土建 0.5 %，池体另加清淤 1 %，管道 0.2 %，排海口 0.5 %。
- 电费：泵每年运行 100 h，扬程统一按 10 m 估。
- 运维比例、运行小时、电价为占位值，待客户给正式值。
- NPV 只用于方案比较，优化和剪枝仍按建设费。
- 分项明细见 Excel「全寿命成本_NPV」。

### 4. 可建性

所有措施都是「有条件可建」：没有不通过的检查项，但都有需要审批、许可或补数据的事项。出现最多的：

| 事项 | 涉及措施 |
|---|---|
{pendt}
## 三、内涝降低了多少

### 1. 当前方案（已算完的场景）

| 场景 | 全域 ≥0.30 m 积水 ha：L1 | L2+planned | **L2+suggested** | 比 L1 减少 | 比 L2+planned 减少 | 峰值积水量 m³：L2+planned → L2+suggested | 达标热点：L1 / L2+planned / **L2+suggested** |
|---|---|---|---|---|---|---|---|
{eff}
### 2. 全部 9 场雨的内涝程度（全域 ≥0.30 m 积水 ha，括号内为四项全过的热点数）

| 场景 | 5 分钟峰值雨强 mm/h | L1 | L2 | L2+planned | L2+suggested |
|---|---|---|---|---|---|
{sev}
### 3. 逐热点（当前方案 vs L2+planned）

| 场景 | 热点 | 最大水深 m | 200 m 内 ≥0.30 m 面积 ha | ≥0.15 m 面积×时间 ha·h | 当前方案判据 |
|---|---|---|---|---|---|
{hsd}
### 4. 没过的原因

- **96 mm / 1 h 的雨峰时刻**：ADM-377～380 共用的大洼坑、ADM-76 隧道引道坑，积水超过 0.30 m（判据 A）。1 小时落 96 mm，地面泵抽不过来；雨后很快退掉。
- **ADM-161**：雨后有一处退不到 0.10 m；它的判据 B 基线接近 0，比较本身偏严。
- **对照**：作废的 v1（所有水泵到一个出水口、不按手册和空间约束）在 96 mm / 1 h 下有 5 / 11 达标，但不可建。可建性约束在极端短历时雨下牺牲了一部分效果。

## 四、L2 与 L3 的分界：剩下的积水能不能交给 L3

L3 = 应急手段（WA-726 第三卷第 11 章：移动泵、罐车、临时封路）。判断每个残余积水点（二维结果里 ≥0.15 m、≥100 m²、≥0.5 h 的连片积水，已剔除塘占地和 DTM 填 0 / 私人地块坑）：

1. **时限**：雨停后，按 300 m 内重点单位（医院 2 h、隧道 3 h、学校 4 h、其他 6 h）和 200 m 内客户热点重要度（Very High 2 h、High 4 h、Medium 10 h）取最严；
2. **自然退干**：二维结果本身的退水过程，在时限内退到 50 m³ 以下 → 不需要 L3；
3. **L3 能力**：积水成形后 2 h 到位（客户预案的首次到场目标），用预案 A 的 12 英寸移动泵（700 m³/h），每点最多 3 台；1.5 km 内没有可接纳的排放口则只能用罐车（≤6 辆）。能在时限内抽干 → **L3 可承接**；不能 → **超出 L3，应由 L2 解决**；
4. **关键点**：隧道、医院、急救站或客户 Very High 热点处、深 ≥0.30 m 的，即使 L3 能抽，也建议由 L2 永久设施兜住。

| 场景 | 状态 | 残余积水点 | 自然退干达标 | L3 可承接（其中关键点） | 超出 L3 | 超出 L3 的积水量 m³ | 需 12 英寸泵 / 罐车 |
|---|---|---|---|---|---|---|---|
{l3t}
- **96 mm / 24 h**：当前方案下剩余积水全部可以交给 L3（约 4 台泵、1–2 台时）；L2+planned 也没有超出 L3 的点，但要约 24 台泵、73 台时。
- **96 mm / 1 h**：当前方案把「超出 L3」的点从 9 个（约 9.9 万 m³）降到 2 个（约 1.8 万 m³）：一处在学校附近、1.6 km 内没有排放去处；一处在医院附近。这 2 处加上 3 个关键点，是 L2 还要继续解决的对象（v4 的重点）。
- 当前方案下客户热点附近的残余积水（雨峰时超过 0.30 m、判据 A 没过的那些）都在时限内自然退干，多在雨后 1–2.5 h 退掉，L3 到场前已经没水。需要 L3 的点都不在客户 11 个热点 200 m 内，预案 A 在这些地方没有预置泵。
- 明细见 Excel「L3承接_汇总」「L3承接清单」，图见 `图/L3承接_*.png`。台班费只算租用（每台时 90 AED），没有算泵队的购置、待命和人员，不能直接和 L2 造价比，NPV 比较待补。

### 联动：把每一级防线剩下的积水交给 L3 调度求解器实际派单

上面是逐点的能力判断；下面把 L1 → L2 → L2+planned → L2+suggested 每一级剩下的积水点，交给系统里第三道防线的调度求解器（滚动 MILP，路网行程，退水重演，客户预案 A 的车队和人员）实际派单。行程时间按 ArcGIS Online 卡车路线实测校正：本地路网估算 × 1.70（966 条卡车路线的中位，2026-10-27 起）；校正前后对比，按时退干的点数在 ADM 全部驻地下少约 5%，只用 Island 驻地时几乎不变（瓶颈是泵和人，不是路程）。

**口径**：

- **L3 的输入**：就是这一级防线之后二维模型里仍然积水的地方，不是另一套点。
- **处置先后**：看后果，不看出身。后果等级按积水碰到了什么来定：
  - 关键：医院、急救、隧道或地铁 300 m 内，且路面或建筑处 ≥0.30 m；或淹到输电变电站。
  - 高：≥0.30 m 的路面 ≥200 m²，或 ≥0.30 m 碰到建筑或配电变电站。
  - 中：≥0.15 m 的路面 ≥100 m²，或碰到建筑。
  - 无暴露：只在空地、未建地块、公园。这类点只监测，不派 L3。
- **客户热点重要度**：只会抬高等级，不会被模型降低。
- **调度资源**：分两种，仅 Island 片区驻地 A13（现状，各片区各管各的），或 ADM 五个驻地集中调度（上限）。
- **按时的判据**：在「雨停 + 处置时限」之前退干。

后果等级的划分是我们提出的，待客户确认。

**客户历史积水点**（当前方案 L2+suggested，只用 Island 驻地；历史点单列，保留客户编号）：

| 热点 | 客户重要度 | 96 mm / 1 h | 96 mm / 24 h | 预案 A 预置 |
|---|---|---|---|---|
{hst}
**各级防线的残余积水与 L3 派单**：

| 场景 | 防线 | 残余点 | 关键 / 高 / 中 / 无暴露 | 自然退干 | Island：按时 / 超时 / 未派 | Island：泵 / 罐车 | ADM 全部：按时 / 超时 / 未派 | ADM 全部：泵 / 罐车 | 超出 L3 → 回流 L2 |
|---|---|---|---|---|---|---|---|---|---|
{tlt}
- **96 mm / 1 h，L1 到 L2+planned**：56 个残余点，其中 8 个关键、23 个高、6 个无暴露。只用 Island 驻地时，按时退干的只有 4–7 个，18–19 个超时，10 个没派上，A13 的 84 人全部用上。调动 ADM 全部驻地后，按时的升到 14–15 个，仍有 15–16 个超时（行程时间按卡车实测校正后；校正前为 15–18 个按时），其中 9 个点超出 L3 单点能力。**L3 兜不住 L2+planned 的残余。**
- **96 mm / 1 h，L2+suggested**：残余点降到 48 个，关键点 4 个，无暴露 7 个只监测。只用 Island 驻地：29 个自然退干，7 个按时，5 个超时，没有没派上的；调动 ADM 全部驻地也没有改善。回流 L2 的是超出 L3 的 2 个点（SIM-OPTCS2-001、005），以及急救站附近超时的关键点 SIM-OPTCS2-002，都不在客户热点上，是 v4 的优化对象。
- **96 mm / 24 h**：L1 到 L2+planned 时，L3 能把残余处理掉（每级 23–24 个按时、1 个超时），但要动用 Island 驻地全部人员和约 20 台泵，台班费约 10–12 万 AED。L2+suggested 之后只剩 3 个点要 L3，用 8 台泵、6 辆罐车，约 3 万 AED。
- **口径提醒**：
  - 调度求解器在整场事件中不释放已派出的班组。这是原有的简化，我没有改，所以人员瓶颈可能被高估。
  - 6 英寸三联泵一组要 6 人。
  - 车辆、软管、燃油、费率为模拟值。
系统里 RD-A 页「三道防线联动」一节可切换场景、防线和 L3 资源，地图显示派车线和每个积水点的处置结果；明细见 Excel「三道防线联动」。

## 五、局限与待办

1. 模型未率定；需要客户的观测、积水记录、实测降雨（见《RD-A 客户补充资料请求》）。
2. 造价是占位值；需要客户的单价和方案比选口径（NPV）。
3. 隧道引道的泵站暂按新建计入；客户确认已有现状泵站则改为 L1、扣除造价。
4. 排海口需 EAD 审批；地下池池底多低于 0 m，需要地下水资料；与现状管线的净距缺埋深数据判不了。
5. 正在计算：造价–效果曲线；地面泵站并入优化的 v4；按手册口径设计雨的最小方案（档 1）；按 AECOM 口径的 61 mm / 1 h 专门优化。
6. 已列入客户确认（《RD-A 客户补充资料请求》M-14、S-5，P1）：设计雨按手册口径还是 AECOM 口径；AECOM 在 11 个热点的永久工程设计（未拿到前不计入任何状态，L2+suggested 可能与之重叠）。

## 六、文件清单

| 文件 | 内容 |
|---|---|
| `RD-A_L2S成果说明.docx` | 本说明 |
| `RD-A_L2S成果_设施台账与效果.xlsx` | 降雨场景；内涝效果（全域、逐热点）；L2+suggested 设施台账（位置、规模、去向、用地、造价、可建性、待办）；地面取水口；现状与计划设施（L1 / L2 / L2+planned）；造价汇总；字段说明 |
| `图/设施分布_L1.png` 等 4 张 | 各状态的设施分布（灰色 = 前一级已有的设施） |
| `图/设施分布_四态对比.png` | 四个状态并排 |
| `图/最大水深对比_*.png` | 已算完场景的四态最大水深 |
| `图/效果对比_9场雨.png` | 9 场雨的 ≥0.30 m 积水面积与达标热点数 |
| `图/L3承接_*.png` | 96 mm 两场：L2+planned 与当前方案的残余积水点，按能否交给 L3 着色 |
| `RDA_61mm1h_全套成果/` | 61 mm / 1 h（Mussafah 雨型）四个状态的全套结果：一二维文件、水深栅格、积水点（按阈值动态计算）、L3 承接、设施 GIS 与台账、飞渡 DTS；说明见其中 `README.md`（其中的 L2+suggested 同为按 96 mm 优化的方案） |
"""

if __name__ == "__main__":
    raise SystemExit(main())
