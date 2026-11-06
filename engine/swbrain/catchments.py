"""DTM 子汇水区：每个进水口/雨水井顶点的地面汇水面积与径流系数。

替掉「每个进水口 600 m²、每米管长 20 m 路面」的估算。做法：
1. 按 4 km 瓦片（外扩 1 km 缓冲）从 5 m DTM 读成 10 m 网格；
2. 带 ε 的优先淹没法填洼，让每个格都有严格更低的邻格（Barnes 2014）；D8 最陡下降得到每格的下游格；nodata（海、界外）与窗边是出口；
3. 进水口所在格作为出水点做流域标记：每个格沿 D8 走到第一个进水口格，面积归它；走到出口的格是「没有管网接的地面」；
4. 径流系数：makani 的建筑（0.85）、路面（0.90）、停车场（0.78）、公园/绿地（0.15）多边形栅格化；不在这四类里的分「地块内」（udm_plot 内，0.55，假设）
   与「空地」（地块外的沙地/荒地，0.30，假设）；每个汇水区取面积加权均值。
5. 只计地面汇流路程 ≤ max_flow_len_m（默认 1,000 m，约 15 分钟以内能到）的格：平坦沙地上 D8 会把几公里外的地面都归到一个进水口，那部分水在设计历时内到不了。
6. plot_self_drain（默认开）：地块内（建筑与院落）按阿布扎比惯例自排（地块内渗井/自有系统），不计入公共雨水管网的汇水；公共路面、地块外停车场、绿地与空地计入。
   两种口径的面积都记录（area_m2 = 计入的，area_all_m2 = 含地块的），供 DMT 决定。
结果缓存 out/state/inlet_catchments.parquet（顶点集合或参数变了才重算）。DTM 不在时整个模块不用。"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import terrain

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "state"
DEFAULTS = {"res_m": 10.0, "tile_m": 4000.0, "buffer_m": 1000.0, "min_area_m2": 100.0, "max_area_m2": 200000.0, "max_flow_len_m": 1000.0, "eps_m": 1e-4, "plot_self_drain": True,
            "runoff_c": {"building": 0.85, "road": 0.90, "parking": 0.78, "green": 0.15, "plot": 0.55, "open": 0.30}}
# 地表覆盖：表、SRID、编码。后画的覆盖先画的，所以地块最先、建筑最后
LANDCOVER = [("udm_plot", 4326, 5), ("udm_park", 4326, 4), ("ud_greenarea", 32640, 4), ("udm_parking", 4326, 3), ("udm_roadsurface", 4326, 2), ("udm_building", 4326, 1)]
CODE_C = {1: "building", 2: "road", 3: "parking", 4: "green", 5: "plot", 0: "open"}
NB = [(-1, -1, np.sqrt(2)), (-1, 0, 1.0), (-1, 1, np.sqrt(2)), (0, -1, 1.0), (0, 1, 1.0), (1, -1, np.sqrt(2)), (1, 0, 1.0), (1, 1, np.sqrt(2))]


def params(cfg: dict) -> dict:
    p = json.loads(json.dumps(DEFAULTS)); c = cfg.get("terrain", {}).get("catchment", {})
    for k, v in c.items():
        if k == "runoff_c" and isinstance(v, dict):
            p["runoff_c"].update(v)
        elif k in p:
            p[k] = v
    return p


def enabled(cfg: dict) -> bool:
    return terrain.available(cfg) and bool(cfg.get("terrain", {}).get("catchment", {}).get("enabled", True))


# ---------- 网格算法（纯 numpy，可单测） ----------

def d8_downstream(filled: np.ndarray) -> np.ndarray:
    """每格的下游格平铺索引；出口（窗边、nodata、无更低邻格）为 -1。nan 邻格视为极低（出口）。"""
    H, W = filled.shape
    z = np.where(np.isfinite(filled), filled, -1e9)
    pad = np.full((H + 2, W + 2), -1e9); pad[1:-1, 1:-1] = z
    best = np.full((H, W), -np.inf); down = np.full((H, W), -1, dtype=np.int64)
    idx = np.arange(H * W).reshape(H, W)
    for dr, dc, dist in NB:
        nz = pad[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        drop = (z - nz) / dist
        better = drop > best
        best = np.where(better, drop, best)
        rr = np.clip(np.arange(H)[:, None] + dr, 0, H - 1); cc = np.clip(np.arange(W)[None, :] + dc, 0, W - 1)
        inside = ((np.arange(H)[:, None] + dr >= 0) & (np.arange(H)[:, None] + dr < H)) & ((np.arange(W)[None, :] + dc >= 0) & (np.arange(W)[None, :] + dc < W))
        target = np.where(inside, idx[rr, cc], -1)
        down = np.where(better, target, down)
    down[best <= 0] = -1                      # 没有更低邻格（ε 填洼后只剩出口格）
    down[~np.isfinite(filled)] = -1
    return down.ravel()


def label_basins(down: np.ndarray, seed_cells: np.ndarray, seed_ids: np.ndarray, n: int, step_len: np.ndarray | None = None):
    """每个格沿 D8 走到第一个种子格，得到种子序号；走到出口的为 -1。指针跳跃，O(log 路径长) 轮。
    给 step_len（每格到其下游格的距离）时同时返回沿程汇流路程。返回 (labels, dist) 或 labels。"""
    lab = np.full(n, -1, dtype=np.int64)
    lab[seed_cells] = seed_ids
    p = down.copy()
    term = (lab >= 0) | (p < 0)
    p[term] = np.nonzero(term)[0]
    d = None
    if step_len is not None:
        d = np.where(term, 0.0, step_len.astype(float))
    for _ in range(64):
        q = p[p]
        if np.array_equal(q, p):
            break
        if d is not None:
            d = d + d[p]
        p = q
    out = lab[p]
    return (out, d) if d is not None else out


def step_lengths(down: np.ndarray, shape: tuple[int, int], res: float) -> np.ndarray:
    """每格到其 D8 下游格的距离（直 res、斜 √2·res；出口格 0）。"""
    H, W = shape
    i = np.arange(H * W)
    ok = down >= 0
    dr = np.abs((down[ok] // W) - (i[ok] // W)); dc = np.abs((down[ok] % W) - (i[ok] % W))
    out = np.zeros(H * W)
    out[ok] = np.where((dr + dc) == 2, np.sqrt(2) * res, res)
    return out


def landcover_codes(mc, x0: float, y0: float, x1: float, y1: float, transform, shape: tuple[int, int]) -> np.ndarray:
    """瓦片范围内的地表覆盖编码栅格：0 其他、1 建筑、2 路面、3 停车场、4 绿地。"""
    from rasterio import features
    from shapely import from_wkb
    out = np.zeros(shape, dtype=np.uint8)
    for table, srid, code in LANDCOVER:
        env = f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"
        if srid != 32640:
            env = f"st_transform({env},{srid})"
        geom = "st_asbinary(st_transform(shape,32640))" if srid != 32640 else "st_asbinary(shape)"
        try:
            rows = mc.execute(f"select {geom} from public.{table} where shape && {env} and shape is not null").fetchall()
        except Exception:
            mc.rollback(); continue
        shapes = []
        for (wkb,) in rows:
            try:
                g = from_wkb(bytes(wkb))
                if not g.is_empty:
                    shapes.append((g, code))
            except Exception:
                continue
        if shapes:
            features.rasterize(shapes, out=out, transform=transform, default_value=code, all_touched=False)
    return out


def tile_catchments(dem: np.ndarray, res: float, inlets: pd.DataFrame, x0: float, y1: float, core: tuple[float, float, float, float],
                    codes: np.ndarray | None, P: dict) -> pd.DataFrame:
    """一块瓦片：dem 左上角 (x0, y1)，inlets 含 id/x/y（只取核心区内的做种子）。返回每个种子的面积、C、格数、是否碰瓦片边。"""
    H, W = dem.shape
    filled = terrain.priority_flood(dem, eps=P["eps_m"])
    down = d8_downstream(filled)
    cx0, cy0, cx1, cy1 = core
    core_in = inlets[(inlets["x"] >= cx0) & (inlets["x"] < cx1) & (inlets["y"] >= cy0) & (inlets["y"] < cy1)]
    if not len(core_in):
        return pd.DataFrame(columns=["vertex_id", "area_m2", "area_all_m2", "c", "n_cells", "truncated", "shared_cell", "capped"])
    cols = np.floor((core_in["x"].values - x0) / res).astype(int); rows = np.floor((y1 - core_in["y"].values) / res).astype(int)
    ok = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    cells = rows[ok] * W + cols[ok]; ids = core_in["id"].values[ok].astype(np.int64)
    # 同格多个进水口：第一个拿面积，其余标 shared
    first = pd.Series(np.arange(len(cells))).groupby(cells).first().values
    seed_cells = cells[first]; seed_idx = np.arange(len(first))
    lab, dist = label_basins(down, seed_cells, seed_idx, H * W, step_lengths(down, (H, W), res))
    far = dist > float(P.get("max_flow_len_m", np.inf) or np.inf)
    lab = np.where(far, -1, lab)                     # 汇流路程超上限的格不计：设计历时内到不了进水口
    valid = lab >= 0
    cnt_all = np.bincount(lab[valid], minlength=len(first)).astype(float)
    if codes is not None:
        cd = codes.ravel()
        contrib = (valid & ~np.isin(cd, [1, 5])) if P.get("plot_self_drain", True) else valid   # 地块内（建筑、院落）自排，不计入公共管网
        cnt = np.bincount(lab[contrib], minlength=len(first)).astype(float)
        cvals = np.array([P["runoff_c"][CODE_C[k]] for k in range(6)], dtype=float)[cd]
        csum = np.bincount(lab[contrib], weights=cvals[contrib], minlength=len(first))
        cmean = np.where(cnt > 0, csum / np.where(cnt > 0, cnt, 1), np.nan)
    else:
        cnt = cnt_all; cmean = np.full(len(first), np.nan)
    border = np.zeros((H, W), dtype=bool); border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    touch = np.zeros(len(first), dtype=bool)
    bl = lab[border.ravel() & valid]
    if len(bl):
        touch[np.unique(bl)] = True
    seed_of = dict(zip(cells[first], seed_idx))
    k_of = pd.Series(cells).value_counts().to_dict()            # 同格进水口数：面积均分
    cap = float(P.get("max_area_m2", np.inf) or np.inf)
    rows_out = []
    for k, (cell, vid) in enumerate(zip(cells, ids)):
        s = seed_of[cell]; kk = k_of[cell]
        area = float(cnt[s] * res * res) / kk
        rows_out.append({"vertex_id": int(vid), "area_m2": min(area, cap), "area_all_m2": float(cnt_all[s] * res * res) / kk, "c": float(cmean[s]), "n_cells": int(cnt[s]),
                         "truncated": bool(touch[s]), "shared_cell": bool(kk > 1), "capped": bool(area > cap)})
    return pd.DataFrame(rows_out)


# ---------- 全网构建 ----------

def _key(ids, P) -> str:
    return hashlib.sha1((json.dumps(sorted(int(i) for i in ids)) + json.dumps(P, sort_keys=True)).encode()).hexdigest()


def build(cfg: dict, vertices: pd.DataFrame, log=None, force: bool = False, write_cache: bool = True) -> pd.DataFrame:
    """所有进水口/雨水井顶点的 DTM 汇水面积与径流系数。缓存 out/state/inlet_catchments.parquet。"""
    P = params(cfg)
    inl = vertices[vertices["node_table"].isin(["inlet", "catchbasin"])][["id", "x", "y"]].dropna().copy()
    inl["id"] = inl["id"].astype(np.int64)
    cache = STATE / "inlet_catchments.parquet"; keyf = STATE / "inlet_catchments.key"
    key = _key(inl["id"].values, P)
    if cache.exists() and keyf.exists() and keyf.read_text().strip() == key and not force:
        return pd.read_parquet(cache)
    from rasterio.windows import from_bounds
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from . import db
    T, B, res = float(P["tile_m"]), float(P["buffer_m"]), float(P["res_m"])
    tiles = sorted(set(zip((inl["x"] // T).astype(int), (inl["y"] // T).astype(int))))
    if log: log(f"  {len(inl):,} 个进水口顶点，{len(tiles)} 块 {T/1000:.0f} km 瓦片，{res:.0f} m 网格")
    parts = []; t0 = time.time()
    with terrain.open_dtm(cfg) as ds, db.connect_source(cfg, "makani") as mc:
        for i, (tx, ty) in enumerate(tiles):
            cx0, cy0 = tx * T, ty * T; cx1, cy1 = cx0 + T, cy0 + T
            x0, y0, x1, y1 = cx0 - B, cy0 - B, cx1 + B, cy1 + B
            w = from_bounds(x0, y0, x1, y1, ds.transform)
            H, W = int(round((y1 - y0) / res)), int(round((x1 - x0) / res))
            a = ds.read(1, window=w, out_shape=(H, W), resampling=Resampling.average, boundless=True, fill_value=ds.nodata).astype(float)
            a = terrain._clean(a, ds.nodata)
            if not np.isfinite(a).any():
                continue
            tr = from_origin(x0, y1, res, res)
            codes = landcover_codes(mc, x0, y0, x1, y1, tr, (H, W))
            sub = inl[(inl["x"] >= x0) & (inl["x"] < x1) & (inl["y"] >= y0) & (inl["y"] < y1)]
            df = tile_catchments(a, res, sub, x0, y1, (cx0, cy0, cx1, cy1), codes, P)
            df["tile"] = f"{tx}_{ty}"
            parts.append(df)
            if log and ((i + 1) % 10 == 0 or i + 1 == len(tiles)):
                log(f"  瓦片 {i + 1}/{len(tiles)}（{time.time() - t0:.0f} s）")
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["vertex_id", "area_m2", "c", "n_cells", "truncated", "shared_cell", "capped", "tile"])
    out = out.drop_duplicates("vertex_id")
    if write_cache:
        STATE.mkdir(parents=True, exist_ok=True)
        out.to_parquet(cache, index=False); keyf.write_text(key)
    return out


def summary(cat: pd.DataFrame, P: dict) -> dict:
    a = cat["area_m2"]
    has = a > 0
    return {"n_inlets": int(len(cat)), "n_with_area": int(has.sum()), "n_shared_cell": int(cat["shared_cell"].sum()), "n_truncated": int(cat["truncated"].sum()),
            "n_capped": int(cat["capped"].sum()) if "capped" in cat else 0, "max_area_m2": P.get("max_area_m2"),
            "area_median_m2": round(float(a[has].median()), 0) if has.any() else None, "area_p90_m2": round(float(a[has].quantile(0.9)), 0) if has.any() else None,
            "area_total_km2": round(float(a.sum()) / 1e6, 1), "area_total_incl_plots_km2": (round(float(cat["area_all_m2"].sum()) / 1e6, 1) if "area_all_m2" in cat else None),
            "plot_self_drain": bool(P.get("plot_self_drain", True)), "n_zero_area": int((a <= 0).sum()),
            "c_median": round(float(cat.loc[has, "c"].median()), 3) if has.any() else None,
            "runoff_c_table": P["runoff_c"]}


def main(argv=None):
    import argparse, tomllib
    ap = argparse.ArgumentParser(description="DTM 子汇水区：进水口汇水面积与径流系数")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tile", nargs=2, type=int, metavar=("TX", "TY"), help="只跑一块瓦片（调试）")
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    if not enabled(cfg):
        raise SystemExit("未配置 DTM 或 [terrain.catchment].enabled=false")
    v = pd.read_parquet(STATE / "vertices.parquet")
    if a.tile:
        P = params(cfg); T = P["tile_m"]
        inl = v[v["node_table"].isin(["inlet", "catchbasin"])][["id", "x", "y"]].dropna()
        sub = inl[((inl["x"] // T).astype(int) == a.tile[0]) & ((inl["y"] // T).astype(int) == a.tile[1])]
        vv = v[v["id"].isin(sub["id"])]
        t0 = time.time(); out = build(cfg, vv, log=print, force=True, write_cache=False); print(f"{time.time()-t0:.1f}s")
    else:
        out = build(cfg, v, log=print, force=a.force)
    print(json.dumps(summary(out, params(cfg)), ensure_ascii=False, indent=2))
    print(out.describe().round(1).to_string())


if __name__ == "__main__":
    main()
