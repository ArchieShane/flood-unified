"""一次计算结果实际用了哪些设施，按客户 L1 / L2 定义重新归类（2026-10-21，首用于 adm_city_250m_v2）。

读结果目录 common/ 下的 pumps_built.json、storage_objects.json、pumps_not_built.csv 和 L1 的 .inp（取节点坐标），
每个设施和我们的参照对上，给出「我们的级别 + 状态」：
  - 参照与判读规则同 tools/facility_register.py：GDB PS_PUMP、台账塘、Existing Storage、GDB 渗井 = L1；
    Parsons 改造后模型的 PO 塘、PMP-<数字> 泵 = L2（雨前临时措施），状态按「泵—压力管—塘」整套挂客户热点表；
    标签带泵站号（RD09、RD-01、RD_I_Axial…）= 现状泵站 = L1
  - 一个泵组（共用吸水井、流量相加）里既有现状泵又有 PMP 临时泵的，标「混合」
  python tools/run_facility_audit.py <结果里任一情景目录> [--out out/<名字>]
产物：facilities_used.csv（utf-8-sig，每行一个设施）、summary.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import facility_register as F  # noqa: E402
from r017_l2_audit import inp_section  # noqa: E402

NEAR_PU_M = 60.0          # 他们的泵组吸水节点 ↔ Parsons 泵点
NEAR_POND_M = 50.0


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def level_of_label(lab):
    """泵标签 → 级别；PMP_10 / PMP-10A 这类是 Parsons 临时泵，带泵站号的是现状站"""
    s = str(lab).strip()
    if s.lower() in ("nan", "", "none"):
        return None
    if (F.STATION_LABEL.search(s) or re.search(r"(axial|mixed|duty|stdby|assist|main)", s, re.I)
            or re.search(r"\bRD[\s_\-]*[A-Z]{1,2}\d*(\b|_)", s) or re.search(r"\bPS-|SW-IS", s) or re.match(r"^(LS-|PMP-LS)", s)):
        return "L1"                                        # 泵站号带字母（RD B_1、RD-T-1、PMP-RD K1）、GDB 式编号、提升站 LS
    if F.TEMP_LABEL.match(s.replace("_", "-")) or re.match(r"^TP-\d+$", s):
        return "L2"                                        # PMP-<数字>；TP-<数字> 按 Temporary Pump 推断
    return None


def main():
    import geopandas as gpd
    from shapely.geometry import Point
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=str(F.ROOT / "out" / "run_facility_audit"))
    a = ap.parse_args()
    R = Path(a.run_dir)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    refs = F.load_refs()
    hs = F.load_hotspots()
    planned = hs[hs.idd_planned.str.lower().str.match(r"^(yes|assess)")]
    cache = {}

    def unit_status(u):
        if u not in cache:
            mem = refs["unit_members"].get(u)
            cache[u] = F.link_members(mem, planned, parsons=True)[:2] if mem else ("待确认", "")
        return cache[u]

    inp = glob.glob(str(R / "L1_*" / "1D" / "*.inp"))[0]
    xy = {r[0]: (float(r[1]), float(r[2])) for r in inp_section(inp, "COORDINATES")}
    pu, ps = refs["pu"], refs["ps"]
    pu_norm = [norm(x) for x in pu.label]
    rows = []

    # 泵组
    for g in json.loads((R / "common" / "pumps_built.json").read_text(encoding="utf-8"))["pumps_built"]:
        pt = Point(xy[g["from"]]) if g["from"] in xy else None
        labs = str(g["unitid"]).split("+")
        dpu = pu.geometry.distance(pt) if pt is not None else None
        dps = ps.geometry.distance(pt) if pt is not None else None
        gdb = (ps.UNITID[dps.idxmin()], ps.PUMP_STATION_ID[dps.idxmin()], float(dps.min())) if dps is not None and dps.min() <= F.NEAR_PS_M else None
        members, levels, units = [], [], set()
        for lab in labs:
            lv = level_of_label(lab)
            hit = None
            if dpu is not None:
                cand = [i for i in pu.index if dpu[i] <= NEAR_PU_M and (lab.lower() == "nan" or pu_norm[i] == norm(lab))]
                if cand:
                    hit = min(cand, key=lambda i: dpu[i])
            if lv is None and hit is not None:
                lv = level_of_label(pu.label[hit])
            if lv is None and gdb:
                lv = "L1"
            lv = lv or "待确认"
            if lv == "L2" and hit is not None:
                units.add(refs["unit_of"][("pump", pu.model[hit], pu.label[hit])])
            levels.append(lv)
            members.append(f"{lab}{'（' + pu.model[hit] + ':' + pu.label[hit] + '）' if hit is not None and lab.lower() == 'nan' else ''}")
        lv = levels[0] if len(set(levels)) == 1 else "混合（" + "/".join(sorted(set(levels))) + "）"
        st = "；".join(sorted({unit_status(u)[0] for u in units})) if units else ("现状" if lv == "L1" else "")
        link = " | ".join(unit_status(u)[1] for u in sorted(units))[:400]
        rows.append({"facility": g["id"], "type": "泵组", "their_use": "L1 里建了", "their_basis": f"{g['q_m3s'] * 1000:.0f} L/s，{g['n']} 台；{g['how']}",
                     "members": " + ".join(members), "our_level": lv, "our_status": st,
                     "evidence": (f"GDB {gdb[0]}（站号 {gdb[1]}，{gdb[2]:.0f} m）" if gdb else "100 m 内无 GDB 泵站"), "hotspot_link": link,
                     "x": round(pt.x, 1) if pt is not None else "", "y": round(pt.y, 1) if pt is not None else ""})

    # 没建的泵
    for r in csv.DictReader(open(R / "common" / "pumps_not_built.csv", encoding="utf-8-sig")):
        lab = r["label"] or r["unitid"]
        lv = level_of_label(lab) or ("L1" if r["source"].lower().startswith("gdb") or not r["company"] else "待确认")
        rows.append({"facility": lab, "type": "泵（没建）", "their_use": "没建", "their_basis": r["reason"],
                     "members": f"{r['company']} / {r['catchment']}", "our_level": lv, "our_status": "现状" if lv == "L1" else "",
                     "evidence": f"source={r['source']}，PUMP_FLOW={r['pump_flow_Lps']}", "hotspot_link": "", "x": "", "y": ""})

    # 蓄水体（塘 / 渗井）
    pp, ex, led = refs["pp"], refs["ex"], refs["ledger"]
    for s in json.loads((R / "common" / "storage_objects.json").read_text(encoding="utf-8"))["ponds"]:
        pt = Point(s["x"], s["y"])
        in_ex = ex is not None and float(ex.geometry.distance(pt).min()) <= NEAR_POND_M
        in_led = float(led.geometry.distance(pt).min()) <= NEAR_POND_M
        if s["source"] == "SOAKAWAY":
            lv, st, ev, link = "L1", "现状", "GDB SOAKAWAY", ""
        elif in_led or in_ex or s["source"] == "GDB":
            lv, st = "L1", "现状"
            ev = "客户台账塘" if in_led else ("Existing Storage 图层" if in_ex else "GDB 塘")
            link = ""
        else:
            d = pp.geometry.distance(pt)
            i = d.idxmin()
            if d[i] <= NEAR_POND_M:
                u = refs["unit_of"][("pond", pp.model[i], pp.label[i])]
                lv, (st, link) = "L2", unit_status(u)
                ev = f"Parsons 改造后模型 {pp.model[i]}:{pp.label[i]}（不在台账 / Existing Storage）"
            else:
                lv, st, ev, link = "待确认", "", "参照里找不到", ""
        rows.append({"facility": s["unitid"], "type": "渗井" if s["source"] == "SOAKAWAY" else "塘", "their_use": "L1 里建了",
                     "their_basis": f"{s['volume_m3']:,} m³，深 {s['depth_m']} m，{s['basis']}{'（估算）' if s['estimated'] else ''}；{s['wiring']}",
                     "members": s["source"], "our_level": lv, "our_status": st, "evidence": ev, "hotspot_link": link[:400],
                     "x": s["x"], "y": s["y"]})

    # 我们算 L1、他们没用上的已建成塘（台账 + Existing Storage），只看他们计算域内的
    dom = gpd.read_file(R / "common" / "domain.geojson").to_crs(32640).union_all()
    used = gpd.GeoSeries([Point(s["x"], s["y"]) for s in json.loads((R / "common" / "storage_objects.json").read_text(encoding="utf-8"))["ponds"]], crs=32640)
    for name, G, col in (("台账塘", led, "unitid"), ("Existing Storage", ex, "label")):
        for _, p in G[G.geometry.intersects(dom)].iterrows():
            if float(used.distance(p.geometry).min()) > NEAR_POND_M:
                c = p.geometry.representative_point()
                rows.append({"facility": f"{name}:{p[col]}", "type": "塘", "their_use": "没用（不在他们的数据里或被去掉）", "their_basis": "",
                             "members": name, "our_level": "L1", "our_status": "现状", "evidence": name, "hotspot_link": "",
                             "x": round(c.x, 1), "y": round(c.y, 1)})

    cols = ["type", "facility", "their_use", "our_level", "our_status", "their_basis", "members", "evidence", "hotspot_link", "x", "y"]
    with open(out / "facilities_used.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    summ = {}
    for r in rows:
        k = f"{r['type']}｜{r['their_use']}｜{r['our_level']}"
        summ.setdefault(k, {"n": 0, "status": {}})
        summ[k]["n"] += 1
        for s in (r["our_status"] or "—").split("；"):
            summ[k]["status"][s] = summ[k]["status"].get(s, 0) + 1
    (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in sorted(summ.items()):
        print(f"{k:50} {v['n']:>4}  {v['status']}")
    print(f"→ {out}（{len(rows)} 行）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
