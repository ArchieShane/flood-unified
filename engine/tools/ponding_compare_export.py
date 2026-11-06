"""交付包补「积水点与 L1 对照」表（2026-10-28 用户：切到 L2+suggested 时 L1 的积水点「消失」看不懂）。

每个非 L1 状态的 `积水点/` 目录写 `积水点_与L1对照_d0.15.csv`：
  · 本状态每个积水点：对应 L1 的哪个（些）积水点、重叠面积、水深 / 积水量变化、变浅 / 变深 / 新出现
  · L1 有、本状态已消除的积水点：L1 时的水深、积水量、面积
对照口径同系统页面（swbrain.ponding.compare：同一网格单元重叠、缺省阈值 0.15 m / 100 m² / 0.5 h、本态塘）。只读二维结果，不重算。
用法：python tools/ponding_compare_export.py
"""
from __future__ import annotations
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from swbrain import ponding  # noqa: E402

HEAD = ["类别", "本状态积水点", "对应 L1 积水点", "与 L1 重叠面积 m²", "L1 最大水深 m", "本状态最大水深 m", "水深变化 m", "L1 峰值积水量 m³", "本状态峰值积水量 m³",
        "积水量变化 m³", "经度", "纬度", "说明"]


def rows(run, st):
    c = ponding.compare(run, st, "L1", geometry=False)
    cur = {f["id"]: f for f in ponding.compute(run, st, geometry=False, pond_scope="state")["feats"]}
    out = []
    for i, f in cur.items():
        m = c["match"].get(i) or []
        if not m:
            out.append(["新出现", i, "", "", "", f["hmax_m"], "", "", f["vol_peak_m3"], "", f["lon"], f["lat"], "L1 时这里没有积水点"]); continue
        b = c["ref_brief"][m[0][0]]
        dh = round(f["hmax_m"] - b["hmax_m"], 2); dv = f["vol_peak_m3"] - b["vol_peak_m3"]
        kind = "仍有积水（变浅）" if dh < -0.005 else ("仍有积水（变深）" if dh > 0.005 else "仍有积水（水深不变）")
        out.append([kind, i, "、".join(x for x, _ in m), "、".join(str(a) for _, a in m), b["hmax_m"], f["hmax_m"], dh, b["vol_peak_m3"], f["vol_peak_m3"], dv,
                    f["lon"], f["lat"], "与多个 L1 积水点重叠时，水深 / 积水量按重叠最大的一个比" if len(m) > 1 else ""])
    for g in c["gone"]:
        out.append(["已消除", "", g["id"], "", g["hmax_m"], "", "", g["vol_peak_m3"], "", "", g["lon"], g["lat"], "L1 时有积水，本状态这里已没有积水点"])
    return out, c["summary"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import export_rda_61_full as F61
    import export_rda_baseline as B
    import export_delivery_rda as X
    jobs = [(s, ROOT / "out/交付/RDA_61mm1h_全套成果_推荐方案_20261027" / fold, F61.DIRN) for s, (fold, _) in F61.STORMS.items()]
    jobs += [(s, ROOT / "out/交付/RDA_9场雨_L1_L2_L2planned_20261027" / f"RDA_{B.label(s)}", X.NM) for s in B.STORMS]
    for storm, out, dirn in jobs:
        for st, sub in dirn.items():
            d = out / sub / "积水点"
            if st == "L1" or not d.exists():
                continue
            r, s = rows(f"RDA_{storm}", st)
            with open(d / "积水点_与L1对照_d0.15.csv", "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f); w.writerow(HEAD); w.writerows(r)
            print(f"{storm} {st}: L1 {s['n_ref']} 处 → 已消除 {s['n_gone']}，仍有积水 {s['n_kept_ref']}；本状态 {s['n_cur']} 处（新出现 {s['n_new']}）")


if __name__ == "__main__":
    main()
