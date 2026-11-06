# -*- coding: utf-8 -*-
"""推演结束后：把各个成果数据的输出目录列出来（存在与否、大小、关键文件），写进终端，也可以存成 json。

每个场景（S33D1 → job_id RDA_33mm1h）的成果：
  deliveries/<job_id>/                  交付（npz、params.json、ponding_ge.json …），management 入库读
  result/<主机>/runtime/console_data/flood2d_ad_<区域>_m<雨量>h<历时>g<网格>/   逐帧图，flood-viewer 读
  shp_dat/<job_id>/<态>/{shp,dat}       shp + dat（DTS Cloud h2d）
目录约定见 config/servers.toml 的 [nas]。

用法：python tools/report_outputs.py --region RDA --scenarios S33D1,S61D24 [--nas /nas] [--host <主机名>] [--grid 15] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def job_id(region: str, tag: str, state: str = "L1") -> str:
    m = re.fullmatch(r"S(\d+(?:\.\d+)?)D(\d+)", tag)
    if not m:
        raise SystemExit(f"场景格式应为 S<雨量>D<历时>[@态]：{tag}")
    return f"{region}_{m.group(1)}mm{m.group(2)}h"          # 多态交付：各态共用一个 job_id（不带态后缀）


def size_of(p: Path) -> str:
    n = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else (p.stat().st_size if p.exists() else 0)
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--nas", default="/nas")
    ap.add_argument("--host", default=socket.gethostname())
    ap.add_argument("--grid", type=int, default=15, help="flood2d 目录名里的网格米数")
    ap.add_argument("--state", default="L1")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    lay = tomllib.loads((ROOT / "config" / "servers.toml").read_text(encoding="utf-8")).get("nas", {})
    nas = Path(a.nas)
    out = []
    for token in (t.strip() for t in a.scenarios.split(",") if t.strip()):
        tag, _, st = token.partition("@")
        st = st or a.state
        jid = job_id(a.region, tag, st)
        mm, h = re.fullmatch(r"S(\d+(?:\.\d+)?)D(\d+)", tag).groups()
        items = {
            "deliveries": nas / lay.get("deliveries", "deliveries") / jid,
            "flood2d (flood-viewer)": nas / lay.get("result", "result") / a.host / "runtime" / "console_data" / f"flood2d_ad_{a.region.lower()}_m{mm.replace('.', 'p')}h{h}g{a.grid}",
            "shp_dat": nas / lay.get("shp_dat", "shp_dat") / jid / st,
        }
        files = {"params.json": items["deliveries"] / "params.json", f"GRID_{a.grid}m.txt（网格精度标记）": items["deliveries"] / f"GRID_{a.grid}m.txt", "ponding_ge.json": items["deliveries"] / "ponding_ge.json",
                 "shp": items["shp_dat"] / "shp", "dat": items["shp_dat"] / "dat"}
        out.append({"scenario": token, "state": st, "grid_m": a.grid, "job_id": jid, "outputs": {k: {"path": str(v), "exists": v.exists(), "size": size_of(v) if v.exists() else None} for k, v in items.items()},
                    "key_files": {k: {"path": str(v), "exists": v.exists()} for k, v in files.items()}})
    print(f"\n==== 成果输出目录（区域 {a.region}，态 {a.state}）====")
    for o in out:
        print(f"\n[{o['scenario']}] 态 {o['state']}  网格 {o['grid_m']} m  job_id = {o['job_id']}")
        for k, v in o["outputs"].items():
            print(f"  {'✓' if v['exists'] else '✗'} {k:<24s} {v['path']}" + (f"  ({v['size']})" if v["size"] else "  （不存在）"))
        missing = [k for k, v in o["key_files"].items() if not v["exists"]]
        if missing:
            print(f"    缺关键文件：{', '.join(missing)}")
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n（已写 {a.json}）")
    return 0 if all(v["exists"] for o in out for v in o["outputs"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
