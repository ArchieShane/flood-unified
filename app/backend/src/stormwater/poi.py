"""地名 POI —— 给没有名字的溢水点起名：「{最近 POI}附近{距离}米的溢水点」。

来源是管网库 `public.poi_google`（Google Places 抓取，约 1.26 万条，EPSG:32640，列名 `geom`）。
API 运行期不连数据库，照「离线批算 → CSV → 在线计算」的老规矩：本模块 `--load-db` 把表导成
`data/processed/poi_google.csv`，在线只读 CSV、建一棵 KD 树查最近点。

口径：
  * 只取**最近的一个** POI，不按类型挑「地标」——距离是平面欧氏距离（UTM 40N，米），取整；
  * 距离不设上限，远了就照实写远（Al Dhafra 一带 POI 稀疏，几公里也照报），不回退成空名；
  * CSV 不在时 `nearest()` 返回 None、溢水点不带名字，页面照旧只显示序号。

名字是中文串，英文在 API 边界由 `i18n._PATTERNS` 的正则模板翻（POI 名本身是英文原名，不翻）。

用法：

    PYTHONPATH=src .venv/bin/python -m stormwater.poi --load-db
"""

from __future__ import annotations

import argparse
import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[2]
POI_CSV = BACKEND_DIR / "data" / "processed" / "poi_google.csv"

FIELDS = ("objectid", "name", "type", "region", "x", "y")

LOAD_SQL = """
select objectid, trim(f_name) as name, f_type as type, nameenglish as region,
       st_x(geom) as x, st_y(geom) as y
from public.poi_google
where geom is not null and nullif(trim(f_name), '') is not null
order by objectid
"""


_RTL = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]")


def label(name: str, distance_m: float) -> str:
    """溢水点名字。改了这个格式，`i18n._PATTERNS` 里对应的正则要一起改。

    阿拉伯文 POI 名套一对 Unicode 方向隔离符（FSI … PDI），否则与后面的中文 / 数字混排时
    浏览器会把「附近1523米」一起卷进从右往左的段落里，读出来次序颠倒。
    """
    if _RTL.search(name):
        name = f"\u2068{name}\u2069"
    return f"Overflow point {round(distance_m)} m from {name}"


@lru_cache(maxsize=1)
def _index() -> tuple[Any, list[dict[str, str]]] | None:
    path = POI_CSV
    if not path.is_file():
        return None
    from scipy.spatial import cKDTree

    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    xy = np.array([[float(r["x"]), float(r["y"])] for r in rows])
    return cKDTree(xy), rows


def nearest(x: float, y: float) -> dict[str, Any] | None:
    """(x, y) 为 EPSG:32640 米坐标，返回最近 POI 与距离；没有 POI 数据时返回 None。"""
    idx = _index()
    if idx is None:
        return None
    tree, rows = idx
    dist, i = tree.query([x, y])
    r = rows[int(i)]
    d = float(dist)
    return {
        "objectid": int(r["objectid"]),
        "name": r["name"],
        "type": r["type"],
        "distance_m": round(d),
        "label": label(r["name"], d),
    }


def load_db() -> int:
    from .db import query

    path = POI_CSV
    rows = query(LOAD_SQL)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "x": round(r["x"], 2), "y": round(r["y"], 2)})
    tmp.replace(path)
    _index.cache_clear()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--load-db", action="store_true", help="从 public.poi_google 重新导出 CSV")
    args = ap.parse_args()
    if args.load_db:
        n = load_db()
        print(f"已导出 {n} 条 POI → {POI_CSV}")
    idx = _index()
    print(f"POI 索引：{len(idx[1]) if idx else 0} 条（{POI_CSV}）")


if __name__ == "__main__":
    main()
