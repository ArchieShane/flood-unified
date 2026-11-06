# -*- coding: utf-8 -*-
"""L2+ 每座设施的「为什么建、为什么建在这里、为什么是这个形式和这个大小」。

------------------------------------------------------------------ 为什么要有这份说明
一张表只给出「箱涵 5,799 m³」是不可审的 —— 审的人会问：凭什么是这个位置？
凭什么是箱涵不是开敞塘？凭什么是 5,799 不是 3,000？
这份说明把每一条都落到**可复核的依据**上：服务哪些积水点、那些点差多少水、
用地是什么、依据手册哪一条。

------------------------------------------------------------------ 两类来源要分开写
**L2+B（我们设计的）**：选址、定容、定管全是本系统算出来的，依据能一条条给。
**L2+A（Parsons 设计的）**：他们的报告没有写逐座的选址理由，只有一句
「proposed within available open spaces, **subject to site suitability**」。
所以这里只写**从交付模型里能读出来的事实**（位置、形式、容积、进出水方式），
并明确标注「报告原文」与「从模型读出」，**不替顾问编理由**。

用法：
    python tools/l2plus_why.py Mussafah --tag R61D2
产物：out/l2plus/why_<区>_<tag>.json，并在终端打印可读版
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

REGION = "Mussafah_00"      # **必须写全**：只写 Mussafah 会被当成整个片区（约 5,041 ha）
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from workdir import WORK, OUT  # noqa: E402

# 手册依据，写在这里便于逐条核
RULES = {
    "lagoon_ban": "WA-726-1 第三版 §4.2：建成区内或紧邻建成区**不得设置开敞式塘**",
    "offline_box": "§4.2 B：离线调蓄应为混凝土箱涵",
    "head": "§4.14.1(b)：进出水高差宜 1.8–2.4 m",
    "clear": "§4.14.1(p)：管内底应高出塘底 ≥ 0.30 m",
    "access": "§4.14.1(d)：需留 5 m 检修通道",
    "spill": "§4.14.1(f)：须设应急溢流",
    "sediment": "§4.2：容积另加 5% 沉沙",
}


def locate(items, log=print):
    """逐座算「离汇水区多边形多远、在不在二维域内」。

    为什么要单独算：设施是按**一维管网里的积水节点**布的，而管网（235 ha）
    铺得比汇水区多边形（82 ha）大，所以有几座会落在多边形外面。
    报「在 Mussafah_00 建了几座」之前必须先把这个距离摆出来，
    否则「区内/区外」就成了一句没有判据的话。
    **必须用多边形算距离，不能用质心** —— 用质心曾经把一座实际相交的塘判成「在区外」。
    """
    import json
    from shapely.geometry import shape, Point
    from shapely.ops import transform as shp_tr
    import pyproj

    f = ROOT / "console" / "data" / "scope.geojson"
    if not f.exists():
        log("  缺 scope.geojson，跳过定位（先跑 tools/export_scope.py）")
        return items
    sc = json.loads(f.read_text(encoding="utf-8"))
    to40 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform
    def pick(role):
        g = next((x["geometry"] for x in sc["features"]
                  if x["properties"].get("role") == role), None)
        return shp_tr(to40, shape(g)) if g else None
    C, N, D = pick("modelled"), pick("net1d"), pick("domain2d")
    for it in items:
        x, y = it.get("x"), it.get("y")
        if x is None or y is None:
            continue
        g = Point(float(x), float(y))
        for key, poly in (("catchment", C), ("net1d", N), ("domain2d", D)):
            if poly is None:
                continue
            d = poly.distance(g)
            it[f"d_{key}_m"] = round(d, 1)
            it[f"in_{key}"] = (d == 0.0)
    return items


def parsons_facts(model: str, tag: str, log=print) -> list[dict]:
    """从客户交付的「治理后」原件里读 L2+A 的事实。**不替顾问编理由。**"""
    import re

    src = WORK / f"{model}_{tag}.inp" if tag else WORK / f"{model}.inp"
    base = WORK / f"{model}_baseline{('_' + tag) if tag else ''}.inp"
    if not src.exists() or not base.exists():
        log(f"  缺 {src.name} 或 {base.name}"); return []

    def sections(p):
        out, cur = {}, None
        for ln in p.open(encoding="utf-8", errors="replace"):
            s = ln.strip()
            if s.startswith("["):
                cur = s.strip("[]").upper(); out[cur] = []; continue
            if cur and s and not s.startswith(";"):
                out[cur].append(s.split())
        return out

    S, B = sections(src), sections(base)
    NODE = ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS")
    LINK = ("CONDUITS", "PUMPS", "ORIFICES", "WEIRS", "OUTLETS")
    new_nodes = {r[0] for s in NODE for r in S.get(s, []) if r} - \
                {r[0] for s in NODE for r in B.get(s, []) if r}
    new_links = {r[0] for s in LINK for r in S.get(s, []) if r} - \
                {r[0] for s in LINK for r in B.get(s, []) if r}
    coord = {r[0]: (float(r[1]), float(r[2])) for r in S.get("COORDINATES", []) if len(r) >= 3}
    crv = {}
    for r in S.get("CURVES", []):
        if not r:
            continue
        rest = r[1:]
        if rest and not re.match(r"^-?[\d.]+$", rest[0]):
            rest = rest[1:]
        v = []
        for x in rest:
            try:
                v.append(float(x))
            except ValueError:
                break
        for i in range(0, len(v) - 1, 2):
            crv.setdefault(r[0], []).append((v[i], v[i + 1]))

    out = []
    for r in S.get("STORAGE", []):
        if not r or r[0] not in new_nodes:
            continue
        nid, inv, ymax = r[0], float(r[1]), float(r[2])
        pts = sorted(crv.get(r[5], [])) if len(r) > 5 else []
        vol = sum(0.5 * (a0 + a1) * (d1 - d0) for (d0, a0), (d1, a1) in zip(pts, pts[1:]))
        ins = [(s, x) for s in LINK for x in S.get(s, []) if len(x) >= 3 and x[2] == nid]
        outs = [(s, x) for s in LINK for x in S.get(s, []) if len(x) >= 3 and x[1] == nid]
        pumps_in = [x[0] for s, x in ins if s == "PUMPS"]
        wall = "直壁" if len(pts) >= 2 and abs(pts[-1][1] - pts[0][1]) < 0.05 * pts[0][1] else "有边坡"
        out.append({
            "id": nid, "source": "L2+A（Parsons 设计）", "x": coord.get(nid, (None, None))[0],
            "y": coord.get(nid, (None, None))[1], "bottom_m": inv, "top_m": inv + ymax,
            "depth_m": ymax, "volume_m3": round(vol), "wall": wall,
            "n_in": len(ins), "n_out": len(outs), "pump_fed": len(pumps_in) > 0,
            "why_type": "报告原文：「在开放空间挖 2 m 深、3:1 边坡的临时塘，"
                        "12 寸移动泵在雨峰把重力管网的水抽进塘」（2027 雨季前实施）",
            "why_here": "报告原文：「proposed within available open spaces, "
                        "**subject to site suitability**」——逐座的选址理由报告未给",
            "observed": [],
        })
    return out, sorted(new_nodes), sorted(new_links)


def ours(model: str, tag: str, n_ponds: int, log=print) -> list[dict]:
    """L2+B：选址/定容/定管的依据，逐条给。"""
    import dig_pond3 as D3

    suffix = f"_{tag}" if tag else ""
    inp = WORK / f"{model}_baseline{suffix}.inp"
    rpt = WORK / f"{model}_baseline{suffix}.rpt"
    if not (inp.exists() and rpt.exists()):
        log(f"  缺 {inp.name} 或 {rpt.name}"); return []
    sites = D3.cluster_sites(inp, rpt, n_ponds, log=lambda *a: None)
    for i, s in enumerate(sites, 1):
        s["id"] = f"NP{i}"
    # 位置要取**实际落位**（按用地挪过之后的轮廓），不是聚类重心。
    # 2026-09-26 起箱涵先避开路面、建筑、地块和红线，离重心最多挪 400 m。
    built = {}
    gf = OUT / f"{model}.L2B{'.' + tag if tag else ''}.ponds.geojson"
    if gf.exists():
        from pyproj import Transformer
        from shapely.geometry import shape
        from shapely.ops import transform as reproj
        to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform
        for f in json.loads(gf.read_text(encoding="utf-8"))["features"]:
            c = reproj(to_utm, shape(f["geometry"])).centroid
            built[f["properties"]["id"]] = (c.x, c.y, f["properties"])
    tot = sum(s.get("served_m3", 0) for s in sites)
    out = []
    for s in sites:
        v = s.get("served_m3", 0.0)
        bx, by, bp = built.get(s["id"], (s.get("x"), s.get("y"), {}))
        land = bp.get("land")
        land_txt = ("" if not land else
                    f"再按用地挪了 {bp.get('moved_m', 0):.0f} m，落到**不压路面、建筑、地块、市政设施用地和铁路红线**的空地上"
                    + ("（避开了道路红线）。" if land == "open_land"
                       else "；这一处只能用**道路红线**内的地，要道路主管部门批准，下面可能有管线。")
                    + "空地是按客户 makani 图层判的，**权属与地下管线没有核**。")
        out.append({
            "id": s["id"], "source": "L2+B（本系统设计）",
            "x": bx, "y": by, "land": land, "moved_m": bp.get("moved_m"),
            "n_served": s.get("n_served"), "served_m3": round(v),
            "share_of_gap": round(v / tot * 100, 1) if tot else None,
            "why_here": f"选址不是挑地块挑出来的，是**按这场雨的积水分布聚类**出来的："
                        f"这一片有 {s.get('n_served')} 个积水节点、合计缺口 {v:,.0f} m³"
                        f"（占本区总缺口 {v / tot * 100 if tot else 0:.1f}%），"
                        f"箱涵就设在这一片的重心附近，离积水点近，水才进得来。" + land_txt,
            # 定容是**三层**，不是一层。早先这里只写了「缺口量 + 5% 沉沙」，
            # 漏掉了 1.5 倍聚类系数，导致说明与模型里的库容差 1.81 倍 —— 对不上就是错的。
            "why_size": f"起点是**这一片实测的缺口量 {v:,.0f} m³**，再乘三个因子："
                        f"①×1.5 聚类系数（覆盖峰值同时性与进水口吞吐不足，"
                        f"**这一条是假设，手册里没有，未经标定**）；"
                        f"②×1.05 沉沙（{RULES['sediment']}）；"
                        f"③×1.15 超高 0.30 m —— **超高是要空着的，不计入可用库容**。"
                        f"所以设计可用库容 {v * 1.5 * 1.05:,.0f} m³，"
                        f"模型里的几何容积 {v * 1.5 * 1.05 * 1.15:,.0f} m³。",
            "vol_usable_m3": round(v * 1.5 * 1.05),
            "vol_geometric_m3": round(v * 1.5 * 1.05 * 1.15),
            "why_type": None,      # 下面按形式填
            "rules": [RULES["clear"], RULES["head"], RULES["spill"], RULES["access"]],
        })
    return out


def loc_line(p, region):
    """一句话说清这座设施落在哪。区内/区外必须给米数，不能只给结论。"""
    if "d_catchment_m" not in p:
        return "位置　　　　　　　无坐标"
    c = ("**落在 " + region + " 汇水区内**" if p.get("in_catchment")
         else f"在 {region} 汇水区**外 {p['d_catchment_m']:,.0f} m**")
    n = ("管网范围内" if p.get("in_net1d")
         else f"管网范围外 {p.get('d_net1d_m', 0):,.0f} m")
    d = "二维域内" if p.get("in_domain2d") else "**二维域外**"
    return f"位置　　　　　　　{c}；{n}；{d}"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="L2+ 每座设施的建设理由与选址依据")
    ap.add_argument("model")
    ap.add_argument("--tag", default="R61D2")
    ap.add_argument("--region", default=REGION,
                    help="**精确到分区**的区域名，别只写 Mussafah")
    ap.add_argument("--n", type=int, default=6, help="本系统设计的设施数")
    a = ap.parse_args(argv)

    print(f"**{a.region}** · L2+ 设施说明（雨情 61 mm / 2 h）\n")
    # 区域必须写全 —— 只写 Mussafah 会被当成整个片区（约 5,041 ha，本区只占 1.6%）
    print(f"  {a.region} 是穆萨法片区 8 个编号分区之一，汇水区多边形 **82.0 ha**，"
          f"占该片区 5,041 ha 的 1.6%。**不是整个穆萨法。**")
    print(f"  客户台账 tbl_ha 记 148.76 ha，几何只有 81.98（0.55×）——以几何为准；"
          f"其余 7 个分区台账/几何比值都在 0.86–1.02，只有这一条差 1.81 倍。")
    print(f"  设施按**一维管网里的积水节点**布点，而管网铺到 235 ha、比汇水区多边形大，"
          f"所以有几座落在多边形之外 —— 下面逐座给距离，不含糊。\n")
    print("=" * 92)
    print("L2+A　Parsons 设计　——　只写从交付模型读出的事实，不替顾问编理由")
    print("=" * 92)
    pa, nn, nl = parsons_facts(a.model, a.tag)
    pa = locate(pa)
    for p in pa:
        print(f"\n  【{p['id']}】{p['volume_m3']:,} m³　深 {p['depth_m']:.2f} m　{p['wall']}"
              f"　塘底 {p['bottom_m']:.2f}　塘顶 {p['top_m']:.2f}")
        print(f"    为什么是这类设施　{p['why_type']}")
        print(f"    为什么建在这里　　{p['why_here']}")
        print(f"    {loc_line(p, a.region)}")
        print(f"    从模型读出　　　　进水 {p['n_in']} 条"
              f"（{'靠泵' if p['pump_fed'] else '重力'}）、出水 {p['n_out']} 条"
              + ("　**没有出水口，灌满即止**" if p["n_out"] == 0 else ""))
    print(f"\n  顾问一共新增 {len(nn)} 个节点、{len(nl)} 个连接件。")

    print("\n" + "=" * 92)
    print("L2+B　本系统设计　——　选址/定容/定管逐条给依据")
    print("=" * 92)
    ob = ours(a.model, a.tag, a.n)
    ob = locate(ob)
    for p in ob:
        print(f"\n  【{p['id']}】服务 {p['n_served']} 个积水点，缺口 {p['served_m3']:,} m³"
              f"（占全区 {p['share_of_gap']}%）")
        print(f"    为什么建在这里　　{p['why_here']}")
        print(f"    为什么是这个大小　{p['why_size']}")
        print(f"    {loc_line(p, a.region)}")
    if ob:
        print(f"\n  形式选择（全区一致，不逐座）：")
        print(f"    选**地下箱涵**而不是地面开敞塘 —— {RULES['lagoon_ban']}；"
              f"离线调蓄按 {RULES['offline_box']}。")
        print(f"    开敞塘只能落在台账的开放/游憩用地上，而这类地块离积水点中位 2.4–2.9 km，"
              f"本质上变成输水干管工程；Mussafah 区内可用地为 0，开敞塘**建不了**。")
        print(f"    进水口按「灌满 ≈ 雨峰窗口」定径（太小进不去、太大变过水通道），"
              f"带闸、箱涵蓄至 95% 关闸；出水口按 24 h 排空定径，"
              f"**且必须排到进水井的下游**（排回进水井会形成 管网→塘→泵→管网 闭环）。")

    d = OUT.parent / "l2plus"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"why_{a.model}_{a.tag}.json"
    f.write_text(json.dumps({"parsons": pa, "ours": ob, "rules": RULES},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写出 {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
