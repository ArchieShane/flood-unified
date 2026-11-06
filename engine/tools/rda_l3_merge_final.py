"""把最终方案的 L3 结果并进页面读的 l3.json（2026-10-28）。

最终方案（OPTA61PH2 = 61 mm / 1 h 推荐方案 + 3 座高后果点泵站；OPTCS3X = 96 mm 方案 + 6 座）的 L3 当时单独算在
l3_hca61.json / l3_hc96.json（tools/rda_threelines.py --tag），页面只读 l3.json。这里把这两个态原样复制进去，其余不动；原文件备份。
重跑 rda_threelines.py（不带 --tag）会重写 l3.json，之后要再跑一次本脚本。
用法：python tools/rda_l3_merge_final.py
"""
from __future__ import annotations
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = {"l3_hca61.json": "OPTA61PH2", "l3_hc96.json": "OPTCS3X"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    bak = ROOT / "out/backup" / f"l3_merge_{time.strftime('%Y%m%d_%H%M%S')}"
    for d in sorted((ROOT / "console/data").glob("flood2d_rda_*")):
        f = d / "l3.json"
        if not f.exists():
            continue
        J = json.loads(f.read_text(encoding="utf-8")); add = []
        for src, st in SRC.items():
            s = d / src
            if s.exists():
                S = json.loads(s.read_text(encoding="utf-8"))["states"].get(st)
                if S:
                    J["states"][st] = S; add.append(st)
        if add:
            (bak / d.name).mkdir(parents=True, exist_ok=True); shutil.copy2(f, bak / d.name / "l3.json")
            f.write_text(json.dumps(J, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            print(f"{d.name}: 并入 {', '.join(add)}")
    print("备份 →", bak if bak.exists() else "（无改动）")


if __name__ == "__main__":
    main()
