#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把交付目录的文件夹名和文件名中的中文改成英文（只改名字，不改文件内容）。
用法：
  python3 rename_en.py <目录>            # 只预览（dry-run），列出会怎么改
  python3 rename_en.py <目录> --apply    # 真改
注意：只对「拷贝出来的那份」用；本脚本拒绝改路径里含 /result/ 的目录（原始交付里的中文名是下游读取程序约定的，改了会读不到）。
文件里面的内容（说明.md 的正文、csv 的表头、geojson 的属性等）仍是中文，本脚本不翻译。"""
import os
import re
import sys

# 长的在前（先换长词，避免「统计值」被「统计」抢先换掉）
MAP = [
    ("艾因_模型版", "AlAin_Model"),
    ("对比_缺口分析", "Comparison_Analysis"),
    ("积水点结果表", "Flood_Points_Table"),
    ("积水点图层", "Flood_Points_Layer"),
    ("耦合运行记录", "coupling_run_log"),
    ("一维报告", "1D_report"),
    ("逐帧水深", "depth_frames"),
    ("最大水深", "max_depth"),
    ("已建成调蓄", "Built_Ponds"),
    ("既有管网", "Existing_Network"),
    ("现状泵站", "existing_pumps"),
    ("已建成塘", "built_ponds"),
    ("输入参数", "input_parameters"),
    ("计算设置", "Computation_Settings"),
    ("统计值", "Statistics"),
    ("积水点", "Flood_Points"),
    ("计算域", "domain"),
    ("热点", "hotspots"),
    ("统计", "Statistics"),
    ("公共", "Common"),
    ("说明", "README"),
    ("一维", "1D"),
    ("二维", "2D"),
]
CJK = re.compile(r"[一-鿿]")


def new_name(n: str) -> str:
    for zh, en in MAP:
        n = n.replace(zh, en)
    return n


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    root = os.path.abspath(sys.argv[1]); apply = "--apply" in sys.argv[2:]
    if "/result/" in root.replace("\\", "/") + "/":
        print("✗ 拒绝：路径里含 /result/（那是原始交付，下游按中文名读取）。请先拷贝到别的目录再改。"); return 1
    if not os.path.isdir(root):
        print("✗ 不是目录：" + root); return 1
    todo, bad = [], []
    for dp, dns, fns in os.walk(root, topdown=False):       # 自底向上：先改文件和子目录，再改上层目录
        for n in fns + dns:
            nn = new_name(n)
            if nn != n:
                todo.append((os.path.join(dp, n), os.path.join(dp, nn)))
            if CJK.search(nn):
                bad.append(os.path.join(dp, nn))
    seen = {}
    for a, b in todo:
        seen.setdefault(b, []).append(a)
    clash = {b: v for b, v in seen.items() if len(v) > 1 or os.path.exists(b)}
    for a, b in todo:
        print(("  " if apply else "  [预览] ") + os.path.relpath(a, root) + "  →  " + os.path.basename(b))
    if clash:
        print("\n✗ 改名后会重名/已存在，没有改任何东西：")
        for b, v in clash.items():
            print("   ", os.path.relpath(b, root), "←", [os.path.relpath(x, root) for x in v])
        return 1
    if bad:
        print("\n! 这些名字里仍有中文（映射表里没有对应词），请告诉我怎么译：")
        for x in bad:
            print("   ", os.path.relpath(x, root))
    if apply:
        for a, b in todo:
            os.rename(a, b)
        print(f"\n✓ 已改 {len(todo)} 个名字（文件夹 + 文件）")
    else:
        print(f"\n（预览）共 {len(todo)} 个名字要改；确认后加 --apply 真改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
