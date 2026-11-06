# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · 一维快速评估（本机 SWMM，几秒到一分钟一次，并行）。

为什么用一维（2026-10-19 实测）：RB-01 一二维一次里二维占 93 %（L1：2D 1,387 s / 共 1,494 s），
单跑一维 96 mm / 1 h 14.5 s、96 mm / 24 h 67 s。筛选与组合用一维，完整一二维只做验证。
一维的口径：热点周边井的溢流量（Node Flooding Summary）——溢流是地面积水的来源，降了地面积水一般跟着降；
二维的地面汇流、回流管网它算不出，所以选出来的组合一律要过一二维验证。

  stage1   每口候选井一个「理想截流」：1.5 × 峰值溢流率的泵 → 不限容积的蓄水 → 这口井截流的上限效果
  options  给定候选编号（candidates.json）逐个单独评估
  combo    给定一组候选编号一起评估

度量（每场雨）：每个热点 200 m / 500 m 内井的溢流量合计、全域溢流量、相对基线变多的井的增量合计（别处变严重的代理）。
用法：python tools/l2opt_screen.py stage1 --run RB01_H61 --storms S96D1,S96D24 [--procs 8]
      python tools/l2opt_screen.py options --run RB01_H61 --storms S96D1,S96D24 --ids GR0001,PP0003,...
产物：out/l2opt/<运行>/screen_<模式>.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import sys
import time
import uuid

import numpy as np

N_NEW = 0.012                       # 新建重力管曼宁 n（WA-726 表 4-1 GRP 良好）

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

TMP = WORK / "l2opt" / "tmp"
POND_FULL_FREE = 0.10        # 塘里留 0.1 m 不打满（同 v4：PO 塘 1.0 m 深打到 0.9 停）
_BASE = {}


# ---------------------------------------------------------------- 写模型
def add_section_lines(txt, sec, lines):
    if not lines:
        return txt
    if f"{sec}\n" in txt:
        return txt.replace(f"{sec}\n", f"{sec}\n" + "".join(l + "\n" for l in lines), 1)
    return txt.rstrip("\n") + f"\n\n{sec}\n" + "".join(l + "\n" for l in lines)


def apply(txt, measures, storage_depth):
    """measures：dict 列表（kind = sink / pump_pond / pump_newpond / gravity / deepen），返回改好的 .inp 文本。
    storage_depth：{现有塘节点: 最大深度}（加深后要改，泵的打满规则跟着变）"""
    pumps, curves, rules, stor, cond, xs, coords, losses, outf = [], [], [], [], [], [], [], [], []
    depth = dict(storage_depth)
    depth_mode = bool(re.search(r"^LINK_OFFSETS\s+DEPTH", txt, re.M))
    jinv = {}
    if depth_mode:
        sec = re.search(r"\[JUNCTIONS\]\n(.*?)\n\[", txt, re.S)
        for ln in (sec.group(1).splitlines() if sec else []):
            q = ln.split()
            if len(q) >= 2 and not ln.startswith(";"):
                try:
                    jinv[q[0]] = float(q[1])
                except ValueError:
                    pass

    def off(node_inv, z):
        """管端标高 z → 这个模型要写的偏移（ELEVATION 模式写 z，DEPTH 模式写 z − 节点底）"""
        return z - node_inv if depth_mode else z
    for m in measures:
        if m["kind"] == "deepen":
            k, dd = m["pond"], m["add_depth_m"]
            pat = re.compile(rf"^({re.escape(k)}\s+)(-?[\d.]+)(\s+)([\d.]+)(\s+)", re.M)
            mt = pat.search(txt)
            if mt:
                z0, d0 = float(mt.group(2)), float(mt.group(4))
                txt = txt[:mt.start()] + f"{mt.group(1)}{z0 - dd:.3f}{mt.group(3)}{d0 + dd:.3f}{mt.group(5)}" + txt[mt.end():]
                depth[k] = d0 + dd
    for m in measures:
        mid = m["id"]
        if m["kind"] in ("sink", "pump_pond", "pump_newpond", "pump_outfall"):
            if m["kind"] == "pump_outfall":                              # 压力管到 RBW3 外海：新出水口，尾水同 RBW3 固定 1.3 m
                dst, full = f"OF_{mid}", None
                outf.append(f"{dst}  -1.000  FIXED  1.3  NO")
                coords.append(f"{dst}  {m['of_xy'][0] + 3:.2f}  {m['of_xy'][1] + 3:.2f}")
            elif m["kind"] == "sink":
                dst, full = f"SK_{mid}", None
                stor.append(f"{dst}  {m['rim'] - 20:.3f}  20.000  0  FUNCTIONAL  0  0  1000000  0  0")
                coords.append(f"{dst}  {m['x'] + 5:.2f}  {m['y'] + 5:.2f}")
            elif m["kind"] == "pump_pond":
                dst = m["dest"]; full = depth.get(dst, 1.0) - POND_FULL_FREE
            else:
                dst = f"NP_{mid}"; dep = m["pond_depth_m"]; full = dep - POND_FULL_FREE
                stor.append(f"{dst}  {m['z_floor']:.3f}  {dep:.3f}  0  FUNCTIONAL  0  0  {m['pond_area_m2']:.1f}  0  0")
                coords.append(f"{dst}  {m['px']:.2f}  {m['py']:.2f}")
            q = m["q_m3s"]
            pumps.append(f"P_{mid}  {m['well']}  {dst}  PC_{mid}  OFF  0  0")
            curves.append(f"PC_{mid}  PUMP2  0  {q:.4f}\nPC_{mid}  100  {q:.4f}")
            r = []
            if full is not None:
                r.append(f"RULE R_{mid}_FULL\nIF NODE {dst} DEPTH >= {full:.2f}\nTHEN PUMP P_{mid} STATUS = OFF\nPRIORITY 3\n")
            r.append(f"RULE R_{mid}_ON\nIF NODE {m['well']} DEPTH > 0.30\n" + (f"AND NODE {dst} DEPTH < {full:.2f}\n" if full is not None else "")
                     + f"THEN PUMP P_{mid} STATUS = ON\nPRIORITY 2\n")
            r.append(f"RULE R_{mid}_OFF\nIF NODE {m['well']} DEPTH < 0.05\nTHEN PUMP P_{mid} STATUS = OFF\nPRIORITY 2\n")
            rules += r
        elif m["kind"] == "cluster":
            ww, of = f"WW_{mid}", f"OF_{mid}"
            wb, wd = m["ww_bottom"], m["ww_depth"]
            stor.append(f"{ww}  {wb:.3f}  {wd:.3f}  0  FUNCTIONAL  0  0  {m['ww_area']:.1f}  0  0")
            coords.append(f"{ww}  {m['ps_x']:.2f}  {m['ps_y']:.2f}")
            outf.append(f"{of}  -1.000  FIXED  {m['tailwater']:.2f}  NO")
            coords.append(f"{of}  {m['of_xy'][0] + 3:.2f}  {m['of_xy'][1] + 3:.2f}")
            for j, ln in enumerate(m["links"]):
                cid = f"GL_{mid}_{j}"
                cond.append(f"{cid}  {ln['well']}  {ww}  {max(ln['L'], 10):.1f}  {N_NEW}  {off(jinv.get(ln['well'], 0.0), ln['inlet_z']):.3f}  {off(wb, m['ww_inlet']):.3f}  0  0")
                xs.append(f"{cid}  CIRCULAR  {ln['d']:.3f}  0  0  0  1")
                losses.append(f"{cid}  0.5  1.0  0  YES  0")
            pumps.append(f"P_{mid}  {ww}  {of}  PC_{mid}  OFF  0  0")
            curves.append(f"PC_{mid}  PUMP2  0  {m['q_m3s']:.4f}\nPC_{mid}  100  {m['q_m3s']:.4f}")
            rules.append(f"RULE R_{mid}_ON\nIF NODE {ww} DEPTH > {m['on_depth']:.2f}\nTHEN PUMP P_{mid} STATUS = ON\nPRIORITY 2\n")
            rules.append(f"RULE R_{mid}_OFF\nIF NODE {ww} DEPTH < {m['off_depth']:.2f}\nTHEN PUMP P_{mid} STATUS = OFF\nPRIORITY 2\n")
        elif m["kind"] == "gravity":
            dst = f"NP_{mid}"; dep = m["pond_depth_m"]
            zf = m["pond_top_m"] - dep
            stor.append(f"{dst}  {zf:.3f}  {dep:.3f}  0  FUNCTIONAL  0  0  {m['pond_area_m2']:.1f}  0  0")
            coords.append(f"{dst}  {m['px']:.2f}  {m['py']:.2f}")
            # 新建重力管 n = 0.012：WA-726 表 4-1 GRP（内壁良好），参数登记表第 8 项（2026-10-20 由 0.013 改）
            zin = m.get("inlet_z", m["rim"] - 0.5)
            zin_off = off(jinv.get(m["well"], 0.0), zin)
            zout_off = off(zf, zf + dep if m.get("tank") else zf)             # 地下池从池顶进水
            cond.append(f"GP_{mid}  {m['well']}  {dst}  {m['pipe_m']:.1f}  {N_NEW}  {zin_off:.3f}  {zout_off:.3f}  0  0")
            xs.append(f"GP_{mid}  CIRCULAR  {m['pipe_d_m']:.3f}  0  0  0  1")
            losses.append(f"GP_{mid}  0.5  1.0  0  YES  0")
    txt = add_section_lines(txt, "[OUTFALLS]", outf)
    txt = add_section_lines(txt, "[STORAGE]", stor)
    txt = add_section_lines(txt, "[CONDUITS]", cond)
    txt = add_section_lines(txt, "[XSECTIONS]", xs)
    txt = add_section_lines(txt, "[PUMPS]", pumps)
    txt = add_section_lines(txt, "[CURVES]", curves)
    txt = add_section_lines(txt, "[COORDINATES]", coords)
    if losses:
        txt = add_section_lines(txt, "[LOSSES]", losses)
    if rules:                                                    # 规则一直生效；[REPORT] CONTROLS 只管报告里记不记开关动作，不改（记了报告会很大）
        txt = add_section_lines(txt, "[CONTROLS]", rules)
    return txt


def node_flood_txt(t):
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            try:
                out[q[0]] = (float(q[5]) * 1000.0, float(q[1]))     # (溢流量 m³, 溢流时长 h)
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------- 并行执行
def _init(base_paths):
    for k, p in base_paths.items():
        t = pathlib.Path(p).read_text(encoding="utf-8")
        _BASE[k] = re.sub(r"^THREADS\s+\d+", "THREADS  1", t, count=1, flags=re.M)


def _run(job):
    """job = (键, 雨, measures, 塘深)。返回 (键, 雨, {井: (量, 时长)}, 全域溢流, 连续性误差, 秒)"""
    from swmm.toolkit import solver
    key, storm, measures, sdepth = job
    TMP.mkdir(parents=True, exist_ok=True)
    stem = TMP / f"{storm}_{uuid.uuid4().hex[:10]}"
    inp, rpt, out = stem.with_suffix(".inp"), stem.with_suffix(".rpt"), stem.with_suffix(".out")
    t0 = time.time()
    try:
        inp.write_text(apply(_BASE[storm], measures, sdepth), encoding="utf-8")
        solver.swmm_run(str(inp), str(rpt), str(out))
        t = rpt.read_text(errors="ignore")
        nf = node_flood_txt(t)
        tot = re.search(r"Flooding Loss \.+\s+(-?[\d.]+)\s+(-?[\d.]+)", t)
        ce = re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", t)
        return key, storm, nf, (float(tot.group(2)) * 1000.0 if tot else None), (float(ce[-1]) if ce else None), round(time.time() - t0, 1)
    except Exception as ex:                                     # noqa: BLE001
        return key, storm, None, None, f"{type(ex).__name__}: {ex}", round(time.time() - t0, 1)
    finally:
        for p in (inp, rpt, out):
            try:
                p.unlink()
            except OSError:
                pass


def run_jobs(jobs, base_paths, procs, log=print):
    import multiprocessing as mp
    res = []
    with mp.get_context("spawn").Pool(procs, initializer=_init, initargs=(base_paths,)) as pool:
        t0 = time.time()
        for i, r in enumerate(pool.imap_unordered(_run, jobs), 1):
            res.append(r)
            if i % max(1, len(jobs) // 20) == 0 or i == len(jobs):
                log(f"  {i}/{len(jobs)}（{(time.time() - t0) / 60:.1f} min）")
    return res


# ---------------------------------------------------------------- 度量
class Zones:
    def __init__(self, inp_txt, hs_xy: dict, radii=(200.0, 500.0)):
        sec = re.search(r"\[COORDINATES\]\n(.*?)(?:\n\[|\Z)", inp_txt, re.S).group(1)      # COORDINATES 是文件最后一节
        self.xy = {}
        for ln in sec.splitlines():
            q = ln.split()
            if len(q) >= 3 and not ln.startswith(";"):
                self.xy[q[0]] = (float(q[1]), float(q[2]))
        names = list(self.xy); P = np.array([self.xy[n] for n in names])
        self.sets = {}
        for h, (x, y) in hs_xy.items():
            d = np.hypot(P[:, 0] - x, P[:, 1] - y)
            for r in radii:
                self.sets[(h, int(r))] = {names[i] for i in np.flatnonzero(d <= r)}

    def metrics(self, nf, base=None):
        z = {f"{h}|{r}": round(sum(nf.get(n, (0, 0))[0] for n in s)) for (h, r), s in self.sets.items()}
        out = {"zones": z, "total": round(sum(v[0] for v in nf.values())),
               "pond_spill": round(sum(v[0] for n, v in nf.items() if n.startswith(("PND_", "NP_"))))}   # 塘满溢出（现有塘 + 新塘）
        if base is not None:
            worse = sum(max(0.0, nf.get(n, (0, 0))[0] - b[0]) for n, b in base.items()) + sum(v[0] for n, v in nf.items() if n not in base)
            out["worse_m3"] = round(worse)
        return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["stage1", "options", "combo"])
    ap.add_argument("--run", default="RB01_H61"); ap.add_argument("--storms", default="S96D1,S96D24"); ap.add_argument("--base-state", default="L2P")
    ap.add_argument("--ids", default=""); ap.add_argument("--procs", type=int, default=8); ap.add_argument("--label", default="")
    ap.add_argument("--prefix", default="RB01", help="模型名前缀：RB01 / RDA（2026-10-20 起 RD-A 共用这套脚本）")
    a = ap.parse_args(argv)
    import geopandas as gpd
    o = ROOT / "out" / "l2opt" / a.run
    D = json.loads((o / "candidates.json").read_text(encoding="utf-8"))
    C = {c["id"]: c for c in D["candidates"]}
    storms = a.storms.split(",")
    base_paths = {s: str(WORK / f"{a.prefix}_{s}_{a.base_state}.inp") for s in storms}
    btxt = pathlib.Path(base_paths[storms[0]]).read_text(encoding="utf-8")
    H = __import__("hotspot_src").load(32640)
    hsids = sorted({c["hotspot"] for c in D["candidates"]})
    H = H[H.hotspot_id.isin(hsids)]
    Z = Zones(btxt, {r.hotspot_id: (r.geometry.x, r.geometry.y) for r in H.itertuples()})
    sdepth = {}
    for ln in re.search(r"\[STORAGE\]\n(.*?)\n\n", btxt, re.S).group(1).splitlines():
        q = ln.split()
        if len(q) > 3 and not ln.startswith(";"):
            sdepth[q[0]] = float(q[2])
    # 基线
    print(f"{a.run} · {a.mode} · 雨 {storms} · 基线 {a.base_state}")
    jobs = [("BASE", s, [], sdepth) for s in storms]
    if a.mode == "stage1":
        wells = {}
        for c in D["candidates"]:
            if c.get("well") and c["well"] not in wells:
                wells[c["well"]] = c
        for k, c in wells.items():
            x, y = Z.xy[k]
            m = {"kind": "sink", "id": f"S_{k}", "well": k, "rim": c["rim_m"], "x": x, "y": y, "q_m3s": round(1.5 * max(c["overflow_rate_m3s"], 0.36), 3)}
            jobs += [(k, s, [m], sdepth) for s in storms]
    else:
        ids = [i for i in a.ids.split(",") if i]
        ms = []
        for i in ids:
            c = dict(C[i]); x, y = Z.xy.get(c.get("well"), (None, None))
            ms.append(prep(c, Z, D))
        if a.mode == "options":
            jobs += [(m["id"], s, [m], sdepth) for m in ms for s in storms]
        else:
            jobs += [(a.label or "COMBO", s, ms, sdepth) for s in storms]
    print(f"  {len(jobs)} 次一维（{a.procs} 并行）")
    res = run_jobs(jobs, base_paths, a.procs)
    base = {s: r[2] for r in res if r[0] == "BASE" for s in [r[1]]}
    out = {"run": a.run, "mode": a.mode, "storms": storms, "base": {}, "items": {}, "errors": []}
    for s in storms:
        out["base"][s] = Z.metrics(base[s])
    for key, s, nf, tot, ce, sec_ in res:
        if key == "BASE":
            continue
        if nf is None:
            out["errors"].append({"key": key, "storm": s, "error": ce}); continue
        mm = Z.metrics(nf, base[s]); mm.update(cont_err=ce, sec=sec_)
        out["items"].setdefault(key, {})[s] = mm
    name = f"screen_{a.mode}{('_' + a.label) if a.label else ''}.json"
    (o / name).write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"→ {o / name}（{len(out['items'])} 项，出错 {len(out['errors'])}）")
    return 0


def prep(c, Z, D):
    """候选（candidates.json 的一行）→ apply() 用的 measure"""
    m = {"kind": c["kind"], "id": c["id"]}
    if c.get("well"):
        m.update(well=c["well"], rim=c["rim_m"])
    if c["kind"] == "pump_pond":
        m.update(dest=c["dest"], q_m3s=c["q_m3s"])
    elif c["kind"] == "pump_outfall":
        m.update(q_m3s=c["q_m3s"], of_xy=c["of_xy"])
    elif c["kind"] == "pump_newpond":
        from pyproj import Transformer
        px, py = Transformer.from_crs(4326, 32640, always_xy=True).transform(c["lon"], c["lat"])
        m.update(q_m3s=c["q_m3s"], pond_depth_m=c["pond_depth_m"], pond_area_m2=c["pond_area_m2"], px=px, py=py,
                 z_floor=c["pond_top_m"] - c["pond_depth_m"])
    elif c["kind"] == "gravity":
        from pyproj import Transformer
        px, py = Transformer.from_crs(4326, 32640, always_xy=True).transform(c["lon"], c["lat"])
        m.update(pond_depth_m=c["pond_depth_m"], pond_area_m2=c["pond_area_m2"], pond_top_m=c["pond_top_m"], pipe_m=c["pipe_m"], pipe_d_m=c["pipe_d_m"], px=px, py=py)
    elif c["kind"] == "deepen":
        m.update(pond=c["pond"], add_depth_m=c["add_depth_m"])
    elif c["kind"] == "tank_gravity":                                  # v3 地下池（重力进水）
        m.update(kind="gravity", tank=True, inlet_z=c["inlet_z"], pond_depth_m=c["pond_depth_m"], pond_area_m2=c["pond_area_m2"], pond_top_m=c["pond_top_m"],
                 pipe_m=c["pipe_m"], pipe_d_m=c["pipe_d_m"], px=c["px"], py=c["py"])
    elif c["kind"] == "tank_pumped":                                   # v3 地下池（井边泵站 → 压力管）
        m.update(kind="pump_newpond", q_m3s=c["q_m3s"], pond_depth_m=c["pond_depth_m"], pond_area_m2=c["pond_area_m2"], px=c["px"], py=c["py"], z_floor=c["z_floor"])
    elif c["kind"] == "ps_cluster":                                    # v3 合并泵站 → 排海口
        import c1_design_l2s5 as V5
        wb = c["wetwell_bottom_z"]; ww_depth = max(c["wetwell_inlet_z"] - wb + 2.0, 3.0)
        links = [{**ln, "d": V5.pipe_d(max(c["overflow_rate_m3s"] / len(c["links"]), 0.05), 0.002, max(ln["L"], 10))} for ln in c["links"]]
        m.update(kind="cluster", q_m3s=c["q_m3s"], ps_x=c["ps_x"], ps_y=c["ps_y"], of_xy=c["of_xy"], ww_bottom=wb, ww_depth=ww_depth, ww_inlet=c["wetwell_inlet_z"],
                 ww_area=max(c["wetwell_m3"] / 1.5, 10.0), links=links, tailwater=1.3, on_depth=1.0, off_depth=0.3)
    return m


if __name__ == "__main__":
    raise SystemExit(main())
