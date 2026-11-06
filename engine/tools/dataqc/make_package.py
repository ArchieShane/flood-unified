# -*- coding: utf-8 -*-
"""打独立运行包：对方拿到后装好 Python 依赖就能跑，不需要平台、不需要本仓库。

    python -m tools.dataqc.make_package                 # 版本号 = 日期；输出 dist/dataqc-<版本>/
    python -m tools.dataqc.make_package --zip           # 另出一个 .zip
    python -m tools.dataqc.make_package --version 1.0

包里有：
  dataqc/                  判定与报告（自包含：只有 requirements.txt 里那几个通用库，不依赖本仓库任何东西）
  dataqc.bat / dataqc.sh   启动器：一条命令跑，不用记 python -m
  使用说明.md              给使用者看的：装什么、怎么跑、参数什么意思、报告怎么看
  requirements.txt         依赖与最低版本（Python 3.10＋）
  示例配置.json            数据源配置模板（--config 可以直接改这个）
  VERSION / manifest.json  版本与每个文件的 sha256

对方先跑 `python -m dataqc.selftest` 自检，再拿真实数据跑。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # 仓库根
BAT = r"""@echo off
rem dataqc 启动器：把参数原样交给 python -m dataqc
setlocal
set HERE=%~dp0
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>nul || (
  echo 需要 Python 3.10 或更高版本。装好后再试，或直接跑：python -m dataqc
  exit /b 1
)
set PYTHONPATH=%HERE%
python -m dataqc %*
exit /b %ERRORLEVEL%
"""
SH = """#!/bin/sh
# dataqc 启动器：把参数原样交给 python -m dataqc
HERE=$(cd "$(dirname "$0")" && pwd)
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m dataqc "$@"
"""
示例配置 = {
    "pipes": "D:/data/DMT_StormWater_AbuDhabi_Processed.gdb",
    "dtm": "D:/data/AUH_DTM_5m_Z40.TIF",
    "domain": None,
    "domain_name": None,
    "clip_pipes": False,
    "dtm_scope": None,
    "tolerance": 0.5,
    "datum_offset": None,
    "thresholds": {},
    "extra": None,
    "title": None,
    "gpkg": False,
    "out": "./out/dataqc",
    "_说明": {
        "pipes": "管网：.gdb 目录、.gdb.zip、.gpkg 或 shapefile 目录；只想查 DTM 就留空",
        "dtm": "DTM：GDAL 可读的栅格（tif 等）；只想查管网就留空",
        "domain": "统计范围面（GeoJSON/GPKG/shp）；给了就只统计面内的格",
        "clip_pipes": "管网也只取与范围面相交的部分",
        "dtm_scope": "network（管网外框外扩 100 m）/ domain（范围面）/ full（全图）；留空按给的东西自动定",
        "thresholds": "阈值覆盖，键用中文名，例：{\"覆土下限\": 0.5}",
        "extra": "报告附加内容 JSON：notes / advice / dq / footer",
        "out": "输出目录；报告、result.json、issues.gpkg 都写在这里",
    },
}


def sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    版本 = datetime.date.today().strftime("%Y.%m.%d")
    ap = argparse.ArgumentParser(prog="python -m tools.dataqc.make_package")
    ap.add_argument("--version", default=版本)
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--keep", action="store_true", help="同名目录已存在时不删")
    a = ap.parse_args(argv)
    目录 = os.path.join(a.out, "dataqc-" + a.version)
    if os.path.exists(目录):
        if not a.keep:
            shutil.rmtree(目录)
        else:
            print("已存在，按 --keep 保留：", 目录)
    os.makedirs(目录, exist_ok=True)
    目的 = os.path.join(目录, "dataqc")
    shutil.copytree(HERE, 目的, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "make_package.py"))
    # 启动器与说明
    open(os.path.join(目录, "dataqc.bat"), "w", encoding="utf-8", newline="\r\n").write(BAT)
    p = os.path.join(目录, "dataqc.sh")
    open(p, "w", encoding="utf-8", newline="\n").write(SH)
    os.chmod(p, 0o755)
    说明 = os.path.join(HERE, "使用说明.md")
    if os.path.exists(说明):
        shutil.copy2(说明, os.path.join(目录, "使用说明.md"))
    json.dump(示例配置, open(os.path.join(目录, "示例配置.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    依赖 = os.path.join(HERE, "requirements.txt")
    if os.path.exists(依赖):
        shutil.copy2(依赖, os.path.join(目录, "requirements.txt"))
    open(os.path.join(目录, "VERSION"), "w", encoding="utf-8").write(a.version + "\n")
    # manifest：大小与 sha256，对方可核对
    文件 = []
    for 根, _, 名们 in os.walk(目录):
        for n in sorted(名们):
            fp = os.path.join(根, n)
            文件.append({"path": os.path.relpath(fp, 目录).replace("\\", "/"), "bytes": os.path.getsize(fp),
                        "sha256": sha256(fp)})
    try:
        提交 = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        提交 = ""
    json.dump({"name": "dataqc", "version": a.version, "git": 提交, "built": datetime.datetime.now().isoformat(timespec="seconds"),
               "files": 文件}, open(os.path.join(目录, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    总 = sum(f["bytes"] for f in 文件)
    print("打包完成：", 目录)
    print("  %d 个文件，%.1f KB" % (len(文件), 总 / 1024))
    print("  里面有：dataqc/（代码）、dataqc.bat & dataqc.sh（启动器）、使用说明.md、requirements.txt、示例配置.json、VERSION、manifest.json")
    if a.zip:
        z = 目录 + ".zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
            for 根, _, 名们 in os.walk(目录):
                for n in 名们:
                    fp = os.path.join(根, n)
                    f.write(fp, os.path.relpath(fp, os.path.dirname(目录)))
        print("  压缩包：%s（%.1f MB）" % (z, os.path.getsize(z) / 1e6))
    print("对方：解压 → pip install -r requirements.txt → python -m dataqc.selftest（自检）→ 照 使用说明.md 跑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
