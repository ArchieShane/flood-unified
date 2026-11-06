"""地形：客户提供的 5 m DTM（AUH_DTM_5m_Z40.TIF，EPSG:32640，全酋，2026-03，ADNOC 来源）。

三件事：
1. 顶点高程：按瓦片分组读，25 GB 整图不进内存；缓存 out/state/vertex_dtm.parquet。
2. 补台账：台账 level = DTM 海拔 + [model].datum_offset_m（2026-09-19 抽 6,000 个进水口实测：中位 99.8 / 100.2 m，82% 在 ±0.5 m 内）。
   井盖标高缺失或 ≤50 的进水口/雨水井、没挂资产的进水口顶点 → 用 DTM 补；台账值与 DTM 差超过容差的只标记不改（桥面、隧道上方台账更可信）。
   管底标高无效的管段 → 地面坡度 ≥ 默认坡度时用地面坡度，否则仍用默认（管道随地面走，平地按设计最小坡）。
3. 热点填洼：以热点 seed_radius_m 内最低格为种子，优先淹没法（priority-flood）求「灌到溢出」的水位、体积、面积、最大深度；
   nodata（海、酋长国界外）视为可自由出流；洼地碰到分析窗边就加倍窗口重算，到上限仍碰边则标「下限」。
rasterio 只在函数内导入，没装 DTM 的机器照常跑其余功能。"""
from __future__ import annotations
import argparse
import hashlib
import heapq
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "state"

NOTE = {  # 填洼结果说明 (zh, en)
    "": ("", ""),
    "flat": ("无明显洼地（<5 cm）", "no distinct depression (<5 cm)"),
    "truncated": ("洼地延伸出 {w} m 分析窗，体积是下限", "depression extends beyond the {w} m window; volume is a lower bound"),
    "coastal": ("紧邻海/水体，水可自流入海", "adjacent to sea or open water; drains freely"),
    "nodata": ("DTM 无覆盖", "outside DTM coverage"),
    "regional": ("大范围洼地（>{a} ha），体积是区域性的，不是这一处的积水", "large basin (>{a} ha); volume is regional, not this spot's ponding"),
}
DEFAULTS = {"cover_tol_m": 2.0, "window_m": 300.0, "seed_radius_m": 30.0, "max_window_m": 1200.0, "coastal_z_m": 0.5, "regional_area_ha": 25.0}


def params(cfg: dict) -> dict:
    p = dict(DEFAULTS); p.update({k: v for k, v in cfg.get("terrain", {}).items() if k in DEFAULTS}); return p


def available(cfg: dict) -> bool:
    t = cfg.get("terrain")
    return bool(t and t.get("dtm_path") and Path(t["dtm_path"]).exists())


def open_dtm(cfg: dict):
    import rasterio
    return rasterio.open(cfg["terrain"]["dtm_path"])


# ---------- 采样 ----------

def _rowcol(ds, xs, ys):
    inv = ~ds.transform
    xs = np.asarray(xs, dtype=float); ys = np.asarray(ys, dtype=float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    cols = np.full(len(xs), -1, dtype="int64"); rows = np.full(len(xs), -1, dtype="int64")
    cols[ok] = np.floor(inv.a * xs[ok] + inv.b * ys[ok] + inv.c).astype("int64")
    rows[ok] = np.floor(inv.d * xs[ok] + inv.e * ys[ok] + inv.f).astype("int64")
    return rows, cols


def _clean(a: np.ndarray, nodata) -> np.ndarray:
    a = a.astype(float)
    if nodata is not None:
        a[a == nodata] = np.nan
    a[(a < -50) | (a > 3000)] = np.nan
    return a


def sample_points(ds, xs, ys) -> np.ndarray:
    """按格点采样；越界 / nodata → nan。按瓦片分组，一块只读一次。"""
    from rasterio.windows import Window
    rows, cols = _rowcol(ds, xs, ys)
    out = np.full(len(rows), np.nan)
    inb = (rows >= 0) & (cols >= 0) & (rows < ds.height) & (cols < ds.width)
    if not inb.any():
        return out
    bh, bw = ds.block_shapes[0]
    df = pd.DataFrame({"i": np.nonzero(inb)[0], "r": rows[inb], "c": cols[inb]})
    df["br"] = df["r"] // bh; df["bc"] = df["c"] // bw
    for (br, bc), g in df.groupby(["br", "bc"]):
        r0, c0 = int(br * bh), int(bc * bw)
        h = min(bh, ds.height - r0); w = min(bw, ds.width - c0)
        a = _clean(ds.read(1, window=Window(c0, r0, w, h)), ds.nodata)
        out[g["i"].values] = a[g["r"].values - r0, g["c"].values - c0]
    return out


def vertex_elevation(vertices: pd.DataFrame, cfg: dict) -> pd.Series:
    """管网顶点的 DTM 海拔（米），按 id 索引。缓存到 out/state/vertex_dtm.parquet，顶点集合变了才重采。"""
    cache = STATE / "vertex_dtm.parquet"
    ids = vertices["id"].astype("int64").values
    if cache.exists():
        c = pd.read_parquet(cache)
        if len(c) == len(ids) and np.array_equal(np.sort(c["id"].values), np.sort(ids)):
            return c.set_index("id")["z"]
    with open_dtm(cfg) as ds:
        z = sample_points(ds, vertices["x"].values, vertices["y"].values)
    s = pd.Series(z, index=pd.Index(ids, name="id"), name="z")
    STATE.mkdir(parents=True, exist_ok=True)
    s.reset_index().to_parquet(cache, index=False)
    return s


# ---------- 补台账 ----------

def fill_cover_levels(inlet_levels: pd.DataFrame, vertices: pd.DataFrame, vz: pd.Series, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """井盖标高缺失或 ≤50 的用 DTM + 基准补（cover_source=DTM）；没挂资产的进水口/雨水井顶点整行补进来（DTM顶点）；
    台账值与 DTM 差超过 cover_tol_m 的标 cover_suspect，不改。返回 (补后的表, 统计)。"""
    off = float(cfg["model"]["datum_offset_m"]); tol = float(params(cfg)["cover_tol_m"])
    il = inlet_levels.copy()
    il["cover_level"] = pd.to_numeric(il["cover_level"], errors="coerce")
    vid = pd.to_numeric(il["vertex_id"], errors="coerce").fillna(-1).astype("int64").values
    dtm_level = pd.Series(vz.reindex(vid).values + off, index=il.index)
    bad = il["cover_level"].isna() | (il["cover_level"] <= 50)
    fill = bad & dtm_level.notna()
    il["cover_source"] = "台账"
    il.loc[fill, "cover_level"] = dtm_level[fill]; il.loc[fill, "cover_source"] = "DTM"
    il["cover_suspect"] = (~bad) & dtm_level.notna() & ((il["cover_level"] - dtm_level).abs() > tol)
    have = set(int(v) for v in vid if v >= 0)
    vv = vertices[vertices["node_table"].isin(["inlet", "catchbasin"]) & ~vertices["id"].astype("int64").isin(have)]
    zz = vz.reindex(vv["id"].astype("int64").values).values + off
    add = pd.DataFrame({"vertex_id": vv["id"].astype("int64").values, "unitid": vv["unitid"].values, "node_table": vv["node_table"].values,
                        "cover_level": zz, "invert_level": np.nan, "unittype": None, "cover_source": "DTM顶点", "cover_suspect": False})
    add = add[add["cover_level"].notna()]
    stats = {"台账": int((il["cover_source"] == "台账").sum()), "DTM补缺": int(fill.sum()), "DTM补顶点": int(len(add)),
             "可疑": int(il["cover_suspect"].sum()), "仍缺": int((bad & ~fill).sum())}
    return pd.concat([il, add], ignore_index=True), stats


def ground_slope(e: pd.DataFrame, vz: pd.Series) -> pd.Series:
    """u→v 的地面坡度（正 = 顺坡）。给 hydraulics.edge_slope 兜「无标高」的管段。"""
    zu = vz.reindex(e["u"].astype("int64").values).values
    zv = vz.reindex(e["v"].astype("int64").values).values
    ln = pd.to_numeric(e["len_m"], errors="coerce").where(lambda s: s > 0.5).values
    return pd.Series((zu - zv) / ln, index=e.index)


# ---------- 填洼 ----------

def priority_flood(dem: np.ndarray, eps: float = 0.0) -> np.ndarray:
    """优先淹没法：每格「能排到窗边界或 nodata 所需的最低水位」= 填平后的地面。nan 视为出口（海、界外）。
    eps > 0 时填平面带微小坡度（Barnes 2014 的 ε 变体），每格都有严格更低的邻格，供 D8 流向用。"""
    H, W = dem.shape
    nd = ~np.isfinite(dem)
    d = np.where(nd, -1e9, dem)
    filled = d.copy()
    seen = np.zeros((H, W), dtype=bool)
    start = nd.copy(); start[0, :] = start[-1, :] = start[:, 0] = start[:, -1] = True
    rs, cs = np.nonzero(start)
    seen[rs, cs] = True
    heap = list(zip(d[rs, cs].tolist(), rs.tolist(), cs.tolist()))
    heapq.heapify(heap)
    push, pop = heapq.heappush, heapq.heappop
    while heap:
        z, r, c = pop(heap)
        for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < H and 0 <= cc < W and not seen[rr, cc]:
                seen[rr, cc] = True
                f = d[rr, cc] if d[rr, cc] > z + eps else z + eps
                filled[rr, cc] = f
                push(heap, (f, rr, cc))
    filled[nd] = np.nan
    return filled


def _flood(cond: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    """从种子出发在 cond 为真的格子上做 4 邻域连通。"""
    H, W = cond.shape
    mask = np.zeros((H, W), dtype=bool)
    r0, c0 = seed
    if not cond[r0, c0]:
        return mask
    mask[r0, c0] = True; stack = [(r0, c0)]
    while stack:
        r, c = stack.pop()
        for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < H and 0 <= cc < W and cond[rr, cc] and not mask[rr, cc]:
                mask[rr, cc] = True; stack.append((rr, cc))
    return mask


def pond_from_seed(dem: np.ndarray, filled: np.ndarray, seed: tuple[int, int]) -> tuple[float, np.ndarray, bool]:
    """种子所在洼地：溢出水位 = filled[seed]；积水格 = 与种子连通且地面低于水位的格。
    返回 (水位, 积水掩膜, 是否碰到窗边)。碰边 = 有积水且「水面」（filled 等于水位的连通格：积水格 + 同高的鞍部/平台）碰到窗边，
    说明溢出点可能在窗外，体积是下限；下游更低的地面不算水面。"""
    r0, c0 = seed
    level = float(filled[r0, c0])
    fin = np.isfinite(dem)
    mask = _flood(fin & (dem < level - 1e-6), seed)
    reach = surface_reach(filled, level, seed)
    touches = bool(mask.any() and (reach[0, :].any() or reach[-1, :].any() or reach[:, 0].any() or reach[:, -1].any()))
    return level, mask, touches


def surface_reach(filled: np.ndarray, level: float, seed: tuple[int, int], eps: float = 1e-3) -> np.ndarray:
    """与种子连通、填平面等于水位的格：积水面 + 同高鞍部/平台。"""
    return _flood(np.isfinite(filled) & (np.abs(filled - level) <= eps), seed)


def _adjacent_to_nodata(mask: np.ndarray, dem: np.ndarray) -> bool:
    nd = ~np.isfinite(dem)
    if not mask.any() or not nd.any():
        return False
    grow = mask.copy()
    grow[1:, :] |= mask[:-1, :]; grow[:-1, :] |= mask[1:, :]; grow[:, 1:] |= mask[:, :-1]; grow[:, :-1] |= mask[:, 1:]
    return bool((grow & nd).any())


def depression_at(ds, x: float, y: float, cfg: dict, window_m: float | None = None, with_mask: bool = False) -> dict:
    """一个点的填洼分析。返回 dtm_z / low_z / spill_z / depth_max_m / pond_volume_m3 / pond_area_m2 / seed_offset_m / window_m / terrain_note。"""
    from rasterio.windows import Window
    P = params(cfg); cell = abs(float(ds.transform.a))
    W = float(window_m or P["window_m"])
    half = max(2, int(round(W / cell))); seed_r = max(1, int(round(P["seed_radius_m"] / cell)))
    empty = {"dtm_z": np.nan, "low_z": np.nan, "spill_z": np.nan, "depth_max_m": np.nan, "pond_volume_m3": np.nan, "pond_area_m2": np.nan,
             "seed_offset_m": np.nan, "window_m": W, "terrain_note": "nodata"}
    rows, cols = _rowcol(ds, [x], [y]); r, c = int(rows[0]), int(cols[0])
    if not (0 <= r < ds.height and 0 <= c < ds.width):
        return empty
    r0, c0 = max(r - half, 0), max(c - half, 0); r1, c1 = min(r + half + 1, ds.height), min(c + half + 1, ds.width)
    a = _clean(ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0)), ds.nodata)
    lr, lc = r - r0, c - c0
    z0 = float(a[lr, lc]) if np.isfinite(a[lr, lc]) else np.nan
    rr, cc = np.ogrid[:a.shape[0], :a.shape[1]]
    within = ((rr - lr) ** 2 + (cc - lc) ** 2) <= seed_r ** 2
    cand = np.where(within & np.isfinite(a), a, np.inf)
    if not np.isfinite(cand).any():
        return empty
    sr, sc = np.unravel_index(int(np.argmin(cand)), a.shape)
    filled = priority_flood(a)
    level, mask, touches = pond_from_seed(a, filled, (int(sr), int(sc)))
    if touches and W < P["max_window_m"]:
        return depression_at(ds, x, y, cfg, window_m=min(W * 2, P["max_window_m"]), with_mask=with_mask)
    n = int(mask.sum())
    low = float(a[mask].min()) if n else float(a[sr, sc])   # 洼地真正的最低点可能在种子半径之外
    vol = float(((level - a)[mask]).sum() * cell * cell) if n else 0.0
    depth = float(level - low) if n else 0.0
    area = n * cell * cell
    coastal = _adjacent_to_nodata(mask | surface_reach(filled, level, (int(sr), int(sc))), a) and low < P["coastal_z_m"]
    note = "flat" if depth < 0.05 else ("coastal" if coastal else ("truncated" if touches else ("regional" if area > P["regional_area_ha"] * 1e4 else "")))
    out = {"dtm_z": round(z0, 2) if np.isfinite(z0) else np.nan, "low_z": round(low, 2), "spill_z": round(level if n else low, 2),
           "depth_max_m": round(depth, 2), "pond_volume_m3": round(vol, 0), "pond_area_m2": round(area, 0),
           "seed_offset_m": round(float(np.hypot(sr - lr, sc - lc) * cell), 1), "window_m": W, "terrain_note": note}
    if with_mask:
        out["_mask"] = mask; out["_r0c0"] = (r0, c0); out["_level"] = level
    return out


def depression_polygon(cfg: dict, lon: float, lat: float, simplify_m: float = 2.5) -> dict:
    """一个点的洼地面：灌到溢出的积水格 → GeoJSON（EPSG:4326），带 depression_at 的数字。单热点详情用。"""
    from pyproj import Transformer
    from rasterio import features
    from rasterio.windows import Window
    import shapely.geometry as sg
    from shapely.ops import unary_union, transform as sh_transform
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True); x, y = tr.transform(float(lon), float(lat))
    with open_dtm(cfg) as ds:
        d = depression_at(ds, x, y, cfg, with_mask=True)
        mask = d.pop("_mask", None); r0c0 = d.pop("_r0c0", None); d.pop("_level", None)
        d = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in d.items()}
        d["terrain_note_zh"], d["terrain_note_en"] = note_text(d.get("terrain_note") or "", float(d.get("window_m") or params(cfg)["window_m"]), params(cfg)["regional_area_ha"])
        if mask is None or not mask.any():
            return {**d, "geometry": None}
        r0, c0 = r0c0
        win_tr = ds.window_transform(Window(c0, r0, mask.shape[1], mask.shape[0]))
        polys = [sg.shape(g) for g, v in features.shapes(mask.astype("uint8"), mask=mask, transform=win_tr) if v == 1]
    geom = unary_union(polys).simplify(simplify_m)
    back = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    geom_ll = sh_transform(lambda xx, yy, zz=None: back.transform(xx, yy), geom)
    return {**d, "geometry": sg.mapping(geom_ll), "n_cells": int(mask.sum())}


def note_text(code: str, window_m: float, regional_area_ha: float = DEFAULTS["regional_area_ha"]) -> tuple[str, str]:
    zh, en = NOTE.get(code or "", ("", ""))
    return zh.format(w=int(window_m), a=int(regional_area_ha)), en.format(w=int(window_m), a=int(regional_area_ha))


def terrain_risk_low(depth_m) -> pd.Series:
    """工作簿「低点/高程风险 0–3」：洼深 ≥0.5 → 3，≥0.3 → 2，≥0.15 → 1，其余 0。"""
    d = pd.to_numeric(pd.Series(depth_m), errors="coerce")
    return pd.Series(np.select([d >= 0.5, d >= 0.3, d >= 0.15], [3, 2, 1], default=0), index=d.index).where(d.notna(), other=pd.NA)


def _key(ids, cfg, coords=None) -> str:
    """热点集合 + 参数 +（有则）坐标（取 5 位小数）——热点坐标改了也要重算。"""
    c = "" if coords is None else json.dumps([[round(float(x), 5), round(float(y), 5)] for x, y in coords])
    return hashlib.sha1((json.dumps(sorted(map(str, ids))) + c + json.dumps(params(cfg), sort_keys=True)).encode()).hexdigest()


def hotspot_terrain(hs: pd.DataFrame, cfg: dict, force: bool = False, log=None) -> pd.DataFrame:
    """每个客户热点的填洼分析。缓存 out/state/hotspot_terrain.parquet（热点集合或参数变了才重算）。"""
    cache = STATE / "hotspot_terrain.parquet"; keyf = STATE / "hotspot_terrain.key"
    ok = hs["lon"].notna() & hs["lat"].notna()
    ids = hs.loc[ok, "hotspot_id"].astype(str).values
    key = _key(ids, cfg, coords=zip(hs.loc[ok, "lon"].values.astype(float), hs.loc[ok, "lat"].values.astype(float)))
    if cache.exists() and keyf.exists() and keyf.read_text().strip() == key and not force:
        return pd.read_parquet(cache)
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    X, Y = tr.transform(hs.loc[ok, "lon"].values.astype(float), hs.loc[ok, "lat"].values.astype(float))
    rows = []; t0 = time.time()
    with open_dtm(cfg) as ds:
        for i, (hid, x, y) in enumerate(zip(ids, X, Y)):
            d = depression_at(ds, float(x), float(y), cfg); d["hotspot_id"] = hid; rows.append(d)
            if log and (i + 1) % 200 == 0:
                log(f"  填洼 {i + 1}/{len(ids)}（{time.time() - t0:.0f} s）")
    out = pd.DataFrame(rows)
    zh_en = [note_text(k, w, params(cfg)["regional_area_ha"]) for k, w in zip(out["terrain_note"], out["window_m"])]
    out["terrain_note_zh"] = [z for z, _ in zh_en]; out["terrain_note_en"] = [e for _, e in zh_en]
    out["risk_low"] = terrain_risk_low(out["depth_max_m"]).astype("Int64")
    STATE.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache, index=False); keyf.write_text(key)
    return out


def summary(ht: pd.DataFrame) -> dict:
    d = pd.to_numeric(ht["depth_max_m"], errors="coerce")
    return {"n": int(len(ht)), "n_dtm": int(d.notna().sum()), "n_depth_015": int((d >= 0.15).sum()), "n_depth_030": int((d >= 0.3).sum()), "n_depth_050": int((d >= 0.5).sum()),
            "depth_median_m": round(float(d.median()), 2) if d.notna().any() else None,
            "pond_volume_total_m3": round(float(pd.to_numeric(ht["pond_volume_m3"], errors="coerce").sum()), 0),
            "n_truncated": int((ht["terrain_note"] == "truncated").sum()), "n_coastal": int((ht["terrain_note"] == "coastal").sum()), "n_flat": int((ht["terrain_note"] == "flat").sum()),
            "n_regional": int((ht["terrain_note"] == "regional").sum())}


# ---------- CLI ----------

def _datum_check(cfg: dict, n: int) -> dict:
    """抽 n 个进水口，井盖标高 − DTM 的分布，验证 datum_offset_m。"""
    from . import db
    with db.connect_source(cfg, "pipe") as pc:
        a = cfg["source"]["pipe"]["schema_assets"]
        df = db.df(pc, f"""select cover_level::float cover, st_x(st_centroid(geom)) x, st_y(st_centroid(geom)) y
                            from {a}.inlet where cover_level is not null and cover_level::float > 50 order by random() limit {int(n)}""")
    with open_dtm(cfg) as ds:
        z = sample_points(ds, df["x"].values, df["y"].values)
    diff = pd.Series(df["cover"].values - z).dropna(); off = float(cfg["model"]["datum_offset_m"])
    return {"n": int(len(diff)), "median": round(float(diff.median()), 3), "p10": round(float(diff.quantile(0.1)), 3), "p90": round(float(diff.quantile(0.9)), 3),
            "within_0.5m_of_offset": round(float(((diff - off).abs() < 0.5).mean()), 3), "datum_offset_m": off}


def main(argv=None):
    import tomllib
    ap = argparse.ArgumentParser(description="5 m DTM：基准校核 / 顶点采样 / 热点填洼")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--check", type=int, metavar="N", help="抽 N 个进水口做基准校核（需连 pipe 库）")
    ap.add_argument("--hotspots", action="store_true", help="算客户热点填洼并打印统计（写 out/state/hotspot_terrain.parquet）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--point", nargs=2, type=float, metavar=("LON", "LAT"))
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    if not available(cfg):
        raise SystemExit("未配置 [terrain].dtm_path 或文件不存在")
    if a.check:
        print(json.dumps(_datum_check(cfg, a.check), ensure_ascii=False, indent=2))
    if a.point:
        from pyproj import Transformer
        x, y = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform(a.point[0], a.point[1])
        with open_dtm(cfg) as ds:
            print(json.dumps(depression_at(ds, x, y, cfg), ensure_ascii=False, indent=2, default=float))
    if a.hotspots:
        from . import hotspots as hsm
        hs = hsm.load(ROOT / cfg["hotspots"]["file"])
        ht = hotspot_terrain(hs, cfg, force=a.force, log=print)
        print(json.dumps(summary(ht), ensure_ascii=False, indent=2))
        m = hs[["hotspot_id", "region", "criticality"]].merge(ht, on="hotspot_id")
        print(m.groupby("region")["depth_max_m"].describe()[["count", "50%", "75%", "max"]].round(2).to_string())
        print(m.groupby("criticality")["depth_max_m"].agg(n="size", ge03=lambda s: int((s >= 0.3).sum()), median="median").round(2).to_string())


if __name__ == "__main__":
    main()
