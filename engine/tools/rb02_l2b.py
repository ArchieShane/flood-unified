# -*- coding: utf-8 -*-
"""29.RB2 的 L2+B（我们的地下箱涵）：沿用 build_l2p 的严格选址，只在 29.RB2 范围内聚类。

前提：$WORK/RB2x_baseline_R61D2.inp（= rb02_model 的 L2）已经跑过一维、有 .rpt。
产物：$WORK/RB2x_L2B.inp、c2d/pilot_RB2x_L2B.json（从 L2 的抄，箱涵不做地表进水）、
      out/RB2x.L2B.R61D2.ponds.geojson
      放宽口径（--vacant，借用未盖楼的已分配地块）写 RB2x_L2B2.inp / pilot_RB2x_L2B2.json /
      RB2x.L2B.R61D2v.ponds.geojson
用法：python tools/rb02_l2b.py [座数] [聚类半径 m] [--vacant]
"""
import json, os, pathlib, shutil, sys
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("L2P_BASE_TAG", "R61D2")
VAC = "--vacant" in sys.argv
if VAC:
    sys.argv.remove("--vacant")
    os.environ["L2P_VACANT"] = "1"
os.environ.setdefault("L2P_OUT_TAG", "R61D2v" if VAC else "R61D2")
os.environ.setdefault("L2P_EMPTY_H", "3")      # 2 h 雨峰在第 1.1 h，排空起判 = ceil(1.1) + 2
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.validation import make_valid  # noqa: E402
import dig_pond3  # noqa: E402
import build_l2p as L  # noqa: E402
from dig_pond import read_sections, parse_coords  # noqa: E402
from workdir import OUT, WORK  # noqa: E402
from swbrain import db  # noqa: E402

NAME, REGION = "RB2x", "29.RB2"
C2D = WORK / "c2d"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    rad = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
    C0 = gpd.read_file(HERE.parent / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    reg = unary_union([make_valid(g).buffer(0) for g in C0[C0.label == REGION].geometry])
    lines, idx = read_sections(WORK / f"{NAME}_baseline_R61D2.inp")
    xy = parse_coords(lines, idx)
    orig = dig_pond3.flooding_nodes

    def only_region(rpt):                      # 选址只看 29.RB2 里面的溢流节点
        fl = orig(rpt)
        keep = {k: v for k, v in fl.items() if k in xy and reg.contains(Point(xy[k]))}
        print(f"  溢流节点 {len(fl):,} 个 → 29.RB2 内 {len(keep):,} 个，"
              f"{sum(keep.values()):,.0f} / {sum(fl.values()):,.0f} m³")
        return keep
    dig_pond3.flooding_nodes = only_region
    L.PLAN[NAME] = (n, rad)
    r = L.build(db.load_config(None), NAME, "box")
    src = WORK / f"{NAME}_L2box_{os.environ['L2P_OUT_TAG']}.inp"
    st = "L2B2" if VAC else "L2B"
    shutil.copy(src, WORK / f"{NAME}_{st}.inp")
    meta = json.loads((C2D / f"pilot_{NAME}_L2.json").read_text(encoding="utf-8"))
    meta["state"] = st
    meta.setdefault("inferred", []).append(
        f"L2+B 箱涵 {r['n']} 座是我们的方案（{'放宽：可借用未盖楼的已分配地块' if VAC else '严格用地选址'}），"
        "地下构筑物不做地表进水")
    (C2D / f"pilot_{NAME}_{st}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    json.dump({**r, "n_boxes": n, "radius_m": rad, "vacant": VAC},
              open(OUT / f"{NAME}_{st}_build.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ {NAME}_{st}.inp、pilot_{NAME}_{st}.json")


if __name__ == "__main__":
    main()
