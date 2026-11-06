# -*- coding: utf-8 -*-
"""261101 新区域的 makani.gpkg（下垫面：建筑 / 道路 / 停车 / 绿地 / 水面 / 地块）。只在 109 上跑（那里的 Makani 库本机免密：docker exec popstr-pg18-5446 psql），
不用密码、不写任何密码。推演时不读 Makani，只读打好的 makani.gpkg。

两步（scripts/g261101-makani.sh 把它们串起来）：
  python tools/g261101_makani.py plan  --work <目录> [--regions RDC,RDF,…]     每个区 × 每张表写一个 .sql（范围 = 汇水区外接框外扩 150 m，同 region_pack.py）
  （宿主机）docker exec -i popstr-pg18-5446 psql -U postgres -d makani_sync_full -At -F $'\\t' < x.sql > x.tsv
  python tools/g261101_makani.py build --work <目录> [--regions …]               读 .tsv → data/regions/<ID>/makani.gpkg，并把 makani.gpkg 的 sha256 补进 manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REGIONS = ["RDC", "RDF", "RB3", "RD09", "ADMLINE"]


def conf():
    c = tomllib.loads((ROOT / "config" / "data_sources.toml").read_text(encoding="utf-8"))["makani"]
    return c["tables"], c.get("extra_columns", {})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["plan", "build"])
    ap.add_argument("--work", required=True)
    ap.add_argument("--regions", default=",".join(REGIONS))
    a = ap.parse_args()
    import geopandas as gpd
    from pyproj import Transformer
    from shapely import wkb
    tables, extra = conf()
    work = Path(a.work)
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    for rid in [r for r in a.regions.split(",") if r]:
        rd = ROOT / "data" / "regions" / rid
        cat = gpd.read_file(rd / "catchments.gpkg")
        x0, y0, x1, y1 = cat.union_all().buffer(150).bounds
        lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)
        wd = work / rid
        if a.cmd == "plan":
            wd.mkdir(parents=True, exist_ok=True)
            for t, srid in tables.items():
                env = f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if srid == 4326 else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"
                cols = "".join(f", {c}" for c in extra.get(t, []))
                (wd / f"{t}.sql").write_text(f"select encode(st_asbinary(st_transform(st_makevalid(shape),32640)),'hex'){cols} from public.{t} where shape && {env} and not st_isempty(shape);\n", encoding="utf-8")
            print(f"{rid}: {len(tables)} 条 SQL → {wd}")
            continue
        gp = rd / "makani.gpkg"
        if gp.exists():
            gp.unlink()
        counts = {}
        for t in tables:
            tsv, err = wd / f"{t}.tsv", wd / f"{t}.err"
            if err.exists() and err.stat().st_size:
                counts[t] = f"读不到：{err.read_text(encoding='utf-8', errors='replace').splitlines()[0][:60]}"
                continue
            if not tsv.exists():
                counts[t] = "没有导出文件"
                continue
            ec = extra.get(t, [])
            geoms, data = [], {c: [] for c in ec}
            for line in tsv.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                try:
                    g = wkb.loads(bytes.fromhex(parts[0]))
                except Exception:  # noqa: BLE001
                    continue
                if g.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") and not g.is_empty:
                    geoms.append(g)
                    for i, c in enumerate(ec):
                        data[c].append(parts[1 + i] if len(parts) > 1 + i else None)
            counts[t] = len(geoms)
            if geoms:
                gpd.GeoDataFrame(data, geometry=geoms, crs=32640).to_file(gp, layer=t, driver="GPKG")
        h = hashlib.sha256(gp.read_bytes()).hexdigest() if gp.exists() else None
        mf = rd / "manifest.json"
        m = json.loads(mf.read_text(encoding="utf-8"))
        m["files"]["makani.gpkg"] = {"bytes": gp.stat().st_size if gp.exists() else None, "sha256": h, "source": "Makani 109:5446/makani_sync_full（只读，docker exec psql）", "counts": counts,
                                     "extent": [round(v, 1) for v in (x0, y0, x1, y1)]}
        mf.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        m2 = json.loads((rd / "manifest_v2.json").read_text(encoding="utf-8"))
        m2["makani_counts"] = counts
        (rd / "manifest_v2.json").write_text(json.dumps(m2, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"{rid}: makani.gpkg {gp.stat().st_size / 1e6 if gp.exists() else 0:.1f} MB  {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
