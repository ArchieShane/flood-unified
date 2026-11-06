"""2026-10-30：恢复 10-29 23:19 被另一窗口 checkout main + git stash（随后丢弃）弄丢的工作区。

来源：
  B = 分支 data-governance-rda-pilot（efb9c06）：已跟踪文件的修改（那个窗口 git add -u 提交进了分支）
  S = 丢弃的 stash 8e039682（已钉在 recover/stash-20261029-2319）：当时未跟踪 / 已暂存的新文件
  M = 当前 main（4024954）= 磁盘上已跟踪文件的现状；MB = main 与分支的分叉点 1f457ae
规则（不提交、不切分支、不删任何文件）：
  ① 只在分支改过的文件：磁盘 == main 版本 → 换成分支版本；否则列为冲突
  ② 两边都改过的文件：git merge-file 三方合并（磁盘 / MB / 分支），无冲突才写回，有冲突列出
  ③ stash 里的新文件：磁盘没有 → 恢复；磁盘有且相同 → 跳过；磁盘有且不同 → 列出，不覆盖
覆盖前把磁盘现版本备份到 out/backup/recover_20261030/。
用法：python tools/recover_20261029.py [--apply]
"""
from __future__ import annotations
import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
B, S, M = "efb9c06", "8e039682", "4024954"
BAK = ROOT / "out/backup/recover_20261030"


def git(*a, binary=False):
    r = subprocess.run(["git", "-c", "core.quotepath=false", *a], cwd=ROOT, capture_output=True)
    if r.returncode not in (0, 1):
        raise RuntimeError(r.stderr.decode("utf-8", "replace"))
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def blob(rev, path):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def names(a, b, flt=None):
    out = git("diff", "--name-status", "--no-renames", a, b)
    rows = [ln.split("\t", 1) for ln in out.splitlines() if ln.strip()]
    return [(s, p) for s, p in rows if flt is None or s in flt]


def same(p: pathlib.Path, data: bytes):
    return p.exists() and p.read_bytes().replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    MB = git("merge-base", M, B).strip()
    m_ch = {p for _, p in names(MB, M)}
    plan, conflict, skip = [], [], []
    for st, p in names(MB, B):                                   # ① ② 分支上的改动
        f = ROOT / p
        bb = blob(B, p)
        if st == "D":
            skip.append((p, "分支删除了它（不删，留着）")); continue
        if p not in m_ch:
            mb = blob(M, p)
            if same(f, bb):
                skip.append((p, "磁盘已是分支版本"))
            elif (mb is None and not f.exists()) or (mb is not None and same(f, mb)):
                plan.append(("branch", p, bb))
            else:
                conflict.append((p, "磁盘既不是 main 也不是分支版本"))
        else:
            if same(f, bb):
                skip.append((p, "磁盘已是分支版本")); continue
            base = blob(MB, p) or b""
            tmp = BAK.parent / "_merge"; tmp.mkdir(parents=True, exist_ok=True)
            (tmp / "cur").write_bytes(f.read_bytes() if f.exists() else b""); (tmp / "base").write_bytes(base); (tmp / "other").write_bytes(bb)
            r = subprocess.run(["git", "merge-file", "-p", str(tmp / "cur"), str(tmp / "base"), str(tmp / "other")], capture_output=True)
            if r.returncode == 0:
                plan.append(("merge3", p, r.stdout))
            else:
                conflict.append((p, f"三方合并有 {r.returncode} 处冲突"))
    done = {p for _, p, _ in plan} | {p for p, _ in conflict} | {p for p, _ in skip}
    for st, p in names(M, S, {"A", "M"}):                        # ③ stash 里的文件
        if p in done:
            continue
        f = ROOT / p; sb = blob(S, p)
        if sb is None:
            continue
        if not f.exists():
            plan.append(("stash", p, sb))
        elif same(f, sb):
            skip.append((p, "磁盘与 stash 相同"))
        else:
            conflict.append((p, "磁盘已有且与 stash 不同（10-29 23:19 之后有人改过）"))
    from collections import Counter
    print("计划：", dict(Counter(k for k, _, _ in plan)), "冲突：", len(conflict), "跳过：", len(skip))
    for p, why in conflict:
        print("  冲突", p, "—", why)
    keys = ("tools/rda_console.py", "tools/rda_hc_select.py", "tools/rda_threelines.py", "swbrain/ponding.py", "swbrain/routes.py", "console/app.js")
    for k in keys:
        print("  ", k, "→", next((x for x, p, _ in plan if p == k), "（不动）"))
    if not a.apply:
        print("（预演，加 --apply 才写）"); return
    n = 0
    for kind, p, data in plan:
        f = ROOT / p
        if f.exists():
            (BAK / p).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, BAK / p)
        f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(data); n += 1
    (BAK / "_conflicts.txt").parent.mkdir(parents=True, exist_ok=True)
    (BAK / "_conflicts.txt").write_text("\n".join(f"{p}\t{w}" for p, w in conflict), encoding="utf-8")
    print(f"已写回 {n} 个文件；覆盖前的版本在 {BAK}；冲突清单 {BAK / '_conflicts.txt'}")


if __name__ == "__main__":
    main()
