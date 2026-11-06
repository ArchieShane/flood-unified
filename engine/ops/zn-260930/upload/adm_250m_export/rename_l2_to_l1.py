#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把导出的客户英文包里「文件名 / 目录名」中的 L2 改成 L1（2026-10-20 用户要求：只改名称，文件内容不动）。

只在导出目录（adm250_export_en.py 建的，有 .adm250_export_en 标记）里改名，原始成果（NAS）不碰。
改什么：路径各段里独立出现的 L2（前后不是字母 / 数字）→ L1，例如
  L2_built_storage/            → L1_built_storage/
  AX_CITY_M61H1_L2.inp         → AX_CITY_M61H1_L1.inp
  AX_CITY_M61H1_L2_2d.npz      → AX_CITY_M61H1_L1_2d.npz
  L2_coupled_run_log.json      → L1_coupled_run_log.json
不改什么：文件内容（csv 表头、json、md 正文、.inp 里的内容仍写着 L2 / built ponds）；顶层 README.md、各场 00_README.md、calc_settings.md 的正文也不改。
会同步的：MANIFEST.csv 里的路径（大小和 sha256 不变，因为内容没变）。--files-only 只改文件名、目录名不动。
安全：缺省只预览；加 --apply 才真改；目标名已存在就整批不改；中途失败自动回滚；改完把「旧路径 → 新路径」写到导出目录的【外面】（<导出目录名>_rename_log.tsv，不会混进发给客户的包里）。
还原：--reverse --apply（把名字里独立出现的 L1 改回 L2；这个包原本没有 L1 名字，所以是安全的）。
用法（宿主机 python3，只用标准库）：
  python3 rename_l2_to_l1.py /导出目录                    # 预览
  python3 rename_l2_to_l1.py /导出目录 --apply            # 真改（文件名 + 目录名）
  python3 rename_l2_to_l1.py /导出目录 --apply --files-only
  python3 rename_l2_to_l1.py /导出目录 --reverse --apply  # 改回 L2
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

MARK = ".adm250_export_en"


def make_tok(reverse: bool):
    src, dst = ("L1", "L2") if reverse else ("L2", "L1")
    rx = re.compile(r"(?<![A-Za-z0-9])" + src + r"(?![A-Za-z0-9])")
    return lambda name: rx.sub(dst, name)


def plan(root: Path, ren, files_only: bool) -> list:
    """[(旧相对路径, 新相对路径)]：文件在前、目录在后（深的在前），逐个改名时每个源路径都还存在。"""
    items = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if rel.name in (MARK, "MANIFEST.csv"):
            continue
        if p.is_dir() and files_only:
            continue
        n = ren(rel.name)
        if n != rel.name:
            items.append((p.is_dir(), len(rel.parts), rel, rel.with_name(n)))
    items.sort(key=lambda t: (t[0], -t[1]))
    return [(a, b) for _, _, a, b in items]


def final_path(rel: Path, ren, files_only: bool) -> Path:
    parts = list(rel.parts)
    return Path(*[ren(x) if (not files_only or i == len(parts) - 1) else x for i, x in enumerate(parts)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--files-only", action="store_true", help="只改文件名，目录名不动")
    ap.add_argument("--reverse", action="store_true", help="L1 改回 L2")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if not (root / MARK).exists():
        raise SystemExit(f"✗ {root} 不是导出目录（没有 {MARK} 标记）：只对 adm250_export_en.py 导出的目录用")
    ren = make_tok(a.reverse)
    pairs = plan(root, ren, a.files_only)
    if not pairs:
        print("没有需要改名的（路径里已经没有独立的 " + ("L1" if a.reverse else "L2") + "）")
        return 0
    seen, bad = set(), []
    for src, dst in pairs:
        if (root / dst).exists():
            bad.append(f"目标已存在：{dst}")
        if dst in seen:
            bad.append(f"重复目标：{dst}")
        seen.add(dst)
    if bad:
        print("\n".join("  ✗ " + b for b in bad[:20]))
        raise SystemExit("✗ 有冲突，整批不改")
    nf = sum(1 for s, _ in pairs if (root / s).is_file())
    print(f"改名：{len(pairs)} 项（文件 {nf}，目录 {len(pairs) - nf}）" + ("" if a.apply else "——只是预览，加 --apply 才真改"))
    for src, dst in pairs[:14]:
        print(f"  {src.as_posix()}  ->  {dst.name}")
    if len(pairs) > 14:
        print(f"  … 还有 {len(pairs) - 14} 项")
    if not a.apply:
        return 0

    log_rows = [(str(s.as_posix()), final_path(s, ren, a.files_only).as_posix()) for s, _ in pairs]
    done = []
    try:
        for src, dst in pairs:
            os.rename(root / src, root / dst)
            done.append((src, dst))
    except OSError as e:
        for src, dst in reversed(done):
            os.rename(root / dst, root / src)
        raise SystemExit(f"✗ 改名失败（{e}），已回滚")

    logf = root.parent / f"{root.name}_rename_log.tsv"          # 放在包外面，别发给客户
    with open(logf, "a" if logf.exists() else "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        if fh.tell() == 0:
            w.writerow(["old", "new"])
        w.writerows(log_rows)
    mf = root / "MANIFEST.csv"
    if mf.exists():
        rows = list(csv.reader(open(mf, encoding="utf-8")))
        out = [rows[0]] + [[final_path(Path(r[0]), ren, a.files_only).as_posix()] + r[1:] for r in rows[1:]]
        with open(mf, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(out)
        print("  MANIFEST.csv 路径已同步")
    print(f"  改名日志：{logf}（在导出目录外面）")
    left = [p for p in root.rglob("*") if re.search(r"(?<![A-Za-z0-9])" + ("L1" if a.reverse else "L2") + r"(?![A-Za-z0-9])", p.name)]
    print(f"✓ 完成：改了 {len(done)} 项；剩下名字里还有独立 {'L1' if a.reverse else 'L2'} 的：{len(left)}" + (f"（{', '.join(p.name for p in left[:5])}）" if left else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
