# -*- coding: utf-8 -*-
"""多态交付（RD-A 全套成果布局）：一个场景的 L1 / L2 / L2planned 放在同一个交付根里，页面登记成**同一个仿真**，多态对比。

布局照 109 上仿真 #1053 的来源 `RDA_61mm1h_全套成果`，页面入库代码 `sim_rda`（`stormwater/sim_rda.py`）按它抽取：

    <交付根>/                              = deliveries/<job_id>/，job_id = RDA_<雨量>mm<历时>h（不带态后缀）
      公共/客户热点.geojson  公共/判据评估_<场景>.json  公共/计算域.geojson     （sim_rda 判形状要前两个）
      L1_现状/                    一二维/RDA_S61D1_L1_{SWMM一维管网.inp, 一二维耦合_SWMM报告.rpt, 二维结果.npz, 耦合交换记录.json, 二维水深与流速时序.sww}
                                  设施/RDA_S61D1_L1_设施.geojson
      L2_雨前预置临时设施/          同上，文件名记号 L2
      L2+planned_计划设施/          同上，文件名记号 L2P
      params.json  outputs.json  ponding_ge.json  GRID_15m.txt  00_README.md  statistics/  common/

各态是各自的进程（可以在同一台服务器上并行，也可以分到不同服务器，只要共用 NAS），写同一个交付根：共用文件在文件锁下合并，不互相覆盖。
sim_rda 要求「公共/判据评估_*.json」存在才认这个形状；RD-A 的 A/B/C/D 判据（AECOM 评估）这里没有算，写成空表并注明，页面的 /rda 专页对应区块为空。
"""
from __future__ import annotations

import contextlib
import csv
import fcntl
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: 本脚本的态 → (交付里的态目录, 文件名里的态记号, 页面里的态 key, 中文名)
STATE_LAYOUT = {
    "L1": ("L1_现状", "L1", "L1", "现状（已有设施）"),
    "L2": ("L2_雨前预置临时设施", "L2", "L2", "L1 + 临时设施"),
    "L2planned": ("L2+planned_计划设施", "L2P", "L2+planned", "L2 + 计划设施"),
}
ORDER = ["L1", "L2", "L2planned"]


@contextlib.contextmanager
def locked(root: Path):
    """交付根上的文件锁：几个态的进程同时写共用文件时排队。"""
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".deliver.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def present_states(root: Path) -> list[str]:
    """交付根里已有二维结果的态（按 L1 / L2 / L2planned 顺序）。"""
    return [s for s in ORDER if any((root / STATE_LAYOUT[s][0] / "一二维").glob("*_二维结果.npz"))]


def facility_features(R, state: str, stages: list[str]) -> list[dict]:
    """设施 GeoJSON 要素（WGS84）：泵（已建模的有曲线）、临时泵吸水 / 排水点、Geocell。
    每个要素带 `防线层级`（页面按它拆「所属态」）和 `建模状态`（缺参数未建模的明说，不让页面当成已算进去）。"""
    import geopandas as gpd
    gp = R.p("network_v2.gpkg")
    feats = []

    def add(gdf, level, kind, ftype, modeled, note=None, idcol="UNITID", extra=None):
        if not len(gdf):
            return
        g = gdf.to_crs(4326)
        for i, r in enumerate(g.itertuples()):
            pr = {"编号": str(getattr(r, idcol, None) or f"{kind}-{i + 1}"), "类型": ftype, "kind": kind, "防线层级": level, "建模状态": "已建模" if modeled else "未建模（数据缺参数）"}
            if note:
                pr["说明"] = note
            for k, fn in (extra or {}).items():
                v = fn(r)
                if v is not None:
                    pr[k] = v
            feats.append({"type": "Feature", "geometry": json.loads(gpd.GeoSeries([r.geometry]).to_json())["features"][0]["geometry"], "properties": pr})
    pm = gpd.read_file(gp, layer="pumps")
    pm = pm[pm["ASSET_STAGE"].isin(stages)]
    for stage, level in (("Existing", "L1 现状"), ("Planned", "L2+planned 客户计划")):
        sub = pm[pm["ASSET_STAGE"] == stage]
        ready = sub[sub["PUMP_CURVE"].notna() & sub["ON_EL_M"].notna()]
        other = sub[~sub.index.isin(ready.index) & sub["CAP_Q_LS"].notna()]
        add(ready, level, "pump", "泵", True, extra={"额定流量 L/s": lambda r: None if r.CAP_Q_LS != r.CAP_Q_LS else float(r.CAP_Q_LS), "开泵水位 m": lambda r: float(r.ON_EL_M), "停泵水位 m": lambda r: float(r.OFF_EL_M)})
        add(other, level, "pump", "泵", False, "缺泵曲线 / 启停水位 / 湿井", extra={"额定流量 L/s": lambda r: None if r.CAP_Q_LS != r.CAP_Q_LS else float(r.CAP_Q_LS)})
    if "Planned" in stages:      # 本态新增且已建模的计划设施：出水口 / 井 / 雨水井（页面点 L2planned 时按「所属态」列出）
        for lay, ftype in (("outfalls", "出水口（计划）"), ("manholes", "检查井（计划）"), ("catchbasins", "雨水井（计划）")):
            d = gpd.read_file(gp, layer=lay)
            add(d[d["ASSET_STAGE"] == "Planned"], "L2+planned 客户计划", "outfall" if lay == "outfalls" else "node", ftype, True)
    if "Temporary" in stages:
        add(gpd.read_file(gp, layer="temp_pump_suction"), "L2 雨前预置", "sump", "临时泵吸水点", False, "只有位置和『6 英寸 × 台数』，没有流量 / 扬程 / 接入节点", idcol="HOTSPOT_ID",
            extra={"泵规格": lambda r: str(r.PUMP_DETAIL), "服务热点": lambda r: str(int(r.HOTSPOT_ID))})
        add(gpd.read_file(gp, layer="temp_pump_discharge"), "L2 雨前预置", "pump", "临时泵排水点", False, "只有位置，没有流量 / 受纳资产", idcol="HOTSPOT_ID",
            extra={"泵规格": lambda r: str(r.PUMP_DETAIL), "服务热点": lambda r: str(r.HOTSPOT_ID)})
    if "Planned" in stages:
        add(gpd.read_file(gp, layer="geocell"), "L2+planned 客户计划", "tank", "Geocell 地下蓄水单元", False, "只有顶 / 底标高和面积，没有进出口连接和流量", idcol="LABEL",
            extra={"顶标高 m": lambda r: float(r.TOP_LVL), "底标高 m": lambda r: float(r.BOT_LVL)})
    return feats


def write_common(root: Path, R, domain, title: str, tag: str, log=print) -> None:
    """公共/：客户热点（sim_rda 判形状 + 热点统计）、判据评估（占位，见模块说明）、计算域。"""
    import json as _j
    from pyproj import Transformer
    from shapely.geometry import Point, mapping
    pub = root / "公共"
    pub.mkdir(parents=True, exist_ok=True)
    allp = _j.loads((HERE.parent / "data" / "hotspots" / "adm506_hotspots.json").read_text(encoding="utf-8"))
    ids = (R.cfg.get("hotspots") or {}).get("ids")
    inside = [h for h in allp if h["hotspot_id"] in ids] if ids else [h for h in allp if domain.contains(Point(h["x"], h["y"]))]
    fc = {"type": "FeatureCollection", "name": f"hotspots_{R.id}", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [h["lon"], h["lat"]]},
         "properties": {"hotspot_id": h["hotspot_id"], "region": "ADM", "area": h.get("area"), "location": h.get("location"), "criticality": h.get("criticality")}} for h in inside]}
    (pub / "客户热点.geojson").write_text(_j.dumps(fc, ensure_ascii=False, indent=1), encoding="utf-8")
    (pub / f"判据评估_{tag}.json").write_text(_j.dumps({"storm": tag, "states": {}, "note": "A / B / C / D 判据（AECOM 评估）本次没有计算，states 为空；页面 RD-A 专页的判据区块为空"}, ensure_ascii=False, indent=1), encoding="utf-8")
    tr = Transformer.from_crs(32640, 4326, always_xy=True)
    polys = list(domain.geoms) if domain.geom_type == "MultiPolygon" else [domain]
    ring = lambda r: [[round(lo, 6), round(la, 6)] for lo, la in (tr.transform(x, y) for x, y in r.coords)]  # noqa: E731
    (pub / "计算域.geojson").write_text(_j.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": title},
                                                  "geometry": {"type": "MultiPolygon", "coordinates": [[ring(p.exterior)] + [ring(i) for i in p.interiors] for p in polys]}}]}, ensure_ascii=False), encoding="utf-8")
    log(f"  公共/：客户热点 {len(inside)} 个（落在二维计算域内的 / region.toml [hotspots]）、判据评估（占位）、计算域")


def deliver_state(root: Path, R, name: str, tag: str, work: Path, state: str, meta: dict, reg: dict, sww: Path, domain, log=print) -> Path:
    """写这一态的目录（一二维 + 设施），同时刷新交付根的公共文件。"""
    import ponding_ge as PG
    c2d = work / "c2d"
    sdir, tok, _, _ = STATE_LAYOUT[state]
    tg = f"{name}_{state}"
    stem = f"{name}_{tok}"
    d2 = root / sdir / "一二维"
    d2.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work / f"{tg}.inp", d2 / f"{stem}_SWMM一维管网.inp")
    shutil.copyfile(c2d / f"{tg}_2way.rpt", d2 / f"{stem}_一二维耦合_SWMM报告.rpt")
    shutil.copyfile(c2d / f"{tg}_2d.npz", d2 / f"{stem}_二维结果.npz")
    rec = json.loads((c2d / f"twoway_{tg}.json").read_text(encoding="utf-8"))
    rec.setdefault("res_m", meta["res_m"])
    rec["state"] = state
    (d2 / f"{stem}_耦合交换记录.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copyfile(sww, d2 / f"{stem}_二维水深与流速时序.sww")
    fac = root / sdir / "设施"
    fac.mkdir(exist_ok=True)
    stages = list((reg.get("facilities") or {}).get("stages") or ["Existing"])
    feats = facility_features(R, state, stages)
    (fac / f"{stem}_设施.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    with (fac / f"{stem}_设施清单.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["编号", "类型", "防线层级", "建模状态"])
        w.writerows([[x["properties"]["编号"], x["properties"]["类型"], x["properties"]["防线层级"], x["properties"]["建模状态"]] for x in feats])
    (root / sdir / "model_inputs").mkdir(exist_ok=True)
    for f in (c2d / f"pilot_{tg}.json", c2d / f"dem_{name}.tif", c2d / f"surf_{name}.tif"):
        if f.is_file():
            shutil.copyfile(f, root / sdir / "model_inputs" / f.name)
    (root / sdir / f"inputs_{tag}_{state}.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log(f"  {sdir}：一二维 + 设施 {len(feats)} 个要素")
    return root / sdir


def finish_root(root: Path, meta: dict, reg: dict, state: str, st: dict, params_json: Path | None, paths: dict, extra: dict, log=print, gpkg: Path | None = None) -> None:
    """交付根上各态共用的文件：params.json（L1 的；没有 L1 就用现有的）、统计表（各态一列）、ponding_ge.json、outputs.json（各态合并）、README、网格标记。
    要在 locked(root) 里调。"""
    import datetime as dt
    import ponding_ge as PG
    sdir, tok, key, zh = STATE_LAYOUT[state]
    if params_json and params_json.is_file():
        shutil.copyfile(params_json, root / sdir / "params.json")
        if state == "L1" or not (root / "params.json").is_file():
            shutil.copyfile(params_json, root / "params.json")
    (root / "statistics").mkdir(exist_ok=True)
    (root / "statistics" / f"run_stats_{state}.json").write_text(json.dumps({"state": state, "zone": st["zone"], "deepest": st["deepest"], "balance": st["balance"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    have = [s for s in ORDER if (root / "statistics" / f"run_stats_{s}.json").is_file()]
    S = {s: json.loads((root / "statistics" / f"run_stats_{s}.json").read_text(encoding="utf-8")) for s in have}
    rows = [["region", "metric", *have]]
    for lab, k in (("domain max depth (m)", "max_depth_m"), ("flooded area >=0.10 m (ha)", "area_ge_0p10_ha"), ("flooded area >=0.30 m (ha)", "area_ge_0p30_ha"), ("peak flood volume (m3)", "peak_vol_m3"),
                   ("time of peak flood volume (h)", "peak_vol_t_h"), ("2D domain area (ha)", "domain_area_ha")):
        rows.append([meta["title"], lab, *[S[s]["zone"][k] for s in have]])
    rows.append([meta["title"], "deepest cell depth (m)", *[S[s]["deepest"]["depth_m"] for s in have]])
    for k in S[have[0]]["balance"]:
        rows.append(["domain", f"water balance: {k}", *[S[s]["balance"].get(k) for s in have]])
    with open(root / "statistics" / "summary.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    # 键用页面的态 key（L1 / L2 / L2+planned）：页面「需要排 → 可以排 → 排完剩余」按 run.states 的 key 取，键对不上那一态的卡片就不出
    npz = {STATE_LAYOUT[s][2]: next((root / STATE_LAYOUT[s][0] / "一二维").glob("*_二维结果.npz"), None) for s in present_states(root)}
    PG.write_ponding_ge(root, npz)
    if gpkg is not None:     # 管线图层：页面「雨水管网」开关画它（没传 gpkg 就不写，已有的保留）
        import region_pipes
        region_pipes.write_pipes(root, gpkg)
    (root / f"GRID_{meta['res_m']:g}m.txt").write_text(f"二维网格精度 {meta['res_m']:g} m（非结构三角网的名义精度）\n", encoding="utf-8")
    # outputs.json：各态合并
    op = root / "outputs.json"
    o = json.loads(op.read_text(encoding="utf-8")) if op.is_file() else {}
    o.update({"schema": "flood_management.run_outputs.v1", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              **{k: meta[k] for k in ("job_id", "name", "tag", "title", "rain_mm", "dur_h", "sim_h", "res_m")}, "grid": {"res_m": meta["res_m"], "label": f"{meta['res_m']:g} m"},
              "region": meta["region"], "host": meta.get("host"), "paths": paths, "layout": "rda_multi_state"})
    o.update({k: v for k, v in extra.items() if k not in ("facilities", "facility_stages", "unmodeled")})
    sts = o.setdefault("states_detail", {})
    sts[state] = {"dir": sdir, "key": key, "label": zh, "facilities": extra.get("facilities"), "facility_stages": extra.get("facility_stages"), "unmodeled": extra.get("unmodeled"),
                  "files": {"npz": f"{sdir}/一二维/{name_of(meta)}_{tok}_二维结果.npz", "sww": f"{sdir}/一二维/{name_of(meta)}_{tok}_二维水深与流速时序.sww", "inp": f"{sdir}/一二维/{name_of(meta)}_{tok}_SWMM一维管网.inp",
                            "facilities": f"{sdir}/设施/{name_of(meta)}_{tok}_设施.geojson", "params": f"{sdir}/params.json"}}
    o["states"] = [s for s in ORDER if s in sts]
    o["state"] = o["states"][0]
    o["continue_from"] = {"base_state": "L1", "how": "L2 sugg / L3 在各态目录里的 inp、model_inputs/（pilot / dem / surf）基础上追加设施继续推演；规则同 params.json 里的版本"}
    op.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    unm = "".join(f"  - [{s}] {u['item']}（{u['stage']}）：{u['reason']}\n" for s in o["states"] for u in (sts[s].get("unmodeled") or []) if u.get("stage") in ("Temporary", "Planned"))
    (root / "00_README.md").write_text(
        f"# {meta['job_id']}\n\n{meta['title']} · {meta['rain_mm']:g} mm / {meta['dur_h']:g} h · **二维网格精度 {meta['res_m']:g} m** · 态：{' / '.join(o['states'])}\n\n"
        + (f"**数据缺参数、未建模的设施：**\n{unm}\n" if unm else "")
        + "- 每个态一个目录：`一二维/`（SWMM inp、报告、二维结果 npz、三角网 .sww、耦合记录）、`设施/`、`params.json`、`model_inputs/`\n- `公共/`：客户热点、计算域、判据评估（占位）\n"
        "- `params.json`：L1 的全部参数（规则 + 输入登记 + 一致性核对）；各态自己的在态目录里\n- `ponding_ge.json`：各态水深 ≥ 0.15 m 的积水量\n- `statistics/`：各态统计\n" + f"- `GRID_{meta['res_m']:g}m.txt`：网格精度标记\n", encoding="utf-8")
    log(f"  交付根 {root}（态 {o['states']}）")


def name_of(meta: dict) -> str:
    return meta["name"]
