"""把 RD-A 的 L3 结果（l3*.json、routes.json）里的积水点编号统一成页面「积水点」面板（/api/ponding）的编号（2026-10-28）。

原因：rda_l3 先扣评估剔除区（DTM 填 0 / 私人地块坑）再判片编号，和积水点面板错开——同一态、同一页面上两个 SIM-L1-029 指两处地方。
做法：每个 L3 点（片的最深点）落在积水点面板哪一片，就用那一片的编号（swbrain.ponding.locate，同一运行、同一态、缺省阈值、本态塘）。
只改编号，不改任何数值。改前逐文件检查一一对应（无重号、无落空），不满足就整份跳过。原文件备份到 out/backup/l3_ids_<时间>/。
用法：python tools/l3_align_ids.py [--dry] [--deliveries]
  --deliveries  同时刷新 2026-10-27 两个交付包里的 L3承接与派单.csv / .geojson / 未进入L3清单的积水点.csv（export_delivery_rda.ponding_extra）
"""
from __future__ import annotations
import argparse
import json
import pathlib
import shutil
import sys
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from swbrain import ponding  # noqa: E402


def remap(o, m):
    """递归：等于旧编号的字符串值 / 字典键换成新编号；字符串里出现的旧编号（如导航「到达 SIM-L1-029」）也换。"""
    if isinstance(o, dict):
        return {m.get(k, k): remap(v, m) for k, v in o.items()}
    if isinstance(o, list):
        return [remap(v, m) for v in o]
    if isinstance(o, str):
        if o in m:
            return m[o]
        if "SIM-" in o:
            import re
            return re.sub(r"SIM-[A-Za-z0-9]+-\d{3}", lambda x: m.get(x.group(0), x.group(0)), o)
    return o


def mapping(run, st, pts):
    new = ponding.locate(run, st, [(p["lon"], p["lat"]) for p in pts])
    old = [p["id"] for p in pts]
    if None in new or any(n > 1 for n in Counter(new).values()):
        return None
    return {a: b for a, b in zip(old, new)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--dry", action="store_true"); ap.add_argument("--deliveries", action="store_true"); a = ap.parse_args()
    bak = ROOT / "out/backup" / f"l3_ids_{time.strftime('%Y%m%d_%H%M%S')}"
    maps = {}                                                    # (storm, state) → 映射（routes.json 用）
    for d in sorted((ROOT / "console/data").glob("flood2d_rda_*")):
        storm = d.name.split("_")[-1].upper(); run = f"RDA_{storm}"
        for f in sorted(d.glob("l3*.json")):
            J = json.loads(f.read_text(encoding="utf-8")); ch = 0; skip = []
            for st, S in (J.get("states") or {}).items():
                try:
                    m = mapping(run, st, S["points"])
                except (FileNotFoundError, ValueError, RuntimeError) as e:
                    skip.append(f"{st}（{e}）"); continue
                if m is None:
                    skip.append(f"{st}（不是一一对应）"); continue
                maps.setdefault((storm, st), m)
                m = {k: v for k, v in m.items() if k != v}
                if m:
                    J["states"][st] = remap(S, m); ch += len(m)
            if ch:
                J["id_basis"] = "积水点编号与页面积水点面板（/api/ponding，缺省阈值、本态塘）一致；2026-10-28 由 tools/l3_align_ids.py 统一"
            print(f"{d.name}/{f.name}: 改 {ch} 个编号" + (f"；跳过 {skip}" if skip else ""))
            if ch and not a.dry:
                (bak / d.name).mkdir(parents=True, exist_ok=True); shutil.copy2(f, bak / d.name / f.name)
                f.write_text(json.dumps(J, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        rf = d / "routes.json"
        if rf.exists():
            R = json.loads(rf.read_text(encoding="utf-8")); ch = 0
            for st, S in (R.get("states") or {}).items():
                m = {k: v for k, v in (maps.get((storm, st)) or {}).items() if k != v}
                if m:
                    R["states"][st] = remap(S, m); ch += 1
            if ch and not a.dry:
                (bak / d.name).mkdir(parents=True, exist_ok=True); shutil.copy2(rf, bak / d.name / rf.name)
                rf.write_text(json.dumps(R, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            if ch:
                print(f"{d.name}/routes.json: {ch} 个态的导航文字已换编号")
    if a.deliveries and not a.dry:
        refresh_deliveries()
    print("备份 →", bak if bak.exists() else "（无改动）")


def refresh_deliveries():
    import export_delivery_rda as X
    import export_rda_baseline as B
    import export_rda_61_full as F61
    ex = X.exclude_zones()
    jobs = []
    for storm, (fold, _) in F61.STORMS.items():
        jobs.append((storm, ROOT / "out/交付/RDA_61mm1h_全套成果_推荐方案_20261027" / fold, F61.DIRN))
    for storm in B.STORMS:
        jobs.append((storm, ROOT / "out/交付/RDA_9场雨_L1_L2_L2planned_20261027" / f"RDA_{B.label(storm)}", X.NM))
    for storm, out, dirn in jobs:
        L3 = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/l3.json").read_text(encoding="utf-8"))
        for st in L3["states"]:
            d = out / dirn[st] if st in dirn else None
            if d is not None and (d / "积水点").exists():
                X.ponding_extra(st, d, L3, ex)
                print("  刷新", d.relative_to(ROOT))


if __name__ == "__main__":
    main()
