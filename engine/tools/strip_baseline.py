# -*- coding: utf-8 -*-
"""把 Parsons 的「治理后」模型剥成「治理前」基准。

缓解措施在模型里是一整套新增构筑：
    既有节点 ──新建短管 CO-nn──▶ 集水井 W-n ──泵 PMP-x──▶ PMP-x_Junction ──▶ 塘 PO-m
但不是每台泵都从集水井取水：RB3 有几台直接从既有节点（RD-05、MH3783）抽进塘。

所以剥离要对节点和链接一起做不动点迭代，种子有两类：
  * 节点种子：塘（PO-*/STO-PO*）、集水井（W-数字）——命名明确，只属于缓解措施；
  * 链接种子：[CONTROLS] 里受塘水位控制的泵——这类泵是为塘服务的，塘没了泵也没有意义。
迭代规则：链接只要有一端是待删节点就删；节点若其余未删链接的另一端全是待删节点，它就是
缓解措施的内部节点（如泵出口 PMP-x_Junction），也删。出水口永不删。

两条都是必须的：
  * 只按命名匹配 `^PMP[-_].*_Junction$`，会把 Mussafah 既有永久泵站的出口节点
    PMP_RD27_DUTY_1_Junction 一起删掉，基准里凭空少了既有泵站；
  * 只删节点不删受控泵，RB3 那几台泵会继续往一个死端节点排水，基准同样失真。

这是我的重建，不是 Parsons 的原始基准模型。要对他们的「治理前」数，得要既有管网版 .inp。
"""
import pathlib, re, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # 兄弟脚本
sys.path.insert(0, str(HERE.parent))        # swbrain 包
from workdir import WORK
NODE_SECTIONS = {"JUNCTIONS", "OUTFALLS", "STORAGE", "DIVIDERS"}
LINK_SECTIONS = {"CONDUITS", "PUMPS", "ORIFICES", "WEIRS", "OUTLETS"}
LINK_REF_SECTIONS = {"XSECTIONS", "LOSSES"}

POND = re.compile(r"^(STO[-_])?PO[-_]?\d+$", re.I)
WELL = re.compile(r"^W[-_]\d+$", re.I)


def parse(lines):
    nodes, links, ctrl, cur = {}, {}, [], None
    for ln in lines:
        s = ln.strip()
        if s.startswith("["):
            cur = s.split()[0].strip("[]").upper(); continue
        if not s or s.startswith(";"):
            continue
        f = s.split()
        if cur in NODE_SECTIONS:
            nodes[f[0]] = cur
        elif cur in LINK_SECTIONS and len(f) >= 3:
            links[f[0]] = (f[1], f[2], cur)
        elif cur == "CONTROLS":
            ctrl.append(s)
    return nodes, links, ctrl


def pond_controlled_pumps(ctrl, ponds):
    """[CONTROLS] 里以塘水位为条件、操作某台泵的规则 → 那台泵为该塘服务。"""
    out, node = set(), None
    for s in ctrl:
        if re.match(r"^RULE\b", s, re.I):
            node = None
        m = re.search(r"\bNODE\s+(\S+)\s+DEPTH", s, re.I)
        if m:
            node = m.group(1)
        m2 = re.search(r"\bPUMP\s+(\S+)\s+STATUS", s, re.I)
        if m2 and node in ponds:
            out.add(m2.group(1))
    return out


def strip(src: pathlib.Path, dst: pathlib.Path):
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    nodes, links, ctrl = parse(lines)
    ponds = {n for n in nodes if POND.match(n)}

    dn = ponds | {n for n in nodes if WELL.match(n)}
    dlk = {p for p in pond_controlled_pumps(ctrl, ponds) if p in links}

    inc = defaultdict(set)
    for lid, (a, b, _) in links.items():
        inc[a].add(lid); inc[b].add(lid)

    for _ in range(50):                                   # 不动点迭代
        n0, l0 = len(dn), len(dlk)
        dlk |= {lid for lid, (a, b, _) in links.items() if a in dn or b in dn}
        for n, kind in nodes.items():
            if n in dn or kind == "OUTFALLS":
                continue
            rest = [lid for lid in inc[n] if lid not in dlk]
            far = {links[lid][1] if links[lid][0] == n else links[lid][0] for lid in rest}
            if not rest or far <= dn:                     # 没有链接了，或剩下的全通向待删节点
                dn.add(n)
        if len(dn) == n0 and len(dlk) == l0:
            break

    out, cur, skip_rule, n_ctrl = [], None, False, 0
    for ln in lines:
        s = ln.strip()
        if s.startswith("["):
            cur = s.split()[0].strip("[]").upper(); skip_rule = False
            out.append(ln); continue
        if not s or s.startswith(";"):
            out.append(ln); continue
        f = s.split()
        if cur in NODE_SECTIONS and f[0] in dn:
            continue
        if cur in (LINK_SECTIONS | LINK_REF_SECTIONS) and f[0] in dlk:
            continue
        if cur in ("COORDINATES", "VERTICES") and f[0] in (dn | dlk):
            continue
        if cur == "INFLOWS" and f[0] in dn:
            continue
        if cur == "CONTROLS":
            if re.match(r"^RULE\b", s, re.I):
                skip_rule = False
            if any(re.search(rf"(?<![\w-]){re.escape(x)}(?![\w-])", s) for x in (dn | dlk)):
                skip_rule = True; n_ctrl += 1
            if skip_rule:
                continue
        out.append(ln)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return nodes, links, dn, dlk, n_ctrl


for name in ("Mussafah", "Shaliela", "RB3"):
    src = WORK / f"{name}.inp"
    if not src.exists():
        print(f"{name}: 没有 {src.name}，跳过"); continue
    dst = WORK / f"{name}_baseline.inp"
    nodes, links, dn, dlk, nc = strip(src, dst)
    ponds = sorted(n for n in dn if POND.match(n))
    wells = sorted(n for n in dn if WELL.match(n))
    other = sorted(n for n in dn if n not in ponds and n not in wells)
    pumps_all = {k for k, v in links.items() if v[2] == "PUMPS"}
    print(f"\n{name}")
    print(f"  节点 {len(nodes)} → 删 {len(dn)}：塘 {len(ponds)}、集水井 {len(wells)}、内部节点 {len(other)}")
    print(f"    塘   {ponds}")
    print(f"    内部 {other[:6]}{' …' if len(other) > 6 else ''}")
    print(f"  链接 {len(links)} → 删 {len(dlk)}；其中泵 {len(dlk & pumps_all)} / {len(pumps_all)} 台")
    print(f"    保留的泵（既有永久设施）：{sorted(pumps_all - dlk)}")
    print(f"  控制规则删 {nc} 行")
