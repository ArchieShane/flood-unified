# -*- coding: utf-8 -*-
"""跑全市一维网的 L1 / L2，并出缺口对账。

口径（与客户那份全市模型一致，也与本项目其余部分一致）：
  `ALLOW_PONDING NO` → 冒出井盖的水**离开系统**，`Flooding Loss` 就是真实损失，
  不像客户三个汇水区模型那样会回落再溢出、跨节点重复计。所以这里的
  **Gap = Flooding Loss**，物理含义干净：管网接不住、必须由别的设施承接的量。

  L1 与 L2 **共用同一张网**，只差 23 座塘，两者之差才只归因于塘。

用法：
    python tools/run_city1d.py L1
    python tools/run_city1d.py L1 L2 --tag _pilot
    python tools/run_city1d.py --report        # 只读已有 rpt，不重跑
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import WORK  # noqa: E402

HA_M = 10_000.0          # hectare-m → m³ 的系数是 10,000


def facts(rpt: pathlib.Path) -> dict:
    """从 .rpt 的连续性表里取水量。用 hectare-m 那一列，它的量纲最稳。"""
    t = rpt.read_text(encoding="utf-8", errors="replace")

    def grab(block: str, key: str):
        b = re.search(block + r"(.*?)Continuity Error", t, re.S)
        if not b:
            return None
        m = re.search(re.escape(key) + r"\s*\.+\s*(-?[\d.]+)", b.group(1))
        return float(m.group(1)) if m else None

    def err(block: str):
        b = re.search(block + r"(.*?)Continuity Error \(%\)\s*\.+\s*(-?[\d.]+)", t, re.S)
        return float(b.group(2)) if b else None

    # 产流是**按节点注入**的（见 build_city1d.py 为什么不用子汇水区），
    # 所以水量记在 External Inflow 里，没有 Runoff Quantity 那一段。
    o = {
        "inflow_m3": ((grab("Flow Routing Continuity", "External Inflow") or 0)
                      + (grab("Flow Routing Continuity", "Wet Weather Inflow") or 0)) * HA_M,
        "outflow_m3": (grab("Flow Routing Continuity", "External Outflow") or 0) * HA_M,
        "flood_m3": (grab("Flow Routing Continuity", "Flooding Loss") or 0) * HA_M,
        "stored_m3": (grab("Flow Routing Continuity", "Final Stored Volume") or 0) * HA_M,
        "err_route": err("Flow Routing Continuity"),
    }
    m = re.search(r"Total elapsed time:\s*(\S+)", t)
    o["elapsed"] = m.group(1) if m else "?"
    b = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
    n = 0
    if b:
        for ln in b.group(1).splitlines():
            c = ln.split()
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                n += 1
    o["n_flooded_nodes"] = n
    return o


def run(state: str, tag: str, log=print) -> pathlib.Path:
    from swmm.toolkit import solver

    inp = WORK / f"City_{state}{tag}.inp"
    if not inp.exists():
        log(f"  缺 {inp.name}，先跑 tools/build_city1d.py {state}"); return None
    rpt, out = inp.with_suffix(".rpt"), inp.with_suffix(".out")
    t0 = time.time()
    log(f"  [{time.strftime('%H:%M:%S')}] 跑 {inp.name}（{inp.stat().st_size / 1048576:.0f} MB）…")
    solver.swmm_run(str(inp), str(rpt), str(out))
    log(f"  [{time.strftime('%H:%M:%S')}] 用时 {(time.time() - t0) / 60:.1f} min")
    return rpt


def _run_one(args):
    """给进程池用：跑一个 .inp，返回 (名字, 耗时分钟, 出错信息)。"""
    import time as _t
    from swmm.toolkit import solver as _s
    p = pathlib.Path(args)
    t0 = _t.time()
    try:
        _s.swmm_run(str(p), str(p.with_suffix(".rpt")), str(p.with_suffix(".out")))
        return (p.name, (_t.time() - t0) / 60.0, None)
    except Exception as e:                      # SWMM 的报错要带出来，不能吞
        return (p.name, (_t.time() - t0) / 60.0, f"{type(e).__name__}: {e}")


def run_parts(states, tag, nparts: int, workers: int, log=print):
    """并行跑所有分片。块间不交换水，所以分开跑是精确的。"""
    from concurrent.futures import ProcessPoolExecutor

    jobs = []
    for s in states:
        for i in range(1, nparts + 1):
            p = WORK / f"City_{s}{tag}_p{i}.inp"
            if p.exists():
                jobs.append(str(p))
            else:
                log(f"  缺 {p.name}")
    if not jobs:
        log("没有可跑的分片"); return
    log(f"  {len(jobs)} 个分片，{workers} 路并行，"
        f"合计 {sum(pathlib.Path(j).stat().st_size for j in jobs) / 1048576:.0f} MB")
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for name, mins, err in ex.map(_run_one, jobs):
            done += 1
            log(f"  [{done}/{len(jobs)}] {name}　{mins:.1f} min"
                + (f"　**出错 {err}**" if err else ""))
    log(f"  全部跑完，墙钟 {(time.time() - t0) / 60:.1f} min")


def part_window_h(inp: pathlib.Path) -> float | None:
    """这一片跑的是多长的窗口。**必须逐片读，不能假设一致。**

    L1 那八片是 12 h，而 L2 里有三片 12 h、五片 6 h（为压缩关键路径改过）。
    窗口不同就意味着 L1 与 L2 的溢出量在不同的积分区间上比 —— 这场雨的累计
    溢出量 4 h 就定型，所以两者实际等价，但**等价是要证的，不是假设的**：
    不把窗口报出来，合计看上去完全正常，谁也不会想到去问一句。
    """
    if not inp.exists():
        return None
    import datetime as _dt
    cur, v = None, {}
    for ln in inp.open(encoding="utf-8", errors="replace"):
        t = ln.strip()
        if t.startswith("["):
            cur = t.strip("[]").upper(); continue
        if cur != "OPTIONS" or not t or t.startswith(";"):
            continue
        c = t.split()
        if c[0] in ("START_DATE", "START_TIME", "END_DATE", "END_TIME") and len(c) > 1:
            v[c[0]] = c[1]
    if "END_TIME" not in v:
        return None

    # **日期也要读**：只看钟点时，END_DATE 比 START_DATE 晚一天、END_TIME 06:00 会被当成 6 h，
    # 实际是 30 h（全市一维 v2 就这样误报过「演算窗口 6 h」）
    def when(d, tm):
        tm = tm if tm.count(":") == 2 else tm + ":00"
        try:
            return _dt.datetime.strptime(f"{d} {tm}", "%m/%d/%Y %H:%M:%S")
        except ValueError:
            return None
    a = when(v.get("START_DATE", "01/01/2000"), v.get("START_TIME", "00:00:00"))
    b = when(v.get("END_DATE", v.get("START_DATE", "01/01/2000")), v["END_TIME"])
    if a is None or b is None:
        return None
    h = (b - a).total_seconds() / 3600.0
    return h if h > 0 else h + 24


def part_ok(rpt: pathlib.Path, inp: pathlib.Path) -> tuple[bool, str]:
    """这一份 rpt 能不能计入合计。**三条都要过，缺一条就不算。**

    为什么要三条：`facts()` 读一份报错的 rpt 会把每一项都返回 0
    （正则匹配不到 → `or 0`），于是失败的分片会被**当成「这片没水」静默加进全市合计**，
    合计看上去完全正常。实际踩到过两次：
      · 只看「rpt 存在」—— 8 个 L2 分片里含塘的 5 个报 ERROR 211 退出，
        rpt 里却仍带着「Analysis ended」，当时数出「16 片完成 5 片」，那 5 片全是废的；
      · 加了「无 ERROR」还不够 —— 重跑后读到 p6/p8 报错，其实那是**上一轮的残留 rpt**，
        比 inp 还旧，新一轮根本还没轮到它们。
    """
    if not rpt.exists():
        return False, "没有 rpt"
    if inp.exists() and rpt.stat().st_mtime < inp.stat().st_mtime:
        return False, "rpt 比 inp 旧（上一轮的残留）"
    t = rpt.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^\s*ERROR\s+\d+", t, re.M)
    if m:
        e = re.search(r"ERROR\s+\d+[^:\n]*", t)
        return False, f"报错：{e.group(0) if e else 'ERROR'}"
    if "Analysis ended" not in t:
        return False, "还没跑完"
    return True, ""


def report_parts(states, tag, nparts: int, log=print):
    """把各分片的水量相加。逐份的误差也报出来，别让某一份的坏数悄悄混进去。"""
    out = {}
    for s in states:
        agg = {"inflow_m3": 0.0, "outflow_m3": 0.0, "flood_m3": 0.0,
               "stored_m3": 0.0, "n_flooded_nodes": 0}
        errs, bad, wins = [], [], []
        for i in range(1, nparts + 1):
            rpt = WORK / f"City_{s}{tag}_p{i}.rpt"
            ok, why = part_ok(rpt, WORK / f"City_{s}{tag}_p{i}.inp")
            if not ok:
                bad.append((i, why)); continue
            fx = facts(rpt)
            for k in agg:
                agg[k] += fx[k] or 0
            errs.append(fx["err_route"])
            wins.append(part_window_h(WORK / f"City_{s}{tag}_p{i}.inp"))
        agg["windows_h"] = sorted({w for w in wins if w})
        agg["n_parts_ok"] = len(errs)
        agg["n_parts"] = nparts
        agg["bad_parts"] = bad
        agg["complete"] = not bad
        if bad:
            # **不能只提一句「不完整」就把合计照常打出去** —— 少一片就是少一整块城区的水量
            log(f"  **{s}: {len(bad)}/{nparts} 片不能计入，下面的合计是残缺的，不要引用**")
            for i, why in bad:
                log(f"      p{i}　{why}")
        agg["err_route"] = max((abs(e or 0) for e in errs), default=None)
        agg["elapsed"] = f"{len(errs)}/{nparts} 片"
        out[s] = agg
        bad = [i + 1 for i, e in enumerate(errs) if abs(e or 0) > 5]
        if bad:
            log(f"  ⚠ {s} 第 {bad} 份连续性误差超 5%")
    return out


def report(states, tag, log=print):
    rows = {}
    for s in states:
        rpt = WORK / f"City_{s}{tag}.rpt"
        if rpt.exists():
            rows[s] = facts(rpt)
    if not rows:
        log("没有可读的 rpt"); return rows
    _print(rows, log)
    return rows


def _print(rows, log=print):
    if not rows:
        log("没有结果"); return
    log(f"\n{'':<8}{'产流注入':>14}{'排到出水口':>14}{'**溢出井盖**':>16}"
        f"{'管内存水':>13}{'排走占比':>10}{'误差%':>9}")
    for s, f in rows.items():
        r = f["outflow_m3"] / f["inflow_m3"] * 100 if f["inflow_m3"] else 0
        log(f"{s:<8}{f['inflow_m3']:>14,.0f}{f['outflow_m3']:>14,.0f}{f['flood_m3']:>16,.0f}"
            f"{f['stored_m3']:>13,.0f}{r:>9.1f}%{str(f['err_route']):>9}")
    # 窗口混用必须摆在台面上 —— 不同积分区间上的溢出量不能默认可比
    wins = {s: f.get("windows_h") or [] for s, f in rows.items()}
    mixed = [s for s, w in wins.items() if len(w) > 1]
    for s, w in wins.items():
        if w:
            log(f"  {s}: 演算窗口 {', '.join(f'{x:g} h' for x in w)}"
                + ("　**同一态里混用了窗口**" if len(w) > 1 else ""))
    keys = [set(w) for w in wins.values() if w]
    if len(keys) > 1 and any(k != keys[0] for k in keys):
        log("  **各态的窗口不一致 —— L1 与 L2 的溢出量在不同积分区间上比。**")
        log("    这场雨的累计溢出量 4 h 就定型，所以两者应当等价，")
        log("    但等价要由 pilot 同网对跑 6 h / 12 h 的结果来证（判据：差 < 1%），")
        log("    没有那份证据之前，这里的 Gap 只能当**参考**。")

    part = [s for s, f in rows.items() if f.get("complete") is False]
    if part:
        log(f"\n  **以下各态的合计是残缺的，不要当成全市数：{', '.join(part)}**")
        for s in part:
            f = rows[s]
            log(f"      {s}: 只有 {f.get('n_parts_ok', 0)}/{f.get('n_parts', '?')} 片可计入"
                + "".join(f"\n        p{i}　{why}" for i, why in f.get("bad_parts", [])))
    # 残缺就**根本不出 Gap**。上面那行警告拦不住别人把「Gap1 0 m³」照抄走 ——
    # 一个看着确定的数字比一句警告有力得多，所以宁可不给。
    if part:
        log("\n  缺片时不出 Gap —— 少一片就是少一整块城区的水量，"
            "给个数只会被当成真的。补齐后重跑本汇总即可。")
    elif "L1" in rows and "L2" in rows:
        a, b = rows["L1"]["flood_m3"], rows["L2"]["flood_m3"]
        log(f"\n**Gap1（L1 溢出量）{a:,.0f} m³**")
        log(f"**Gap2（L2 溢出量）{b:,.0f} m³**　塘减掉 {a - b:,.0f} m³"
            f"（{(a - b) / a * 100 if a else 0:.1f}%）")
        log("注：L2 的塘只能收到**管网送来的水**。17/23 座塘实际是地表汇流进水，")
        log("    一维模型看不见地表汇流，所以这个削减量是**下界**，不是定论。")
    for s, f in rows.items():
        bad = abs(f["err_route"] or 0) > 5
        log(f"  {s}: 演算误差最大 {f['err_route']}%　溢流节点 {f['n_flooded_nodes']:,}"
            f"　{f['elapsed']}" + ("　⚠ 误差超线，数值不可靠" if bad else ""))


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="跑全市一维网并出缺口对账")
    ap.add_argument("states", nargs="*", default=["L1", "L2"])
    ap.add_argument("--tag", default="")
    ap.add_argument("--report", action="store_true", help="只读已有 rpt，不重跑")
    ap.add_argument("--parts", type=int, default=0, help="分片数（0 = 单个整模型）")
    ap.add_argument("--workers", type=int, default=8, help="并行路数")
    a = ap.parse_args(argv)
    states = a.states or ["L1", "L2"]
    if a.parts:
        if not a.report:
            run_parts(states, a.tag, a.parts, a.workers)
        rows = report_parts(states, a.tag, a.parts)
        _print(rows)
    else:
        if not a.report:
            for s in states:
                run(s, a.tag)
        report(states, a.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
