# -*- coding: utf-8 -*-
"""导入客户邮件附件（FW: Hot Spots 506）里的 4 个汇水区 KMZ：Al Wathba / Mu / Shahamah_Zayed / island。

为什么（2026-09-28）：这 4 个文件一直没进系统；客户说的某些汇水区（如 C1）可能在里面，用户要在地图上直接看到名称注记。
名称的来历写进每个面（label_src）：
  name   KMZ 里面自己的 <name>
  point  面自己没名字，用落在面里的点的名字（Mu 的 86 个面都没名字；点多是泵站 / 出水口编号）——**推断**
  attr   再没有，用规划属性里的分区编号 SECNUMTPD（如 M35）；没有就用用途 COMMNAME，再没有用「分区名 SECTPINTID」
点（泵站、出水口、塘等）另存一层。

用法：python tools/import_client_kmz.py [KMZ 目录]
产物：console/data/catchments_kmz.geojson（面，带 label 与注记点 label_ll）、console/data/catchments_kmz_pts.geojson（点）
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "console" / "data"
SRC = pathlib.Path(r"D:\世界模型\FW__Hot_Spots_506")
FILES = {"Al Wathba catchments.kmz": "Al Wathba", "Mu catchments.kmz": "Mussafah (Mu)",
         "Shahamah_Zayed catchments.kmz": "Shahamah / Zayed", "island catchment.kmz": "Island"}


def attrs(pm):
    d = re.search(r"<description>(.*?)</description>", pm, re.S)
    if not d:
        return {}
    t = html.unescape(d.group(1))
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):          # 逐行「字段名 — 值」；第一行是空表头，不能按 td 两两配对
        tds = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) == 2 and tds[0] and tds[1] and not re.fullmatch(r"30-Dec-99|0|[?\s]+", tds[1]):   # 纯问号：客户导出时阿拉伯文丢了
            out[tds[0]] = tds[1]
    return out


def coords(s):
    return [tuple(map(float, c.split(",")[:2])) for c in s.split()]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from pyproj import Transformer
    from shapely.geometry import Point, Polygon, mapping
    from shapely.ops import transform
    from shapely.validation import make_valid
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    utm = Transformer.from_crs(4326, 32640, always_xy=True).transform
    polys, pts = [], []
    for fn, grp in FILES.items():
        z = zipfile.ZipFile(src / fn)
        s = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".kml"))
        for pm in re.findall(r"<Placemark[^>]*>(.*?)</Placemark>", s, re.S):
            m = re.search(r"<name>(.*?)</name>", pm, re.S); nm = html.unescape(m.group(1)).strip() if m else ""
            a = attrs(pm)
            if "<Polygon" in pm:
                outer = re.search(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", pm, re.S) or re.search(r"<coordinates>(.*?)</coordinates>", pm, re.S)
                inner = [coords(c) for c in re.findall(r"<innerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", pm, re.S)]
                g = make_valid(Polygon(coords(outer.group(1)), inner)).buffer(0)
                if g.is_empty:
                    continue
                polys.append({"g": g, "name": nm, "group": grp, "file": fn, "attrs": a})
            elif "<Point" in pm:
                c = re.search(r"<coordinates>(.*?)</coordinates>", pm, re.S)
                pts.append({"g": Point(coords(c.group(1))[0]), "name": nm, "group": grp, "file": fn, "attrs": a})
    feats = []
    for p in polys:
        inside = sorted({q["name"] for q in pts if q["group"] == p["group"] and q["name"] and p["g"].contains(q["g"])})
        a = p["attrs"]
        if p["name"]:
            lab, how = p["name"], "name"
        elif inside:
            lab, how = " / ".join(inside), "point"
        else:
            # 分区编号（M35、MW5 …）最短也最好认；分区名（Musaffah / Abu Dhabi Industrial City）放在属性里
            lab = a.get("SECNUMTPD") or a.get("COMMNAME") or (f"{a.get('ZONETPSSNA', '')} {a.get('SECTPINTID', '')}".strip()) or "（无名）"
            how = "attr"
        gu = transform(utm, p["g"])
        rp = p["g"].representative_point()           # 注记放在面内（凹多边形的形心可能在面外）
        feats.append({"type": "Feature", "geometry": mapping(p["g"]),
                      "properties": {"label": lab, "label_src": how, "name": p["name"], "group": p["group"], "file": p["file"],
                                     "points_inside": inside, "area_ha": round(gu.area / 1e4, 1),
                                     "label_ll": [round(rp.y, 6), round(rp.x, 6)], **{f"a_{k}": v for k, v in a.items()}}})
    gj = {"type": "FeatureCollection", "features": feats, "layer": "catchments_kmz",
          "source": "客户邮件附件 FW: Hot Spots 506 的 4 个汇水区 KMZ（Al Wathba / Mu / Shahamah_Zayed / island）",
          "note": "名称来历见 label_src：name = KMZ 自带；point = 用落在面里的点名（推断）；attr = 规划属性"}
    (DATA / "catchments_kmz.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    pf = [{"type": "Feature", "geometry": mapping(q["g"]), "properties": {"name": q["name"], "group": q["group"], "file": q["file"],
                                                                          **{f"a_{k}": v for k, v in q["attrs"].items()}}} for q in pts]
    (DATA / "catchments_kmz_pts.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pf, "layer": "catchments_kmz_pts",
                                                                   "source": gj["source"]}, ensure_ascii=False), encoding="utf-8")
    # 客户汇水区图层（catchments_sw）也补注记点：同名的多个部件只标最大的那块
    cs = json.loads((DATA / "catchments_sw.geojson").read_text(encoding="utf-8"))
    from shapely.geometry import shape
    best = {}
    for f in cs["features"]:
        g = make_valid(shape(f["geometry"])).buffer(0); lab = f["properties"].get("label")
        a_ = transform(utm, g).area
        if lab not in best or a_ > best[lab][0]:
            gg = max(g.geoms, key=lambda q: q.area) if hasattr(g, "geoms") else g
            rp = gg.representative_point(); best[lab] = (a_, [round(rp.y, 6), round(rp.x, 6)], id(f))
    for f in cs["features"]:
        b_ = best.get(f["properties"].get("label"))
        f["properties"]["label_ll"] = b_[1] if b_ and b_[2] == id(f) else None
    (DATA / "catchments_sw.geojson").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(f"面 {len(feats)}：", dict(Counter(f["properties"]["group"] for f in feats)), "名称来历", dict(Counter(f["properties"]["label_src"] for f in feats)))
    print(f"点 {len(pf)}：", dict(Counter(q["group"] for q in pts)))
    print("面积合计", round(sum(f["properties"]["area_ha"] for f in feats)), "ha")
    for f in feats[:8]:
        print("  ", f["properties"]["group"], "|", f["properties"]["label"], "|", f["properties"]["label_src"], "|", f["properties"]["area_ha"], "ha")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
