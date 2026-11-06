# -*- coding: utf-8 -*-
"""跑一态水力模型，把结果落到 out/three_lines/<汇水区>.<态>.*。

    --state L1    *_baseline.inp   现状① 防线1  既有管网（由 strip_baseline.py 重建）
    --state L2    *.inp            现状② 防线2  Parsons 已规划的塘（客户交付原件）
    --state L2P   *_L2v.inp        现状③ 防线2+ 第四版（零长度孔口，已作废，只留作对照）
    --state L2B   *_L2box.inp      现状③-箱涵 地下离线调蓄箱涵，就地、重力进水
    --state L2L   *_L2lag.inp      现状③-地面塘 只落在台账开放/游憩地块，自带输水干管

坐标每一态各存一份，不能共用基准那一份：各态的节点集不一样（防线2 多出集水井 W-*、
泵出口 *_Junction，防线2+ 多出塘 NP-*，塘位还会随用地约束挪最远 658 m），
查不到坐标的节点会被**静默丢掉**——实测 Shaliela 因此漏过 35,692 m³。
"""
import argparse, pathlib, re, shutil, sys, time
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # 兄弟脚本
sys.path.insert(0, str(HERE.parent))        # swbrain 包
from workdir import OUT, WORK


def coords(inp: pathlib.Path) -> pd.DataFrame:
    """读 [COORDINATES]，UTM40N → WGS84。"""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    rows, on = [], False
    with inp.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.strip()
            if s.startswith("["):
                on = s.upper().startswith("[COORDINATES")
                continue
            if not on or not s or s.startswith(";"):
                continue
            c = s.split()
            if len(c) >= 3:
                try:
                    rows.append((c[0], float(c[1]), float(c[2])))
                except ValueError:
                    pass
    d = pd.DataFrame(rows, columns=["node", "x", "y"])
    d["lon"], d["lat"] = tr.transform(d["x"].values, d["y"].values)
    return d[["node", "lon", "lat"]]


def flood(rpt: pathlib.Path):
    """Node Flooding Summary → (节点, 洪水量 m³)；口径与 threelines.py 一致。"""
    t = rpt.read_text(encoding="utf-8", errors="replace")
    b = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
    out = []
    if b:
        for ln in b.group(1).splitlines():
            c = ln.split()
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                try:
                    out.append((c[0], float(c[-2]) * 1000))
                except ValueError:
                    pass
    return out


POND = re.compile(r"^(NP|PO|STO)[-_]?\d", re.I)
SUFFIX = {"L1": "_baseline", "L2": "", "L2P": "_L2v",
          "L2B": "_L2box", "L2L": "_L2lag"}   # 箱涵 / 地面塘

ap = argparse.ArgumentParser(description="跑一态水力模型，结果落到 out/three_lines/")
ap.add_argument("--state", choices=tuple(SUFFIX), default="L2P")
ap.add_argument("--baseline", action="store_true", help="等同 --state L1")
ap.add_argument("catchments", nargs="*", default=None)
a = ap.parse_args()
state = "L1" if a.baseline else a.state
models = a.catchments or ["Mussafah", "Shaliela", "RB3"]

for m in models:
    inp = WORK / f"{m}{SUFFIX[state]}.inp"
    if not inp.exists():
        print(f"[{time.strftime('%H:%M:%S')}] {m}: 缺 {inp.name}，跳过", flush=True)
        continue
    rpt, out = inp.with_suffix(".rpt"), inp.with_suffix(".out")
    t0 = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] {m}: 跑 {inp.name} …", flush=True)
    from swmm.toolkit import solver
    solver.swmm_run(str(inp), str(rpt), str(out))
    dt = time.perf_counter() - t0
    fl = flood(rpt)
    tot = sum(v for _, v in fl)
    pond = sum(v for n, v in fl if POND.match(n))
    street = tot - pond
    err = re.search(r"Continuity Error \(%\)\s*\.*\s+(-?\d[\d.]*)",
                    rpt.read_text(encoding="utf-8", errors="replace"))
    shutil.copy(rpt, OUT / f"{m}.{state}.rpt")
    coords(inp).to_parquet(OUT / f"{m}.{state}.nodes.parquet", index=False)
    print(f"[{time.strftime('%H:%M:%S')}] {m}: {dt / 60:.1f} min｜街道积水 {street:,.0f} m³"
          f"（塘自身溢流 {pond:,.0f}）｜溢流节点 {len(fl)}｜连续性误差 "
          f"{err.group(1) if err else '?'}%  → 已落盘", flush=True)
print("三个区全部跑完。")
