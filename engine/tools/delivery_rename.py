# -*- coding: utf-8 -*-
"""交付目录里态的显示名（2026-09-28 用户定）：L2+A → L2+planned，L2+A+B → L2+suggested，L2+B 不改。

导出脚本的最后一步调用 finalize(目录)：把文件名开头的旧态名、以及 .md / .txt / .csv / .json / .xlsx 内容里的旧态名换成新名。
数据键（L2A、L2AB …）与计算目录里的原件都不动。

用法：python tools/delivery_rename.py out/交付/Mussafah_00_61mm2h out/交付/29.RB2_61mm2h
"""
from __future__ import annotations

import pathlib
import re
import sys

RE = [(re.compile(r"L2\+A\+B"), "L2+suggested"), (re.compile(r"L2\+A\+C"), "L2+planned+C"), (re.compile(r"L2\+A(?![+A-Za-z0-9])"), "L2+planned")]
TEXT = {".md", ".txt", ".csv", ".json"}


def disp(s: str) -> str:
    for r, w in RE:
        s = r.sub(w, s)
    return s


def finalize(root, log=print) -> int:
    root = pathlib.Path(root); n = 0
    # 1) 文件名（先文件后目录，深的先改）
    for p in sorted(root.rglob("*"), key=lambda q: -len(q.parts)):
        new = disp(p.name)
        if new != p.name:
            dst = p.with_name(new)
            if dst.exists():
                log(f"  ! 已存在，跳过改名：{dst}"); continue
            p.rename(dst); n += 1
    # 2) 文本内容
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in TEXT and p.stat().st_size < 50e6:
            enc = "utf-8-sig" if p.suffix.lower() == ".csv" else "utf-8"
            try:
                t = p.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
            u = disp(t)
            if u != t:
                p.write_text(u, encoding=enc); n += 1
    # 3) Excel
    from openpyxl import load_workbook
    for p in root.rglob("*.xlsx"):
        wb = load_workbook(p); ch = False
        for ws in wb.worksheets:
            t = disp(ws.title)
            if t != ws.title:
                ws.title = t; ch = True
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and "L2+A" in c.value:
                        c.value = disp(c.value); ch = True
        if ch:
            wb.save(p); n += 1
    log(f"  态名改为显示名：{n} 处（{root.name}）")
    return n


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for a in sys.argv[1:]:
        finalize(a)
