"""RD-A 主线计算进度 → console/data/flood2d_rda/progress.json（系统 RD-A「结论与待办」页的「计算进度」一节读，2026-10-26 用户：「想在系统页面里看看最新的进展」）。

只读：看二维结果文件（c2d/<运行>_<态>_2d.npz）是否已有、没有就读日志最后一个「t= X h」算进度；看优化目录里的候选 / 贪心 / 剪枝文件；
判据从 eval2d_<雨>_ex.json 读。不启动、不改任何计算。后台每 15 分钟跑一次（scratchpad/progress_loop.sh）。
用法：python tools/rda_progress.py
"""
from __future__ import annotations
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"; LOG = C2D / "logs"
OUT = ROOT / "console/data/flood2d_rda/progress.json"
SIM = {"D1": 11.0, "D2": 12.0, "D24": 34.0}
NINE = ["S33D1", "S33D2", "S33D24", "S61D1", "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]


def sname(s):
    mm, h = s[1:].split("D"); mm = {"25": "25.4", "31": "30.8"}.get(mm, mm)
    return f"{mm} mm / {h} h"


def run(storm, state):
    """一个二维算例：done / running（进度）/ queued"""
    if (C2D / f"RDA_{storm}_{state}_2d.npz").exists():
        return {"storm": storm, "label": sname(storm), "state": state, "status": "done", "pct": 100}
    f = LOG / f"RDA_{storm}_{state}.log"
    total = SIM["D" + storm.split("D")[1]]
    if f.exists():
        try:
            tail = f.read_bytes()[-4000:].decode("utf-8", "ignore")
            m = re.findall(r"t=\s*([0-9.]+)h", tail)
            if m:
                return {"storm": storm, "label": sname(storm), "state": state, "status": "running", "pct": round(min(99, float(m[-1]) / total * 100)), "t_h": float(m[-1]), "total_h": total}
        except OSError:
            pass
    return {"storm": storm, "label": sname(storm), "state": state, "status": "queued", "pct": 0}


def evals(run_dir, storm, states):
    f = ROOT / "out/l2opt" / run_dir / f"eval2d_{storm}_ex.json"
    if not f.exists():
        return {}
    E = json.loads(f.read_text(encoding="utf-8"))["states"]
    return {s: {"pass": sum(all(v["pass"].values()) for v in E[s]["hotspots"].values()), "ge030_ha": E[s]["domain"]["ge030_ha"]} for s in states if s in E}


def greedy(run_dir):
    o = ROOT / "out/l2opt" / run_dir; r = {}
    r["candidates"] = (o / "candidates.json").exists()
    if r["candidates"]:
        r["n_cand"] = len(json.loads((o / "candidates.json").read_text(encoding="utf-8"))["candidates"])
    r["screen"] = (o / "stage1_ranked.json").exists()
    if (o / "greedy_path.json").exists():
        P = json.loads((o / "greedy_path.json").read_text(encoding="utf-8")); P = P if isinstance(P, list) else P["path"]
        r["greedy_step"] = max(p["step"] for p in P); r["greedy_cost_musd"] = round(P[-1].get("cost_usd", 0) / 1e6, 1); r["greedy_n"] = len(P[-1].get("chosen", []))
    if (o / "prune.json").exists():
        PR = json.loads((o / "prune.json").read_text(encoding="utf-8"))
        r["prune"] = {"n": PR["n_final"], "cost_musd": round(PR["cost_final"] / 1e6, 1), "n_ref": PR["n_ref"], "cost_ref_musd": round(PR["cost_ref"] / 1e6, 1)}
    return r


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    items = []
    # ① 当前方案（OPTCS2）9 场雨
    cur = {s: evals("RDA_96C", s, ["L2P", "OPTCS2"]) for s in NINE}
    items.append({"key": "cur", "title": "当前方案（L2+suggested，按极端雨优化）9 场雨验证", "status": "done" if all("OPTCS2" in cur[s] for s in NINE) else "running",
                  "runs": [], "title_en": "Current scheme (L2+suggested, optimised for the extreme storm): check under 9 storms",
                  "results": [{"storm": sname(s), "storm_en": sname(s), **{k: v["pass"] for k, v in cur[s].items()}} for s in NINE]})
    # ② 档 1：设计标准雨的最小方案
    base = [run(s, st) for s in ("S25D1", "S31D2") for st in ("L1", "L2", "L2P")]
    g = greedy("RDA_D10")
    val = [run(s, "OPTD10") for s in ("S25D1", "S31D2", "S61D24")]
    steps = [{"name": "设计雨基线（L1 / L2 / 客户计划）", "name_en": "Design-storm baselines (L1 / L2 / client plan)", "done": all(r["status"] == "done" for r in base)},
             {"name": "按设计雨生成候选（三层约束）", "name_en": "Candidates for the design storms (three-layer constraints)", "done": g.get("candidates", False),
              "note": f"{g.get('n_cand')} 个可建候选" if g.get("n_cand") else "", "note_en": f"{g.get('n_cand')} buildable candidates" if g.get("n_cand") else ""},
             {"name": "一维筛选", "name_en": "1D screening", "done": g.get("screen", False)},
             {"name": "逐步挑选（20 轮）", "name_en": "Stepwise selection (20 rounds)", "done": "prune" in g or g.get("greedy_step", 0) >= 20,
              "note": f"第 {g['greedy_step']} 轮，已选 {g['greedy_n']} 项，约 {g['greedy_cost_musd']} 百万美元" if g.get("greedy_step") else "",
              "note_en": f"round {g['greedy_step']}, {g['greedy_n']} measures chosen, about USD {g['greedy_cost_musd']} M" if g.get("greedy_step") else ""},
             {"name": "回头瘦身", "name_en": "Backward pruning", "done": "prune" in g, "note": f"{g['prune']['n']} 项，约 {g['prune']['cost_musd']} 百万美元" if g.get("prune") else "",
              "note_en": f"{g['prune']['n']} measures, about USD {g['prune']['cost_musd']} M" if g.get("prune") else ""},
             {"name": "二维验收（3 场设计雨）", "name_en": "2D acceptance (3 design storms)", "done": all(r["status"] == "done" for r in val)}]
    d10 = {s: evals("RDA_D10", s, ["L1", "L2", "L2P", "OPTD10"]) for s in ("S25D1", "S31D2", "S61D24")}
    d10["S61D24"] = {**evals("RDA_96C", "S61D24", ["L1", "L2", "L2P"]), **d10["S61D24"]}
    items.append({"key": "d10", "title": "档 1：达到设计标准（10 年一遇 + 20% 气候变化）的最小方案", "title_en": "Tier 1: minimum scheme meeting the design standard (10-yr + 20 % CC)",
                  "status": "done" if steps[-1]["done"] else "running",
                  "steps": steps, "runs": base + val, "results": [{"storm": sname(s) + "（设计标准）", "storm_en": sname(s) + " (design standard)", **{k: v["pass"] for k, v in d10[s].items()}} for s in d10 if d10[s]]})
    # ③ v4：地面泵站一起参与优化
    gv = greedy("RDA_96D")
    vr = [run(s, "OPTV4") for s in ["S96D1", "S96D24"] + [x for x in NINE if x not in ("S96D1", "S96D24")]]
    v4e = {s: evals("RDA_96C", s, ["OPTCS2", "OPTV4"]) for s in NINE}
    items.append({"key": "v4", "title": "v4：地面泵站一起参与优化（与当前方案比）", "title_en": "v4: surface pump stations optimised together (compared with the current scheme)",
                  "status": "done" if all(r["status"] == "done" for r in vr) else "running",
                  "steps": [{"name": "逐步挑选 + 回头瘦身", "name_en": "Stepwise selection + backward pruning", "done": "prune" in gv,
                             "note": f"{gv['prune']['n']} 项，约 {gv['prune']['cost_musd']} 百万美元（当前方案 60 项，133.7）" if gv.get("prune") else "",
                             "note_en": f"{gv['prune']['n']} measures, about USD {gv['prune']['cost_musd']} M (current scheme: 60 measures, 133.7)" if gv.get("prune") else ""},
                            {"name": "二维验收（9 场雨）", "name_en": "2D acceptance (9 storms)", "done": all(r["status"] == "done" for r in vr)}],
                  "runs": vr, "results": [{"storm": sname(s), "storm_en": sname(s), **{k: v["pass"] for k, v in v4e[s].items()}} for s in NINE if "OPTV4" in v4e[s]]})
    # ④ 造价–效果曲线（贪心路径各轮的二维验证）
    cr = [run(s, f"G{k}") for k in (2, 4, 6, 8, 11, 14, 20) for s in ("S96D1", "S96D24")]
    items.append({"key": "curve", "title": "造价–效果曲线（逐步挑选各阶段的二维验证）", "title_en": "Cost–effect curve (2D check of each stepwise-selection stage)", "status": "done" if all(r["status"] == "done" for r in cr) else "running", "runs": cr})
    # ⑤ 按 AECOM 口径的 61 mm / 1 h 专门优化（2026-10-26 用户要求；控制雨 A61D1 = AECOM「10yrs.+20%CC 1 h」原雨型 60.7 mm）
    ga = greedy("RDA_A61")
    ab = [run("A61D1", st) for st in ("L1", "L2", "L2P")]
    av = [run(s, "OPTA61") for s in ("A61D1", "S61D1", "S61D24")]
    asteps = [{"name": "AECOM 设计雨基线（L1 / L2 / 客户计划）", "name_en": "AECOM design-storm baselines (L1 / L2 / client plan)", "done": all(r["status"] == "done" for r in ab)},
              {"name": "按 AECOM 设计雨生成候选（三层约束）", "name_en": "Candidates for the AECOM design storm (three-layer constraints)", "done": ga.get("candidates", False),
               "note": f"{ga.get('n_cand')} 个可建候选" if ga.get("n_cand") else "", "note_en": f"{ga.get('n_cand')} buildable candidates" if ga.get("n_cand") else ""},
              {"name": "一维筛选", "name_en": "1D screening", "done": ga.get("screen", False)},
              {"name": "逐步挑选（20 轮）", "name_en": "Stepwise selection (20 rounds)", "done": "prune" in ga or ga.get("greedy_step", 0) >= 20,
               "note": f"第 {ga['greedy_step']} 轮，已选 {ga['greedy_n']} 项，约 {ga['greedy_cost_musd']} 百万美元" if ga.get("greedy_step") else "",
               "note_en": f"round {ga['greedy_step']}, {ga['greedy_n']} measures chosen, about USD {ga['greedy_cost_musd']} M" if ga.get("greedy_step") else ""},
              {"name": "回头瘦身", "name_en": "Backward pruning", "done": "prune" in ga, "note": f"{ga['prune']['n']} 项，约 {ga['prune']['cost_musd']} 百万美元" if ga.get("prune") else "",
               "note_en": f"{ga['prune']['n']} measures, about USD {ga['prune']['cost_musd']} M" if ga.get("prune") else ""},
              {"name": "二维验收（AECOM 雨型、Mussafah 尖峰雨型、24 h）", "name_en": "2D acceptance (AECOM pattern, Mussafah peaked pattern, 24 h)", "done": all(r["status"] == "done" for r in av)}]
    ae = {s: evals("RDA_A61", s, ["L1", "L2", "L2P", "OPTA61"]) for s in ("A61D1", "S61D1", "S61D24")}
    items.append({"key": "a61", "title": "按 AECOM 口径的 61 mm / 1 h 专门优化（AECOM「10 年 + 20%」1 小时设计雨）",
                  "title_en": "Dedicated 61 mm / 1 h optimisation on the AECOM basis (AECOM 10-yr + 20 % CC 1 h design storm)", "status": "done" if asteps[-1]["done"] else "running",
                  "steps": asteps, "runs": ab + av,
                  "results": [{"storm": ("60.7 mm / 1 h（AECOM 雨型）" if s == "A61D1" else sname(s)), "storm_en": ("60.7 mm / 1 h (AECOM pattern)" if s == "A61D1" else sname(s)),
                               **{k: v["pass"] for k, v in ae[s].items()}} for s in ae if ae[s]]})
    # ⑥ 补地面泵站 + 设计雨验收（2026-10-27 用户同意：档 1 / AECOM 方案补地面泵站，当前方案放到三场设计雨下）
    sr = [run(s, st) for s, st in (("S25D1", "OPTD10S"), ("S31D2", "OPTD10S"), ("S61D24", "OPTD10S"), ("A61D1", "OPTA61S"), ("S61D1", "OPTA61S"),
                                   ("S61D24", "OPTA61S"), ("S25D1", "OPTCS2"), ("S31D2", "OPTCS2"), ("A61D1", "OPTCS2"))]
    for r in sr:
        if r["storm"] == "A61D1":
            r["label"] = "60.7 mm / 1 h（AECOM 雨型）"; r["label_en"] = "60.7 mm / 1 h (AECOM pattern)"
    se = {**{s: evals("RDA_D10", s, ["L2P", "OPTD10", "OPTD10S", "OPTCS2"]) for s in ("S25D1", "S31D2")},
          "A61D1": evals("RDA_A61", "A61D1", ["L2P", "OPTA61", "OPTA61S", "OPTCS2"])}
    items.append({"key": "sumps", "title": "补地面泵站（档 1、AECOM 方案）+ 当前方案在设计雨下的验收",
                  "title_en": "Surface pump stations added (Tier 1, AECOM scheme) + current scheme checked under the design storms",
                  "status": "done" if all(r["status"] == "done" for r in sr) else "running",
                  "runs": sr, "results": [{"storm": ("60.7 mm / 1 h（AECOM 雨型）" if s == "A61D1" else sname(s) + "（设计标准）"),
                                           "storm_en": ("60.7 mm / 1 h (AECOM pattern)" if s == "A61D1" else sname(s) + " (design standard)"),
                                           **{k: v["pass"] for k, v in se[s].items()}} for s in se if se[s]]})
    res = {"generated": time.strftime("%Y-%m-%d %H:%M"), "items": items,
           "labels": {"L1": "现状", "L2": "L2", "L2P": "客户计划", "OPTCS2": "当前方案", "OPTD10": "档 1 方案", "OPTV4": "v4", "OPTA61": "AECOM 61/1h 方案", "OPTD10S": "档 1 + 地面泵站", "OPTA61S": "AECOM 方案 + 地面泵站"},
           "labels_en": {"L1": "Existing", "L2": "L2", "L2P": "Client plan", "OPTCS2": "Current scheme", "OPTD10": "Tier 1 scheme", "OPTV4": "v4", "OPTA61": "AECOM 61/1h scheme",
                         "OPTD10S": "Tier 1 + surface pump stations", "OPTA61S": "AECOM scheme + surface pump stations"}}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", OUT, res["generated"], [(i["key"], i["status"]) for i in items])


if __name__ == "__main__":
    main()
