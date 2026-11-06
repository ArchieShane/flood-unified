"""标准化结果画像：每层、每类、每个字段的完整率和置信度分布，写成 md。

  python -m tools.dgov.profile --gpkg out/dgov/std/dmt_stormwater__rda.gpkg [--gpkg ...] --out out/dgov/std/画像_rda.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from .schema import LAYERS

KEY = {"node": ["rim_elev", "invert_elev"], "link": ["from_src", "to_src", "diameter_mm", "upelev", "downelev", "length_attr", "material", "manning_n"],
       "pump": ["flow_lps", "head_m", "on_elev", "off_elev", "curve_id", "wetwell_src"], "storage": ["volume_m3", "depth_m", "floor_elev", "rim_elev"],
       "catchment": ["area_m2", "cn", "tc_min", "out_node_src"]}


def profile(paths):
    import pyogrio
    L = ["# 标准化结果画像", ""]
    for p in paths:
        p = Path(p)
        log = json.loads(p.with_suffix(".log.json").read_text(encoding="utf-8")) if p.with_suffix(".log.json").exists() else {}
        L += [f"## {p.name}", "", f"来源 `{log.get('source')}`，范围 `{log.get('extent')}`，标准 v{log.get('std_version')}", ""]
        for lay in [x[0] for x in pyogrio.list_layers(p)]:
            if lay not in LAYERS:
                continue
            df = pyogrio.read_dataframe(p, layer=lay, read_geometry=False)
            kcol = lay + "_kind"
            groups = df.groupby(kcol) if kcol in df.columns else [(lay, df)]
            L += [f"### {lay}（{len(df)} 行）", "", "| 类 | 行数 | " + " | ".join(KEY[lay]) + " |", "|---|---|" + "---|" * len(KEY[lay])]
            for k, g in groups:
                cells = []
                for f in KEY[lay]:
                    if f not in g.columns:
                        cells.append("—")
                        continue
                    fl = Counter(json.loads(c).get(f, "—") for c in g["conf"])
                    have = len(g) - fl.get("missing", 0) - fl.get("—", 0)
                    dist = "/".join(f"{x}{fl[x]}" for x in "ABCDE" if fl.get(x))
                    cells.append(f"{100 * have / len(g):.0f}%" + (f"（{dist}）" if dist and set(fl) - {"B", "C", "missing"} else ""))
                L.append(f"| {k} | {len(g)} | " + " | ".join(cells) + " |")
            L.append("")
        fb = {lay_: {f: v.get("fallback_raw") for f, v in d["fields"].items() if v.get("fallback_raw")} for lay_, d in log.get("layers", {}).items()}
        fb = {k: v for k, v in fb.items() if v}
        if fb:
            L += ["换算后不合理、按原值兜底（置信度 D）的：", ""] + [f"- `{k}`：{v}" for k, v in fb.items()] + [""]
        um = {f"{lay_}.{f}": v["unmapped"] for lay_, d in log.get("layers", {}).items() for f, v in d["fields"].items() if v.get("unmapped")}
        if um:
            L += ["值对照里没有、当成缺失的原值：", ""] + [f"- `{k}`：{v}" for k, v in um.items()] + [""]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.profile")
    ap.add_argument("--gpkg", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    Path(a.out).write_text(profile(a.gpkg), encoding="utf-8")
    print("→", a.out)


if __name__ == "__main__":
    main()
