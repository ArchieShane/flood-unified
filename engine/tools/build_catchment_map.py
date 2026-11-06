"""把《Catchment_Simulation_Plan_October》里的积水点与对应汇水区叠加到电子地图上，生成单文件 HTML。

积水点坐标取自客户 506 热点 KMZ；汇水区取自客户汇水区 KMZ（RBE1-OF1 / RD-A / RD 14 / Mu 小汇水区）
与客户水力模型的子汇水区并集（RB3 / Musaffah_RD09，来自 out/units_from_models）。
用法：python tools/build_catchment_map.py  →  out/hotspot_catchment_map.html（底图需联网）
"""
import json
import re
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, Polygon

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
KMZ = ROOT / "data" / "FW__Hot_Spots_506"
OUT = ROOT / "out"

# 表格行：主编号、同址/同汇水区编号、地点、片区、汇水区标注、面积、等级、完成日
ROWS = [
    dict(id="487", also=[], loc="Arabian Gulf St, Al Jusoor bridges towards Khalifa City", zone="Zayed", cat="RB3", area=38.1, crit="Very High", due="2026-10-12", geo="RB3"),
    dict(id="94", also=[], loc="Abu Dhabi-Al Ain Rd, service road before Mazyad Mall bridge", zone="Zayed", cat="RB3", area=38.1, crit="Very High", due="2026-10-12", geo="RB3"),
    dict(id="192", also=[], loc="Arabian Gulf St, opposite the Cricket Stadium", zone="Zayed", cat="RB3", area=38.1, crit="Very High", due="2026-10-12", geo="RB3"),
    dict(id="90", also=[], loc="Arabian Gulf St, exit to Al Ain Rd", zone="Zayed", cat="Musaffah_RD09", area=38.1, crit="Very High", due="2026-10-12", geo="RD09"),
    dict(id="86", also=["14"], loc="Industrial tunnel 1, inbound", zone="Mussafah", cat="Musaffah_00", area=1.5, crit="Very High (14: High)", due="2026-10-18", geo="MU86"),
    dict(id="87", also=["189"], loc="Industrial tunnel 2, inbound", zone="Mussafah", cat="Musaffah_00", area=1.5, crit="Very High", due="2026-10-18", geo="MU87"),
    dict(id="3", also=["85", "490", "7"], loc="Yas Bridges Complex, Sheikh Zayed Rd / E12 interchange", zone="Al Shahama", cat="RBE1-OF1 (catchment KMZ)", area=22, crit="Very High (all four)", due="2026-10-24", geo="RBE1-OF1"),
    dict(id="76", also=[], loc="King Abdullah bin Abdulaziz St, Emirates Palace gate", zone="Island", cat="RD-A", area=3.2, crit="Very High", due="2026-10-28", geo="RD-A"),
    dict(id="75", also=["377", "379", "380"], loc="Al Khalidiya Corniche, opposite Spinneys", zone="Island", cat="RD-A", area=3.2, crit="High", due="2026-10-28", geo="RD-A"),
    dict(id="49", also=[], loc="Bani Yas, Al Khuwaira St", zone="Al Wathbah", cat="RD 14", area=15.4, crit="Very High", due="2026-10-31", geo="RD14"),
]


def placemarks(kmz):
    t = zipfile.ZipFile(kmz).read("doc.kml").decode("utf8")
    for m in re.finditer(r"<Placemark.*?</Placemark>", t, re.S):
        p = m.group(0)
        n = (re.search(r"<name>(.*?)</name>", p, re.S) or [0, ""])[1].strip()
        yield n, p


def ring(p):
    c = re.search(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", p, re.S).group(1)
    return Polygon([tuple(map(float, x.split(",")[:2])) for x in c.split()])


def kmz_polygons(fname):
    """返回 [(名称, 多边形, 图层路径, FID)]；图层取 KMZ 里最内层文件夹名（QGIS 里显示为“文件名 — 图层名”）。"""
    import xml.etree.ElementTree as ET
    ns = "{http://www.opengis.net/kml/2.2}"
    txt = zipfile.ZipFile(KMZ / fname).read("doc.kml").decode("utf8")
    root = ET.fromstring(re.sub(r"\sxsi:\w+=\"[^\"]*\"", "", txt).encode("utf8"))  # 有的 KMZ 里 xsi 前缀未声明
    out = []

    def walk(el, path):
        for ch in el:
            tag = ch.tag.replace(ns, "")
            if tag in ("Folder", "Document"):
                nm = ch.find(ns + "name")
                walk(ch, path + ([nm.text.strip()] if nm is not None and tag == "Folder" else []))
            elif tag == "Placemark" and ch.find(".//" + ns + "Polygon") is not None:
                nm = (ch.findtext(ns + "name") or "").strip()
                raw = re.sub(r"(</?)\w+:", r"\1", ET.tostring(ch, encoding="unicode"))
                fid = re.search(r">FID</[^>]+>\s*<[^>]+>\s*(\d+)", raw)
                if not fid:
                    fid = re.search(r"FID\D{0,60}?(\d+)", re.sub(r"<[^>]+>", " ", raw))
                out.append((nm, ring(raw), path[-1] if path else "", fid.group(1) if fid else None))

    walk(root, [])
    return out


def main():
    pts = {}
    for n, p in placemarks(KMZ / "506 Hot Spots AUH.kmz"):
        c = re.search(r"<Point>.*?<coordinates>(.*?)</coordinates>", p, re.S)
        if c:
            pts[n] = Point(*map(float, c.group(1).split(",")[:2]))

    geo, origin = {}, {}
    for f, name, key in [("Shahamah_Zayed catchments.kmz", "RBE1-OF1", "RBE1-OF1"),
                         ("island catchment.kmz", "RD-A", "RD-A"),
                         ("Al Wathba catchments.kmz", "RD 14", "RD14")]:
        n_, g_, lay_, fid_ = next(t for t in kmz_polygons(f) if t[0] == name)
        geo[key] = g_
        origin[key] = dict(file=f"data/FW__Hot_Spots_506/{f}", layer=lay_, feature=f"{name}" + (f"（FID {fid_}）" if fid_ else ""))
    mu = kmz_polygons("Mu catchments.kmz")
    for key, hid in [("MU86", "86"), ("MU87", "87"), ("MU14", "14"), ("MU189", "189")]:
        _, geo[key], lay_, fid_ = next(t for t in mu if t[1].contains(pts[hid]))
        origin[key] = dict(file="data/FW__Hot_Spots_506/Mu catchments.kmz", layer=lay_, feature=f"FID {fid_}（无名称，按积水点 {hid} 坐标落点选出）")
    units = gpd.read_file(OUT / "units_from_models" / "model_units.geojson").set_index("unit_id")
    geo["RB3"] = units.loc["MODEL:RB3"].geometry
    geo["RD09"] = units.loc["MODEL:Musaffah_RD09"].geometry
    origin["RB3"] = dict(file="data/OneDrive_1_9-26-2026/RB3/CA.shp", layer="CA（子汇水区图层）",
                         feature="全部子汇水区合并（out/units_from_models/model_units.geojson 中的 MODEL:RB3）")
    origin["RD09"] = dict(file="data/OneDrive_1_9-26-2026/Musaffah_RD09/Catchments.shp", layer="Catchments（子汇水区图层）",
                          feature="全部子汇水区合并（out/units_from_models/model_units.geojson 中的 MODEL:Musaffah_RD09）")
    domain = gpd.read_file(OUT / "c1_domain.geojson").geometry.iloc[0]

    def feat(g, props):
        g = g.simplify(0.00005, preserve_topology=True)
        return {"type": "Feature", "properties": props, "geometry": g.__geo_interface__}

    label = {"RB3": "RB3 模型汇水区", "RD09": "Musaffah_RD09 模型汇水区", "MU86": "Mu 小汇水区(86)", "MU87": "Mu 小汇水区(87)",
             "MU14": "Mu 小汇水区(14)", "MU189": "Mu 小汇水区(189)", "RBE1-OF1": "RBE1-OF1", "RD-A": "RD-A", "RD14": "RD 14"}
    polys = []
    for k, g in geo.items():
        polys.append(feat(g, {"key": k, "name": label[k], **origin[k],
                              "area_km2": None if k in ("RB3", "RD09") else None}))
    for f in polys:  # 面积（粗略等距换算，仅供核对）
        g = geo[f["properties"]["key"]]
        lat = g.centroid.y
        import math
        f["properties"]["area_km2"] = round(g.area * 111.32 * math.cos(math.radians(lat)) * 110.57, 2)

    points = []
    for r in ROWS:
        for hid in [r["id"]] + r["also"]:
            if hid not in pts:
                continue
            gk = {"86": "MU86", "14": "MU14", "87": "MU87", "189": "MU189"}.get(hid, r["geo"])
            points.append({"type": "Feature", "geometry": pts[hid].__geo_interface__,
                           "properties": {"id": hid, "main": hid == r["id"], "row": r["id"], "geo": gk, "in_poly": bool(geo[gk].contains(pts[hid])),
                                          "loc": r["loc"], "zone": r["zone"], "cat": r["cat"], "area": r["area"], "crit": r["crit"], "due": r["due"]}})
    data = {"polys": {"type": "FeatureCollection", "features": polys},
            "points": {"type": "FeatureCollection", "features": points},
            "domain": feat(domain, {"name": "C1 计算域 38.1 km²"})}
    html = (Path(__file__).with_name("catchment_map.template.html")).read_text(encoding="utf8")
    out = OUT / "hotspot_catchment_map.html"
    out.write_text(html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False)), encoding="utf8")
    print(out, len(points), "points", len(polys), "polygons")


if __name__ == "__main__":
    main()
