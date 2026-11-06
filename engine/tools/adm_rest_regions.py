# -*- coding: utf-8 -*-
"""ADM 剩余汇水区（库图层 57 条里未推演的 44 条）· 案例数据生成（2026-10-19）。

口径（用户 10-19）：二维格网 < 1 km² 的汇水区用 5 m，其余 30 m；61 mm / 1 h；L1 + L2。
计算域 = ADM汇水区推演状态.geojson 里该汇水区的多边形（KMZ 或客户 flood 库 catchment.catchment 取来的），不外扩。
  · OLD 是 6 块互不相连的多边形（合计 23.28 km²），二维计算域只能是单个多边形 → 拆成 6 个任务 OLD1…OLD6（按面积从大到小）
  · B_C 多边形里有一个 400 m² 的洞 → 填掉（取外环）

在开发机上跑一次（用 .venv 的 python：本机系统 python 的 numpy.random 被应用控制策略拦了），产物进 cases/ad/（随上传包走）：
  cases/ad/rest_regions.json                 44 个区域（ad_regions.py 启动时并进 REGIONS）：代号、名称、目录名、格网 res、面积、积水点数、服务器
  cases/ad/<代号>_domain.geojson / _existing_pumps.json / _ponds.json
  cases/ad/hotspots_ad.json                  把这批区域的积水点并进去（原有 15 个区的不动）
  cases/ad/rest_plan.json                    各台服务器的任务清单（脚本按主机名取自己那份）

代号：名称去掉非字母数字后大写（RD-H → RDH）；重名的（OUTLET ×6）加库 id（OUTLET63）；代号里不能有 "_"（情景名 <代号>_M61H1 按第一个 "_" 拆）。
交付目录名（NAS 上 <区域目录>_61mm1h_<网格>m）：名称里的空格换成 "_"；OUTLET 加库 id（OUTLET-63），保证在 flood-aio 里一个名字对一个任务。
用法：.venv/Scripts/python.exe tools/adm_rest_regions.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import ad_regions as AR  # noqa: E402

CASES = AR.CASES
STATUS = ROOT.parent / "ADM汇水区推演状态.geojson"
PLAN_ITEMS = ROOT.parent / "_build" / "rest_plan_items.json"
SRC_NAME = "ADM汇水区推演状态.geojson"


def code_of(label: str, lid: int, dup: set) -> str:
    c = re.sub(r"[^0-9A-Za-z]", "", label).upper()
    return f"{c}{lid}" if label in dup else c


def dir_of(label: str, lid: int, dup: set) -> str:
    d = re.sub(r"\s+", "_", label.strip())
    return f"{d}-{lid}" if label in dup else d


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely.geometry import Polygon, shape
    import rg_regions as RG

    items = json.loads(PLAN_ITEMS.read_text(encoding="utf-8"))
    feats = json.loads(STATUS.read_text(encoding="utf-8"))["features"]
    labels = [i["label"] for i in items]
    dup = {l for l in labels if labels.count(l) > 1}
    taken = set(AR.REGIONS)
    out, jobs = {}, []
    for it in items:
        m = [f for f in feats if f["properties"]["name"] == it["label"] and abs(f["properties"]["km2"] - it["km2"]) < 0.02 and f["properties"]["status"] == "未推演"]
        if len(m) != 1:
            raise SystemExit(f"{it['label']} {it['km2']}：状态图层里匹配到 {len(m)} 个多边形")
        g = gpd.GeoSeries([shape(m[0]["geometry"])], crs=4326).to_crs(32640).iloc[0]
        parts = sorted(g.geoms, key=lambda x: -x.area) if g.geom_type == "MultiPolygon" else [g]
        for k, p in enumerate(parts, 1):
            p = Polygon(p.exterior)                      # 填洞（B_C 的 400 m² 小洞）
            suffix = f"{k}" if len(parts) > 1 else ""
            code = code_of(it["label"], it["lid"], dup) + suffix
            if code in taken or "_" in code:
                raise SystemExit(f"代号 {code} 重复或含下划线")
            taken.add(code)
            title = it["label"] + (f" (part {k}/{len(parts)})" if suffix else "")
            res = it["res"] if len(parts) == 1 else (5 if p.area / 1e6 < 1.0 else 30)
            out[code] = dict(title=title, kmz=SRC_NAME, feat=it["label"], dir=dir_of(it["label"], it["lid"], dup) + (f"-{k}" if suffix else ""),
                             adm=f"库{it['lid']}", km2=round(p.area / 1e6, 2), res=res, libid=it["lid"], server=it["server"], rains=[[61, 1]])
            jobs.append((code, p))
    # write_pumps 每个区域都要读一遍全市 45 万根管、重建节点（每次约 2 分钟）：缓存起来，49 个区域只读一次
    import build_city1d as B
    _rp, _bn = B.read_pipes, B.build_nodes
    _c = {}
    B.read_pipes = lambda *a, **k: _c.setdefault("df", _rp(*a, **k))
    B.build_nodes = lambda df, *a, **k: _c.setdefault("nodes", _bn(df, *a, **k))
    # 域文件 + 泵站 + 塘
    RG.REGIONS.update(out)
    RG.CASES = CASES
    CASES.mkdir(parents=True, exist_ok=True)
    for code, D in jobs:
        gpd.GeoDataFrame([{"id": code, "name": out[code]["title"], "area_km2": round(D.area / 1e6, 3), "source": f"{SRC_NAME} / {out[code]['feat']}"}],
                         geometry=[D], crs=32640).to_crs(4326).to_file(CASES / f"{code}_domain.geojson", driver="GeoJSON")
        print(f"== {code} {out[code]['title']}  {D.area / 1e6:.2f} km²  {out[code]['res']} m  → 服务器{out[code]['server']}")
        AR.write_ponds(code, D)
        RG.write_pumps(code, D)
    # 积水点：落在各域内的 ADM 热点；并进 hotspots_ad.json（原有区域的不动，重跑时先去掉这批的旧行）
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    hp = CASES / "hotspots_ad.json"
    cur = json.loads(hp.read_text(encoding="utf-8"))
    rows = [h for h in cur["hotspots"] if h["region"] not in out]
    n_hs = {}
    for code, D in jobs:
        sel = H[H.within(D)]
        for hid, r in sel.set_index("hotspot_id").iterrows():
            rows.append({"hotspot_id": hid, "region": code, "adm": out[code]["adm"], "catchment": out[code]["feat"], "area": r["area"], "location": r["location"],
                         "criticality": r["criticality"], "x": round(r.geometry.x, 2), "y": round(r.geometry.y, 2)})
        n_hs[code] = len(sel)
    regions = {k: v for k, v in cur["regions"].items() if k not in out}
    regions.update(out)
    hp.write_text(json.dumps({"crs": "EPSG:32640", "regions": regions, "hotspots": rows}, ensure_ascii=False, indent=0), encoding="utf-8")
    for code in out:
        out[code]["hs_n"] = n_hs[code]
    (CASES / "rest_regions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    plan = {str(s): [f"{c}_M61H1" for c, v in out.items() if v["server"] == s] for s in (1, 2, 3)}
    (CASES / "rest_plan.json").write_text(json.dumps({"tags": plan, "regions": {c: dict(dir=v["dir"], res=v["res"], title=v["title"], km2=v["km2"], hs=v["hs_n"]) for c, v in out.items()}},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    TAB = chr(9)
    rows = [TAB.join([f"{c}_M61H1", c, v["dir"], str(v["res"]), str(v["km2"]), str(v["hs_n"]), str(v["server"])]) for c, v in out.items()]
    (CASES / "rest_all.tsv").write_text(chr(10).join(rows) + chr(10), encoding="utf-8", newline=chr(10))
    print(f"\n{len(out)} 个任务；积水点共 {sum(n_hs.values())} 个（原表 ADM 积水点数合计 {sum(i['hs'] for i in items)}）")
    for s in (1, 2, 3):
        print(f"服务器{s}：{len(plan[str(s)])} 个 → {' '.join(plan[str(s)])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
