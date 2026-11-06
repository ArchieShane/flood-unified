# -*- coding: utf-8 -*-
"""Excel 工作簿出中文版（2026-10-21，客户热点表 v4.4）：格式、列宽、合并单元格照原样，文字按对照表替换。

公式改成它保存时的计算结果（原表有 2.3 万个公式，里面有按英文值比对的条件；把单元格文字译成中文后公式会算错，所以中文版只放值）。
工作表名也译（≤ 31 字符，去掉 Excel 不允许的字符）。原文件不动。
用法：python tools/xlsx_translate.py <原.xlsx> <对照表.json> <输出.xlsx> [--sheet-names <工作表名对照.json>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("table"); ap.add_argument("out"); ap.add_argument("--sheet-names", default="")
    a = ap.parse_args(argv)
    import openpyxl
    T = json.loads(open(a.table, encoding="utf-8").read())
    SN = json.loads(open(a.sheet_names, encoding="utf-8").read()) if a.sheet_names else {}
    wb = openpyxl.load_workbook(a.src)
    wv = openpyxl.load_workbook(a.src, data_only=True)
    n_tr = n_f = n_miss = n_fnone = 0
    miss = set()
    for ws in wb.worksheets:
        vs = wv[ws.title]
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("="):
                    v = vs[c.coordinate].value; n_f += 1
                    if v is None:
                        n_fnone += 1
                if isinstance(v, str):
                    s = v.strip()
                    if s in T:
                        v = v.replace(s, T[s]); n_tr += 1
                    elif s and re.search(r"[A-Za-z؀-ۿ]", s):
                        miss.add(s); n_miss += 1
                try:
                    c.value = v
                except (AttributeError, ValueError):
                    pass
        if ws.title in SN:
            ws.title = re.sub(r"[\\/*?:\[\]]", "-", SN[ws.title])[:31]
    wb.save(a.out)
    print(f"→ {a.out}：替换 {n_tr} 处，公式转值 {n_f} 个（其中没有缓存值 {n_fnone} 个），没对上 {n_miss} 处（{len(miss)} 种）")
    for s in list(miss)[:15]:
        print("   未译：", s[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
