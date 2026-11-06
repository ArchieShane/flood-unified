# -*- coding: utf-8 -*-
"""顾问方案到底建了什么？把交付的「治理后」模型与基准模型逐构件比对。

**为什么要有这个工具**：报告正文写的是「在开放空间**挖 2 m 深、3:1 边坡**的临时塘，
12 寸移动泵在雨峰把重力管网的水抽进塘」。可 Mussafah 那座塘在交付模型里
**塘底比周围地面还高 0.30 m、是直壁、深 1.5 m**——正文与模型对不上。
三个区各有 1 / 10 / 7 座塘，逐个手工比不现实，所以把判法写死在这里。

------------------------------------------------------------------ 怎么判地上地下
唯一判据是**塘底标高 vs 该点实测地面**（客户自己的 5 m DTM）。
不取单格：塘位周边 60 m 的所有格都取出来，报「塘底高过其中百分之多少的格」。
单格会被噪声和一棵树带偏，分位数不会。

注意基准：客户的**水力模型**用的是 DTM 原始基准（Mussafah 塘底 3.91 对 DTM 3.61），
**不是**资产台账那套 +100（见 tools/pond_link.py）。两者别混。

------------------------------------------------------------------ 怎么判边坡
从 [CURVES] 的深度—面积曲线**反算边坡比**，直接和报告的「3:1」对。
把塘当棱台，底面积 A0、顶面积 A1、深 d，边坡 m:1（水平:垂直）时
    √A1 = √A0 + 2·m·d　→　m = (√A1 − √A0) / (2d)
只看面积增幅百分比会判错：同样 3:1 边坡，小塘增幅大、大塘增幅小
（157,426 m³ 那座 3:1 也只增 7%），必须反算成边坡比才可比。

------------------------------------------------------------------ 塘还是集水井
客户模型里 [STORAGE] 既有塘也有泵站湿井，混着数会得出「Shaliela 36 座塘」
这种错数。按**功能**分，不按名字：泵进水且没有出水口 = 塘；
重力进水且有出水口 = 集水井（泵从这里抽水去塘）。

------------------------------------------------------------------ 怎么判进水能力
塘的进水口只有泵时，**进塘能力 = 那几台泵的额定流量之和**，与塘多大无关。
把它和全网入流洪峰比，就知道这个方案在短历时雨下能不能起作用——
Mussafah 是 0.361 / 20.71 = 1.7%，塘再大也灌不进去。

用法：
    python tools/audit_parsons.py                 # 三个区
    python tools/audit_parsons.py Shaliela RB3
需要环境变量 SWBRAIN_SWMM_WORK 指向放 .inp 的目录（与 rain_scenario 一致）。
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from workdir import WORK  # noqa: E402

MODELS = ["Mussafah", "Shaliela", "RB3"]
# 各区全网入流洪峰 m³/s（61 mm / 2 h，tools/runoff.py 算的）
PEAK_2H = {"Mussafah": 20.71, "Shaliela": 349.21, "RB3": 354.73}
RING_M = 60.0        # 取塘位周边多大范围的地面来判「地上还是地下」


def sections(p: pathlib.Path) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    cur = None
    for ln in p.open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        if s.startswith("["):
            cur = s.strip("[]").upper(); out[cur] = []; continue
        if cur and s and not s.startswith(";"):
            out[cur].append(s.split())
    return out


NODE_SECS = ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS")
LINK_SECS = ("CONDUITS", "PUMPS", "ORIFICES", "WEIRS", "OUTLETS")


def ids(S: dict, secs) -> set[str]:
    return {r[0] for sec in secs for r in S.get(sec, []) if r}


def curves(S: dict) -> dict[str, list[tuple[float, float]]]:
    c: dict[str, list[tuple[float, float]]] = {}
    for r in S.get("CURVES", []):
        if len(r) >= 3:
            try:
                c.setdefault(r[0], []).append((float(r[-2]), float(r[-1])))
            except ValueError:
                pass
    return {k: sorted(v) for k, v in c.items()}


def ground_stats(ds, terrain, x: float, y: float, ring_m: float = RING_M):
    """塘位周边地面的分位数。不取单格——单格会被噪声带偏。"""
    from rasterio.windows import from_bounds

    w = from_bounds(x - ring_m, y - ring_m, x + ring_m, y + ring_m, ds.transform)
    a = ds.read(1, window=w).astype("float64")
    if ds.nodata is not None:
        a[a == ds.nodata] = np.nan
    z = a[np.isfinite(a)]
    if z.size < 8:
        return None
    return z


def audit(model: str, log=print) -> dict:
    base, plan = WORK / f"{model}_baseline.inp", WORK / f"{model}_R61D2.inp"
    if not base.exists() or not plan.exists():
        log(f"{model}: 缺 {base.name} 或 {plan.name}，跳过"); return {}
    B, S = sections(base), sections(plan)
    new_nodes = ids(S, NODE_SECS) - ids(B, NODE_SECS)
    new_links = ids(S, LINK_SECS) - ids(B, LINK_SECS)
    crv = curves(S)
    coord = {r[0]: (float(r[1]), float(r[2])) for r in S.get("COORDINATES", []) if len(r) >= 3}

    from swbrain import db, terrain
    cfg = db.load_config(None)
    ds = terrain.open_dtm(cfg) if terrain.available(cfg) else None

    # 泵能力
    pumps_new = [r for r in S.get("PUMPS", []) if r and r[0] in new_links]
    qmax = {}
    for r in pumps_new:
        if len(r) >= 4:
            v = crv.get(r[3])
            qmax[r[0]] = (max(q for _, q in v) / 1000.0) if v else 0.0

    log(f"\n{'=' * 78}\n{model}　新增节点 {len(new_nodes)}　新增连接件 {len(new_links)}"
        f"　其中泵 {len(pumps_new)} 台，额定合计 {sum(qmax.values()):.3f} m³/s")

    ponds = []
    hdr = (f"  {'塘':<14}{'底':>8}{'顶':>8}{'深':>6}{'容积m³':>10}"
           f"{'地面':>8}{'底-地面':>9}{'高过周边':>9}　{'壁':<6}{'进水':<16}{'出水'}")
    log(hdr)
    for r in S.get("STORAGE", []):
        if not r or r[0] not in new_nodes:
            continue                      # 基准态就有的是既有湿井，不是顾问新建
        nid = r[0]
        inv, ymax = float(r[1]), float(r[2])
        cid = r[5] if len(r) > 5 and r[4].upper() == "TABULAR" else None
        pts = crv.get(cid, [])
        vol = sum(0.5 * (a0 + a1) * (d1 - d0)
                  for (d0, a0), (d1, a1) in zip(pts, pts[1:])) if len(pts) >= 2 else float("nan")
        # 边坡比：按棱台反算 m = (√A1 − √A0) / (2d)，直接和报告的 3:1 对
        slope = float("nan"); wall = "—"
        if len(pts) >= 2 and pts[0][1] > 0 and ymax > 0:
            slope = (math.sqrt(pts[-1][1]) - math.sqrt(pts[0][1])) / (2 * ymax)
            wall = "直壁" if slope < 0.25 else f"{slope:.1f}:1"
        # 地上还是地下
        gz = above = pct = float("nan")
        if ds is not None and nid in coord:
            z = ground_stats(ds, terrain, *coord[nid])
            if z is not None:
                gz = float(np.median(z)); above = inv - gz
                pct = float((z < inv).mean() * 100)
        # 进水 / 出水
        ins = [(sec, r2) for sec in LINK_SECS for r2 in S.get(sec, [])
               if len(r2) >= 3 and r2[2] == nid]
        outs = [(sec, r2) for sec in LINK_SECS for r2 in S.get(sec, [])
                if len(r2) >= 3 and r2[1] == nid]
        # 进水链路上游是不是泵
        fed_by_pump = 0.0
        for sec, r2 in ins:
            if sec == "PUMPS":
                fed_by_pump += qmax.get(r2[0], 0.0)
            else:                                   # 管：看它上游节点是不是泵的出口
                up = r2[1]
                for rp in pumps_new:
                    if len(rp) >= 3 and rp[2] == up:
                        fed_by_pump += qmax.get(rp[0], 0.0)
        in_txt = (f"泵 {fed_by_pump:.3f} m³/s" if fed_by_pump > 0
                  else (f"{len(ins)} 条重力" if ins else "**无进水**"))
        out_txt = "—（死端）" if not outs else f"{len(outs)} 条"
        log(f"  {('塘' if (fed_by_pump > 0 and not outs) else '井'):<2}{nid:<13}"
            f"{inv:>8.2f}{inv + ymax:>8.2f}{ymax:>6.2f}{vol:>10,.0f}"
            f"{gz:>8.2f}{above:>+9.2f}{pct:>8.0f}%　{wall:<6}{in_txt:<16}{out_txt}")
        # 按**功能**分类，不按名字：泵进水且无出水口 = 塘；否则是泵站湿井
        kind = "塘" if (fed_by_pump > 0 and not outs) else "湿井"
        ponds.append(dict(node=nid, kind=kind, invert=inv, top=inv + ymax, depth=ymax,
                          volume=vol, ground=gz, above=above, pct_above=pct, wall=wall,
                          slope=slope, q_in=fed_by_pump, n_out=len(outs)))

    pd = [p for p in ponds if p["kind"] == "塘"]
    wls = [p for p in ponds if p["kind"] == "湿井"]
    if pd:
        va = [p for p in pd if p["above"] == p["above"]]
        n_up = sum(1 for p in va if p["above"] > 0.05)
        n_dn = sum(1 for p in va if p["above"] < -0.05)
        n_wall = sum(1 for p in pd if p["wall"] == "直壁")
        n_dead = sum(1 for p in pd if p["n_out"] == 0)
        V = sum(p["volume"] for p in pd if p["volume"] == p["volume"])
        Q = sum(p["q_in"] for p in pd)
        pk = PEAK_2H.get(model, float("nan"))
        sl = [p["slope"] for p in pd if p["slope"] == p["slope"] and p["slope"] >= 0.25]
        log(f"  —— **塘 {len(pd)} 座**（另有泵站湿井 {len(wls)} 座，不是塘）：")
        log(f"     地上 {n_up} 座、地下 {n_dn} 座　直壁 {n_wall} 座　有边坡 {len(sl)} 座"
            + (f"（{min(sl):.1f}:1 ～ {max(sl):.1f}:1）" if sl else "")
            + f"　无出水口（死端）{n_dead} 座")
        log(f"     合计容积 {V:,.0f} m³　合计进塘能力 {Q:.3f} m³/s"
            f"　占全网 2 h 洪峰 {pk:.2f} m³/s 的 **{Q / pk * 100:.1f}%**")
        if Q > 0:
            log(f"     按这个进水能力灌满 {V:,.0f} m³ 需要 {V / Q / 3600:.1f} h"
                f"（雨峰只有 1–2 h）")
    return {"model": model, "ponds": ponds, "n_new_nodes": len(new_nodes),
            "n_new_links": len(new_links), "n_pumps": len(pumps_new),
            "q_pumps": sum(qmax.values())}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="核顾问方案：建的是什么、地上还是地下")
    ap.add_argument("models", nargs="*", default=None, choices=MODELS + [[]])
    a = ap.parse_args(argv)
    todo = a.models or MODELS
    print(f"判据：塘底标高 vs 周边 {RING_M:g} m 实测地面（客户 5 m DTM，模型用 DTM 原始基准）")
    res = [audit(m) for m in todo]
    res = [r for r in res if r]
    if len(res) > 1:
        pd = [p for r in res for p in r["ponds"] if p["kind"] == "塘"]
        wls = [p for r in res for p in r["ponds"] if p["kind"] == "湿井"]
        up = sum(1 for p in pd if p["above"] > 0.05)
        st = sum(1 for p in pd if p["wall"] == "直壁")
        dd = sum(1 for p in pd if p["n_out"] == 0)
        V = sum(p["volume"] for p in pd if p["volume"] == p["volume"])
        Q = sum(p["q_in"] for p in pd)
        pk = sum(PEAK_2H[r["model"]] for r in res)
        print(f"\n{'=' * 78}")
        print(f"三区合计 **塘 {len(pd)} 座**（另有泵站湿井 {len(wls)} 座，不是塘）：")
        print(f"  **地上 {up} 座**、地下 {len(pd) - up} 座　直壁 {st} 座　死端（无出水口）{dd} 座")
        print(f"  合计容积 {V:,.0f} m³　新增泵 {sum(r['n_pumps'] for r in res)} 台")
        print(f"  合计进塘能力 {Q:.2f} m³/s —— 三区 2 h 洪峰合计 {pk:.0f} m³/s 的 "
              f"**{Q / pk * 100:.1f}%**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
