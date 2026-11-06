# -*- coding: utf-8 -*-
"""把 L2+ 的每一座设施导成图层，点开能看到「为什么建、为什么在这、为什么这个大小」。

为什么要有这一层：
  四态表只能告诉人「L2+B 比 L2+A 少淹 42%」，回答不了审的人真正会问的问题 ——
  凭什么是这个位置？凭什么是箱涵不是开敞塘？凭什么是 6,669 m³ 不是 3,000？
  这些依据本来就算出来了（out/l2plus/why_*.json），但只存在文件里，
  在系统里看不见，等于要靠人转述。

两类来源必须分开画、分开写：
  **L2+A（Parsons 设计）** —— 报告里没有逐座的选址理由，只有一句
  「proposed within available open spaces, subject to site suitability」。
  所以这一类只写**从交付模型里读得出来的事实**（位置、形式、容积、进出水），
  并标注哪句是报告原文、哪句是从模型读出，**不替顾问编理由**。
  **L2+B（本系统设计）** —— 选址/定容/定管都是算出来的，依据逐条给。

位置一律给**到汇水区多边形的距离**：设施是按一维管网里的积水节点布的，
而管网（235 ha）比汇水区多边形（82 ha）大，所以有几座落在多边形之外。
不给距离，「区内/区外」就是一句没有判据的话。

用法：python tools/export_l2plus.py
产物：console/data/l2plus.geojson
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "console" / "data"
SRC = ROOT / "out" / "l2plus"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    import pyproj

    hits = sorted(SRC.glob("why_*.json"))
    if not hits:
        print(f"没有 {SRC}/why_*.json，先跑 tools/l2plus_why.py")
        return 1
    d = json.loads(hits[-1].read_text(encoding="utf-8"))
    # 独立复核的结果（离路面/建筑/地块/红线的净距），有就带上
    lc = SRC / "box_land_check_R61D2.json"
    LAND = json.loads(lc.read_text(encoding="utf-8")) if lc.exists() else {}
    to84 = pyproj.Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True).transform

    feats = []
    for grp, state, who in (("parsons", "L2+A", "Parsons 顾问设计"),
                            ("ours", "L2+B", "本系统设计")):
        for p in d.get(grp, []):
            x, y = p.get("x"), p.get("y")
            if x is None or y is None:
                print(f"  {p.get('id')} 没有坐标，跳过"); continue
            lon, lat = to84(float(x), float(y))
            # 容积：顾问那边是模型里读出的几何容积；我们这边分「可用」与「几何」
            vol = p.get("volume_m3") or p.get("vol_geometric_m3")
            pr = {
                "id": p.get("id"), "state": state, "who": who,
                "volume_m3": vol,
                "vol_usable_m3": p.get("vol_usable_m3"),
                "vol_geometric_m3": p.get("vol_geometric_m3"),
                "why_type": p.get("why_type"),
                "why_here": p.get("why_here"),
                "why_size": p.get("why_size"),
                "n_served": p.get("n_served"), "served_m3": p.get("served_m3"),
                "share_pct": p.get("share_of_gap"),
                "depth_m": p.get("depth_m"), "wall": p.get("wall"),
                "n_in": p.get("n_in"), "n_out": p.get("n_out"),
                "pump_fed": p.get("pump_fed"),
                "d_catchment_m": p.get("d_catchment_m"),
                "in_catchment": p.get("in_catchment"),
                "d_net1d_m": p.get("d_net1d_m"), "in_net1d": p.get("in_net1d"),
                "in_domain2d": p.get("in_domain2d"),
                "rules": p.get("rules") or [],
                # 用地复核（2026-09-26 起箱涵先避开路面、建筑、地块和红线）：挪了多少、落在什么地上、离各类用地多远
                "land": p.get("land"), "moved_m": p.get("moved_m"),
                **({f"clr_{k}_m": v for k, v in (LAND.get(p.get("id")) or {}).items()
                    if k in ("pave", "bld", "plot", "row")} if grp == "ours" else {}),
                "evidence": ("报告原文 + 从交付模型读出" if grp == "parsons"
                             else "本系统算出，依据逐条可复核"),
            }
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point",
                                       "coordinates": [round(lon, 6), round(lat, 6)]},
                          "properties": pr})

    fc = {"type": "FeatureCollection", "layer": "l2plus",
          "source": f"{hits[-1].name}（L2+A 只写模型读出的事实，不替顾问编理由）",
          "rules": d.get("rules", {}),
          "features": feats}
    DATA.mkdir(parents=True, exist_ok=True)
    f = DATA / "l2plus.geojson"
    f.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")

    # 箱涵的真实占地轮廓：地图上按比例画方形，而不是按容积画圆
    OUT3 = ROOT / "out" / "three_lines" / "Mussafah.L2B.R61D2.ponds.geojson"
    if OUT3.exists():
        g = json.loads(OUT3.read_text(encoding="utf-8"))
        fp = {"type": "FeatureCollection", "layer": "l2plus_fp",
              "features": [{"type": "Feature", "geometry": x["geometry"],
                            "properties": {"id": x["properties"]["id"], "state": "L2+B"}}
                           for x in g["features"] if x.get("geometry")]}
        (DATA / "l2plus_fp.geojson").write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
        print(f"箱涵轮廓 {len(fp['features'])} 个 → {DATA / 'l2plus_fp.geojson'}")

    print(f"{'设施':<8}{'态':<7}{'容积 m³':>11}{'位置':>22}")
    for x in feats:
        p = x["properties"]
        loc = ("**区内**" if p["in_catchment"]
               else f"区外 {p['d_catchment_m']:,.0f} m" if p["d_catchment_m"] is not None else "?")
        print(f"{str(p['id']):<8}{p['state']:<7}{(p['volume_m3'] or 0):>11,.0f}{loc:>22}")
    n_in = sum(1 for x in feats if x["properties"]["in_catchment"])
    print(f"\n{len(feats)} 座 → {f}")
    print(f"  落在 Mussafah_00 汇水区多边形内 {n_in} 座，其余在管网范围内但在多边形外")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
