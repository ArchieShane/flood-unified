"""RD-A L2+suggested 方案的全寿命成本（NPV）对比表（DEC-10：只进报告，不进优化 / 剪枝；2026-10-25）。

1. 造价拆分：按候选生成时的同一套公式和单价（tools/l2s_candidates_v3.py 的 PH、fm_rate_usd、outfall_cost_usd，
   tools/l2s_sumps_v3.py 的取水井单价）把每项措施的建设费拆成：
     地下池池体 / 泵站土建（站房基础、湿井、取水井）/ 机电（水泵、排空泵）/ 压力管（含排气阀）/ 重力连接管 / 排海口；
   拆完逐项与原造价核对（差额记入「重力连接管」——它在原公式里按每口井的管径逐段求和，没单独存下来；核对差额见 check）。
2. NPV（DEC-10 占位参数，60 年，5 %，另算 3 % / 8 %）：
     建设费 + 每年运维（机电 4 %、土建 0.5 %、池体清淤 1 % + 土建 0.5 %、压力管 / 重力管 0.2 %、排海口 0.5 %）
     + 电费（每年运行 100 h，0.082 USD/kWh，线到水效率 0.65）+ 更换（机电 15 年一换、土建 / 排海口 50 年一换、管道 60 年不换）；不计残值。
   自定（已告知用户）：泵站扬程统一按 10 m 估电费（湿井到排口的静扬程 + 管损，未逐站算）；DEC-10 的「仪表 5 年更换」没有单列仪表造价，未计；
   重力进水池的排空泵电费忽略（每年运行时间短、功率小）。
输出：out/rda/npv.json（被 tools/rda_report.py、tools/rda_package.py 读）。
用法：python tools/rda_npv.py"""
from __future__ import annotations
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import l2s_candidates_v3 as V3   # noqa: E402
import l2s_sumps_v3 as SU        # noqa: E402

OPT3 = ROOT / "out/l2opt/RDA_96C"
OUT = ROOT / "out/rda/npv.json"
PH = V3.PH
PARTS = {"tank": "地下池池体", "civil": "泵站土建（站房、湿井、取水井）", "mech": "机电（水泵、排空泵）", "fm": "压力管（含排气阀）", "gpipe": "重力连接管", "outfall": "排海口"}
CLS = {"tank": "rc_structure", "civil": "rc_structure", "mech": "mech_elec", "fm": "pipes", "gpipe": "pipes", "outfall": "rc_structure"}
HEAD_M = 10.0


def dec10():
    d = json.loads((ROOT / "swbrain/ontology/constraints/decisions.json").read_text(encoding="utf-8"))
    return next(x for x in d["items"] if x["id"] == "DEC-10")["value"]


def pumps_cost(n, qe):
    return n * PH["pump_per_036"] * (qe / 0.361) ** 0.7


def split_station(c, n_intakes=0):
    """泵站 → 压力管 → 排海口（ps_cluster / surface_ps）；c 要有 n_pumps、pump_each_m3s、wetwell_m3、pipe_m、main_d_m、n_mains、outfall_new。"""
    L, dn, m = float(c["pipe_m"]), float(c["main_d_m"]), int(c.get("n_mains") or 1)
    fm = V3.fm_rate_usd(dn)[0] * L * m + PH["air_valve"] * max(1, int(L / 1000))
    of = V3.outfall_cost_usd(dn)[0] if c.get("outfall_new", True) else 0.0
    civil = PH["station_base"] + float(c["wetwell_m3"]) * 3 * PH["rc_wetwell_m3"] + SU.PH_INTAKE * n_intakes
    mech = pumps_cost(int(c["n_pumps"]), float(c["pump_each_m3s"]))
    p = {"civil": civil, "mech": mech, "fm": fm, "outfall": of}
    p["gpipe"] = float(c["cost_usd"]) - sum(p.values())
    return p


def split(m, c):
    k = m["kind"]
    if k == "tank_gravity":
        p = {"tank": float(c["pond_vol_m3"]) * PH["tank_m3"], "mech": PH["drain_pump"]}
        p["gpipe"] = float(m["cost_usd"]) - sum(p.values())
    elif k == "tank_pumped":
        L, dn, mm = float(c["pipe_m"]), float(c["main_d_m"]), int(c.get("n_mains") or 1)
        p = {"tank": float(c["pond_vol_m3"]) * PH["tank_m3"], "civil": PH["station_base"] + float(c["wetwell_m3"]) * 3 * PH["rc_wetwell_m3"],
             "mech": pumps_cost(int(c["n_pumps"]), float(c["pump_each_m3s"])) + PH["drain_pump"],
             "fm": V3.fm_rate_usd(dn)[0] * L * mm + PH["air_valve"] * max(1, int(L / 1000))}
        p["tank"] += float(m["cost_usd"]) - sum(p.values())          # 这一类没有重力连接管；差额只是字段取整（几百美元），记入池体
    elif k == "ps_cluster":
        p = split_station({**c, "cost_usd": m["cost_usd"]})
    else:
        raise ValueError(k)
    return p


def scheme_parts(with_sumps):
    var = json.loads((OPT3 / "variant_S96D1_OPTC.json").read_text(encoding="utf-8"))
    cand = {c["id"]: c for c in json.loads((OPT3 / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    rows = []
    for m in var["measures"]:
        p = split(m, cand[m["id"]]); q = float(cand[m["id"]].get("overflow_rate_m3s") or 0) if m["kind"] != "tank_gravity" else 0.0
        rows.append({"id": m["id"], "kind": m["kind"], "cost_usd": float(m["cost_usd"]), "parts": p, "q_pump_m3s": q})
    if with_sumps:
        for s in json.loads((OPT3 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))["stations"]:
            p = split_station(s, n_intakes=len(s.get("intakes") or []))
            rows.append({"id": s["id"], "kind": "surface_ps", "cost_usd": float(s["cost_usd"]), "parts": p, "q_pump_m3s": float(s["q_m3s"])})
    return rows


def npv(rows, D, r):
    om = D["annual_om_pct_of_capex"]; rep = D["replace_years"]; T = int(D["period_years"])
    tot = {k: sum(x["parts"].get(k, 0.0) for x in rows) for k in PARTS}
    capex = sum(tot.values())
    annual_om = (tot["mech"] * om["pump_station_mech_elec"] + (tot["civil"] + tot["outfall"] * 0) * om["rc_structure"]
                 + tot["tank"] * (om["rc_structure"] + om["tank_cleaning"]) + tot["fm"] * om["pressure_pipe"] + tot["gpipe"] * om["gravity_pipe"]
                 + tot["outfall"] * om["outfall"])
    kw = sum(1000 * 9.81 * x["q_pump_m3s"] * HEAD_M / D["pump_wire_to_water_eff"] / 1000 for x in rows)
    annual_energy = kw * D["pump_run_hours_per_year"] * D["electricity_usd_per_kwh"]
    pv = lambda t: 1 / (1 + r) ** t
    ann = sum(pv(t) for t in range(1, T + 1))
    rep_me = sum(tot["mech"] * pv(t) for t in range(rep["mech_elec"], T, rep["mech_elec"]))
    rep_rc = sum((tot["tank"] + tot["civil"] + tot["outfall"]) * pv(t) for t in range(rep["rc_structure"], T, rep["rc_structure"]))
    return {"rate": r, "capex": round(capex), "om_per_year": round(annual_om), "energy_per_year": round(annual_energy), "pumps_kw": round(kw),
            "pv_om": round(annual_om * ann), "pv_energy": round(annual_energy * ann), "pv_replace": round(rep_me + rep_rc),
            "npv": round(capex + (annual_om + annual_energy) * ann + rep_me + rep_rc)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    D = dec10(); res = {"basis": {"dec10": D, "head_m": HEAD_M, "parts": PARTS, "class": CLS,
                                  "notes": ["只进报告，优化和剪枝仍按建设费（DEC-10，用户 2026-10-23）", "运维比例、运行小时、电价、效率为占位值，待客户给正式值",
                                            "泵站扬程统一按 10 m 估电费；仪表 5 年更换未单列；不计残值；重力池排空泵电费忽略"]}, "schemes": {}}
    for key, lab, ws in (("OPTC", "v3（无地面泵站）", False), ("OPTCS2", "L2+suggested（v3 + 7 座地面泵站）", True)):
        rows = scheme_parts(ws)
        chk = max(abs(sum(x["parts"].values()) - x["cost_usd"]) for x in rows)
        neg = [x["id"] for x in rows if x["parts"].get("gpipe", 0) < -1]
        tot = {k: round(sum(x["parts"].get(k, 0.0) for x in rows)) for k in PARTS}
        res["schemes"][key] = {"label": lab, "n": len(rows), "parts": tot, "check_max_abs_diff_usd": round(chk, 2), "negative_gpipe": neg,
                               "npv": {str(r): npv(rows, D, r) for r in [D["discount_rate"]] + list(D["discount_sensitivity"])}}
        n5 = res["schemes"][key]["npv"][str(D["discount_rate"])]
        print(f"{key}: 建设 {n5['capex'] / 1e6:.1f} M，运维 {n5['om_per_year'] / 1e6:.2f} M/年，电费 {n5['energy_per_year'] / 1e3:.0f} k/年，"
              f"NPV(5%) {n5['npv'] / 1e6:.1f} M；拆分核对最大差 {chk:.2f} USD；重力管为负的：{neg}")
        print("   ", {PARTS[k]: f"{v / 1e6:.1f}" for k, v in tot.items()})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", OUT)


if __name__ == "__main__":
    main()
