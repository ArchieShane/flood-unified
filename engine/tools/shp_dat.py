#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推演后处理的固定一步：各态 .sww → DTS Cloud h2d（shp + RESULTSxxx.dat），目录用 job_id（和两个页面显示的 id 一致）。

输出（SHP_ROOT 与 result/ 同级，通常是 NAS 的 shp_dat/）：
    <SHP_ROOT>/<job_id>/index.json            job_id、交付名、各态与文件清单、（可选）management 的 #数字
    <SHP_ROOT>/<job_id>/<态>/shp/*.shp|shx|dbf|prj|cpg
    <SHP_ROOT>/<job_id>/<态>/dat/RESULTS001.dat …
    <SHP_ROOT>/<job_id>/<态>/h2d_index.json   转换器自己的清单，有它就算这个态转完了（可重复执行，已转的跳过）

job_id = 任务单里的 id = 交付目录名（<片区>_<雨量>mm<历时>h[_<网格>m]…），flood2d_* 的 manifest 与 management 页面都显示它。

用法（计算服务器上，只用标准库 + docker）：
    python3 tools/shp_dat.py --job-id ADM_City_61mm1h_100m_after --delivery <交付目录> --shp-root /nas/shp_dat [--run-id 1140]
    环境：H2D_IMAGE（缺省 h2d-convert:1.0，没有就用 tools/h2d/ 离线构建，要 anuga-swmm:1.0 + 同目录的 pyshp wheel）、
          H2D_ARGS（缺省 --no-csv）、SRC_CRS（缺省 EPSG:32640）、CONV_JOBS（并行数，缺省 2）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = os.environ.get("H2D_IMAGE", "h2d-convert:1.0")
H2D_ARGS = os.environ.get("H2D_ARGS", "--no-csv").split()
SRC_CRS = os.environ.get("SRC_CRS", "EPSG:32640")
STATE_KEYS = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2PLANNED": "L2planned", "L2S3": "L2+suggested"}


def discover(delivery: Path) -> dict[str, Path]:
    """交付里按文件名 …_<L1|L2|L2P|L2S3>.sww 找各态；同一态多个时取最大的。"""
    found: dict[str, Path] = {}
    for p in sorted(delivery.rglob("*.sww")):
        if p.name.startswith("._") or "_bak" in str(p):
            continue
        m = re.search(r"_(L2S3|L2PLANNED|L2P|L2|L1)\.sww$", p.name, re.I)
        if m:
            k = STATE_KEYS[m.group(1).upper()]
            if k not in found or p.stat().st_size > found[k].stat().st_size:
                found[k] = p
    return found


def ensure_image() -> None:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0:
        return
    print(f"转换镜像 {IMAGE} 不在，用 {HERE / 'h2d'} 离线构建（anuga-swmm:1.0 + pyshp + hydro2d_convert.py）")
    subprocess.run(["docker", "build", "-t", IMAGE, "-f", str(HERE / "h2d" / "Dockerfile"), str(HERE / "h2d")], check=True)


def convert(state: str, sww: Path, out_dir: Path, log: Path) -> bool:
    out = out_dir / state
    if (out / "h2d_index.json").is_file():
        print(f"  · {state} 已转过，跳过")
        return True
    part = out_dir / f"{state}.partial"
    shutil.rmtree(part, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "-e", "OMP_NUM_THREADS=1", "-v", f"{sww.parent.resolve()}:/in:ro", "-v", f"{out_dir.resolve()}:/out",
           IMAGE, f"/in/{sww.name}", "-o", f"/out/{state}.partial", "--name", "boundary", "--src-crs", SRC_CRS, *H2D_ARGS]
    with open(log, "w", encoding="utf-8") as fh:
        ok = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode == 0
    if not ok:
        print(f"  ✗ {state} 转换失败（日志 {log}）")
        return False
    shutil.rmtree(out, ignore_errors=True)
    part.rename(out)
    print(f"  ✓ {state} → {out}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--delivery", required=True, type=Path)
    ap.add_argument("--shp-root", required=True, type=Path)
    ap.add_argument("--run-id", type=int, help="management 页面上的 #数字（入库后才有；之后可以重跑本脚本补进 index.json）")
    a = ap.parse_args()
    swws = discover(a.delivery)
    if not swws:
        print(f"{a.delivery} 里没有 .sww（态命名 …_L1.sww / …_L2.sww）", file=sys.stderr)
        return 1
    ensure_image()
    job_dir = a.shp_root / a.job_id
    jobs = int(os.environ.get("CONV_JOBS", "2"))
    with ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(lambda kv: (kv[0], convert(kv[0], kv[1], job_dir, a.shp_root / "_logs" / f"{a.job_id}_{kv[0]}.log")), swws.items()))
    idx = job_dir / "index.json"
    old = json.loads(idx.read_text(encoding="utf-8")) if idx.is_file() else {}
    states = {st: {"dir": st, "source_sww": swws[st].name, "converted": (job_dir / st / "h2d_index.json").is_file()} for st in swws}
    idx.write_text(json.dumps({"job_id": a.job_id, "delivery": a.delivery.name, "run_id": a.run_id or old.get("run_id"), "src_crs": SRC_CRS,
                               "args": H2D_ARGS, "states": states}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"index.json → {idx}")
    return 0 if all(ok for _, ok in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
