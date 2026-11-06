# -*- coding: utf-8 -*-
"""C1 各场雨的雨型（给报告和系统画雨型图，用户 2026-09-29）：按建模时同一套做法重算降雨过程——
雨型取客户 Mussafah 模型（Mussafah_baseline.inp）里同历时的设计雨型，按总雨量等比缩放（build_pilot.rain_series / build_c1.py 同源）；
净雨 = SCS CN 82 累计净雨（与 pilot json 的 rain_excess_mm 核对）。
产物：console/data/c1_hyeto.json = {R61|R96|D24: {rain_mm, dur_h, sim_h, dt_min, t_h[], p_mmph[], p_cum_mm[], exc_cum_mm[], peak_mmph, peak_t_h, source}}
用法：python tools/c1_hyetograph.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import build_city1d as B  # noqa: E402
import runoff as R  # noqa: E402
from workdir import WORK  # noqa: E402

SC = {"R61": (61.0, 2.0), "R96": (96.0, 2.0), "D24": (61.34, 24.0)}
DT_OUT = {2.0: 5, 24.0: 30}          # 出图时段：2 h 雨 5 分钟，24 h 雨 30 分钟


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    out = {}
    for tag, (P, dur) in SC.items():
        pj = WORK / "c2d" / f"pilot_C1_{tag}_L1.json"
        if not pj.exists():
            continue
        meta = json.loads(pj.read_text(encoding="utf-8"))
        sim_h = dur + 4.0
        shape = R.read_shape(WORK / "Mussafah_baseline.inp", dur, log=lambda *a: None)
        t, Pc = R.hyetograph(P, dur, 1.0 / 60.0, shape, 0.0, total_h=sim_h + 1.0)
        exc = R.scs_cum_excess(Pc, B.CN)
        # 核对：与模型实际用的累计净雨一致
        e_model = np.interp(t, meta["rain_t_h"], meta["rain_excess_mm"])
        err = float(np.abs(e_model - exc).max())
        k = DT_OUT[dur]; tt = np.arange(0, sim_h + 1e-9, k / 60.0)
        pc = np.interp(tt, t, Pc); ec = np.interp(tt, t, exc)
        p_int = np.diff(pc) / (k / 60.0)                       # 时段平均雨强 mm/h，记在时段起点
        i = int(np.argmax(p_int))
        out[tag] = {"rain_mm": P, "dur_h": dur, "sim_h": sim_h, "dt_min": k, "cn": B.CN,
                    "t_h": [round(float(x), 4) for x in tt[:-1]], "p_mmph": [round(float(x), 3) for x in p_int],
                    "p_cum_mm": [round(float(x), 3) for x in pc[1:]], "exc_cum_mm": [round(float(x), 3) for x in ec[1:]],
                    "peak_mmph": round(float(p_int[i]), 1), "peak_t_h": round(float(tt[i]), 3),
                    "total_mm": round(float(pc[-1]), 2), "excess_mm": round(float(ec[-1]), 2),
                    "source": f"客户 Mussafah 模型 {dur:g} h 设计雨型，按 {P:g} mm 等比缩放" + ("（SCS II 型）" if dur == 24 else ""),
                    "check_excess_max_diff_mm": round(err, 4)}
        print(f"{tag}: {P} mm / {dur:g} h，峰值 {p_int[i]:.1f} mm/h @ {tt[i]:.2f} h，累计 {pc[-1]:.2f} mm，净雨 {ec[-1]:.2f} mm，与模型净雨最大差 {err:.4f} mm")
    (ROOT / "console" / "data" / "c1_hyeto.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
