"""RD-A L3 承接清单：各态二维残余积水点 → 自然退干能否赶上时限 → 要几台移动泵 / 罐车、能否交给 L3（2026-10-24）。

复用第三道防线的现成部件：
- 积水点：swbrain.ponding.extract（二维结果逐片连通，阈值 0.15 m / 100 m² / 0.5 h），剔除 L2 塘占地与评估剔除区（DTM 填 0、私人地块坑）；
- 过程线：片内逐帧 Σ水深×面积；swbrain.replay 把它拆成「净入流 + 自然去水」，加泵重演退干时刻；
- 优先级 / 处置时限：swbrain.dispatch.priority（重点单位 300 m、客户热点重要度，时限取小者）；
- 排放去处：同 dispatch.feasibility，1.5 km 内有出水口 / 泵站 / 渗井终点才派泵，否则只能罐车；
- 泵型：客户预案 A 车队（exchange/fleet.plan_a.json）的 12 英寸泵 700 m³/h，每点最多 3 台（[dispatch.v2] max_units_per_point）。
自定（已告知用户）：时限从雨停起算；泵在积水点成形后 trt_min（120 min）到位；二维视界之后按末帧水量不再自然退。
判定：自然退干在时限内 → 不需 L3；3 台泵 / 6 辆罐车内能赶上 → L3 可承接；否则 → 超出 L3 单点能力，应由 L2 解决；
关键点（隧道 / 医院 / 客户 Very High 且深 ≥0.30 m）另标「建议 L2」。
用法：python tools/rda_l3.py [--storms S96D1,S96D24] [--states L1,L2,L2P,OPTCS2]"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import tomllib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from swbrain import ponding, replay as RP, dispatch as D1, hotspots as hsm, refdata   # noqa: E402
from l2opt_eval2d import load, pond_cells   # noqa: E402

EXCLUDE = ROOT / "out/l2opt/RDA_96C/exclude_OPTCS.geojson"
OUT = ROOT / "out/rda"
STATES = ["L1", "L2", "L2P", "OPTCS2"]
STORMS = ["S33D1", "S33D2", "S33D24", "S61D1", "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]
CRIT_TARGET = {"Very High": 120, "High": 240, "Medium": 600, "Low": 2160}     # 客户预案 A / ADM 993 四级时限（同 floodpoints.TARGET_MIN）
MAX_UNITS, MAX_TANKERS = 3, 6
SITEDATA = ROOT / "out/rda/sitedata.gpkg"          # Makani 空间数据（UTM 40N），判积水碰到了什么
KEY_KINDS = ("hospital", "ambulance", "tunnel", "metro")
LEVELS = ["无暴露", "中", "高", "关键"]
CLIENT_LEVEL = {"Very High": "关键", "High": "高", "Medium": "中", "Low": "中"}    # 客户重要度只抬高、不降低
_EXPO: dict = {}


def exposure_masks(xy):
    """网格单元中心落在建筑（外扩 3 m）/ 路面 / 变电站 / 已建成地块里的掩膜与建筑编号；同一网格只算一次。"""
    k = len(xy)
    if k not in _EXPO:
        import geopandas as gpd
        import shapely
        rd = gpd.read_file(SITEDATA, layer="road_surface").geometry
        sbl = gpd.read_file(SITEDATA, layer="substations")
        sb = sbl[sbl["type"] == "STRAN"].geometry                                   # 输电变电站（约 3000 m²）
        sd = sbl[sbl["type"].isin(["S/S", "P/U", "SW/S"])].geometry.buffer(3.0)      # 配电变电站；配电亭 / 环网柜 / 冷水机房不计
        pl = gpd.read_file(SITEDATA, layer="plots"); pl = pl[pl["construction_status"] == "Constructed"].geometry
        bd = gpd.read_file(SITEDATA, layer="buildings").geometry.buffer(3.0).reset_index(drop=True)
        P = shapely.points(xy[:, 0], xy[:, 1])
        bix = np.full(k, -1)
        hit = shapely.STRtree(bd.values).query(P, predicate="within")
        bix[hit[0]] = hit[1]
        _EXPO[k] = {"road": shapely.contains_xy(shapely.union_all(rd.values), xy[:, 0], xy[:, 1]),
                    "subst": shapely.contains_xy(shapely.union_all(sb.values), xy[:, 0], xy[:, 1]),
                    "sdist": shapely.contains_xy(shapely.union_all(sd.values), xy[:, 0], xy[:, 1]),
                    "built": shapely.contains_xy(shapely.union_all(pl.values), xy[:, 0], xy[:, 1]), "bldg": bix}
    return _EXPO[k]


def exposure(c, hm, area, E):
    """一个积水片碰到了什么：路面 / 已建成地块面积（≥0.15、≥0.30 m）、碰到的建筑数、是否淹到变电站。"""
    g15, g30 = hm[c] >= 0.15, hm[c] >= 0.30
    a = area[c]
    return {"road15_m2": round(float(a[g15 & E["road"][c]].sum())), "road30_m2": round(float(a[g30 & E["road"][c]].sum())),
            "built30_m2": round(float(a[g30 & E["built"][c]].sum())),
            "bldg15": int(len(set(E["bldg"][c][g15 & (E["bldg"][c] >= 0)]))), "bldg30": int(len(set(E["bldg"][c][g30 & (E["bldg"][c] >= 0)]))),
            "subst": bool((g15 & E["subst"][c]).any()), "sdist30": bool((g30 & E["sdist"][c]).any())}


def consequence(ex, top_kind, crit):
    """后果等级（自定，已告知用户 2026-10-24）：
    关键 = 300 m 内有医院 / 急救 / 隧道 / 地铁且路面或建筑处 ≥0.30 m，或淹到输电变电站（STRAN）；
    高   = ≥0.30 m 的路面 ≥200 m²（轻型车过不去），或 ≥0.30 m 碰到建筑，或配电变电站（S/S、P/U、SW/S）处 ≥0.30 m；
    中   = ≥0.15 m 的路面 ≥100 m²，或碰到建筑，或已建成地块里 ≥0.30 m ≥100 m²；
    无暴露 = 只在空地 / 未建地块 / 公园，没碰到路面和建筑 → 只监测、不派 L3。
    客户历史热点的重要度只抬高等级（Very High → 关键，High → 高，Medium / Low → 中），不被模型降低。"""
    why = []
    if (top_kind in KEY_KINDS and (ex["road30_m2"] > 0 or ex["bldg30"] > 0)) or ex["subst"]:
        lv = "关键"; why.append("淹到输电变电站" if ex["subst"] else f"{top_kind} 300 m 内，路面 / 建筑处 ≥0.30 m")
    elif ex["road30_m2"] >= 200 or ex["bldg30"] > 0 or ex["sdist30"]:
        lv = "高"; why.append(f"≥0.30 m 路面 {ex['road30_m2']} m²" if ex["road30_m2"] >= 200 else (f"≥0.30 m 碰到 {ex['bldg30']} 栋建筑" if ex["bldg30"] else "配电变电站处 ≥0.30 m"))
    elif ex["road15_m2"] >= 100 or ex["bldg15"] > 0 or ex["built30_m2"] >= 100:
        lv = "中"; why.append(f"≥0.15 m 路面 {ex['road15_m2']} m²" if ex["road15_m2"] >= 100 else (f"碰到 {ex['bldg15']} 栋建筑" if ex["bldg15"] else "已建成地块内 ≥0.30 m"))
    else:
        lv = "无暴露"; why.append("只在空地 / 未建地块 / 公园")
    if crit in CLIENT_LEVEL and LEVELS.index(CLIENT_LEVEL[crit]) > LEVELS.index(lv):
        lv = CLIENT_LEVEL[crit]; why.append(f"客户热点重要度 {crit}")
    return lv, "；".join(why)


def points(name, st, exm):
    z, m = load(name, st)
    xy = z["xy"].astype(float); area = z["area"].astype(float)
    pm = pond_cells(m, xy) | exm
    feats, _, _ = ponding.extract(xy, area, z["elev"].astype(float), z["hmax"].astype(float), z["frames"], z["t_h"], pond=pm, state=st)
    E = exposure_masks(xy); hm = z["hmax"].astype(float)
    fr = z["frames"].astype(np.float32); t_min = z["t_h"].astype(float) * 60
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    hid, hxy = ponding._hotspots((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()))
    for f in feats:
        c = f.pop("_cells")
        f["expo"] = exposure(c, hm, area, E)
        f["lon"], f["lat"] = [round(v, 6) for v in T.transform(*f.pop("deep_xy"))]; f.pop("cen_xy", None)
        d = cKDTree(xy[c]).query(hxy)[0] if len(hid) else []
        f["near"] = [[hid[i], round(float(d[i]))] for i in np.argsort(d) if d[i] <= ponding.R_NEAR] if len(hid) else []
        V = (fr[:, c] * area[c]).sum(1)
        q = np.diff(V, prepend=0.0) / np.diff(t_min, prepend=0.0).clip(1e-9) * 60
        f["hyd"] = [{"t_min": round(float(a), 1), "volume_m3": round(float(v), 1), "inflow_m3h": round(float(max(e, 0)), 1)} for a, v, e in zip(t_min, V, q)]
        f["t_form_min"] = float(t_min[np.argmax(V > RP.MIN_VOL_M3)]) if (V > RP.MIN_VOL_M3).any() else None
    # 编号对齐页面积水点面板（2026-10-28）：这里先扣剔除区再判片，自带的序号会和 /api/ponding 错开（同一页上两个 SIM-L1-029 指两处）；
    # 按最深点落在面板哪一片改用那一片的编号。对不上（落空 / 重号）时保留自带编号并加后缀 x，提示人工看
    try:
        new = ponding.locate(name, st, [(f["lon"], f["lat"]) for f in feats])
        from collections import Counter
        cnt = Counter(n for n in new if n)
        for f, n in zip(feats, new):
            f["id"] = n if (n and cnt[n] == 1) else f"{f['id']}x"
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"  {name} {st}：积水点编号未能对齐面板（{e}），保留 L3 自带编号")
    return feats, float(t_min[-1])


def context():
    """各场雨、各态共用的输入：配置、客户热点、重点单位、预案 A 车队、排放去处、评估剔除区。"""
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    fleet = json.loads((ROOT / cfg["exchange"]["dir"] / "fleet.plan_a.json").read_text(encoding="utf-8"))
    cs = pd.read_parquet(ROOT / "out/state/cs.parquet")
    main_sc = list(cfg["model"]["scenarios"])[0]
    ok = cs[cs["terminal_kind"].isin(["出水口", "泵站", "渗井"]) & cs["lon"].notna()]; ok = ok[ok[f"over_share_{main_sc}"].fillna(0) < 0.8]   # 排放去处（同 dispatch.feasibility）
    import shapely
    from shapely.geometry import shape
    from scipy.spatial import cKDTree
    k = np.cos(np.radians(24.4)) * 111000
    return {"cfg": cfg, "trt": float(cfg["dispatch"]["manual_levels"]["trt_min"]), "H": hsm.load(ROOT / cfg["hotspots"]["file"]), "ref": refdata.load_cache(),
            "fleet": fleet, "cs": cs, "rate": {m["model"]: float(m["rate_m3h"]) for m in fleet["models"]}, "duty": {m["model"]: float(m.get("duty_aed_h") or 0) for m in fleet["models"]},
            "tk": D1.tanker_params(cfg), "ex": shapely.union_all([shape(f["geometry"]) for f in json.loads(EXCLUDE.read_text(encoding="utf-8"))["features"]]),
            "kx": k, "outlets": cKDTree(np.c_[ok["lon"].values * k, ok["lat"].values * 111000])}


def rain_end_min(s):
    return float(json.loads((ROOT / f"out/rda_inputs_{s}_L1.json").read_text(encoding="utf-8"))["dur_h"]) * 60


def assess(s, st, C):
    """一场雨、一个态 → (积水点 feats, 逐点判定 rows, 二维视界 min)；没有二维结果返回 None。"""
    import shapely
    name = f"RDA_{s}"
    if not (ponding.c2d() / f"{name}_{st}_2d.npz").exists():
        return None
    cfg, H, rate, duty, tk, trt = C["cfg"], C["H"], C["rate"], C["duty"], C["tk"], C["trt"]
    rain_end = rain_end_min(s)
    xy0 = np.load(ponding.c2d() / f"{name}_{st}_2d.npz")["xy"]
    feats, t_end = points(name, st, shapely.contains_xy(C["ex"], xy0[:, 0], xy0[:, 1]))
    if not feats:
        return feats, [], t_end
    fp = {"points": [{"fp_id": f["id"], "lon": f["lon"], "lat": f["lat"], "area_m2": f["area_m2"], "max_depth_m": f["hmax_m"], "volume_m3": f["vol_peak_m3"],
                      "duration_min": f["dur_h"] * 60, "hydrograph": f["hyd"]} for f in feats]}
    P = D1.priority(D1.points_frame(fp), C["ref"]["facilities"], cfg, H)
    # 客户热点时限：200 m 内有客户热点的，按其重要度（取最严）
    crit = {}
    for f in feats:
        cs_ = [H.loc[H.hotspot_id == h, "criticality"].iloc[0] for h, _ in f.get("near", []) if (H.hotspot_id == h).any()] if f.get("near") else []
        crit[f["id"]] = min(cs_, key=lambda c: CRIT_TARGET.get(c, 9e9)) if cs_ else None
    k = C["kx"]
    dd, _ = C["outlets"].query(np.c_[P["lon"].values * k, P["lat"].values * 111000])
    rows = []
    for (i, p), f, dist in zip(P.iterrows(), feats, dd):
        tgt = float(p["target_drain_min"]); c = crit[f["id"]]
        if c in CRIT_TARGET:
            tgt = min(tgt, CRIT_TARGET[c])
        deadline = rain_end + tgt
        nat = RP.replay(f["hyd"], [], t_end=max(t_end, deadline) + 60, series=False)["kpi"]["base"]
        t_dry = nat["t_empty_min"]
        arr = (f["t_form_min"] or 0) + trt
        need, how, rep = 0, "无需", None
        if t_dry is None or t_dry > deadline:
            disch = dist <= float(cfg["dispatch"]["discharge_radius_m"])
            if disch:
                for n in range(1, MAX_UNITS + 1):
                    r = RP.replay(f["hyd"], [{"arrive_min": arr, "rate_m3h": rate["diesel-12in"]}] * n, t_end=max(t_end, deadline) + 60, series=False)["kpi"]
                    if r["pump"]["t_empty_min"] is not None and r["pump"]["t_empty_min"] <= deadline:
                        need, how, rep = n, "12 英寸泵", r; break
            else:
                tr, _ = D1.tanker_rate_m3h(cfg, None)
                for n in (2, 4, MAX_TANKERS):
                    r = RP.replay(f["hyd"], [{"arrive_min": arr, "rate_m3h": tr}] * n, t_end=max(t_end, deadline) + 60, series=False)["kpi"]
                    if r["pump"]["t_empty_min"] is not None and r["pump"]["t_empty_min"] <= deadline:
                        need, how, rep = n, "罐车", r; break
            if not need:
                how = "超出 L3 单点能力"
        lv, lv_why = consequence(f["expo"], p["top_kind"], c)
        key = lv == "关键"                                   # 2026-10-24 起按后果等级判关键点（原来只看 300 m 内有无医院 / 隧道）
        if lv == "无暴露":
            verdict = "无暴露，只监测（不派 L3）"
        elif how == "无需":
            verdict = "自然退干达标，不需 L3"
        elif need:
            verdict = "L3 可承接" + ("（关键点，建议 L2 兜住）" if key else "")
        else:
            verdict = "超出 L3 → 应由 L2 解决"
        hrs = (rep["pump"]["t_empty_min"] - arr) / 60 if rep else 0.0
        model = "diesel-12in" if how == "12 英寸泵" else ("tanker-17m3" if how == "罐车" else None)
        aed = round(need * hrs * (duty.get(model, 0) if model != "tanker-17m3" else float(tk.get("duty_aed_h") or 0))) if need else 0
        rows.append({"storm": s, "state": st, "id": f["id"], "lon": f["lon"], "lat": f["lat"], "hotspots_200m": ",".join(h for h, _ in f.get("near") or []),
                     "criticality": c or "", "top_kind": p["top_kind"], "max_depth_m": f["hmax_m"], "area_m2": f["area_m2"], "area30_m2": f["area30_m2"],
                     "vol_peak_m3": f["vol_peak_m3"], "t_peak_h": f["t_peak_h"], "target_min": tgt, "deadline_h": round(deadline / 60, 2),
                     "t_dry_nat_h": None if t_dry is None else round(t_dry / 60, 2), "residual_at_deadline_m3": round(float(np.interp(deadline, [h["t_min"] for h in f["hyd"]], [h["volume_m3"] for h in f["hyd"]]))),
                     "discharge_m": round(float(dist)), "l3_means": how, "l3_units": need, "arrive_h": round(arr / 60, 2),
                     "t_dry_l3_h": round(rep["pump"]["t_empty_min"] / 60, 2) if rep else None, "pumped_m3": rep["pumped_total_m3"] if rep else 0, "unit_hours": round(need * hrs, 1), "cost_aed": aed,
                     "key_point": key, "verdict": verdict, "horizon_h": round(t_end / 60, 1),
                     "level": lv, "level_why": lv_why, **f["expo"]})
    return feats, rows, t_end


def summarize(s, st, rows):
    g = pd.DataFrame(rows)
    if not len(g):
        return {"storm": s, "state": st, "n": 0}
    ex = g.verdict.str.startswith("无暴露")
    act = g[~ex]                                                  # 无暴露的点只监测，不计 L3 资源
    return {"storm": s, "state": st, "n": len(g), "vol_peak_m3": int(g.vol_peak_m3.sum()), "n_noexpo": int(ex.sum()),
            "levels": {k: int(v) for k, v in g.level.value_counts().items()} if "level" in g else {},
            "n_nat_ok": int((act.l3_means == "无需").sum()), "n_l3": int(g.verdict.str.startswith("L3").sum()), "n_l3_key": int(g.verdict.str.contains("关键点").sum()),
            "n_beyond": int(g.verdict.str.startswith("超出").sum()), "vol_beyond_m3": int(g.loc[g.verdict.str.startswith("超出"), "vol_peak_m3"].sum()),
            "pumps_12in": int(act.loc[act.l3_means == "12 英寸泵", "l3_units"].sum()), "tankers": int(act.loc[act.l3_means == "罐车", "l3_units"].sum()),
            "unit_hours": round(float(act.unit_hours.sum()), 1), "cost_aed": int(act.cost_aed.sum()), "pumped_m3": int(act.pumped_m3.sum())}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--storms", default=",".join(STORMS)); ap.add_argument("--states", default=",".join(STATES))
    a = ap.parse_args(argv)
    C = context(); H, rate, trt = C["H"], C["rate"], C["trt"]
    rows, summ = [], []
    for s in a.storms.split(","):
        for st in a.states.split(","):
            r = assess(s, st, C)
            if r is None:
                continue
            rows += r[1]; summ.append(summarize(s, st, r[1]))
            print(" ", s, st, summ[-1], flush=True)
    planA = H[H.hotspot_id.isin(sorted({h for r in rows for h in r["hotspots_200m"].split(",") if h}))][["hotspot_id", "criticality", "planA_pumps", "planA_pump12", "planA_pump8", "planA_pump6", "planA_tankers", "planA_manpower"]]
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"rows": rows, "summary": summ, "planA": json.loads(planA.to_json(orient="records", force_ascii=False)),
           "basis": {"points": "ponding.extract 0.15 m / 100 m² / 0.5 h，剔除 L2 塘占地与 exclude_OPTCS（DTM 填 0、私人坑）", "deadline": "雨停 + min(重点单位时限, 客户热点重要度时限)",
                     "arrive": f"积水量首超 {RP.MIN_VOL_M3:g} m³ 后 {trt:g} min（trt_min）", "pump": f"12 英寸 {rate['diesel-12in']:g} m³/h，每点 ≤{MAX_UNITS} 台；1.5 km 内无排放去处 → 罐车 ≤{MAX_TANKERS} 辆",
                     "dry": f"积水量 < {RP.MIN_VOL_M3:g} m³", "horizon": "二维视界之后末帧水量不再自然退（偏保守）"}}
    (OUT / "l3_handover.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("→", OUT / "l3_handover.json")


if __name__ == "__main__":
    main()
