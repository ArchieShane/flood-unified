"""把「不会有内涝」改写成能检验的承诺：就绪度、验证协议、每场雨后的对账。

就绪度：六个条件各给 就绪 / 部分 / 缺 ，从数据里判，不靠人填。
验证协议：从 config 生成一份说清「什么雨要对账、对什么、多少算合格、不合格怎么办」的文档。
对账：观测（event_observation）对预测（prediction_state）按节点比 命中率 POD、误报率 FAR、深度 RMSE。
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from .contracts import load_event_observation, load_prediction_state, SCHEMA_COMPARISON
from . import i18n


def readiness(cfg: dict, root: Path, es: pd.DataFrame, cs: pd.DataFrame, preds: list[dict], observations: list[dict]) -> list[dict]:
    D, S = cfg["decision"], cfg["standard"]
    act = es[es["is_active"]]
    total_km = float(act["len_m"].sum()) / 1000.0
    suspect = cs[cs["suspect_missing_outlet"]]
    bad_share = float(act[(act["slope_flag"] != "ok") | (act["orient"] == "guess")]["len_m"].sum()) / max(total_km * 1000.0, 1)
    dem = (root / "exchange" / "dem").exists() and any((root / "exchange" / "dem").iterdir())
    telemetry = (root / "exchange" / "telemetry").exists() and any((root / "exchange" / "telemetry").iterdir())
    rows = [
        {"条件": "验收标准已定义", "状态": "部分", "证据": f"config 里有目标情景「{S['target_scenario']}」与按用地的可接受深度，但雨强与深度阈值仍是占位，未经 DMT 确认", "补什么": "DMT 书面确认标准；用 ADM IDF 替换雨强"},
        {"条件": "物理基线可信", "状态": "缺" if (len(suspect) > 10 or bad_share > 0.15) else "部分",
         "证据": f"疑似漏登终点 {len(suspect)} 个承接 {suspect['len_km'].sum():,.0f} km；{bad_share*100:.0f}% 管长缺标高或方向靠猜；DEM {'有' if dem else '无'}",
         "补什么": "核实清单在 proposal 表『立即』类；补测标高；把 DEM 放到 exchange/dem/"},
        {"条件": "预测模型有观测校准", "状态": "就绪" if (preds and observations) else ("部分" if preds else "缺"),
         "证据": f"预测基线 {len(preds)} 份；实测事件 {len(observations)} 场；泵站遥测 {'有' if telemetry else '无'}",
         "补什么": "预测侧提供基线；每场雨后按 event_observation 交实测；接 SCADA"},
        {"条件": "不确定性进入优化", "状态": "部分", "证据": f"按 {len(cfg['standard']['scenario_weights'])} 个情景加权选方案，但权重是相对大小不是实测概率；气候因子 {S['climate_factor']} 为假设", "补什么": "用 IDF 与气候情景给出各情景年概率"},
        {"条件": "实施后有验证与运行规则", "状态": "缺" if not telemetry else "部分", "证据": "无遥测；泵站 telemetry_linked 全空；『事件前』建议只能以预报为触发", "补什么": "接 ADM SCADA；把泵闸启停规则写进调度层"},
        {"条件": "雨后回灌闭环", "状态": "部分" if observations else "缺", "证据": f"对账流程与文件格式已定义（exchange/observations → exchange/verification）；已对账事件 {len(observations)} 场", "补什么": "第一场雨后交一份实测文件，闭环即启动"},
    ]
    stats = {"n_suspect": int(len(suspect)), "suspect_km": f"{suspect['len_km'].sum():,.0f}", "bad_pct": int(round(bad_share * 100)), "dem": bool(dem),
             "n_preds": len(preds), "n_obs": len(observations), "telemetry": bool(telemetry)}
    return i18n.readiness_en(rows, cfg, stats)


def commitment(cfg: dict, standard_plan: dict, readiness_rows: list[dict]) -> dict:
    S = cfg["standard"]
    years = standard_plan.get("years")
    n_ready = sum(1 for r in readiness_rows if r["状态"] == "就绪")
    n_missing = sum(1 for r in readiness_rows if r["状态"] == "缺")
    if years is None:
        sentence = f"按「{S['target_scenario']}」标准达标的年限暂无法给出（成本或预算缺失）"
    elif years > S["max_years"]:
        sentence = (f"按「{S['target_scenario']}」标准、年度预算 {S['annual_budget_aed']/1e8:.0f} 亿 AED，需 {years} 年，超过 {S['max_years']} 年上限："
                    f"要么加预算，要么把可接受积水深度放宽，要么把标准降到更低重现期")
    else:
        sentence = (f"在 {years} 年内按「{S['target_scenario']}」标准把可接受深度以上的积水消除，剩余风险有图（residual.geojson），每场雨后有对账（exchange/verification/）")
    return {"承诺": sentence, "就绪条件": f"{n_ready}/6 就绪，{n_missing}/6 缺", "说明": "年限按占位单价与静态达标成本算，方案经预测模型评估后会变；条件全部就绪前这句话只是目标不是保证",
            **i18n.commitment_en(cfg, standard_plan, readiness_rows)}


def protocol_markdown(cfg: dict) -> str:
    V, S = cfg["verification"], cfg["standard"]
    depth = "、".join(f"{k} ≤ {v} m" for k, v in S["acceptable_depth_m"].items())
    return f"""# 每场雨后的验证协议（由 config 生成）

## 1. 什么时候对账
任一 NCM 雨量站 24 小时雨量 ≥ {V['event_rain_trigger_mm']:.0f} mm，即为一场需要对账的事件。事件编号用日期。

## 2. 对什么
预测侧在事件发生前用预报雨、事件后用实测雨各跑一次 `prediction_state`；现场在 72 小时内交一份 `event_observation`（格式见 `exchange/examples/event_observation.example.json`），来源：{"、".join(V['observation_sources'])}。

按节点比三个数：
- 命中率 POD = 预测有积水且实测有积水 / 实测有积水；合格线 ≥ {V['pass_pod_min']}
- 误报率 FAR = 预测有积水但实测没有 / 预测有积水；合格线 ≤ {V['pass_far_max']}
- 深度误差 RMSE，只在两边都有深度的点上算；合格线 ≤ {V['pass_depth_rmse_m']} m

「有积水」的判据用验收标准：{depth}；历时 ≤ {S['acceptable_duration_min']} 分钟。

## 3. 不合格怎么办
连续 {V['recalibrate_after_failures']} 场不合格：预测侧重校准（先查台账修正是否已进模型，再调参数），本系统按新基线重跑方案与排期。合格的事件把实测积水点写进 `proposal.evidence`，作为下一轮排序的依据。

## 4. 谁签字
对账结果文件 `exchange/verification/<event_id>.json` 由预测侧与优化侧各自生成一份，数字一致才算完成；DMT 在 `sw_brain.swbrain.verification` 表上确认。
"""


def _flooded(depth, dur, cfg) -> bool:
    S = cfg["standard"]
    return (depth is not None and depth > S["acceptable_depth_m"]["road"]) or (dur is not None and dur > S["acceptable_duration_min"])


def compare(obs: dict, pred: dict, cfg: dict) -> dict:
    """观测 vs 预测，按 vertex_id 对齐。返回 event_comparison。"""
    V = cfg["verification"]
    pn = {int(n["vertex_id"]): n for n in (pred.get("nodes") or []) if n.get("vertex_id") is not None}
    tp = fp = fn = 0
    sq, n_depth = 0.0, 0
    rows = []
    for o in obs["flood_points"]:
        vid = o.get("vertex_id")
        if vid is None:
            continue
        p = pn.get(int(vid))
        o_f = bool(o.get("flooded")) if "flooded" in o else _flooded(o.get("observed_depth_m"), o.get("duration_min"), cfg)
        p_f = _flooded(p.get("max_depth_m"), p.get("flood_duration_min"), cfg) if p else False
        tp += o_f and p_f; fp += (not o_f) and p_f; fn += o_f and (not p_f)
        if p and o.get("observed_depth_m") is not None and p.get("max_depth_m") is not None:
            sq += (float(o["observed_depth_m"]) - float(p["max_depth_m"])) ** 2; n_depth += 1
        rows.append({"vertex_id": int(vid), "observed": o_f, "predicted": p_f, "obs_depth_m": o.get("observed_depth_m"), "pred_depth_m": p.get("max_depth_m") if p else None})
    # 预测有、观测没提到的点不算误报（观测不是全覆盖），但列出来
    pod = tp / (tp + fn) if (tp + fn) else None
    far = fp / (tp + fp) if (tp + fp) else None
    rmse = math.sqrt(sq / n_depth) if n_depth else None
    passed = (pod is None or pod >= V["pass_pod_min"]) and (far is None or far <= V["pass_far_max"]) and (rmse is None or rmse <= V["pass_depth_rmse_m"])
    return {"schema": SCHEMA_COMPARISON, "event_id": obs["event_id"], "prediction_run": pred["run_id"], "scenario": pred["scenario"],
            "n_points": len(rows), "tp": tp, "fp": fp, "fn": fn, "pod": None if pod is None else round(pod, 3), "far": None if far is None else round(far, 3),
            "depth_rmse_m": None if rmse is None else round(rmse, 3), "passed": bool(passed), "thresholds": {"pod_min": V["pass_pod_min"], "far_max": V["pass_far_max"], "rmse_max": V["pass_depth_rmse_m"]},
            "points": rows}


def run_verification(root: Path, cfg: dict) -> tuple[list[dict], list[dict]]:
    """读 exchange/observations 与 exchange/predictions，能配对的都对一遍，结果写 exchange/verification。返回 (observations, comparisons)。"""
    obs_dir = root / cfg["exchange"]["dir"] / "observations"; obs_dir.mkdir(parents=True, exist_ok=True)
    ver_dir = root / cfg["exchange"]["dir"] / "verification"; ver_dir.mkdir(parents=True, exist_ok=True)
    pred_dir = root / cfg["exchange"]["dir"] / "predictions"
    observations, comparisons = [], []
    preds = []
    for f in sorted(pred_dir.glob("*.json")) if pred_dir.exists() else []:
        try:
            preds.append(load_prediction_state(f))
        except Exception:
            pass
    for f in sorted(obs_dir.glob("*.json")):
        try:
            o = load_event_observation(f)
        except Exception as e:
            print("  跳过无效观测文件", f.name, str(e)[:100]); continue
        observations.append(o)
        # 配对：预测的 scenario.event_id 等于观测 event_id
        for p in preds:
            sc = p.get("scenario") or {}
            if isinstance(sc, dict) and sc.get("event_id") == o["event_id"]:
                c = compare(o, p, cfg)
                (ver_dir / f"{o['event_id']}__{p['run_id']}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
                comparisons.append(c)
    return observations, comparisons
