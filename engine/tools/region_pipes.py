"""交付根上的 pipes.geojson：这次推演进模型的全部管线（WGS84），页面「雨水管网」开关画它（点管线看属性）。
用法（已交付的旧结果补写）：python tools/region_pipes.py <network_v2.gpkg> <交付根> [<交付根> …]"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def write_pipes(root: Path, gpkg: Path) -> int:
    import geopandas as gpd
    g = gpd.read_file(gpkg, layer="pipes").to_crs(4326)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g[["UNITID", "PIPE_DIAME", "PIPE_LENGT", "PIPE_MATER", "STATUS", "ASSET_STAGE", "geometry"]].rename(
        columns={"UNITID": "编号", "PIPE_DIAME": "管径 mm", "PIPE_LENGT": "管长 m", "PIPE_MATER": "管材", "STATUS": "状态", "ASSET_STAGE": "设施阶段"})
    fc = json.loads(g.to_json(drop_id=True))
    (root / "pipes.geojson").write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    feats = fc["features"]
    return len(feats)


if __name__ == "__main__":
    gp = Path(sys.argv[1])
    for d in sys.argv[2:]:
        print(d, write_pipes(Path(d), gp), "条管线")
