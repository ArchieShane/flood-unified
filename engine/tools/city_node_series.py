# -*- coding: utf-8 -*-
"""全市一维 L1 / L2：每个溢水点（冒水的井）每 5 分钟的溢流流量与井内水深 → 交付表 + 控制台逐井过程线。

数据：City_{L1,L2}_v2ts_p1…p8（与报告那一版 v2 同设置，只是存了逐时结果；演算 6 h，所以累计溢流比 v2 的 30 h 窗口少约 1.2%）。
口径：「溢水点」= 6 h 内溢流 ≥ 1 m³ 的井。L1 / L2 对比沿用 city_gap 的口径：不接塘的管网 L2 取 L1（分片数值噪声记 0）；
      交付的各态逐时表是各态自己的原始数。

产物：
  out/交付/全市_61mm2h/<态>/一维/溢水点/溢水点汇总.csv、逐时溢流_m3s.csv、逐时水深_m.csv
  out/交付/全市_61mm2h/对比_缺口分析/溢水点_L1对L2.csv
  console/data/city_nodeseries/<tx>_<ty>.json（0.02° 分块，点井时按需取）；city_nodes.json 补上井号
用法：python tools/city_node_series.py
"""
from __future__ import annotations

import csv
import json
import math
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from city_gap import pond_networks, xy_of  # noqa: E402
from workdir import WORK  # noqa: E402

D = ROOT / "out" / "交付" / "全市_61mm2h"
FOLD = {"L1": "L1_既有管网", "L2": "L2_加22座已建成塘"}
MIN_M3 = 1.0
TILE = 0.02                   # 控制台分块（度）；app.js 用同一个数
STEP_S = 300.0


def flood_summary(rpt):
    """Node Flooding Summary：井 → (淹没时长 h, 最大溢流 m³/s, 最大出现时刻 h, 累计溢流 m³)。"""
    t = pathlib.Path(rpt).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Node Flooding Summary.*?-{20,}\n.*?-{20,}\n(.*?)\n\s*\n", t, re.S)      # 表头两道横线之后到第一个空行
    blk = m.group(1) if m else ""
    out = {}
    for ln in blk.splitlines():
        p = ln.split()
        if len(p) >= 6 and re.match(r"^\d+:\d\d$", p[4]):
            d, hm = int(p[3]), p[4].split(":")
            out[p[0]] = (float(p[1]), float(p[2]), d * 24 + int(hm[0]) + int(hm[1]) / 60, float(p[5]) * 1000)
    return out


def read_out(path):
    from swmm.toolkit import output, shared_enum as E
    h = output.init(); output.open(h, str(path))
    n = output.get_proj_size(h)[1]; per = output.get_times(h, E.Time.NUM_PERIODS)
    names = [output.get_elem_name(h, E.ElementType.NODE, i) for i in range(n)]
    q = np.array([output.get_node_attribute(h, t, E.NodeAttribute.FLOODING_LOSSES) for t in range(per)], np.float32)
    d = np.array([output.get_node_attribute(h, t, E.NodeAttribute.INVERT_DEPTH) for t in range(per)], np.float32)
    output.close(h)
    return names, q, d, per


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from pyproj import Transformer
    from shapely import STRtree
    from shapely.geometry import Point
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    C = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    ctree = STRtree(list(C.geometry)); clab = list(C["label"])
    aff, _ = pond_networks("_v2", 8)
    S = {}                    # 态 → 井 → dict
    T = None
    for st in ("L1", "L2"):
        S[st] = {}
        for p in range(1, 9):
            b = WORK / f"City_{st}_v2ts_p{p}"
            fs = flood_summary(b.with_suffix(".rpt"))
            names, q, d, per = read_out(b.with_suffix(".out"))
            T = [(i + 1) * STEP_S / 3600 for i in range(per)]
            keep = [i for i, k in enumerate(names) if fs.get(k, (0, 0, 0, 0))[3] >= MIN_M3]
            for i in keep:
                k = names[i]; hrs, qmax, tmax, vol = fs[k]
                qi = q[:, i]; on = np.flatnonzero(qi > 1e-4)
                S[st][k] = {"piece": p, "hours": hrs, "qmax": qmax, "tmax": tmax, "vol": vol, "dmax": float(d[:, i].max()),
                            "t_on": T[on[0]] if len(on) else None, "q": qi, "d": d[:, i]}
            print(f"  {st} 片{p}：{len(names):,} 井，溢水点 {len(keep):,}", flush=True)
    # 位置与汇水区
    allk = sorted(set(S["L1"]) | set(S["L2"]))
    geo = {}
    for k in allk:
        xy = xy_of(k)
        if not xy:
            continue
        lo, la = tr.transform(*xy)
        hit = ctree.query(Point(xy), predicate="within")
        geo[k] = (round(la, 6), round(lo, 6), clab[int(hit[0])] if len(hit) else "")
    tcols = [f"t{t:05.2f}h" for t in T]
    # 交付：各态三张表
    for st in ("L1", "L2"):
        o = D / FOLD[st] / "一维" / "溢水点"; o.mkdir(parents=True, exist_ok=True)
        ks = sorted(S[st], key=lambda k: -S[st][k]["vol"])
        with open(o / "溢水点汇总.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["井号", "纬度", "经度", "片区", "客户汇水区", "开始溢流_h", "峰值溢流_m3s", "峰值时刻_h", "溢流时长_h", "累计溢流_m3", "井内最大水深_m", "接塘管网"])
            for k in ks:
                r = S[st][k]; g = geo.get(k, (None, None, ""))
                w.writerow([k, g[0], g[1], r["piece"], g[2], None if r["t_on"] is None else round(r["t_on"], 2), round(r["qmax"], 4),
                            round(r["tmax"], 2), round(r["hours"], 2), round(r["vol"], 1), round(r["dmax"], 3), "是" if k in aff else ""])
        for fn, key, dg in (("逐时溢流_m3s.csv", "q", 4), ("逐时水深_m.csv", "d", 3)):
            with open(o / fn, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh); w.writerow(["井号"] + tcols)
                for k in ks:
                    w.writerow([k] + [round(float(v), dg) for v in S[st][k][key]])
        (o / "说明.txt").write_text(
            f"{st} 全市一维，61 mm / 2 h，每 5 分钟一个时刻（t = 该时刻末），共 {len(T)} 个。\n"
            f"溢水点 = 6 h 内累计溢流 ≥ {MIN_M3:g} m³ 的井，共 {len(ks):,} 口。\n"
            "溢流流量 = 从井盖冒出去的流量（m³/s），全市模型冒出井盖就算损失（ALLOW_PONDING NO）。\n"
            "数据来自 一维/逐时过程/*.out（与 一维/报告 同设置、只算到 6 h，所以累计溢流比报告那一版少约 1.2%）。\n"
            "开始溢流 / 峰值 / 时长 / 累计取自 SWMM 报告的 Node Flooding Summary；经纬度 WGS84，井号即 UTM 坐标。\n", encoding="utf-8")
        print(f"  {st}：交付表 {len(ks):,} 行 → {o}")
    # 交付：L1 对 L2
    pick = lambda k: S["L2"].get(k) if k in aff else S["L1"].get(k)
    with open(D / "对比_缺口分析" / "溢水点_L1对L2.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["井号", "纬度", "经度", "客户汇水区", "接塘管网", "L1累计溢流_m3", "L2累计溢流_m3", "差_m3", "L1峰值_m3s", "L2峰值_m3s", "L1时长_h", "L2时长_h", "变化"])
        rows = []
        for k in allk:
            a, b = S["L1"].get(k), pick(k)
            va, vb = (a or {}).get("vol", 0.0), (b or {}).get("vol", 0.0)
            ch = "不再溢流" if va >= MIN_M3 and vb < MIN_M3 else "新增溢流" if va < MIN_M3 and vb >= MIN_M3 else "减少" if vb < va - 1 else "增加" if vb > va + 1 else "不变"
            g = geo.get(k, (None, None, ""))
            rows.append([k, g[0], g[1], g[2], "是" if k in aff else "", round(va, 1), round(vb, 1), round(vb - va, 1),
                         round((a or {}).get("qmax", 0), 4), round((b or {}).get("qmax", 0), 4), round((a or {}).get("hours", 0), 2), round((b or {}).get("hours", 0), 2), ch])
        rows.sort(key=lambda r: r[7])
        w.writerows(rows)
    from collections import Counter
    print("  L1 对 L2：", dict(Counter(r[-1] for r in rows)))
    # 控制台：分块逐井过程线（溢流 L/s 取整、水深 cm 取整；L2 按对比口径）
    od = ROOT / "console" / "data" / "city_nodeseries"; od.mkdir(parents=True, exist_ok=True)
    for f in od.glob("*.json"):
        f.unlink()
    tiles = {}
    for k in allk:
        if k not in geo:
            continue
        la, lo, lab = geo[k]
        a, b = S["L1"].get(k), pick(k)
        z = lambda r, key, f: [int(round(float(v) * f)) for v in r[key]] if r else None
        tiles.setdefault(f"{math.floor(lo / TILE)}_{math.floor(la / TILE)}", {})[k] = {
            "ll": [la, lo], "c": lab, "p": k in aff, "q1": z(a, "q", 1000), "q2": z(b, "q", 1000), "d1": z(a, "d", 100), "d2": z(b, "d", 100),
            "v1": round((a or {}).get("vol", 0)), "v2": round((b or {}).get("vol", 0))}
    for tk, v in tiles.items():
        (od / f"{tk}.json").write_text(json.dumps(v, separators=(",", ":")), encoding="utf-8")
    (od / "index.json").write_text(json.dumps({"tile_deg": TILE, "t_h": [round(t, 4) for t in T], "units": {"q": "L/s", "d": "cm", "v": "m3"},
                                               "n": sum(len(v) for v in tiles.values()), "tiles": sorted(tiles)}, ensure_ascii=False), encoding="utf-8")
    sz = sum(f.stat().st_size for f in od.glob("*.json"))
    print(f"  控制台：{len(tiles)} 块、{sum(len(v) for v in tiles.values()):,} 口井，{sz / 1e6:.0f} MB → {od}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
