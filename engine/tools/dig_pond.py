# -*- coding: utf-8 -*-
"""防线 2+：在模型里选一块地挖塘，改出一个新 .inp，跑完得到第三个「现状」。

客户的逻辑是三态对比：
  现状① 防线1  既有管网                     = 剥离后的基准模型
  现状② 防线2  Parsons 已设计的塘           = 交付的原模型
  现状③ 防线2+ 我们在电脑里新挖的塘          = 这个脚本产出的模型
三者一比，就回答「在哪挖、挖多大、花多少钱、内涝减多少」。

在模型里挖一个塘 = 往 .inp 里加四样东西：
  [STORAGE]     一个蓄水节点（底高程 = 接入点管底 − 落差，保证能自流）
  [CURVES]      水位-面积曲线（等截面棱柱，面积 = 地块面积）
  [CONDUITS]    一根重力管从接入点连过来  + [XSECTIONS] 管径
  [COORDINATES] 位置

用法：
  python dig_pond.py <模型> <新方案名> --at <东坐标> <北坐标> --area <m2> --depth <m> [--dn 900]
  python dig_pond.py <模型> <新方案名> --auto N        # 自动选 N 个最该挖的地方
"""
import argparse, math, pathlib, re, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # 兄弟脚本
sys.path.insert(0, str(HERE.parent))        # swbrain 包
from workdir import WORK


def read_sections(path):
    """保留原文行序，按小节切开，方便原地插入。"""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    idx, cur = {}, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("["):
            cur = s.split()[0].strip("[]").upper()
            idx.setdefault(cur, [i, i])
        elif cur:
            idx[cur][1] = i
    return lines, idx


def parse_nodes(lines, idx):
    """节点 → (类型, 管底高程)；连接管 → 两端。"""
    inv, kind = {}, {}
    for sec in ("JUNCTIONS", "OUTFALLS", "STORAGE"):
        if sec not in idx:
            continue
        a, b = idx[sec]
        for ln in lines[a + 1:b + 1]:
            s = ln.strip()
            if not s or s.startswith(";") or s.startswith("["):
                continue
            f = s.split()
            try:
                inv[f[0]] = float(f[1])
            except (IndexError, ValueError):
                continue
            kind[f[0]] = sec
    return inv, kind


def parse_coords(lines, idx):
    out = {}
    if "COORDINATES" not in idx:
        return out
    a, b = idx["COORDINATES"]
    for ln in lines[a + 1:b + 1]:
        s = ln.strip()
        if not s or s.startswith(";") or s.startswith("["):
            continue
        f = s.split()
        if len(f) >= 3:
            try:
                out[f[0]] = (float(f[1]), float(f[2]))
            except ValueError:
                pass
    return out


def flooding_nodes(rpt: pathlib.Path):
    """.rpt 里各节点的溢流量 m³——挖塘要挨着真正在溢水的点。"""
    if not rpt.exists():
        return {}
    t = rpt.read_text(encoding="utf-8", errors="replace")
    blk = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
    out = {}
    if blk:
        for ln in blk.group(1).splitlines():
            c = ln.split()
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                try:
                    out[c[0]] = float(c[-2]) * 1000.0
                except ValueError:
                    pass
    return out


def full_flow(dn_m, slope=0.002, n=0.013):
    return (1 / n) * (math.pi * dn_m ** 2 / 4) * (dn_m / 4) ** (2 / 3) * math.sqrt(slope) * 3600


def dig(src: pathlib.Path, dst: pathlib.Path, ponds: list[dict], log=print):
    """ponds: [{'id','x','y','area_m2','depth_m','connect','dn_mm','slope'}…]"""
    lines, idx = read_sections(src)
    inv, kind = parse_nodes(lines, idx)
    coords = parse_coords(lines, idx)

    add_sto, add_cur, add_con, add_xs, add_crd = [], [], [], [], []
    for p in ponds:
        pid = p["id"]; cn = p["connect"]
        if cn not in inv:
            log(f"  ✗ {pid}: 接入点 {cn} 不在模型里"); continue
        dn = float(p.get("dn_mm", 900)) / 1000.0
        slope = float(p.get("slope", 0.002))
        cx, cy = coords.get(cn, (p["x"], p["y"]))
        L = max(math.hypot(p["x"] - cx, p["y"] - cy), 20.0)
        drop = max(slope * L, 0.30)                      # 管子本身的落差，至少 0.3 m
        z = inv[cn] - drop - float(p["depth_m"])         # 塘底：接入点管底再往下挖一个塘深
        add_sto.append(f"{pid} {z:.3f} {p['depth_m']:.2f} 0 TABULAR {pid}_Curve 0 0 0 0 0")
        add_cur.append(f"{pid}_Curve Storage 0 {p['area_m2']:.1f} {p['depth_m']:.2f} {p['area_m2']:.1f}")
        lid = f"CN_{pid}"
        add_con.append(f"{lid} {cn} {pid} {L:.2f} 0.013 0 0 0 0")
        add_xs.append(f"{lid} CIRCULAR {dn:.3f} 0 0 0 1")
        add_crd.append(f"{pid} {p['x']:.3f} {p['y']:.3f}")
        log(f"  ✓ {pid}: {p['area_m2']:,.0f} m² × {p['depth_m']:.1f} m = "
            f"{p['area_m2'] * p['depth_m']:,.0f} m³｜接 {cn}（管底 {inv[cn]:.2f} → 塘底 {z:.2f}）"
            f"｜DN{int(dn * 1000)} × {L:.0f} m，满管流 {full_flow(dn, slope):,.0f} m³/h")

    ins = {"STORAGE": add_sto, "CURVES": add_cur, "CONDUITS": add_con,
           "XSECTIONS": add_xs, "COORDINATES": add_crd}
    out = []
    for i, ln in enumerate(lines):
        out.append(ln)
        for sec, rows in ins.items():
            if sec in idx and i == idx[sec][0] and rows:        # 紧跟在节标题后面插
                out.extend(rows)
    for sec, rows in ins.items():
        if sec not in idx and rows:
            out.append(f"[{sec}]"); out.extend(rows)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return dst


def auto_sites(model: str, base_rpt: pathlib.Path, inp: pathlib.Path, n: int,
               area_m2: float, depth_m: float, dn_mm: float, min_sep_m: float = 400.0):
    """自动选址：挨着溢流量最大的点挖，彼此至少隔开 min_sep_m。

    这是最朴素的一版「贪心」，专门用来和 MILP 快筛的结果做对照——
    如果 MILP 选的地方明显更好，那套快筛就有价值；如果差不多，就说明问题本身不难。
    """
    lines, idx = read_sections(inp)
    coords = parse_coords(lines, idx)
    fl = flooding_nodes(base_rpt)
    cand = sorted(((v, k) for k, v in fl.items() if k in coords), reverse=True)
    picked = []
    for v, k in cand:
        x, y = coords[k]
        if any(math.hypot(x - p["x"], y - p["y"]) < min_sep_m for p in picked):
            continue
        picked.append({"id": f"NP{len(picked) + 1}", "x": x + 60.0, "y": y + 60.0,
                       "area_m2": area_m2, "depth_m": depth_m, "connect": k, "dn_mm": dn_mm,
                       "node_flood_m3": v})
        if len(picked) >= n:
            break
    return picked


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="防线 2+：在模型里挖塘")
    ap.add_argument("model"); ap.add_argument("plan")
    ap.add_argument("--from", dest="src", default="baseline", choices=["baseline", "mit"],
                    help="在哪个模型上挖：baseline=防线1，mit=防线2（叠加）")
    ap.add_argument("--auto", type=int, default=0)
    ap.add_argument("--at", nargs=2, type=float)
    ap.add_argument("--connect", default=None)
    ap.add_argument("--area", type=float, default=8000.0)
    ap.add_argument("--depth", type=float, default=2.0)
    ap.add_argument("--dn", type=float, default=900.0)
    ap.add_argument("--sep", type=float, default=400.0)
    a = ap.parse_args()

    stem = f"{a.model}_baseline" if a.src == "baseline" else a.model
    src = WORK / f"{stem}.inp"
    dst = WORK / f"{a.model}_{a.plan}.inp"
    print(f"底图：{src.name}（{'防线1 既有管网' if a.src == 'baseline' else '防线2 已设计的塘'}）")
    if a.auto:
        sites = auto_sites(a.model, WORK / f"{stem}.rpt", src, a.auto, a.area, a.depth, a.dn, a.sep)
        print(f"自动选了 {len(sites)} 个塘位（挨着溢流量最大的点，彼此隔 {a.sep:.0f} m 以上）：")
    else:
        sites = [{"id": "NP1", "x": a.at[0], "y": a.at[1], "area_m2": a.area,
                  "depth_m": a.depth, "connect": a.connect, "dn_mm": a.dn}]
    dig(src, dst, sites)
    tv = sum(s["area_m2"] * s["depth_m"] for s in sites)
    print(f"→ {dst.name}：新增 {len(sites)} 个塘，合计库容 {tv:,.0f} m³")
