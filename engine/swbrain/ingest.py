"""接收预测侧的 prediction_state，把模型算出的节点/管段/出水口状态并到本系统的状态表上。

有预测结果时，建议的『严重度』改用模型量（节点冒溢量、管段满管分钟、出水口顶托分钟），
本系统自己的承载雨强只作兜底。文件放 exchange/predictions/*.json。
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from .contracts import load_prediction_state


def load_all(root: Path, cfg: dict) -> list[dict]:
    d = root / cfg["exchange"]["dir"] / "predictions"
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(load_prediction_state(f))
        except Exception as e:
            print("  跳过无效预测文件", f.name, str(e)[:120])
    return out


def baseline_states(preds: list[dict]) -> dict[str, dict]:
    """每个情景取最新的基线（intervention_set_id 为空）预测。"""
    base = {}
    for p in preds:
        if p.get("intervention_set_id"):
            continue
        sc = p["scenario"]["name"] if isinstance(p["scenario"], dict) else str(p["scenario"])
        base[sc] = p
    return base


def merge_into_states(es: pd.DataFrame, cs: pd.DataFrame, preds: dict[str, dict], node_terminal: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """把基线预测并到 es / cs：新增列 pred_*；返回 (es, cs, summary)。"""
    summary = {}
    for sc, p in preds.items():
        nodes = pd.DataFrame(p.get("nodes") or [])
        edges = pd.DataFrame(p.get("edges") or [])
        outs = pd.DataFrame(p.get("outfalls") or [])
        if len(edges) and "gid" in edges:
            e2 = edges.set_index("gid")
            for col in ("max_fill_ratio", "surcharge_min", "max_flow_m3s"):
                if col in e2:
                    es[f"pred_{col}@{sc}"] = es["gid"].map(e2[col])
        if len(nodes) and "vertex_id" in nodes:
            nodes["terminal"] = nodes["vertex_id"].map(lambda v: node_terminal.get(int(v)) if pd.notna(v) else None)
            agg = nodes.dropna(subset=["terminal"]).groupby("terminal").agg(
                **{f"pred_flood_volume_m3@{sc}": ("flood_volume_m3", "sum") if "flood_volume_m3" in nodes else ("vertex_id", "size"),
                   f"pred_flooded_nodes@{sc}": ("vertex_id", "size"),
                   f"pred_max_depth_m@{sc}": ("max_depth_m", "max") if "max_depth_m" in nodes else ("vertex_id", "size")})
            cs = cs.merge(agg, left_on="terminal", right_index=True, how="left")
        if len(outs) and "unitid" in outs and "tide_locked_min" in outs:
            cs[f"pred_tide_locked_min@{sc}"] = cs["terminal_unitid"].map(outs.set_index("unitid")["tide_locked_min"])
        summary[sc] = {"run_id": p["run_id"], "kpis": p.get("kpis", {}), "n_nodes": int(len(nodes)), "n_edges": int(len(edges)), "n_outfalls": int(len(outs))}
    return es, cs, summary


def reprioritize(props: pd.DataFrame, cs: pd.DataFrame, main: str) -> pd.DataFrame:
    """有基线预测时，用模型冒溢量重排工程类建议的严重度：同一汇水区模型冒溢越多，优先级越高。"""
    col = f"pred_flood_volume_m3@{main}"
    if col not in cs or not len(props):
        return props
    vol = cs.set_index("terminal")[col]
    vmax = float(vol.max()) if vol.notna().any() and vol.max() > 0 else None
    if not vmax:
        return props
    p = props.copy()
    sev = p["terminal"].map(vol).fillna(0.0) / vmax
    engineered = p["when_class"].isin(["资本计划", "并入在批", "事件前"])
    p.loc[engineered, "priority"] = (0.6 * p.loc[engineered, "priority"] + 40.0 * sev[engineered]).round(1)
    p.loc[engineered, "evidence"] = p.loc[engineered].apply(lambda r: {**(r["evidence"] if isinstance(r["evidence"], dict) else {}), "模型冒溢量m3": round(float(vol.get(r["terminal"], 0) or 0), 0)}, axis=1)
    p = p.sort_values("priority", ascending=False).reset_index(drop=True)
    p["rank"] = range(1, len(p) + 1)
    return p
