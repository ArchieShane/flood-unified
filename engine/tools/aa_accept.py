# -*- coding: utf-8 -*-
"""艾因一个情景算完后的验收（在 swbrain 容器里跑；2026-10-18）。硬门槛只有一条：L2 的溢水量（一维域内井溢流）< L1；其余只作提示；加 --files 时再核交付文件齐全（缺文件也算不通过）。
  1. 一维连续性误差：L1、L2 都 < 1%
  2. L2 一维域内井溢流 ≤ L1 × 1.0002（加了塘不该让管网多冒水）
  3. L2 全域最大水深 ≤ L1 + 0.01 m，≥0.10 m 面积 ≤ L1 × 1.002，最大积水量 ≤ L1 × 1.002
  4. 热点并集（若已补）最大水深 ≤ L1 + 0.005 m，最大积水量 ≤ L1 × 1.002
  5. 交付 公共/ 6 件套、统计值.csv 表头「区域,口径,指标」、积水点结果表都在（rerun-l2 里在 aa_hotspots 之后再跑一次 --files）
用法：python tools/aa_accept.py --tag ALAIN_M61H1 [--files]"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path("/app")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--files", action="store_true")
    a = ap.parse_args(); t = a.tag
    res = json.loads((ROOT / "out" / f"aa_stats_{t}.json").read_text(encoding="utf-8"))
    bad, notes = [], []
    A, B = res["all"], res["balance"]
    # 唯一的硬门槛（用户 2026-10-18：只需要确认 L2 比 L1 少）：L2 的一维域内井溢流（= 页面上的「溢水量」）必须 < L1。其余只提示，不拦。
    k = "一维：域内井溢流（=交给二维）"
    f1, f2 = B["L1"][k], B["L2"][k]
    (notes if f2 < f1 else bad).append(f"溢水量（一维域内井溢流）L1 {f1:,} → L2 {f2:,} m³（{f2 - f1:+,}，{(f2 / f1 - 1) * 100:+.3f}%）：要求 L2 < L1")
    for s_ in ("L1", "L2"):
        ce = B[s_].get("一维：连续性误差 %")
        notes.append(f"（提示）{s_} 一维连续性误差 {ce}%" + ("" if ce is not None and abs(ce) < 1.0 else "  ← 超过 1%，留意"))
    d1, d2 = A["L1"]["max_depth_m"], A["L2"]["max_depth_m"]
    notes.append(f"（提示）全域最大水深 L1 {d1} → L2 {d2} m")
    for key, lab in (("area_ge_0p10_ha", "≥0.10 m 面积 ha"), ("peak_vol_m3", "最大积水量 m³")):
        x1, x2 = A["L1"][key], A["L2"][key]
        notes.append(f"（提示）全域{lab} L1 {x1:,} → L2 {x2:,}（{(x2 / x1 - 1) * 100:+.3f}%）" + ("" if x2 <= x1 else "  ← L2 比 L1 大，留意"))
    cs_p = ROOT / "console" / "data" / f"flood2d_aa_{t.lower()}g250" / "aa_stats.json"
    if cs_p.exists():
        Z = json.loads(cs_p.read_text(encoding="utf-8")).get("zones", {})
        u = next((z for z in Z if "个热点" in z), None)
        if u:
            h1, h2 = Z[u]["L1"], Z[u]["L2"]
            notes.append(f"（提示）热点并集最大水深 L1 {h1['max_depth_m']} → L2 {h2['max_depth_m']} m；最大积水量 L1 {h1['peak_vol_m3']:,} → L2 {h2['peak_vol_m3']:,} m³")
    if a.files:
        m = re.match(r"ALAIN_M(\d+)H(\d+)", t); DD = ROOT / "out" / "交付" / f"艾因_模型版_{m[1]}mm{m[2]}h_250m"
        need = ["公共/计算域.geojson", "公共/现状泵站.json", "公共/已建成塘.json", "公共/积水点.json", "公共/热点.geojson", "公共/输入参数.json",
                "积水点/积水点结果表.csv", "积水点/积水点结果表.xlsx", "积水点/积水点图层.geojson", "统计/aa_stats.json", "对比_缺口分析/统计值.csv", "对比_缺口分析/diff_L2_minus_L1.png",
                "00_说明.md", "计算设置.md"]
        for st, dn in (("L1", "L1_既有管网"), ("L2", "L2_已建成调蓄")):
            nm = f"AA_{t}_{st}"
            need += [f"{dn}/一维/{nm}.inp", f"{dn}/一维/{nm}_一维报告.rpt", f"{dn}/二维/{nm}.sww", f"{dn}/二维/{nm}_最大水深.tif", f"{dn}/二维/{nm}_逐帧水深.json",
                     f"{dn}/二维/{nm}_逐帧水深.tif", f"{dn}/二维/{st}_耦合运行记录.json"]
        miss = [n for n in need if not (DD / n).exists()]
        (bad if miss else notes).append("交付文件齐全" if not miss else f"交付缺文件：{miss}")
        sv = DD / "对比_缺口分析" / "统计值.csv"
        if sv.exists():
            head = sv.read_text(encoding="utf-8-sig").splitlines()[0]
            (bad if not head.startswith("区域,口径,指标") else notes).append(f"统计值.csv 表头 {head}")
    for n in notes:
        print("  ✓ " + n)
    for b_ in bad:
        print("  ✗ " + b_)
    print("验收通过 ✓" if not bad else f"验收未通过 ✗（{len(bad)} 项）")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
