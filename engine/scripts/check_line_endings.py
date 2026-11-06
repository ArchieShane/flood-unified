# -*- coding: utf-8 -*-
"""仓库里存的（git 索引里的）文本文件必须是 LF。

两个坑，都在 2026-09-23 踩过：
  1. 原来 CI 里写的是 `grep $'\\r'`。Windows 上 MSYS 的 grep 按文本模式读，CR 会被吃掉，
     给它一个已知的 CRLF 文件也报不出来——本地跑等于没跑。
  2. 直接读工作区也不对：.gitattributes 声明了 `text=auto eol=lf`，Windows 上 checkout 出来
     就是 CRLF，本地必然一片红，而 CI（Linux）必然全绿。两边结论相反。
真正的不变量是「索引里是 LF」，所以看 `git ls-files --eol` 的 i/ 列。

  python scripts/check_line_endings.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ci.ps1 在工作区必须是 CRLF + BOM：PowerShell 5.1 读 UTF-8 无 BOM 会按 ANSI 解，中文注释直接让它语法错。
# 索引里仍是 LF（.gitattributes 负责 checkout 时转回 CRLF），所以不用排除。


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    ga = root / ".gitattributes"
    if not ga.exists() or "eol=lf" not in ga.read_text(encoding="utf-8", errors="replace"):
        print("缺 .gitattributes 里的 `eol=lf`：没有它，索引里存什么取决于是谁在哪台机器上提交的")
        return 1

    out = subprocess.run(["git", "ls-files", "--eol"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        print("git ls-files --eol 跑不了：", (out.stderr or "").strip()[:200])
        return 1

    bad, n = [], 0
    for line in (out.stdout or "").splitlines():
        if "\t" not in line:
            continue
        flags, path = line.split("\t", 1)
        idx = next((f[2:] for f in flags.split() if f.startswith("i/")), "")
        if idx in ("", "none", "-text"):        # 二进制或没内容，不管
            continue
        n += 1
        if idx != "lf":
            bad.append(f"{path.strip()}（索引里是 {idx}）")

    if bad:
        print(f"索引里不是 LF 的文件 {len(bad)} 个：")
        for b in bad:
            print("  " + b)
        return 1
    print(f"行尾检查：{n} 个文本文件在索引里都是 LF，.gitattributes 的 eol=lf 在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
