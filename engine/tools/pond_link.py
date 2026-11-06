# -*- coding: utf-8 -*-
"""把全酋 23 座塘接进排水管网，并定下它和 DTM 的关系（挖 / 不挖）。

**为什么要做这件事**：客户 flood 库里的 23 座塘（L2）和全酋管网、DTM 之间
一条关联都没有——

  · PIPELINE 的 ASSET_BEFORE / ASSET_AFTER / OUTFALLID / StartCode / EndCode
    26 万行里**没有一行**提到任何一座塘（`probe` 实测，不是推测）；
  · 可实际上 23 座塘**全部**有管线贴着或穿过（22 座距离 0 m），
    不少还是 1,800 / 2,400 mm 的干管。

所以「割裂」不是物理上没挨着，是**拓扑里没有这条边**。这个模块把边补上。

------------------------------------------------------------------ 基准
三套高程数据三个基准，先对齐再谈接管（都用 `probe` 在全量上验过）：

  PIPELINE.StartElev / EndElev      DEM 原始基准（StartElevSource 全是 dem_calculated）
  PIPELINE.INVERT_LEVEL_*           **DEM + 100**
      判据：StartElev − INVERT_UP 中位 −98.45，减 100 之后埋深中位 1.55 m、
      四分位 0.70 ～ 2.77 m、90% 位 4.53 m —— 标准埋深分布，基准对得上。
  pond.cover_level / bottom_floor_level  **多数 +100，个别是原始基准**
      PND_38 底 125.30 − 100 = 25.30，DTM 实测最低 25.44，差 0.14 m ✓
      PND_40 盖 31.23，DTM 地面 31.60，差 0.37 m —— 这一条**没有** +100。
      所以逐条判：减 100 与不减，哪个离 DTM 近就用哪个。

------------------------------------------------------------------ 挖不挖
判据是「**塘轮廓自己能兜住多少水**」：窗口里把轮廓外的格置 NaN，
priority_flood 把 NaN 当自由出口，于是 filled − dem 就是流不出轮廓的那部分。

这个定义避开了两个陷阱，两个我都踩过：
  · 沿轮廓取样算溢流位 → 缓冲区压到塘内边坡，溢流位取低了，库容算没；
  · 直接对种子做 priority_flood → 找到的是**包含塘的那片天然洼地**，
    量的是整条 wadi（实测只有 2%～20% 落在塘轮廓里），不是这座塘。

实测结果：23 座塘轮廓内合计只兜得住 7.2 万 m³，是台账 194 万 m³ 的 4%。
且**不是配准误差**——把轮廓在 ±250 m 内平移搜最佳位置，方位 28°～336° 四散、
7/23 顶到搜索边界，没有系统性偏移。也**不是 DTM 太老**——2024-09 建成的那座
照样有 3.7 m 坑，2019-12 的那座反而是平的。

所以结论是：**台账体积是设计/规划值，地形里没有对应的盆，绝大多数要挖。**
这一条直接决定二维模型怎么处理它（见 tools/couple_2d.py 的挖坑规则）。

------------------------------------------------------------------ 接法
沿用 tools/dig_pond5.py 给三个汇水区定的那一套，不另起炉灶：

  进水  上游井 →（带闸孔口，按控流定径）→ 塘
  出水  塘 →（单向拍门堰）→ 下游井；下游没有更低的管就得上泵
  超限  应急溢流堰，Gated YES（不带闸会从堰倒灌回管网，NP4 漏过 672 m³）
  净空  管内底比塘底高 ≥ 0.30 m（手册 §4.14.1(p)）

用法：
    python tools/pond_link.py              # 全部 23 座
    python tools/pond_link.py --only SW_MAR_RD113_PND_39
    python tools/pond_link.py --near 40    # 放宽候选管的搜索半径（m）

产物：out/pondlink/ponds_linked.json（明细）与 ponds_linked.geojson（控制台图层）
源库与 GDB 都只读。
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

GDB = "/vsizip/D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb.zip"
OUT = ROOT / "out" / "pondlink"

DATUM = 100.0          # 台账 → DTM 的基准差（terrain.py 按 6,000 个进水口标定）
NEAR_M = 50.0          # 候选进出水管：离塘轮廓多远之内算「接得上」（一条支管的量级）
CLEAR_M = 0.30         # 管内底高出塘底的净空，手册 §4.14.1(p)
MIN_DEPTH = 1.0        # 塘的最小可用深度
MAX_DEPTH = 6.0        # 可建开挖深度上限：再深就要支护/触地下水，按缺口如实报
FREEBOARD = 0.30       # 最高水位低于四周地面的超高
WET_EPS = 0.05         # 水深小于这个值不算积水

PIPE_COLS = ["UNITID", "StartCode", "EndCode", "StartElev", "EndElev",
             "PIPE_DIAMETER", "INVERT_LEVEL_UPSTREAM", "INVERT_LEVEL_DOWNSTREAM",
             "OUTFALLID", "STATUS", "PIPE_LENGTH"]


# ------------------------------------------------------------------ 基准
def pick_datum(led: float | None, dtm_ref: float) -> tuple[float | None, str]:
    """台账高程逐条判基准：减 100 与不减，哪个离 DTM 参照近就用哪个。

    整表照搬会出事——PND_38 要减 100（125.30 → 25.30，DTM 25.44），
    PND_40 不能减（31.23，DTM 31.60；减了成 −68.77）。
    """
    if led is None or not np.isfinite(led) or led == 0:
        return None, "空"
    a, b = abs(led - DATUM - dtm_ref), abs(led - dtm_ref)
    if min(a, b) > 8.0:                     # 两种都对不上，不敢用
        return None, f"对不上DTM（差{min(a, b):.1f}m）"
    return (led - DATUM, "+100") if a <= b else (led, "原始")


# ------------------------------------------------------------------ 地形
def dtm_profile(ds, geom, terrain) -> dict:
    """塘轮廓自己能兜住多少水，外加一条深度—库容曲线。

    轮廓外置 NaN，priority_flood 把 NaN 当自由出口 → filled − dem 就是
    「流不出这个轮廓」的水。曲线直接喂 SWMM 的 STORAGE 节点。
    """
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    minx, miny, maxx, maxy = geom.bounds
    w = from_bounds(minx - 20, miny - 20, maxx + 20, maxy + 20, ds.transform)
    a = ds.read(1, window=w).astype("float64")
    tr = ds.window_transform(w)
    if ds.nodata is not None:
        a[a == ds.nodata] = np.nan
    cell = abs(ds.transform.a) * abs(ds.transform.e)
    m_in = ~geometry_mask([geom], a.shape, tr, invert=False)
    if not (m_in & np.isfinite(a)).any():
        return {"ok": False, "why": "轮廓内无有效 DTM"}

    dem = np.where(m_in, a, np.nan)
    filled = terrain.priority_flood(dem)
    d = np.where(np.isfinite(filled) & np.isfinite(dem), filled - dem, 0.0)
    wet = d > WET_EPS
    z_bot = float(np.nanmin(dem))
    dmax = float(d.max()) if d.size else 0.0

    # 四周地面：轮廓外 5～30 m 的环，避开塘壁本身
    ring = geom.buffer(30.0).difference(geom.buffer(5.0))
    m_rg = ~geometry_mask([ring], a.shape, tr, invert=False)
    zr = a[m_rg & np.isfinite(a)]
    z_gnd = float(np.median(zr)) if zr.size >= 4 else float(np.nanmedian(a))

    return {"ok": True,
            "z_bottom": round(z_bot, 2), "z_ground": round(z_gnd, 2),
            "z_spill_own": round(z_bot + dmax, 2),
            "vol_dtm_m3": round(float(d[wet].sum() * cell)),
            "area_wet_m2": round(float(wet.sum() * cell)),
            "depth_dtm_m": round(dmax, 2),
            "pit_vs_ground_m": round(z_gnd - z_bot, 2),
            "n_cells": int((m_in & np.isfinite(a)).sum())}


def dig_to_volume(ds, geom, z_top: float, target_m3: float,
                  max_depth: float = MAX_DEPTH) -> dict:
    """要挖到什么标高，才装得下 target_m3？

    模型很简单，和 dig_pond5.py 给三个汇水区用的是同一套：
      · 最高水位 z_top = 四周地面 − 超高（水面不许高过街面）
      · 开挖到 z_floor，挖后地面 = min(dem, z_floor)
      · 库容 V(z_floor) = Σ (z_top − min(dem, z_floor))+ × 格面积
    z_floor 越低 V 越大，二分求解即可。

    挖不到目标就**如实报缺口**，不许把深度硬调上去——
    超过 max_depth 要支护、可能触地下水，那是另一个造价量级。
    """
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    minx, miny, maxx, maxy = geom.bounds
    w = from_bounds(minx - 10, miny - 10, maxx + 10, maxy + 10, ds.transform)
    a = ds.read(1, window=w).astype("float64")
    tr = ds.window_transform(w)
    if ds.nodata is not None:
        a[a == ds.nodata] = np.nan
    cell = abs(ds.transform.a) * abs(ds.transform.e)
    m = ~geometry_mask([geom], a.shape, tr, invert=False) & np.isfinite(a)
    if not m.any():
        return {"ok": False}
    dem = a[m]

    def store(zf: float) -> float:
        return float(np.clip(z_top - np.minimum(dem, zf), 0, None).sum() * cell)

    def cut(zf: float) -> float:
        return float(np.clip(dem - zf, 0, None).sum() * cell)

    v_now = store(z_top)                      # 一锹不挖时的库容（现状洼地）
    z_lo = z_top - max_depth
    v_max = store(z_lo)
    if target_m3 <= v_now + 1.0:
        return {"ok": True, "z_floor": round(z_top, 2), "depth_m": 0.0,
                "volume_m3": round(v_now), "dig_m3": 0,
                "enough": True, "v_now_m3": round(v_now), "v_max_m3": round(v_max)}
    if target_m3 > v_max:                      # 挖到上限也不够
        return {"ok": True, "z_floor": round(z_lo, 2), "depth_m": round(max_depth, 2),
                "volume_m3": round(v_max), "dig_m3": round(cut(z_lo)),
                "enough": False, "short_m3": round(target_m3 - v_max),
                "v_now_m3": round(v_now), "v_max_m3": round(v_max)}
    lo, hi = z_lo, z_top                       # 二分：store 关于 z_floor 单调减
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if store(mid) >= target_m3:
            lo = mid
        else:
            hi = mid
    zf = lo
    return {"ok": True, "z_floor": round(zf, 2), "depth_m": round(z_top - zf, 2),
            "volume_m3": round(store(zf)), "dig_m3": round(cut(zf)),
            "enough": True, "v_now_m3": round(v_now), "v_max_m3": round(v_max)}


def stage_storage(ds, geom, z_floor: float, z_top: float, n: int = 10) -> list[list[float]]:
    """挖成之后的深度—库容曲线，直接喂 SWMM 的 STORAGE 节点。
    按「挖后地面 min(dem, z_floor)」逐级灌水算，不用 A×h 糊弄——
    轮廓内本来就有高差，柱体公式会高估。"""
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    minx, miny, maxx, maxy = geom.bounds
    w = from_bounds(minx - 10, miny - 10, maxx + 10, maxy + 10, ds.transform)
    a = ds.read(1, window=w).astype("float64")
    tr = ds.window_transform(w)
    if ds.nodata is not None:
        a[a == ds.nodata] = np.nan
    cell = abs(ds.transform.a) * abs(ds.transform.e)
    m = ~geometry_mask([geom], a.shape, tr, invert=False) & np.isfinite(a)
    if not m.any():
        return []
    ground = np.minimum(a[m], z_floor)
    depth = max(z_top - z_floor, 0.01)
    out = []
    for h in np.linspace(0, depth, n + 1)[1:]:
        v = float(np.clip((z_floor + h) - ground, 0, None).sum() * cell)
        out.append([round(float(h), 2), round(v)])
    return out


# ------------------------------------------------------------------ 管网
def pipes_near(geom, near_m: float):
    """塘周边的管线，带换算好的真实管内底。"""
    import pyogrio

    minx, miny, maxx, maxy = geom.bounds
    df = pyogrio.read_dataframe(GDB, layer="PIPELINE", columns=PIPE_COLS,
                                bbox=(minx - near_m - 50, miny - near_m - 50,
                                      maxx + near_m + 50, maxy + near_m + 50))
    if len(df) == 0:
        return df
    df = df.assign(dist=df.geometry.distance(geom))
    df = df[df.dist <= near_m].copy()
    for src, dst in (("INVERT_LEVEL_UPSTREAM", "inv_up"),
                     ("INVERT_LEVEL_DOWNSTREAM", "inv_dn")):
        v = df[src].astype("float64")
        # 0 与离谱值当缺失；其余减 100 回到 DTM 基准
        df[dst] = np.where((v != 0) & np.isfinite(v) & (v > 50) & (v < 500), v - DATUM, np.nan)
    df["dia_mm"] = df["PIPE_DIAMETER"].astype("float64")
    return df.sort_values(["dist", "dia_mm"], ascending=[True, False])


def choose_links(df, z_bot: float, z_top: float) -> dict:
    """选进水管和出水管。

    进水：管内底要**高于塘底 + 净空**，否则塘里的水会顶回管里。不设上限——
          管内底高过最高水位只是跌水进塘，照样进得去。同类里取管径最大的。
    出水：管内底要**低于塘底**，重力排得掉；一根都没有就必须上泵，
          这正是顾问方案在三个汇水区的处境（55 台泵），要如实标出来。
    """
    if len(df) == 0:
        return {"inflow": None, "outflow": None, "need_pump": True, "why": "周边无管"}
    d = df.copy()
    up, dn = d.inv_up.values.astype("float64"), d.inv_dn.values.astype("float64")
    both = np.isfinite(up) & np.isfinite(dn)
    inv = np.where(both, np.minimum(np.nan_to_num(up, nan=np.inf),
                                    np.nan_to_num(dn, nan=np.inf)),
                   np.where(np.isfinite(up), up, dn))
    d["inv"] = inv
    ok = np.isfinite(d.inv.values)
    if not ok.any():
        return {"inflow": None, "outflow": None, "need_pump": True,
                "why": f"周边 {len(d)} 根管的管内底全部无效"}
    d = d[ok]

    # 进水只要**高于塘底 + 净空**就行，不设上限：
    # 管内底高过最高水位是跌水进塘，照样进得去，卡上限会把绝大多数进水管误判掉。
    cand_in = d[d.inv >= z_bot + CLEAR_M]
    inflow = None
    if len(cand_in):
        r = cand_in.sort_values(["dia_mm", "dist"], ascending=[False, True]).iloc[0]
        inflow = _rec(r)

    cand_out = d[d.inv < z_bot - 0.05]
    outflow = None
    if len(cand_out):
        r = cand_out.sort_values(["inv", "dia_mm"], ascending=[True, False]).iloc[0]
        outflow = _rec(r)

    return {"inflow": inflow, "outflow": outflow,
            "need_pump": outflow is None,
            "why": "" if outflow is not None else
                   f"周边 {len(d)} 根有效管的管内底都不低于塘底 {z_bot:.2f} m，重力排不掉",
            "n_candidate": int(len(d))}


def _rec(r) -> dict:
    return {"unitid": str(r.UNITID), "dist_m": round(float(r.dist), 1),
            "dia_mm": round(float(r.dia_mm)), "invert_m": round(float(r.inv), 2),
            "start_code": str(r.StartCode), "end_code": str(r.EndCode),
            "ground_start_m": round(float(r.StartElev), 2) if np.isfinite(r.StartElev) else None,
            "ground_end_m": round(float(r.EndElev), 2) if np.isfinite(r.EndElev) else None,
            "outfall": str(r.OUTFALLID), "status": str(r.STATUS)}


# ------------------------------------------------------------------ 组装
def build(cfg, only: str | None, near_m: float, log=print) -> list[dict]:
    import psycopg
    from shapely import wkb as shwkb
    from shapely.geometry import mapping
    from swbrain import terrain

    c = (cfg.get("source") or {}).get("flood")
    ds = terrain.open_dtm(cfg)
    q = """select unitid, volume, depth, bottom_floor_level, cover_level, shape_area,
                  no_of_connected_pipes, status, zone_or_district_name,
                  asset_installation_date::date,
                  st_asbinary(st_transform(geom, 32640)),
                  st_asgeojson(st_transform(geom, 4326))
             from stormwater_drainage.pond where geom is not null"""
    if only:
        q += " and unitid = %s"
    q += " order by volume desc nulls last"
    with psycopg.connect(host=c["host"], port=c["port"], user=c["user"],
                         password=c["password"], dbname=c["dbname"],
                         connect_timeout=30) as conn, conn.cursor() as cur:
        cur.execute(q, (only,) if only else None)
        rows = cur.fetchall()

    out = []
    for (uid, vol, dep, bot, cov, area, npipe, status, zone, inst, wkbb, gj4326) in rows:
        g = shwkb.loads(bytes(wkbb))
        t = dtm_profile(ds, g, terrain)
        if not t.get("ok"):
            log(f"  {uid}：{t.get('why')}")
            continue

        # 台账高程逐条对基准
        bot_led, how_b = pick_datum(bot, t["z_bottom"])
        cov_led, how_c = pick_datum(cov, t["z_ground"])

        # 最高水位：四周地面减超高。水面不许高过街面，这是塘不是水库。
        # 台账盖标高对得上 DTM 就用台账的（那是设计值），否则用地面。
        z_top = cov_led if cov_led is not None else (t["z_ground"] - FREEBOARD)

        # 目标库容 = 台账体积。台账为 0 的没有目标，按现状地形如实报。
        v_led = float(vol or 0)
        v_dtm = float(t["vol_dtm_m3"])
        dd = dig_to_volume(ds, g, z_top, v_led if v_led > 0 else 0.0)
        if not dd.get("ok"):
            log(f"  {uid}：开挖反解失败"); continue

        no_target = v_led <= 0
        if no_target:
            # 台账没写体积就没有开挖目标，不能假装算出了一个深度。
            # 如实标出来：这是台账的缺口，要客户补设计容量。
            dig = dd["v_now_m3"] < 500
            why = ("台账没写体积 → 无开挖目标。现状轮廓几乎兜不住水（"
                   f"{dd['v_now_m3']:,} m³），要挖才成塘，挖多深取决于客户补的设计容量"
                   if dig else
                   f"台账没写体积 → 无开挖目标。现状轮廓已能兜住 {dd['v_now_m3']:,} m³")
        elif dd["depth_m"] <= 0.01:
            dig = False
            why = f"现状轮廓已能兜住 {dd['v_now_m3']:,} m³ ≥ 台账 {v_led:,.0f} m³，不用挖"
        elif not dd.get("enough", True):
            dig = True
            why = (f"挖到上限 {MAX_DEPTH:g} m 也只有 {dd['v_max_m3']:,} m³，"
                   f"差台账 {dd['short_m3']:,} m³ —— 这个轮廓装不下台账写的体积")
        else:
            dig = True
            why = (f"现状只兜得住 {dd['v_now_m3']:,} m³，挖深 {dd['depth_m']:.2f} m "
                   f"（挖方 {dd['dig_m3']:,} m³）才够台账 {v_led:,.0f} m³")

        z_floor = dd["z_floor"]
        curve = stage_storage(ds, g, z_floor, z_top)

        df = pipes_near(g, near_m)
        link = choose_links(df, z_floor, z_top)

        rec = {"unitid": uid, "zone": zone, "status": status, "installed": str(inst),
               "area_poly_m2": round(float(area or 0)),
               "ledger": {"volume_m3": v_led, "depth_m": float(dep or 0),
                          "bottom_m": bot, "cover_m": cov,
                          "bottom_datum": how_b, "cover_datum": how_c,
                          "n_connected_pipes": npipe},
               "dtm": t,
               "design": {"z_floor_m": z_floor, "z_top_m": round(z_top, 2),
                          "depth_m": dd["depth_m"],
                          "volume_design_m3": dd["volume_m3"],
                          "volume_now_m3": dd["v_now_m3"],
                          "volume_max_m3": dd["v_max_m3"],
                          "need_dig": bool(dig), "dig_reason": why,
                          "dig_volume_m3": dd["dig_m3"],
                          "short_of_ledger_m3": dd.get("short_m3", 0),
                          "no_target": bool(no_target),
                          "ledger_suspect": bool(v_led > 0 and area
                                                 and v_led < 0.1 * float(area)),
                          "stage_storage": curve},
               "link": link,
               "geometry": json.loads(gj4326)}
        out.append(rec)
        flag = ("无目标  " if no_target else
                ("挖 %4.2fm" % dd["depth_m"]) if dig else "不用挖  ")
        short = "　装不下" if not dd.get("enough", True) else ""
        pump = "　要泵" if link["need_pump"] else ""
        log(f"  {uid:<24}{flag}　库容 {dd['volume_m3']:>9,} m³（台账 {v_led:>9,.0f}）"
            f"　进水 {str((link['inflow'] or {}).get('dia_mm', '—')):>5}mm"
            f"　出水 {str((link['outflow'] or {}).get('dia_mm', '—')):>5}mm{pump}{short}")
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="把全酋的塘接进管网，并定下与 DTM 的关系")
    ap.add_argument("--only", default=None, help="只处理这一个 unitid")
    ap.add_argument("--near", type=float, default=NEAR_M, help="候选进出水管的搜索半径 m")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)

    from swbrain import db, terrain
    cfg = db.load_config(a.config)
    if not terrain.available(cfg):
        print("没有 DTM，做不了「挖不挖」的判定"); return 1
    print(f"接管 + 挖坑判定（候选半径 {a.near:g} m，净空 {CLEAR_M:g} m）：")
    recs = build(cfg, a.only, a.near)
    if not recs:
        print("没有可处理的塘"); return 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ponds_linked.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    fc = {"type": "FeatureCollection", "layer": "ponds_linked",
          "source": "客户 flood 库 stormwater_drainage.pond ＋ DMT_StormWater GDB ＋ 5 m DTM",
          "features": [{"type": "Feature", "geometry": r.pop("geometry"),
                        "properties": _flat(r)} for r in [dict(x) for x in recs]]}
    (OUT / "ponds_linked.geojson").write_text(
        json.dumps(fc, ensure_ascii=False, default=float), encoding="utf-8")
    # 控制台的图层目录里也放一份，前端直接读，不用再搬
    cd = ROOT / "console" / "data"
    if cd.exists():
        (cd / "ponds_linked.geojson").write_text(
            json.dumps(fc, ensure_ascii=False, default=float), encoding="utf-8")

    n = len(recs)
    n_dig = sum(1 for r in recs if r["design"]["need_dig"])
    n_pump = sum(1 for r in recs if r["link"]["need_pump"])
    n_in = sum(1 for r in recs if r["link"]["inflow"])
    n_out = sum(1 for r in recs if r["link"]["outflow"])
    n_short = sum(1 for r in recs if r["design"]["short_of_ledger_m3"])
    n_none = sum(1 for r in recs if r["link"].get("n_candidate", 0) == 0)
    v_led = sum(r["ledger"]["volume_m3"] for r in recs)
    v_now = sum(r["design"]["volume_now_m3"] for r in recs)
    v_des = sum(r["design"]["volume_design_m3"] for r in recs)
    v_dig = sum(r["design"]["dig_volume_m3"] for r in recs)
    v_sh = sum(r["design"]["short_of_ledger_m3"] for r in recs)
    v_free = sum(r["dtm"]["vol_dtm_m3"] for r in recs)
    print(f"\n{n} 座塘的库容账（三个口径，假设不同，不能混着用）：")
    print(f"  台账写的                 {v_led:>12,.0f} m³")
    print(f"  ① 什么都不做             {v_free:>12,.0f} m³　"
          f"（占台账 {v_free / v_led * 100 if v_led else 0:.0f}%）"
          f"　水可以从轮廓边上流走，这是现状")
    print(f"  ② 只筑一圈围堰到地面      {v_now:>12,.0f} m³　"
          f"（占台账 {v_now / v_led * 100 if v_led else 0:.0f}%）　不开挖")
    # ③ 只统计有开挖目标的那些；台账没写体积的两座单列，不许混进合计撑大数字
    v_tgt = sum(r["design"]["volume_design_m3"] for r in recs if not r["design"]["no_target"])
    v_nt = sum(r["design"]["volume_design_m3"] for r in recs if r["design"]["no_target"])
    print(f"  ③ 挖到台账体积           {v_tgt:>12,.0f} m³　"
          f"　挖方 {v_dig:,.0f} m³")
    if v_nt:
        print(f"  另：台账没写体积的 2 座    {v_nt:>12,.0f} m³　"
          f"（按「围堰到地面」估的上限，不是设计值，不并入 ③）")
    if v_sh:
        print(f"  挖到 {MAX_DEPTH:g} m 仍差   {v_sh:>12,.0f} m³　（{n_short} 座装不下台账写的体积）")
    n_nt = sum(1 for r in recs if r["design"]["no_target"])
    n_sus = sum(1 for r in recs if r["design"]["ledger_suspect"])
    print(f"\n挖不挖：要挖 {n_dig}/{n} 座，不用挖 {n - n_dig}/{n} 座")
    if n_nt or n_sus:
        print(f"台账缺陷：{n_nt} 座没写体积（没有开挖目标，要客户补设计容量），"
              f"{n_sus} 座体积明显不实（小于轮廓面积 × 0.1 m）")
    print(f"接管：重力出水管 {n_out}/{n} 座　管道进水 {n_in}/{n} 座　"
          f"必须上泵 {n_pump}/{n} 座　周边无有效管 {n_none}/{n} 座")
    print("  注：管道进水少不是接漏了——这类塘是**地表汇流进水、干管重力排水**。")
    print("      不少干管（1,800 / 2,400 mm）从塘底下穿过，管内底比挖后塘底还低，")
    print("      只能当出水口；进水那一侧要靠二维地表汇流，不在一维管网里。")
    print(f"\n写出 {OUT / 'ponds_linked.json'}")
    return 0


def _flat(r: dict) -> dict:
    """GeoJSON 属性拍平，控制台弹窗直接读。曲线不进属性，太长。"""
    d = r["design"]; t = r["dtm"]; k = r["link"]
    return {"unitid": r["unitid"], "zone": r["zone"], "status": r["status"],
            "installed": r["installed"], "area_m2": r["area_poly_m2"],
            "vol_ledger_m3": r["ledger"]["volume_m3"],
            "vol_dtm_m3": t["vol_dtm_m3"], "vol_design_m3": d["volume_design_m3"],
            "vol_now_m3": d["volume_now_m3"], "short_m3": d["short_of_ledger_m3"],
            "z_floor_m": d["z_floor_m"], "z_top_m": d["z_top_m"], "depth_m": d["depth_m"],
            "z_ground_m": t["z_ground"], "pit_vs_ground_m": t["pit_vs_ground_m"],
            "need_dig": d["need_dig"], "dig_volume_m3": d["dig_volume_m3"],
            "no_target": d["no_target"], "ledger_suspect": d["ledger_suspect"],
            "dig_reason": d["dig_reason"],
            "inflow_pipe": (k["inflow"] or {}).get("unitid"),
            "inflow_dia_mm": (k["inflow"] or {}).get("dia_mm"),
            "inflow_invert_m": (k["inflow"] or {}).get("invert_m"),
            "outflow_pipe": (k["outflow"] or {}).get("unitid"),
            "outflow_dia_mm": (k["outflow"] or {}).get("dia_mm"),
            "outflow_invert_m": (k["outflow"] or {}).get("invert_m"),
            "need_pump": k["need_pump"], "link_note": k.get("why", "")}


if __name__ == "__main__":
    raise SystemExit(main())
