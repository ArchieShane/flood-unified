"""设施设计约束：WA-726 约束登记表 + 取值决定 → 本体的校验规则（2026-10-23）。

数据和代码分开放：
- wa726.json      WA-726 第一至三卷逐条抽取的约束，每条带原文摘录、PDF 页码和核对状态（docs/WA726_设施约束登记表_20261023.md）
- decisions.json  原文矛盾或含糊之处的取值决定（谁、什么时候、为什么）
- 本模块          每条检查只写「怎么判」，阈值一律按编号从上面两个文件取；改数值就改数据文件，不改代码

check(facility, obj) 返回逐条结果：result 取 pass / fail / unknown / review。
- pass、fail：按约束判过了
- unknown：对象缺所需属性
- review：只能人工判断或要走审批，如 EAD 批准
"""
from __future__ import annotations

import json
import math
import pathlib
import re
from functools import lru_cache

HERE = pathlib.Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def register() -> dict[str, dict]:
    d = json.loads((HERE / "wa726.json").read_text(encoding="utf-8"))
    return {c["id"]: c for c in d["items"]}


@lru_cache(maxsize=1)
def decisions() -> dict[str, dict]:
    d = json.loads((HERE / "decisions.json").read_text(encoding="utf-8"))
    return {c["id"]: c for c in d["items"]}


def val(cid: str):
    """登记表条目的数值（缺了直接报错：说明登记表被改过、编号对不上）"""
    return register()[cid]["value"]


def dec(did: str):
    return decisions()[did]["value"]


def cite(*ids: str) -> list[dict]:
    R, D = register(), decisions()
    out = []
    for i in ids:
        if i in R:
            c = R[i]; out.append({"id": i, "section": c.get("section"), "vol": c.get("vol"), "pdf_page": c.get("pdf_page"), "modal": c.get("modal")})
        elif i in D:
            out.append({"id": i, "topic": D[i]["topic"]})
    return out


def clear_width_b(depth_m: float, bore_mm: float) -> tuple[float, str] | None:
    """图 4-1：管道与建筑基础的最小净宽 B（按埋深和管径从登记表里查）"""
    def rng(s):
        s = s.strip()
        if s.startswith("<"):
            return -math.inf, float(s[1:])
        if s.startswith(">"):
            return float(s[1:]), math.inf
        a, b = s.split("-"); return float(a), float(b)
    for cid, c in register().items():
        if c["parameter"] != "clear_width_to_building_m":
            continue
        m = re.match(r"D (\S+) m; pipe bore (\S+) mm", c.get("condition", ""))
        if not m:
            continue
        d0, d1 = rng(m.group(1)); b0, b1 = rng(m.group(2))
        if d0 <= depth_m < d1 and b0 <= max(bore_mm, 300) < b1 + (1 if b1 == math.inf else 0.001):
            return float(c["value"]), cid
    return None


def _r(cid, zh, result, detail, ids):
    return {"check": cid, "zh": zh, "result": result, "detail": detail, "refs": cite(*ids)}


def _need(o, *keys):
    return all(o.get(k) is not None for k in keys)


# ---------------------------------------------------------------- 各类设施的检查
def check_pond(o: dict) -> list[dict]:
    """调蓄设施（Pond；pond_type = open 地面塘 / landscaped 景观化临时蓄洪地 / underground 地下箱涵 / geocellular 土工格室）"""
    out = []
    t = o.get("pond_type")
    ids = ("WA726-1-126", "WA726-1-132", "DEC-03")
    if t is None or o.get("in_developed_area") is None:
        out.append(_r("open_pond_siting", "已开发区内或附近不建开敞塘", "unknown", "缺 pond_type 或 in_developed_area", ids))
    elif t == "open" and o["in_developed_area"]:
        out.append(_r("open_pond_siting", "已开发区内或附近不建开敞塘", "fail", "开敞塘位于已开发区内或附近", ids))
    elif t == "landscaped":
        out.append(_r("open_pond_siting", "已开发区内或附近不建开敞塘", "review", "游乐 / 公园地块上的景观化临时蓄洪地：手册允许（may），需客户确认", ids))
    else:
        out.append(_r("open_pond_siting", "已开发区内或附近不建开敞塘", "pass", f"{t}", ids))
    if _need(o, "required_volume_m3", "effective_volume_m3"):
        k = 1 + val("WA726-1-128") / 100.0
        ok = o["effective_volume_m3"] >= o["required_volume_m3"] * k - 1e-6
        out.append(_r("volume_sediment_margin", "容积比所需大 5 %（积砂）", "pass" if ok else "fail",
                      f"有效 {o['effective_volume_m3']:.0f} m³，需 ≥ {o['required_volume_m3'] * k:.0f} m³", ("WA726-1-128", "WA726-1-129")))
    else:
        out.append(_r("volume_sediment_margin", "容积比所需大 5 %（积砂）", "unknown", "缺 required_volume_m3 / effective_volume_m3", ("WA726-1-128",)))
    dm = o.get("drain_mode")
    out.append(_r("auto_drain", "雨后立即自动排空（重力回流或泵回管网）",
                  "unknown" if dm is None else ("pass" if dm in ("gravity", "pump") else "fail"), f"drain_mode = {dm}", ("WA726-1-135", "WA726-1-134")))
    if t in ("open", "landscaped"):
        w = o.get("access_width_m"); need = dec("DEC-06")
        out.append(_r("maintenance_access", "检修通道宽 ≥ 5 m", "unknown" if w is None else ("pass" if w >= need else "fail"),
                      f"通道 {w} m，需 ≥ {need} m", ("WA726-1-245", "WA726-2-080", "DEC-06")))
        s = o.get("side_slope_h_per_v"); D5 = dec("DEC-05")
        need_s = D5["public_park_h_per_v"] if t == "landscaped" else (D5["fenced_h_per_v"] if o.get("fenced") else D5["unfenced_min_h_per_v"])
        out.append(_r("side_slope", "边坡（H:V）", "unknown" if s is None else ("pass" if s >= need_s else "fail"),
                      f"{s}:1，需 ≥ {need_s}:1", ("WA726-1-582", "WA726-1-583", "WA726-1-584", "DEC-05")))
        fb = o.get("freeboard_m"); need_f = dec("DEC-02")["normal_m"]
        out.append(_r("freeboard", "超高", "unknown" if fb is None else ("pass" if fb >= need_f else "fail"),
                      f"{fb} m，需 ≥ {need_f} m（应急 ≥ {dec('DEC-02')['emergency_m']} m）", ("WA726-2-070", "WA726-2-075", "DEC-02")))
        sp = o.get("has_emergency_spillway")
        out.append(_r("emergency_spillway", "设应急溢洪道且不影响下游构筑物", "unknown" if sp is None else ("pass" if sp else "fail"),
                      f"has_emergency_spillway = {sp}", ("WA726-1-247", "WA726-1-248")))
        if o.get("public_access"):
            fn = o.get("fenced")
            out.append(_r("public_safety", "公众可达：围栏或栏杆、逃生梯和救生圈 ≤100 m、警示牌 ≤50 m",
                          "unknown" if fn is None else ("pass" if fn else "fail"), f"fenced = {fn}",
                          ("WA726-1-262", "WA726-1-263", "WA726-1-264", "WA726-1-265")))
    if o.get("in_water_body_buffer") is not None:
        out.append(_r("not_in_water_body", "不在地表水体或其缓冲区内", "fail" if o["in_water_body_buffer"] else "pass", "", ("WA726-1-242",)))
    if t in ("underground", "geocellular") and o.get("gw_below_base_m") is not None:
        need = val("WA726-2-130") / 1000.0
        out.append(_r("groundwater", "施工前地下水位低于池底 ≥ 0.5 m（否则做抗浮评估）",
                      "pass" if o["gw_below_base_m"] >= need else "review", f"{o['gw_below_base_m']} m", ("WA726-2-130",)))
    if o.get("outside_developer_area"):
        out.append(_r("land_permission", "开发商用地外：需主管部门和业主许可", "review", "", ("WA726-1-127",)))
    return out


def check_pump_station(o: dict) -> list[dict]:
    out = []
    gf = o.get("gravity_infeasible")
    out.append(_r("gravity_first", "重力优先：只有重力接不上才可设泵站，并做 NPV 比选",
                  "unknown" if gf is None else ("pass" if gf else "fail"), f"gravity_infeasible = {gf}", ("WA726-1-007", "WA726-1-282")))
    n = o.get("pump_count"); D4 = dec("DEC-04")
    out.append(_r("min_pumps", "至少 2 台同型号泵", "unknown" if n is None else ("pass" if n >= D4["min_pumps"] else "fail"),
                  f"{n} 台", ("WA726-1-309", "DEC-04")))
    if _need(o, "pump_count", "pump_capacity_m3s", "design_flow_m3s") and o["pump_count"] >= 1:
        need_q = o["design_flow_m3s"] * (1 + val("WA726-1-302") / 100.0)
        tot = o["pump_count"] * o["pump_capacity_m3s"]; one_out = (o["pump_count"] - 1) * o["pump_capacity_m3s"]
        ok = tot >= need_q - 1e-9 and one_out >= D4["min_capacity_with_one_out_pct"] / 100.0 * o["design_flow_m3s"] - 1e-9
        out.append(_r("capacity", "总能力 ≥ 设计流量 × 1.1；坏一台时 ≥ 66 %", "pass" if ok else "fail",
                      f"总 {tot:.3f}、坏一台 {one_out:.3f}，设计 {o['design_flow_m3s']:.3f} m³/s", ("WA726-1-302", "WA726-1-310", "WA726-1-311", "DEC-04")))
    else:
        out.append(_r("capacity", "总能力 ≥ 设计流量 × 1.1；坏一台时 ≥ 66 %", "unknown", "缺 pump_count / pump_capacity_m3s / design_flow_m3s", ("WA726-1-302",)))
    if _need(o, "wetwell_volume_m3", "pump_capacity_m3s"):
        T = 3600.0 / dec("DEC-09"); vmin = 0.25 * o["pump_capacity_m3s"] * T
        out.append(_r("wetwell_volume", "湿井有效容积 ≥ 0.25·Q·T", "pass" if o["wetwell_volume_m3"] >= vmin - 1e-6 else "fail",
                      f"{o['wetwell_volume_m3']:.1f} m³，需 ≥ {vmin:.1f} m³（T = {T:.0f} s）", ("WA726-1-295", "WA726-1-296", "DEC-09")))
    else:
        out.append(_r("wetwell_volume", "湿井有效容积 ≥ 0.25·Q·T", "unknown", "缺 wetwell_volume_m3 / pump_capacity_m3s", ("WA726-1-296",)))
    if o.get("pump_capacity_m3s") is not None:
        out.append(_r("wetwell_shape", "单泵 >180 L/s 用矩形湿井", "pass", "矩形" if o["pump_capacity_m3s"] * 1000 > val("WA726-1-292") else "圆形可", ("WA726-1-292", "WA726-1-293")))
    site = o.get("surface_site_m2")
    out.append(_r("surface_site", "地面独立电气房 + 重型车辆可进出的场地", "unknown" if site is None else ("pass" if site > 0 else "fail"),
                  f"地面场地 {site} m²（面积下限手册未给，需客户提供或参照已建泵站）", ("WA726-1-346", "WA726-2-080", "WA726-1-487")))
    out.append(_r("site_approval", "场地平面须经 DPM 与城市规划批准", "review", "", ("WA726-1-487",)))
    return out


def check_pressure_pipe(o: dict) -> list[dict]:
    out = []
    v = o.get("velocity_peak_ms")
    out.append(_r("velocity_peak", "峰值流速 ≤ 2.0 m/s", "unknown" if v is None else ("pass" if v <= val("WA726-1-378") + 1e-9 else "fail"),
                  f"{v} m/s", ("WA726-1-378",)))
    ve = o.get("velocity_emergency_ms")
    if ve is not None:
        out.append(_r("velocity_emergency", "应急工况流速 ≤ 2.5 m/s", "pass" if ve <= val("WA726-1-379") + 1e-9 else "fail", f"{ve} m/s", ("WA726-1-379",)))
    m = o.get("material")
    out.append(_r("material", "材质：球墨铸铁 / GRP / HDPE", "unknown" if m is None else ("pass" if str(m).upper() in val("WA726-1-376") else "fail"), f"{m}", ("WA726-1-376",)))
    out += _clearances(o)
    if o.get("valve_chambers_in_carriageway") is not None:
        out.append(_r("valve_chamber", "阀室不放在车行道", "fail" if o["valve_chambers_in_carriageway"] else "pass", "", ("WA726-1-455",)))
    out.append(_r("service_reservation", "管位预留（Service Reservation）须经城市规划批准", "review", "", ("WA726-1-485", "WA726-1-093")))
    return out


def check_gravity_pipe(o: dict) -> list[dict]:
    out = []
    c = o.get("cover_m")
    out.append(_r("min_cover", "最小覆土 1.2 m（不加保护）", "unknown" if c is None else ("pass" if c >= val("WA726-1-162") else "fail"), f"{c} m", ("WA726-1-162",)))
    v = o.get("velocity_peak_ms")
    if v is not None:
        out.append(_r("self_cleansing", "峰值流速 ≥ 0.75 m/s（自清）", "pass" if v >= val("WA726-1-151") else "fail", f"{v} m/s", ("WA726-1-151",)))
    out += _clearances(o)
    return out


def _clearances(o: dict) -> list[dict]:
    out = []
    g = o.get("clearance_to_utilities_m")
    out.append(_r("utility_gap", "与现状管线平面净距 ≥ 500 mm", "unknown" if g is None else ("pass" if g >= val("WA726-1-119") / 1000.0 else "fail"),
                  f"{g} m", ("WA726-1-119",)))
    b = o.get("clearance_to_building_m")
    if b is None or not _need(o, "depth_m", "diameter_mm"):
        out.append(_r("building_clear_width", "与建筑基础净宽 ≥ B（图 4-1）", "unknown", "缺 clearance_to_building_m / depth_m / diameter_mm", ("WA726-1-099",)))
    else:
        lk = clear_width_b(o["depth_m"], o["diameter_mm"])
        if lk is None:
            out.append(_r("building_clear_width", "与建筑基础净宽 ≥ B（图 4-1）", "unknown", "图 4-1 查不到这档", ()))
        else:
            need, cid = lk
            out.append(_r("building_clear_width", "与建筑基础净宽 ≥ B（图 4-1）", "pass" if b >= need else "review",
                          f"{b} m，需 ≥ {need} m（不足须做岩土勘察并报批）", (cid, "WA726-1-901")))
    return out


def check_outfall(o: dict) -> list[dict]:
    out = []
    s = o.get("soffit_m_nadd")
    out.append(_r("soffit_below_MLLW", "排海管管顶低于 MLLW（−0.7 m NADD）", "unknown" if s is None else ("pass" if s < val("WA726-1-214") else "fail"),
                  f"{s} m", ("WA726-1-214",)))
    nc = o.get("nrv_chamber_cover_m_nadd")
    out.append(_r("nrv_chamber", "上游止回阀井，盖板高于 HAT（+1.3 m）", "unknown" if nc is None else ("pass" if nc > val("WA726-1-215") else "fail"),
                  f"{nc} m", ("WA726-1-215",)))
    fv = o.get("flap_valve")
    out.append(_r("flap_valve", "排口设拍门 / 鸭嘴阀 / 锥形止回阀", "unknown" if fv is None else ("pass" if fv else "fail"), f"{fv}", ("WA726-1-431",)))
    if o.get("is_new"):
        out.append(_r("EAD_approval", "新建排海口：EAD 写明位置的专项批准 + TCA-AD 考古报告", "review", "初步设计阶段就要", ("WA726-1-473", "WA726-1-472")))
    return out


CHECKERS = {"Pond": check_pond, "PumpStation": check_pump_station, "PressurePipe": check_pressure_pipe,
            "GravityPipe": check_gravity_pipe, "Outfall": check_outfall}


def check(facility: str, o: dict) -> dict:
    """一个设施对象的逐条校验 + 汇总（compliant：没有 fail；pending：有 unknown 或 review）"""
    items = CHECKERS[facility](o)
    fails = [i for i in items if i["result"] == "fail"]
    return {"facility": facility, "compliant": not fails, "fails": [f"{i['zh']}：{i['detail']}" for i in fails],
            "pending": [f"{i['zh']}（{i['result']}）" for i in items if i["result"] in ("unknown", "review")], "items": items}
