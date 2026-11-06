"""把 RD-A 推演成果复制到另一个目录，并把复制出来的所有文件夹名、文件名改成英文（原目录不动）。
用法：python3 tools/export_english.py <NAS 根目录> <目标目录> [--region RDA] [--dry-run]
复制的内容（相对 NAS 根目录）：deliveries/RDA_*、shp_dat/RDA_*、result/*/runtime/console_data/flood2d_ad_rda_*。
只改「名字」，不改文件内容：outputs.json / params.json 等里面的中文说明和路径文本原样保留。
改名后的副本页面认不出（入库代码按中文目录名识别），要入库请用原目录。
改完检查整棵目录，还有中文名的会列出来，退出码 1。"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# 长的在前（按子串替换，长词先换，避免「设施清单」被「设施」先换掉）
NAMES = [
    ("L2_雨前预置临时设施", "L2_pre_deployed_temporary_facilities"),
    ("L2+planned_计划设施", "L2+planned_planned_facilities"),
    ("L1_现状", "L1_existing"),
    ("二维水深与流速时序", "2D_depth_velocity_timeseries"),
    ("一二维耦合_SWMM报告", "1D2D_coupled_SWMM_report"),
    ("SWMM一维管网", "SWMM_1D_network"),
    ("耦合交换记录", "coupling_exchange_record"),
    ("判据评估", "criteria_assessment"),
    ("客户热点", "client_hotspots"),
    ("二维结果", "2D_result"),
    ("设施清单", "facility_list"),
    ("计算域", "compute_domain"),
    ("一二维", "1D2D"),
    ("公共", "shared"),
    ("设施", "facilities"),
]
CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def en(name: str) -> str:
    for zh, e in NAMES:
        name = name.replace(zh, e)
    return name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nas"); ap.add_argument("dest"); ap.add_argument("--region", default="RDA"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    nas, dest = Path(a.nas).resolve(), Path(a.dest).resolve()
    if not nas.is_dir():
        sys.exit(f"NAS 目录不存在：{nas}")
    if dest == nas or nas in dest.parents or dest in nas.parents:
        sys.exit("目标目录不能在 NAS 目录里面，也不能包住它")
    r = a.region
    srcs = sorted(nas.glob(f"deliveries/{r}_*")) + sorted(nas.glob(f"shp_dat/{r}_*")) + sorted(nas.glob(f"result/*/runtime/console_data/flood2d_ad_{r.lower()}_*"))
    srcs = [s for s in srcs if s.is_dir() and "_old_test_" not in str(s)]
    if not srcs:
        sys.exit("没有找到要复制的成果目录")
    n = left = 0
    bad: list[str] = []
    for s in srcs:
        for f in sorted(p for p in s.rglob("*") if p.is_file() and not p.name.startswith("._")):
            rel = f.relative_to(nas)
            out = dest.joinpath(*[en(x) for x in rel.parts])
            if any(CJK.search(x) for x in out.relative_to(dest).parts):
                bad.append(str(out.relative_to(dest)))
            n += 1
            if a.dry_run:
                if CJK.search(str(rel)):
                    print(f"{rel}  →  {out.relative_to(dest)}")
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
    print(f"{'将复制' if a.dry_run else '已复制'} {n} 个文件，来自 {len(srcs)} 个成果目录 → {dest}")
    if bad:
        print(f"还有 {len(bad)} 个路径含中文（映射表里没有的名字），前 20 个：")
        for b in bad[:20]:
            print("  ", b)
        return 1
    print("检查通过：目标目录下所有文件夹名和文件名都不含中文")
    return 0


if __name__ == "__main__":
    sys.exit(main())
