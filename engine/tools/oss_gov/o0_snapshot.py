"""开源治理引擎 PoC 第 0 步：输入快照（只读复制，不碰 ArcGIS 主线）。

  python -I tools/oss_gov/o0_snapshot.py

把 Utility Network 主线（D:/swbrain_work/exp_gov）里本 PoC 要用的输入原样复制到 D:/swbrain_work/oss_gov/input/：
  prep.gdb                     预处理后装进 UN 的要素（第 2 步产物）
  Expanded_local.gdb           本地化后的资产包（连通规则 B_Rules、端子、层级）
  Stormwater_AD_reporting.gdb  第八轮 UN 导出（真值：子网名、ISCONNECTED 等）
  Summary.geodatabase          UDMS SummarizeUNErrors 的错误明细（移动库 = SQLite）
  Stormwater_AD.geodatabase    UN 移动库本体（脏区表、子网表；只读打开副本）
  s5_report.json / remaining.json / remaining_isolated.csv / controllers_in.csv
  rda_domain.geojson           RD-A 汇水区面
只用 shutil 按字节复制，源目录只读；*.lock 文件不复制。复制前后记录大小和用时 → input/snapshot.json。
"""
import json
import shutil
import time
from pathlib import Path

SRC = Path(r"D:/swbrain_work/exp_gov")
REPO = Path(r"D:/阿布扎比/排水智能体")
DST = Path(r"D:/swbrain_work/oss_gov/input")
ITEMS = [
    (SRC / "02_prep" / "prep.gdb", "prep.gdb"),
    (SRC / "Expanded_local.gdb", "Expanded_local.gdb"),
    (SRC / "07_export" / "Stormwater_AD_reporting.gdb", "Stormwater_AD_reporting.gdb"),
    (SRC / "06_validate" / "un_errors" / "Summary.geodatabase", "Summary.geodatabase"),
    (SRC / "05_un" / "Stormwater_AD.geodatabase", "Stormwater_AD.geodatabase"),
    (SRC / "06_validate" / "s5_report.json", "s5_report.json"),
    (SRC / "06_validate" / "remaining.json", "remaining.json"),
    (SRC / "06_validate" / "remaining_isolated.csv", "remaining_isolated.csv"),
    (SRC / "06_validate" / "controllers_in.csv", "controllers_in.csv"),
    (SRC / "07_export" / "s6_report.json", "s6_report.json"),
    (REPO / "out" / "dataqc" / "RD-A" / "input" / "rda_domain.geojson", "rda_domain.geojson"),
]


def size(p):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def main():
    DST.mkdir(parents=True, exist_ok=True)
    log = []
    t0 = time.time()
    for src, name in ITEMS:
        t = time.time()
        dst = DST / name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.lock"))
        else:
            shutil.copy2(src, dst)
        log.append({"src": str(src), "dst": str(dst), "bytes": size(dst), "src_mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(src.stat().st_mtime)),
                    "s": round(time.time() - t, 2)})
        print(log[-1], flush=True)
    json.dump({"items": log, "seconds": round(time.time() - t0, 2), "at": time.strftime("%Y-%m-%d %H:%M:%S")},
              open(DST / "snapshot.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
