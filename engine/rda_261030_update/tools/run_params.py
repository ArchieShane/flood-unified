# -*- coding: utf-8 -*-
"""本次推演的全部参数 → 交付根目录的 params.json（management 页面「推演参数」模块读它）。

内容 = 公共规则（config/model_rules.toml，每条带 来源 / 出处）+ 本次运行的输入登记（inputs_<tag>_<态>.json：节点数、管数、雨量、出水口、
深坑、建筑洞…）+ 一致性核对（规则文件 vs 这次运行实际用的值，不一致的页面标红）。客户要看的就是这份。

用法：
  python tools/run_params.py --job-id RDA_A61D1 --state L1 --inputs <inputs_A61D1_L1F.json> --out <交付目录>
  python tools/run_params.py --check-labels         # 规则文件里每个取值都必须有中英文名称（config/model_rules_labels.toml）
别的脚本里：from run_params import build, write_params
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_rules as MR  # noqa: E402

SCHEMA = "flood_management.run_params.v1"
LABELS = Path(__file__).resolve().parent.parent / "config" / "model_rules_labels.toml"
SKIP = {"source", "ref", "status"}
GROUPS = {  # 规则文件顶层表 → 页面分组标题
    "states": ("三态定义", "State definitions"),
    "swmm": ("一维 SWMM 选项", "1D SWMM options"),
    "pipes": ("管道糙率", "Pipe roughness"),
    "init_state": ("管网初始状态", "Initial network state"),
    "outfalls": ("出水口与潮位", "Outfalls and tide"),
    "runoff": ("产流与下渗", "Runoff and infiltration"),
    "exchange": ("一二维交换", "1D-2D exchange"),
    "twod": ("二维地表", "2D surface"),
    "rain": ("降雨库", "Rainfall library"),
    "known_gaps": ("已知缺项", "Known gaps"),
}


def _labels() -> dict:
    import tomllib
    with open(LABELS, "rb") as f:
        return tomllib.load(f)["labels"]


def _leaves(d: dict, path: str = ""):
    for k, v in d.items():
        p = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            yield from _leaves(v, p)
        elif k not in SKIP:
            yield p, v


def _inherit(rules: dict, path: str, field: str):
    """取最近的祖先表里的 source / ref / status。"""
    parts = path.split(".")
    for n in range(len(parts) - 1, 0, -1):
        node = rules
        try:
            for k in parts[:n]:
                node = node[k]
        except (KeyError, TypeError):
            continue
        if isinstance(node, dict) and field in node:
            return node[field]
    return None


def missing_labels(rules: dict | None = None) -> list[str]:
    rules, lab = rules or MR.load(), _labels()
    return [p for p, _ in _leaves({k: v for k, v in rules.items() if k != "meta"}) if p not in lab]


def rule_groups(rules: dict) -> list[dict]:
    lab, groups = _labels(), {}
    for p, v in _leaves({k: v for k, v in rules.items() if k != "meta"}):
        top = p.split(".")[0]
        l = lab.get(p, {"zh": p, "en": p})
        groups.setdefault(top, []).append({
            "key": p, "zh": l["zh"], "en": l["en"], "unit": l.get("unit", ""), "value": v,
            "source": _inherit(rules, p, "source"), "ref": _inherit(rules, p, "ref"), "status": _inherit(rules, p, "status")})
    return [{"key": k, "zh": GROUPS[k][0], "en": GROUPS[k][1], "items": groups[k]} for k in GROUPS if k in groups]


def _it(key, zh, en, value, unit="", source="输入登记", ref=None):
    return {"key": key, "zh": zh, "en": en, "unit": unit, "value": value, "source": source, "ref": ref, "status": None}


def run_group(inp: dict, ref: str) -> list[dict]:
    """本次运行的输入登记（inputs_<tag>_<态>.json）→ 分组；缺的字段跳过，不编。"""
    g = []
    def grp(key, zh, en, items):
        items = [i for i in items if i["value"] is not None]
        if items:
            g.append({"key": key, "zh": zh, "en": en, "items": items})
    I = lambda *a, **k: _it(*a, ref=ref, **k)  # noqa: E731
    grp("run.model", "本次运行 · 模型规模", "This run · model size", [
        I("run.name", "算例名", "Case name", inp.get("name")), I("run.state", "态", "State", inp.get("state")),
        I("run.junctions", "井 / 节点数", "Junctions", inp.get("n_junctions")), I("run.conduits", "管道数", "Conduits", inp.get("n_conduits")),
        I("run.outfalls", "出水口数", "Outfalls", inp.get("n_outfalls")), I("run.pumps", "泵数", "Pumps", inp.get("n_pumps")),
        I("run.storage", "蓄水节点数", "Storage nodes", inp.get("n_storage")), I("run.domain", "计算域面积", "Domain area", inp.get("domain_km2"), "km²"),
        I("run.sim_h", "模拟时长", "Simulation length", inp.get("sim_h"), "h")])
    gr = inp.get("grid") or {}
    grp("run.grid", "本次运行 · 二维网格精度", "This run · 2D grid resolution", [
        I("run.grid_m", "二维网格精度（非结构三角网名义精度）", "2D grid resolution (nominal, unstructured triangular mesh)", gr.get("res_m"), "m")])
    fc = inp.get("facilities") or {}
    pi, pu = fc.get("pipes") or {}, fc.get("pipes_used") or {}
    unm = [f"{u.get('item')}（{u.get('stage')}）：{u.get('reason')}" for u in fc.get("unmodeled") or []]
    grp("run.facilities", "本次运行 · 设施状态与管线状态", "This run · facility stages and pipe status", [
        I("run.stages", "本态包含的设施状态（ASSET_STAGE）", "Facility stages included (ASSET_STAGE)", fc.get("stages")),
        I("run.fac_counts", "进入本态的设施个数", "Facilities included in this state", fc.get("facilities")),
        I("run.pipe_rule", "管线取用规则", "Pipe selection rule", pi.get("rule")),
        I("run.pipe_stage", "数据包内管线按 ASSET_STAGE 个数（三态都用全部管线）", "Pipes in data pack by ASSET_STAGE (all used in every state)", pi.get("by_asset_stage")),
        I("run.pipe_status_all", "数据包内管线按 STATUS 个数", "Pipes in data pack by STATUS", pi.get("by_STATUS")),
        I("run.pipe_used", "进入模型的管线数 / 按 STATUS", "Pipes in the model / by STATUS", {"n": pu.get("n"), "by_STATUS": pu.get("by_STATUS")} if pu else None),
        I("run.pipe_odd", "使用了 STATUS 为 Abandoned / Expired / Removed 的管线（根数）", "Pipes with STATUS Abandoned / Expired / Removed that were used", pu.get("n_abandoned_expired_removed")),
        I("run.pipe_odd_note", "说明", "Note", pu.get("note")),
        I("run.outfall_rule", "出水口取用与边界", "Outfall selection and boundary", fc.get("outfall_rule")),
        I("run.unmodeled", "数据缺参数、未建模的设施", "Facilities not modelled (parameters missing in the data)", unm or None)])
    ro = inp.get("runoff") or {}
    grp("run.rain", "本次运行 · 降雨与产流", "This run · rainfall and runoff", [
        I("run.storm", "雨标签", "Storm tag", ro.get("storm")), I("run.rain_mm", "雨量", "Depth", ro.get("rain_mm"), "mm"),
        I("run.dur_h", "历时", "Duration", ro.get("dur_h"), "h"), I("run.shape", "雨型来源", "Shape source", ro.get("shape")),
        I("run.subs_n", "产流子区数", "Runoff sub-areas", ro.get("subs_n")), I("run.subs_ha", "产流面积", "Runoff area", ro.get("subs_ha"), "ha"),
        I("run.excess", "面积加权净雨", "Area-weighted excess rain", ro.get("excess_mm_aw"), "mm"),
        I("run.runoff_m3", "总入流水量", "Total runoff volume", ro.get("runoff_m3"), "m³"),
        I("run.outlets", "径流出口节点数", "Runoff outlet nodes", ro.get("outlets"))])
    f = inp.get("init_fill") or {}
    grp("run.init", "本次运行 · 管网初始水量", "This run · initial network water", [
        I("run.fill", "初始填充率", "Fill fraction", f.get("frac_area")), I("run.y_over_d", "对应水深 / 管径", "y/D", f.get("y_over_D")),
        I("run.fill_nodes", "设置初始水量的节点数", "Nodes set", f.get("nodes_set")),
        I("run.vol_init", "初始管内水量", "Initial pipe volume", f.get("pipe_vol_init_m3"), "m³"),
        I("run.vol_full", "满管容积", "Full pipe volume", f.get("pipe_vol_full_m3"), "m³")])
    outs = [f"{o.get('id')}：管底 {o.get('invert_m')} m，{o.get('boundary')}，拍门 {o.get('flap')}" for o in inp.get("outfalls") or []]
    sealed = inp.get("sealed") or {}
    grp("run.network", "本次运行 · 出水口与封口", "This run · outfalls and sealing", [
        I("run.outfall_list", "出水口", "Outfalls", outs or None), I("run.force_mains", "压力管", "Force mains", len(inp["force_mains"]) if inp.get("force_mains") else None),
        I("run.end_caps", "封死的末端节点", "Sealed end caps", sealed.get("end_caps")),
        I("run.outside_sealed", "计算域外非结构节点（封死）", "Sealed nodes outside domain", sealed.get("outside_domain_nonstructure"))])
    ex = inp.get("exchange") or {}
    il = inp.get("inlet") or {}
    grp("run.exchange", "本次运行 · 一二维交换", "This run · 1D-2D exchange", [
        I("run.ex_nodes", "交换节点数", "Exchange nodes", ex.get("nodes")), I("run.ex_types", "交换类型", "Exchange types", ex.get("by_type")),
        I("run.ex_elevated", "高架井（只溢流）数", "Elevated overflow-only nodes", len(ex["elevated_overflow_only"]) if ex.get("elevated_overflow_only") else None),
        I("run.ground_basis", "地面高程取法", "Ground basis", inp.get("ground_basis")),
        I("run.inlet_p", "雨水口周长 P", "Inlet perimeter P", il.get("P"), "m"), I("run.inlet_a", "雨水口开孔面积 A", "Inlet open area A", il.get("A"), "m²"),
        I("run.inlet_clog", "防堵折减", "Clogging factor", il.get("clog"))])
    bh = inp.get("building_holes") or {}
    grp("run.surface", "本次运行 · 地形与建筑", "This run · terrain and buildings", [
        I("run.holes", "建筑挖洞数", "Building holes", bh.get("n_holes")),
        I("run.fills", "深坑填平", "Deep-pit fills", [f"{d.get('id')}：填至 {d.get('crest_m')} m" for d in inp.get("dem_fills") or []] or None),
        I("run.infil", "二维下渗", "2D infiltration", inp.get("infil_mmh") if inp.get("infil_mmh") is not None else "无")])
    pm = inp.get("pipe_n_by_material") or {}
    grp("run.pipes", "本次运行 · 管材糙率分布（根数）", "This run · pipe roughness by material (count)", [
        I(f"run.pipe.{k}", k.split(" → ")[0], k.split(" → ")[0], {"n": k.split(" → ")[-1], "count": v}) for k, v in pm.items()])
    src = inp.get("source") or {}
    grp("run.source", "本次运行 · 一维管网数据来源", "This run · 1D network source", [
        I("run.src", "来源", "Source", src.get("source")), I("run.round", "治理轮次", "Governance round", src.get("round")),
        I("run.pipes_conn", "计算域内连通的管数", "Connected pipes in domain", src.get("pipes_in_domain_connected")),
        I("run.new_junc", "新增节点数", "New junctions", src.get("new_junctions")),
        I("run.pipes_out", "计算域外仅一维的管数", "1D-only pipes outside domain", src.get("pipes_outside_domain_1d_only")),
        I("run.inv_filled", "补全管底高程", "Filled inverts", src.get("invert_filled"))])
    return g


def checks(rules: dict, inp: dict) -> list[dict]:
    """规则文件 vs 这次运行实际用的值。"""
    out = []
    def add(zh, en, rule, actual):
        out.append({"zh": zh, "en": en, "rule": rule, "actual": actual, "ok": str(rule) == str(actual)})
    opts = dict(MR.swmm_options(rules))
    for k, v in (inp.get("routing") or {}).items():
        add(f"一维 {k}", f"1D {k}", opts.get(k), v)
    il = inp.get("inlet") or {}
    if "clog" in il:
        add("雨水口防堵折减", "Inlet clogging factor", rules["exchange"]["inlet"]["clog_factor"], il["clog"])
    if "P" in il:
        add("雨水口周长 P", "Inlet perimeter P", round(rules["exchange"]["inlet"]["grate_weir_sides"] * rules["exchange"]["inlet"]["grate_size_m"], 3), il["P"])
    if "A" in il:
        add("雨水口开孔面积 A", "Inlet open area A", rules["exchange"]["inlet"]["grate_open_area_m2"], il["A"])
    if inp.get("ground_basis"):
        add("地面高程取法", "Ground basis", rules["exchange"]["ground_basis"], inp["ground_basis"])
    f = (inp.get("init_fill") or {}).get("frac_area")
    if f is not None:
        add("管网初始填充率", "Initial fill", rules["init_state"]["pipe_fill_fraction_of_area"], f)
    for o in inp.get("outfalls") or []:
        m = re.match(r"FIXED\s+([\d.]+)", str(o.get("boundary", "")))
        if m:
            add(f"出水口 {o.get('id')} 固定水位", f"Outfall {o.get('id')} fixed level", rules["outfalls"]["sea_level_m"], float(m.group(1)))
    for k in (inp.get("pipe_n_by_material") or {}):
        mat, n = k.split(" → ")
        add(f"管材 {mat} 糙率", f"Material {mat} n", MR.manning_for(mat, rules), float(n))
    return out


def build(job_id: str, state: str, inputs: dict | None = None, inputs_file: str | None = None, rules: dict | None = None) -> dict:
    rules = rules or MR.load()
    miss = missing_labels(rules)
    if miss:
        raise SystemExit("规则文件里这些取值没有中英文名称，先补 config/model_rules_labels.toml：\n  " + "\n  ".join(miss))
    groups = (run_group(inputs, inputs_file or "inputs_<tag>_<态>.json") if inputs else []) + rule_groups(rules)
    ck = checks(rules, inputs) if inputs else []
    return {"schema": SCHEMA, "job_id": job_id, "state": state, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "rules": {"series": rules["meta"]["series"], "version": rules["meta"]["version"], "date": rules["meta"]["date"], "file": "config/model_rules.toml"},
            "checks": {"n": len(ck), "n_mismatch": sum(1 for c in ck if not c["ok"]), "items": ck}, "groups": groups}


def write_params(out_dir, job_id: str, state: str, inputs: dict | None = None, inputs_file: str | None = None) -> Path | None:
    """写 <out_dir>/params.json；失败只打印不抛（不挡交付）。"""
    try:
        p = Path(out_dir) / "params.json"
        p.write_text(json.dumps(build(job_id, state, inputs, inputs_file), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"params.json → {p}")
        return p
    except SystemExit as e:
        print(f"params.json 失败：{e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"params.json 失败：{type(e).__name__}: {e}", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job-id"); ap.add_argument("--state", default="L1"); ap.add_argument("--inputs", type=Path); ap.add_argument("--out", type=Path)
    ap.add_argument("--check-labels", action="store_true")
    a = ap.parse_args()
    if a.check_labels:
        m = missing_labels()
        print("所有取值都有名称" if not m else "缺名称：\n  " + "\n  ".join(m))
        return 1 if m else 0
    if not (a.job_id and a.out):
        ap.error("需要 --job-id 和 --out")
    inp = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else None
    return 0 if write_params(a.out, a.job_id, a.state, inp, a.inputs.name if a.inputs else None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
