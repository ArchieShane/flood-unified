# -*- coding: utf-8 -*-
"""三档方案的「客户热点 + 模型高后果积水点」评估（2026-10-27 用户：优化目标扩大到客户热点 + 模型算出的高后果积水点，先评估现有方案）。

只读二维结果：每个方案、每场本档设计雨，用 tools/rda_l3.py 的 assess（同 L3 承接的口径）把残余积水片逐个分级：
  后果等级：关键 / 高 / 中 / 无暴露（碰到医院急救隧道地铁、变电站、道路、建筑；见 rda_l3.consequence）
  处置：自然退干达标 / L3 可承接 / 超出 L3 → 应由 L2 解决 / 无暴露只监测
「高后果积水点」= 等级为关键或高；「未解决」= 高后果且不能在处置时限内自然退干（要靠 L3 或 L3 也兜不住）。
同一场雨的客户计划（L2+planned）作对照。
产物：out/rda/高后果积水点评估_20261027.xlsx（汇总、逐点清单）；终端打印汇总。
用法：python -X utf8 tools/rda_highconseq_eval.py（需 unset PROJ_LIB、设 SWBRAIN_SWMM_WORK）
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import pandas as pd  # noqa: E402
import rda_l3 as L3  # noqa: E402

CASES = [  # (档, 方案态, 方案名, 造价 M USD, 本档的雨)
    ("手册口径", "OPTD10S", "档 1 + 地面泵站", 94.24, ["S25D1", "S31D2", "S61D24"]),
    ("AECOM 口径 61 mm / 1 h", "OPTA61PS", "推荐方案（当前方案瘦身版）", 118.71, ["A61D1", "S61D1", "S61D24"]),
    ("96 mm 极端雨", "OPTCS2", "当前方案", 133.74, ["S96D1", "S96D2", "S96D24"]),
]
LAB = {"S25D1": "25.4 mm / 1 h", "S31D2": "30.8 mm / 2 h", "S61D24": "61 mm / 24 h", "A61D1": "60.7 mm / 1 h（AECOM）", "S61D1": "61 mm / 1 h（Mussafah）",
       "S96D1": "96 mm / 1 h", "S96D2": "96 mm / 2 h", "S96D24": "96 mm / 24 h"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    C = L3.context()
    summ, detail = [], []
    for tier, st, nm, cost, storms in CASES:
        for s in storms:
            for state, sname in (("L2P", "客户计划（对照）"), (st, nm)):
                r = L3.assess(s, state, C)
                if r is None:
                    print("缺二维结果", s, state); continue
                _, rows, _ = r
                g = pd.DataFrame(rows)
                if g.empty:
                    g = pd.DataFrame(columns=["level", "hotspots_200m", "verdict", "vol_peak_m3", "area30_m2"])
                near = g["hotspots_200m"].astype(str).str.len() > 0
                hi = g["level"].isin(["关键", "高"])
                unres = hi & ~g["verdict"].astype(str).str.startswith("自然退干")
                summ.append({"档": tier, "降雨": LAB[s], "方案": sname, "造价 M USD": cost if state == st else None,
                             "残余积水点": len(g), "关键": int((g["level"] == "关键").sum()), "高": int((g["level"] == "高").sum()),
                             "中": int((g["level"] == "中").sum()), "无暴露": int((g["level"] == "无暴露").sum()),
                             "高后果点（关键 + 高）": int(hi.sum()), "其中在客户热点 200 m 内": int((hi & near).sum()), "其中不在客户热点附近": int((hi & ~near).sum()),
                             "高后果且未能自然退干": int(unres.sum()), "其中不在客户热点附近 ": int((unres & ~near).sum()),
                             "其中超出 L3（应由 L2 解决）": int((unres & g["verdict"].astype(str).str.startswith("超出")).sum()),
                             "高后果点峰值积水量合计 m³": int(g.loc[hi, "vol_peak_m3"].sum()) if hi.any() else 0,
                             "高后果点 ≥0.30 m 面积合计 m²": int(g.loc[hi, "area30_m2"].sum()) if hi.any() else 0})
                if state == st:
                    for _, x in g[hi].iterrows():
                        detail.append({"档": tier, "降雨": LAB[s], "方案": nm, "积水点": x["id"], "经度": x["lon"], "纬度": x["lat"], "后果等级": x["level"],
                                       "定级原因": x["level_why"], "200 m 内客户热点": x["hotspots_200m"] or "（无）", "最大水深 m": x["max_depth_m"],
                                       "面积 m²": x["area_m2"], "≥0.30 m 面积 m²": x["area30_m2"], "峰值积水量 m³": x["vol_peak_m3"],
                                       "处置时限（雨停起）min": x["target_min"], "自然退干 h": x["t_dry_nat_h"], "处置判定": x["verdict"], "L3 手段": x["l3_means"], "台 / 辆": x["l3_units"]})
                print(f"{tier} {LAB[s]} {sname}: 残余 {len(g)}，高后果 {int(hi.sum())}（不在热点附近 {int((hi & ~near).sum())}），未能自然退干 {int(unres.sum())}，超出 L3 {int((unres & g['verdict'].astype(str).str.startswith('超出')).sum())}", flush=True)
    out = ROOT / "out/rda/高后果积水点评估_20261027.xlsx"
    with pd.ExcelWriter(out) as w:
        pd.DataFrame(summ).to_excel(w, sheet_name="汇总", index=False)
        pd.DataFrame(detail).to_excel(w, sheet_name="方案下的高后果积水点", index=False)
        pd.DataFrame([{"项": k, "说明": v} for k, v in (
            ("后果等级", "同 L3 承接（tools/rda_l3.py consequence）：关键 = 医院 / 急救 / 隧道 / 地铁 300 m 内且路面或建筑处 ≥0.30 m，或淹到输电变电站；高 = ≥0.30 m 路面 ≥200 m²、≥0.30 m 碰到建筑或配电变电站；中 = ≥0.15 m 路面 ≥100 m² 或碰到建筑；无暴露 = 只在空地 / 未建地块 / 公园"),
            ("积水点", "≥0.15 m、≥100 m²、≥0.5 h 的二维积水片，扣本状态塘占地和评估剔除区（DTM 填 0、私人深坑）"),
            ("未能自然退干", "在处置时限内（雨停 + 时限，客户热点按重要度取最严）不能自己退干，要靠 L3 抽排或 L3 也兜不住"),
            ("口径说明", "后果等级是我们的建议，待客户确认；判据 A–D 只看客户热点，本表是对模型积水点的补充评估"))]).to_excel(w, sheet_name="口径", index=False)
    print("→", out)


if __name__ == "__main__":
    main()
