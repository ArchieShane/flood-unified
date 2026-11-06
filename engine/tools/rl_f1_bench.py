"""强化学习第 2 步：F 系列一维单次耗时实测（2026-10-30 用户同意）。

拷贝 F 系列一维 inp（$WORK/RDAG_A61D1_<态>.inp）到 $WORK/bench/，原文件不动；跑 SWMM 一维（不耦合二维），记录墙钟、连续性误差、总溢流。
另做截短版（雨后只算 2 h，END_TIME 03:00）：样本生成可能只需要雨期 + 雨后几小时的溢流指标，看能省多少。
用法：python tools/rl_f1_bench.py [--states L1F,OPTA61PH2F] [--threads 6]
产物：out/l2s_rl/bench_F1.json
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from workdir import WORK  # noqa: E402


def run(inp: pathlib.Path):
    from swmm.toolkit import solver
    rpt, out = inp.with_suffix(".rpt"), inp.with_suffix(".out")
    t0 = time.time(); solver.swmm_run(str(inp), str(rpt), str(out)); dt = time.time() - t0
    txt = rpt.read_text(encoding="utf-8", errors="ignore")
    ce = re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", txt)
    fl = re.search(r"Flooding Loss \.+\s+([\d.]+)\s+([\d.]+)", txt)
    return {"wall_s": round(dt, 1), "continuity_runoff_pct": float(ce[0]) if len(ce) > 1 else None, "continuity_flow_pct": float(ce[-1]) if ce else None,
            "flooding_loss": fl.group(0).split()[-2:] if fl else None}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--states", default="L1F,OPTA61PH2F"); ap.add_argument("--threads", type=int, default=6); a = ap.parse_args()
    bd = WORK / "bench"; bd.mkdir(exist_ok=True)
    res = {"generated": time.strftime("%Y-%m-%d %H:%M"), "threads": a.threads, "runs": []}
    for st in a.states.split(","):
        src = WORK / f"RDAG_A61D1_{st}.inp"
        for tag, end in (("full_11h", None), ("short_3h", "03:00:00")):
            dst = bd / f"bench_{st}_{tag}.inp"; shutil.copy2(src, dst)
            t = dst.read_text(encoding="utf-8", errors="ignore")
            t = re.sub(r"^THREADS\s+\d+", f"THREADS  {a.threads}", t, flags=re.M)
            if end:
                t = re.sub(r"^END_TIME\s+\S+", f"END_TIME  {end}", t, flags=re.M)
            dst.write_text(t, encoding="utf-8")
            r = {"state": st, "case": tag, **run(dst)}
            res["runs"].append(r); print(r, flush=True)
    o = ROOT / "out/l2s_rl/bench_F1.json"; o.parent.mkdir(parents=True, exist_ok=True)
    o.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"); print("→", o)


if __name__ == "__main__":
    main()
