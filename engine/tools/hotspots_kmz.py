# -*- coding: utf-8 -*-
"""客户关注的积水点（506 Hot Spots AUH.kmz）→ engine/data/hotspots/adm506_hotspots.json（+ .geojson）。

输出格式同交付 common/hotspots.json（management 的 city_ponding.prepare 读）：
  hotspot_id = ADM-<序号 م>、area（英文片区名）、location（原文描述）、criticality（Very Important / Important）、lon / lat / x / y（EPSG:32640）。
用法：python tools/hotspots_kmz.py "<…/506 Hot Spots AUH.kmz>" [输出目录]
"""
import json, math, re, sys, zipfile
from pathlib import Path

AREA = {"مدينة زايد": "Madinat Zayed", "المدينة": "Al Madina", "الوثبة": "Al Wathba", "الشهامة": "Al Shahama", "مصفح": "Mussafah"}


def to_utm40(lon, lat):
    """WGS84 → EPSG:32640（Krüger 级数，同 management rda._lonlat_to_utm40）。"""
    a, f = 6378137.0, 1 / 298.257223563
    k0, lon0 = 0.9996, math.radians(57.0)
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    al = [n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16, 13 * n**2 / 48 - 3 * n**3 / 5, 61 * n**3 / 240]
    phi, dl = math.radians(lat), math.radians(lon) - lon0
    e = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(dl)), math.atanh(math.sin(dl) / math.sqrt(1 + t * t))
    x, y = eta, xi
    for j in (1, 2, 3):
        x += al[j - 1] * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        y += al[j - 1] * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
    return 500000.0 + k0 * A * x, k0 * A * y


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "data" / "hotspots"
    kml = zipfile.ZipFile(src).read("doc.kml").decode("utf-8")
    rows = []
    for p in re.findall(r"<Placemark>.*?</Placemark>", kml, flags=re.S):
        if "SchemaData" not in p:
            continue
        d = dict(re.findall(r'<SimpleData name="([^"]+)">([^<]*)', p))
        lon, lat = (float(v) for v in re.search(r"<coordinates>\s*([-\d.]+),([-\d.]+)", p).groups())
        x, y = to_utm40(lon, lat)
        c = d["المركز"].strip()
        rows.append({"hotspot_id": f"ADM-{int(d['م'])}", "area": AREA.get(c, c), "area_ar": c, "location": d["الوصف"].strip(),
                     "criticality": d["الاولوية"].strip(), "lon": lon, "lat": lat, "x": round(x, 2), "y": round(y, 2)})
    rows.sort(key=lambda r: int(r["hotspot_id"][4:]))
    assert len(rows) == 506 and len({r["hotspot_id"] for r in rows}) == 506, len(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "adm506_hotspots.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "adm506_hotspots.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: v for k, v in r.items() if k not in ("lon", "lat")}, "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]}} for r in rows]}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(rows)} 个热点 → {out}")


if __name__ == "__main__":
    main()
