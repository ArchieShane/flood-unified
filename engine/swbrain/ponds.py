# -*- coding: utf-8 -*-
"""第二道防线：汇水区里挖滞蓄塘，并把水汇进去。

和 decisions.py 里那条「补 X m³ 蓄渗容量」的区别：那是个静态的体积缺口，没说水能不能
**及时**进去。这里把它变成「选址 + 定容 + 配进水能力 + 按时间验证」。

核心判据是**进水能力**而不是库容：

    有效库容 = min( 库容 , 进水能力 × 雨峰窗口时长 )

一个雨峰过去之后才灌满的塘，库容利用率写出来是 100%，防洪效果是零。实测量级（DMT 设计
手册 B 区 10 年一遇、15 min 雨强 57.7 mm/h）：100 ha 汇水区雨峰一小时产流 40,390 m³；
一台 12 寸泵一小时 700 m³，占 1.7%；一根 DN1500 重力管（2‰）11,381 m³/h，抵 16.3 台泵。
所以**进水必须靠重力**，泵只用在重力够不到的局部低点、以及事后排空腾库容。

建模和第三道防线（dispatch2.py）同一个套路：每个候选塘位枚举有限个「方案包」（容积档 ×
进水方式档），每个包用水库演算算成几个常数，再让 MILP 在预算下挑。非线性都留在演算里，
MILP 只做选择。

    python -m swbrain.ponds --rp 10cc                  # 全网
    python -m swbrain.ponds --rp 10cc --zone Mussafah  # 只看一个片区
    python -m swbrain.ponds --rp 10cc --budget 5e7     # 给预算
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from . import hydraulics as HY
from .replay import _grid as rp_grid, decompose, kpi as rp_kpi, simulate as rp_sim

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {
    "serve_radius_m": 1200.0,            # 一个塘往上游服务多远
    "assign_by": "lowest",               # lowest = 归给能到达的最低点（汇水区尺度）；nearest = 归给最近的
    "min_head_m": 0.3,                   # 点要比塘底高这么多才算能自流
    "min_natural_m3": 100.0,             # 天然洼地小于这个不当候选
    "exclude_terrain_note": ["flat", "regional"],   # 没有真洼地的、以及洼地漫到分析窗口边界外的
    "design_depth_m": 2.5,               # 塘的设计水深；库容定了，占地跟着算
    "volume_tiers": [0.25, 0.5, 1.0, 1.5],  # 相对「服务范围峰值积水量」的倍数
    "max_pond_area_m2": 200000.0,        # 单塘占地上限
    "pipe_dn_mm": [600, 900, 1200, 1500],
    "pipe_slope": 0.002,
    "pipe_n": 0.013,
    "pump_counts": [2, 4],
    "pump_rate_m3h": 700.0,
    "peak_window_min": 120.0,            # 雨峰窗口：只有在这段时间里进的水才真正顶用
    "infiltration_mm_h": 5.0,            # 塘底下渗
    "release_m3h": 0.0,                  # 受控出流（接下游管网），缺省 0 = 只靠渗和事后抽
    "dt_min": 10.0,
    "horizon_min": 1440.0,
    "min_saved_h": 0.05,                 # 省不到这么多积水小时的方案包直接丢掉，不进 MILP
    "milp_time_limit_s": 30.0,
    "value_aed_per_priority_hour": 100.0,   # 和第三道防线同一把尺子，三道防线才能放一起比
    "cost": {
        "excavate_aed_m3": 45.0,
        "pipe_aed_m": {"600": 1800.0, "900": 2900.0, "1200": 4300.0, "1500": 6100.0},
        "pipe_min_len_m": 50.0,          # 再近的点也得有一段收水管
        "hose_aed_m": 400.0,             # 泵配的临时排水管，比埋管便宜得多
        "pump_capital_aed": 120000.0,
        "land_aed_m2": 0.0,              # 市政用地缺省不计地价
    },
}


def params(cfg: dict) -> dict:
    P = dict(DEFAULTS)
    for k, v in (cfg.get("ponds") or {}).items():
        P[k] = ({**DEFAULTS[k], **v} if isinstance(v, dict) and isinstance(DEFAULTS.get(k), dict) else v)
    return P


def _m_per_deg(lat: float = 24.4) -> tuple[float, float]:
    return 111000.0 * math.cos(math.radians(lat)), 111000.0


def terrain_table() -> pd.DataFrame:
    """热点填洼分析的结果（terrain.py 从 5 m DTM 算的）。没有就返回空表。"""
    p = ROOT / "out" / "state" / "hotspot_terrain.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------- 候选塘位
def candidate_sites(cfg: dict, hotspots: pd.DataFrame | None = None) -> pd.DataFrame:
    """两个来源：客户 1D 模型里已有的塘，和热点填洼分析出来的天然洼地。

    天然洼地用 `low_z`（洼底标高）和 `spill_z`（溢流口标高），都是 terrain.py 从 5 m DTM
    直接算的，不是反推的。`terrain_note` 标 flat 的（845 个热点里 353 个）根本没有洼地，
    标 regional 的（5 个）洼地一路漫到分析窗口边界、算出来的库容不可信，两类都排除。
    """
    P = params(cfg)
    rows = []

    # 1 客户 1D 模型里的临时塘（Parsons 方案）
    pf = ROOT / "out" / "dmt_model" / "ponds_sites.json"
    if pf.exists():
        try:
            for r in json.loads(pf.read_text(encoding="utf-8")):
                if r.get("lon") is None or r.get("lat") is None:
                    continue
                rows.append({"site_id": str(r["site_id"]), "name": r.get("name"),
                             "lon": float(r["lon"]), "lat": float(r["lat"]),
                             "natural_m3": float(r.get("max_stor_m3") or 0.0), "area_m2": np.nan,
                             "low_z": np.nan, "spill_z": np.nan, "origin": "client_model",
                             "area": r.get("area"), "hotspot_id": None})
        except Exception:
            pass

    # 2 热点填洼分析出来的天然洼地
    t = terrain_table()
    if len(t) and hotspots is not None and len(hotspots) and "hotspot_id" in hotspots.columns:
        cols = ["hotspot_id", "lon", "lat"] + (["area"] if "area" in hotspots.columns else [])
        t = t.merge(hotspots[cols].drop_duplicates("hotspot_id"), on="hotspot_id", how="inner")
        bad = {str(x) for x in (P.get("exclude_terrain_note") or [])}
        t = t[~t["terrain_note"].astype(str).str.strip().isin(bad)]
        t = t[t["pond_volume_m3"].astype(float) >= float(P["min_natural_m3"])]
        t = t[t["lon"].notna() & t["lat"].notna()]
        for _, r in t.iterrows():
            hid = str(r["hotspot_id"])
            rows.append({"site_id": f"NAT-{hid}", "name": f"{hid} 天然洼地",
                         "lon": float(r["lon"]), "lat": float(r["lat"]),
                         "natural_m3": float(r["pond_volume_m3"]), "area_m2": float(r["pond_area_m2"]),
                         "low_z": float(r["low_z"]), "spill_z": float(r["spill_z"]),
                         "origin": "terrain", "area": r.get("area"), "hotspot_id": hid})

    df = pd.DataFrame(rows)
    if not len(df):
        return df
    return df.drop_duplicates(subset=["lon", "lat"]).reset_index(drop=True)


def assign_points(sites: pd.DataFrame, pts: pd.DataFrame, cfg: dict) -> dict:
    """每个积水点只归给一个塘：半径内、且点比塘底高（能自流）。

    归给哪一个由 `assign_by` 决定：

    * `lowest`（缺省）——归给能到达的**最低**的那个塘。水本来就往低处流，一个汇水区里多个
      点共用同一个低点，这才是汇水区尺度的塘。客户 1D 模型里已有的塘优先，那是真facility。
    * `nearest`——归给最近的那个。每个热点脚下都有自己的小洼地，于是几乎每个点自己挖一个坑，
      平均一个塘只服务 1.27 个点，退化成「逐点挖坑」，不是第二道防线。留着做对照。

    「一个点只归一个塘」是有意的简化——两个塘服务同一个点，收益会被算两遍，MILP 里就得加
    防重复计收益的约束。代价是失去了「同一个点由两个塘分担」这类方案。

    返回 {塘位下标: [(点下标, 距离 m), …]}。
    """
    P = params(cfg)
    if not len(sites) or not len(pts):
        return {}
    kx, ky = _m_per_deg()
    sx = sites["lon"].values.astype(float) * kx
    sy = sites["lat"].values.astype(float) * ky
    px = pts["lon"].values.astype(float) * kx
    py = pts["lat"].values.astype(float) * ky
    pz = pts["dtm_z"].values.astype(float) if "dtm_z" in pts.columns else np.full(len(pts), np.nan)
    sz = sites["low_z"].values.astype(float)
    is_cli = (sites["origin"].values == "client_model") if "origin" in sites.columns else np.zeros(len(sites), bool)
    R = float(P["serve_radius_m"]); head = float(P["min_head_m"])
    by = str(P.get("assign_by", "lowest"))
    # 排序键：客户已有的塘排在前面，然后比洼底标高（没有标高的排最后），最后比距离。
    # np.lexsort 最后一个键是主键。
    rank0 = np.where(is_cli, 0, 1)
    zkey = np.where(np.isfinite(sz), sz, np.inf)
    out: dict[int, list[tuple[int, float]]] = {}
    for j in range(len(pts)):
        d = np.hypot(px[j] - sx, py[j] - sy)
        ok = d <= R
        if np.isfinite(pz[j]):                       # 有高程就判能不能自流：点要比塘底高 head
            ok = ok & ~(np.isfinite(sz) & (sz > pz[j] - head))
        idx = np.nonzero(ok)[0]
        if not len(idx):
            continue
        if by == "nearest":
            i = int(idx[np.argmin(d[idx])])
        else:
            order = np.lexsort((d[idx], zkey[idx], rank0[idx]))
            i = int(idx[order[0]])
        out.setdefault(i, []).append((j, float(d[i])))
    return out


# ---------------------------------------------------------------- 方案包的两个维度
def volume_options(site: pd.Series, free_natural_m3: float, need_m3: float, cfg: dict) -> list[dict]:
    """容积档按**需求**定，不按挖深定。

    需求 = 服务范围内各点峰值积水量之和，也就是「全接住」要多大库容。档位取它的 25% / 50%
    / 100% / 150%，再按设计水深 2.5 m 反算占地。反过来按固定挖深列档会出笑话：天然洼地动辄
    两三万平米，最浅的 1 m 档就是两万多方库容，而需求才两千方——挖十倍的坑存一成的水。

    可用天然库容（free_natural_m3）是白给的，先扣掉，剩下的才要挖。
    """
    P = params(cfg)
    d = float(P["design_depth_m"])
    avail = float(site.get("area_m2") or 0.0)                     # 洼地范围当作可用地的代理
    if not np.isfinite(avail) or avail <= 0:
        avail = float(P["max_pond_area_m2"])                      # 客户塘没给面积，按上限放开
    avail = min(avail, float(P["max_pond_area_m2"]))
    c = (P.get("cost") or {})
    out, seen = [], set()
    if free_natural_m3 > 0:                                       # 什么都不挖，只用现有库容
        out.append({"dig_m": 0.0, "volume_m3": round(free_natural_m3, 0), "area_m2": round(avail, 0),
                    "footprint_m2": 0.0, "excavate_m3": 0.0, "capped": False, "cost_aed": 0.0,
                    "label": "只用现有库容（不挖）"})
        seen.add(round(free_natural_m3, -1))
    for f in P["volume_tiers"]:
        v_dig = max(float(f) * float(need_m3) - free_natural_m3, 0.0)
        if v_dig <= 0:
            continue
        foot = v_dig / d
        capped = foot > avail
        if capped:                                                # 地不够，能挖多少算多少
            foot = avail; v_dig = foot * d
        vol = free_natural_m3 + v_dig
        key = round(vol, -1)
        if key in seen:
            continue
        seen.add(key)
        out.append({"dig_m": d, "volume_m3": round(vol, 0),
                    "area_m2": round(foot, 0),                # 水面面积，只用来算下渗
                    "footprint_m2": round(foot, 0), "excavate_m3": round(v_dig, 0), "capped": capped,
                    "cost_aed": round(v_dig * float(c.get("excavate_aed_m3", 45.0))
                                      + foot * float(c.get("land_aed_m2", 0.0)), 0),
                    "label": f"开挖 {v_dig:,.0f} m³（{foot:,.0f} m² × {d:g} m）" + ("，受占地上限" if capped else "")})
    return out


def conveyance_options(dists_m: np.ndarray, cfg: dict) -> list[dict]:
    """进水方式档：新建重力管、抽排。容量统一用 m³/h，造价按到塘的**实际距离**算。

    **没有「维持现状、水自己流进来」这一档。** 374 个积水点的 land_use 全是 road（360）或
    tunnel（14）——路面和隧道里不能挖塘，塘必然在别处，水必然要靠管或泵送过去。早先版本留了
    一个零造价、不限流量的「塘边点自己淌进来」档，结果模型一律选「就地挖坑、进水免费」，
    单位成本看着漂亮，物理上却是在车行道里挖坑。这一档是被 land_use 这个数据否掉的，不是
    建模偏好。要恢复它，得先拿到候选塘位地块的权属/用地数据（见假设总账 pond_land）。

    管长按每个服务点到塘的距离求和，最短 50 m。泵除了台数造价还要配临时排水管，单价低得多。
    """
    P = params(cfg); c = (P.get("cost") or {})
    L = float(np.maximum(np.asarray(dists_m, dtype=float), float(c.get("pipe_min_len_m", 50.0))).sum())
    out = []
    for dn in P["pipe_dn_mm"]:
        q = float(HY.manning_full_flow(float(dn) / 1000.0, float(P["pipe_slope"]), float(P["pipe_n"]))) * 3600.0
        price = float((c.get("pipe_aed_m") or {}).get(str(int(dn)), 0.0))
        out.append({"kind": "pipe", "dn_mm": int(dn), "capacity_m3h": round(q, 0), "len_m": round(L, 0),
                    "cost_aed": round(price * L, 0), "label": f"新建 DN{int(dn)} 重力管 {L:,.0f} m",
                    "note": f"坡度 {float(P['pipe_slope']) * 1000:g}‰，满管流"})
    hose = float(c.get("hose_aed_m", 0.0))
    for n in P["pump_counts"]:
        out.append({"kind": "pump", "n": int(n), "capacity_m3h": round(float(n) * float(P["pump_rate_m3h"]), 0),
                    "len_m": round(L, 0),
                    "cost_aed": round(float(n) * float(c.get("pump_capital_aed", 0.0)) + hose * L, 0),
                    "label": f"抽排 {int(n)} 台 12 寸 + 临时管 {L:,.0f} m",
                    "note": "雨峰期流量有限，主要用于重力够不到的低点"})
    return out


# ---------------------------------------------------------------- 水量平衡：塘和各点联算
def prepare(pts: pd.DataFrame, served: list[tuple[int, float]], cfg: dict) -> dict | None:
    """把服务范围内各点的过程线拆成 (净入流 e, 自然去水 n)，并算出无塘时的基线。

    这一步与方案包无关，每个塘位只做一次——同一个塘位后面要评十几个包。
    """
    P = params(cfg)
    dt = float(P["dt_min"]); te = float(P["horizon_min"])
    E, N, W, ids, dists = [], [], [], [], []
    t = None
    for j, d in served:
        row = pts.iloc[j]
        hyd = row.get("hydrograph")
        if not isinstance(hyd, list) or not hyd:
            continue
        t, V, _ = rp_grid(hyd, te, dt)
        e, n = decompose(t, V)
        E.append(e); N.append(n); W.append(float(row.get("priority") or 1.0))
        ids.append(str(row.get("fp_id"))); dists.append(float(d))
    if t is None or not E:
        return None
    E = np.asarray(E); N = np.asarray(N)
    base = []
    for k in range(len(E)):
        v, _ = rp_sim(t, E[k], N[k], [])                 # 用同一台模拟器重放基线，保证可比
        base.append(rp_kpi(t, v))
    # 世界模型（dtm_proxy）那套过程线只涨不退，自然去水恒为 0，基线就是整个视界都淹。
    # 这类点上任何能把水收走的塘都能拿到「省满 24 h」的满分——数字没错，但要标出来，
    # 不然会让人以为这些塘的效果比实际可信度更高。
    n_never = int(sum(1 for k in range(len(N)) if float(N[k].sum()) == 0.0))
    return {"t": t, "E": E, "N": N, "w": np.asarray(W), "ids": ids, "dist": np.asarray(dists),
            "base": base, "n_never_drains": n_never,
            "base_flood_h": float(sum(b["flood_h"] for b in base)),
            "base_w_flood_h": float(sum(w * b["flood_h"] for w, b in zip(W, base))),
            "need_m3": float(sum(b["peak_m3"] for b in base)),
            # 塘就坐在这个洼地上、天然库容已经被它自己的积水占掉的那部分，不能再算成新增库容
            "peak_local_m3": float(sum(b["peak_m3"] for b, d in zip(base, dists) if d <= 50.0))}


def route(prep: dict, volume_m3: float, area_m2: float, capacity_m3h: float, cfg: dict) -> dict:
    """塘和它服务的各点放在同一个时间循环里算。

    每一步：各点先扣自然去水，剩下的水按「塘里还空多少」和「进水能力」往塘里收。
    进水能力对所有服务点一视同仁地共享——塘只有一套进水设施。收不下的水留在原地，
    也就是那个点继续淹。
    """
    P = params(cfg)
    t = prep["t"]; E = prep["E"]; N = prep["N"]
    dt_h = (float(t[1] - t[0]) if len(t) > 1 else float(P["dt_min"])) / 60.0
    infil_full = float(P["infiltration_mm_h"]) / 1000.0 * float(area_m2 or 0.0)   # m³/h，满塘时
    rel = float(P["release_m3h"])
    m = E.shape[0]
    v = np.zeros(m); S = 0.0
    V = np.zeros_like(E); cap_t = np.zeros(len(t)); stor = np.zeros(len(t))
    for i in range(len(t)):
        avail = v / dt_h + E[:, i]
        tn = np.minimum(N[:, i], avail)
        rest = np.maximum(avail - tn, 0.0)
        room = max(volume_m3 - S, 0.0) / dt_h
        take = np.zeros(m)
        cap = min(float(capacity_m3h), room)                 # 进水设施能力与塘的剩余库容，取小
        tot = float(rest.sum())
        if cap > 0 and tot > 0:
            take = rest * min(1.0, cap / tot)                # 不够分就按各点的可取水量摊
        v = np.maximum(v + (E[:, i] - tn - take) * dt_h, 0.0)
        S = min(S + float(take.sum()) * dt_h, volume_m3)
        fill = (S / volume_m3) if volume_m3 > 0 else 0.0     # 渗透按实际水面折减，不按满塘算
        S = max(S - (infil_full * fill + rel) * dt_h, 0.0)
        V[:, i] = v; cap_t[i] = float(take.sum()); stor[i] = S
    nwin = max(int(round(float(P["peak_window_min"]) / (dt_h * 60.0))), 1)
    after = [rp_kpi(t, V[k]) for k in range(m)]
    return {"after": after, "peak_intake_m3h": float(cap_t.max() if len(cap_t) else 0.0),
            "captured_total_m3": float(cap_t.sum() * dt_h),
            "captured_in_peak_m3": float(cap_t[:nwin].sum() * dt_h),
            "peak_storage_m3": float(stor.max() if len(stor) else 0.0),
            "utilisation": float(stor.max() / volume_m3) if volume_m3 > 0 else 0.0,
            "flood_h": float(sum(a["flood_h"] for a in after)),
            "w_flood_h": float(sum(w * a["flood_h"] for w, a in zip(prep["w"], after)))}


def evaluate(prep: dict, vol: dict, conv: dict, cfg: dict) -> dict:
    r = route(prep, float(vol["volume_m3"]), float(vol["area_m2"]), float(conv["capacity_m3h"]), cfg)
    cost = float(vol["cost_aed"]) + float(conv["cost_aed"])
    return {"volume_m3": vol["volume_m3"], "dig_m": vol["dig_m"], "area_m2": vol["area_m2"],
            "excavate_m3": vol["excavate_m3"],
            "conveyance": conv["label"], "conveyance_kind": conv["kind"],
            "capacity_m3h": conv["capacity_m3h"], "cost_aed": round(cost, 0),
            "captured_total_m3": round(r["captured_total_m3"], 0),
            "captured_in_peak_m3": round(r["captured_in_peak_m3"], 0),
            "peak_storage_m3": round(r["peak_storage_m3"], 0),
            "utilisation": round(r["utilisation"], 3),
            "flood_h_base": round(prep["base_flood_h"], 2), "flood_h_after": round(r["flood_h"], 2),
            "flood_h_saved": round(prep["base_flood_h"] - r["flood_h"], 2),
            "w_flood_h_saved": round(prep["base_w_flood_h"] - r["w_flood_h"], 1),
            "n_points": int(len(prep["ids"])), "conveyance_len_m": conv.get("len_m", 0.0),
            "peak_intake_m3h": round(r["peak_intake_m3h"], 0),
            "n_never_drains": int(prep["n_never_drains"]), "need_m3": round(prep["need_m3"], 0),
            "footprint_m2": vol.get("footprint_m2", 0.0), "capped_by_land": bool(vol.get("capped")),
            "points": list(prep["ids"]),
            "label": f"{vol['label']} + {conv['label']}"}


def packages(sites: pd.DataFrame, pts: pd.DataFrame, assign: dict, cfg: dict) -> dict:
    """每个塘位的全部方案包：容积档 × 进水方式档，逐个演算。

    天然库容里已经被塘边那些点自己的积水占掉的部分要扣掉——不扣就会把「本来就存在那儿的
    水」当成新增库容算两遍。
    """
    P = params(cfg)
    min_saved = float(P["min_saved_h"])
    out: dict[int, list[dict]] = {}
    for i, served in assign.items():
        prep = prepare(pts, served, cfg)
        if prep is None:
            continue
        site = sites.iloc[i]
        free = max(float(site.get("natural_m3") or 0.0) - prep["peak_local_m3"], 0.0)
        convs = conveyance_options(prep["dist"], cfg)         # 造价跟服务点到塘的距离走
        gs = []
        for v in volume_options(site, free, prep["need_m3"], cfg):
            for c in convs:
                g = evaluate(prep, v, c, cfg)
                if g["flood_h_saved"] < min_saved:
                    continue
                g["gid"] = len(gs); gs.append(g)
        if gs:
            out[i] = gs
    return out


# ---------------------------------------------------------------- 选方案
def solve(sites: pd.DataFrame, pkgs: dict, cfg: dict, budget_aed: float | None = None) -> dict:
    """每个塘位最多选一个方案包，预算内让加权积水时长降得最多。

        min  Σ_{s,g} [ −w_saved[s,g] + cost[s,g]/V ] · y[s,g]
        s.t. Σ_g y[s,g] ≤ 1        每个塘位最多一个方案
             Σ cost·y ≤ B          预算（给了才加这行）
             y ∈ {0,1}

    服务范围在 assign_points 里已经做成互不重叠的，所以这里不需要防重复计收益的约束。
    """
    P = params(cfg)
    V = float(P["value_aed_per_priority_hour"])
    keys = [(s, g["gid"]) for s, gs in pkgs.items() for g in gs]
    if not keys:
        return {"chosen": [], "status": "没有候选方案", "n_vars": 0, "ms_milp": 0.0}
    ix = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    c = np.zeros(n); money = np.zeros(n)
    for (s, gid), i in ix.items():
        g = pkgs[s][gid]
        c[i] = -float(g["w_flood_h_saved"]) + float(g["cost_aed"]) / V
        money[i] = float(g["cost_aed"])
    rows, cols, vals, lo, hi = [], [], [], [], []
    ri = 0
    for s, gs in pkgs.items():
        for g in gs:
            rows.append(ri); cols.append(ix[(s, g["gid"])]); vals.append(1.0)
        lo.append(-np.inf); hi.append(1.0); ri += 1
    if budget_aed is not None:
        for i in range(n):
            if money[i]:
                rows.append(ri); cols.append(i); vals.append(float(money[i]))
        lo.append(-np.inf); hi.append(float(budget_aed)); ri += 1
    A = coo_matrix((vals, (rows, cols)), shape=(ri, n)).tocsr()
    t0 = time.perf_counter()
    r = milp(c, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n),
             bounds=Bounds(np.zeros(n), np.ones(n)),
             options={"time_limit": float(P["milp_time_limit_s"])})
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if r.x is None:
        return {"chosen": [], "status": str(r.message), "ms_milp": ms, "n_vars": n}
    x = np.round(r.x).astype(int)
    chosen = [{"site": {k: v for k, v in sites.iloc[s].items()}, **pkgs[s][gid]}
              for (s, gid), i in ix.items() if x[i] == 1]
    chosen.sort(key=lambda z: -z["w_flood_h_saved"])
    return {"chosen": chosen, "status": "最优" if r.success else str(r.message), "ms_milp": ms,
            "n_vars": n, "objective": round(float(r.fun), 2)}


def summarise(res: dict, cfg: dict) -> dict:
    ch = res.get("chosen") or []
    tot_cost = sum(float(z["cost_aed"]) for z in ch)
    tot_saved = sum(float(z["flood_h_saved"]) for z in ch)
    by_kind: dict[str, int] = {}
    for z in ch:
        by_kind[z["conveyance_kind"]] = by_kind.get(z["conveyance_kind"], 0) + 1
    return {"n_ponds": len(ch), "cost_aed": round(tot_cost, 0),
            "volume_m3": round(sum(float(z["volume_m3"]) for z in ch), 0),
            "excavate_m3": round(sum(float(z["excavate_m3"]) for z in ch), 0),
            "captured_in_peak_m3": round(sum(float(z["captured_in_peak_m3"]) for z in ch), 0),
            "flood_h_saved": round(tot_saved, 1),
            "w_flood_h_saved": round(sum(float(z["w_flood_h_saved"]) for z in ch), 1),
            "aed_per_flood_h": round(tot_cost / tot_saved, 0) if tot_saved > 0 else None,
            "conveyance_mix": by_kind,
            "points_served": sum(int(z["n_points"]) for z in ch),
            "points_never_drains": sum(int(z.get("n_never_drains") or 0) for z in ch),
            "footprint_m2": round(sum(float(z.get("footprint_m2") or 0.0) for z in ch), 0),
            "n_capped_by_land": sum(1 for z in ch if z.get("capped_by_land")),
            "peak_window_min": params(cfg)["peak_window_min"]}


# ---------------------------------------------------------------- 入口
def enrich_points(pts: pd.DataFrame, hotspots: pd.DataFrame | None = None) -> pd.DataFrame:
    """给积水点补上地面标高（判自流用）和片区名（筛选用），都从热点编号连过去。"""
    pts = pts.copy()
    key = "client_hotspot_id" if "client_hotspot_id" in pts.columns else "fp_id"
    t = terrain_table()
    if len(t) and "dtm_z" not in pts.columns:
        pts = pts.merge(t[["hotspot_id", "dtm_z"]].drop_duplicates("hotspot_id").rename(columns={"hotspot_id": key}),
                        on=key, how="left")
    if hotspots is not None and len(hotspots) and "area" in hotspots.columns and "area" not in pts.columns:
        pts = pts.merge(hotspots[["hotspot_id", "area"]].drop_duplicates("hotspot_id").rename(columns={"hotspot_id": key}),
                        on=key, how="left")
    return pts


def plan(ctx: dict, cfg: dict, budget_aed: float | None = None, zone: str | None = None,
         log=lambda *_: None) -> dict:
    """一次完整规划：候选塘位 → 点归属 → 方案包 → MILP 选方案。"""
    t0 = time.perf_counter()
    hs = ctx.get("hotspots")
    pts = enrich_points(ctx["pts0"], hs)
    if zone:
        z = str(zone).lower()
        m = pts["fp_id"].astype(str).str.lower().str.contains(z, regex=False)
        if "area" in pts.columns:
            m = m | pts["area"].astype(str).str.lower().str.contains(z, regex=False, na=False)
        pts = pts[m].reset_index(drop=True)
    sites = candidate_sites(cfg, hs)
    if zone and len(sites) and "area" in sites.columns:
        sites = sites[sites["area"].astype(str).str.lower().str.contains(str(zone).lower(), regex=False, na=False)]
        sites = sites.reset_index(drop=True)
    n_cli = int((sites["origin"] == "client_model").sum()) if len(sites) else 0
    n_nat = int((sites["origin"] == "terrain").sum()) if len(sites) else 0
    log(f"候选塘位 {len(sites)} 个（客户模型 {n_cli}、天然洼地 {n_nat}），积水点 {len(pts)} 个")
    assign = assign_points(sites, pts, cfg)
    log(f"能服务到点的塘位 {len(assign)} 个，覆盖 {sum(len(v) for v in assign.values())} 个积水点")
    pkgs = packages(sites, pts, assign, cfg)
    log(f"方案包 {sum(len(v) for v in pkgs.values())} 个")
    res = solve(sites, pkgs, cfg, budget_aed)
    res["summary"] = summarise(res, cfg)
    res.update({"n_sites": len(sites), "n_sites_client": n_cli, "n_sites_terrain": n_nat,
                "n_sites_serving": len(assign), "n_points": len(pts),
                "n_points_covered": sum(len(v) for v in assign.values()),
                "n_packages": sum(len(v) for v in pkgs.values()),
                "budget_aed": budget_aed, "zone": zone,
                "ms": round((time.perf_counter() - t0) * 1000)})
    return res


def main(argv=None):
    from . import db, artifactcache as ac
    ap = argparse.ArgumentParser(description="第二道防线：滞蓄塘选址、定容与进水方式")
    ap.add_argument("--rp", default="10cc", help="情景 key（要盘上已有该情景的上下文缓存）")
    ap.add_argument("--budget", type=float, default=None, help="预算 AED")
    ap.add_argument("--zone", default=None, help="只看某个片区")
    ap.add_argument("--assign", choices=["lowest", "nearest"], default=None, help="点归给哪个塘")
    ap.add_argument("--top", type=int, default=15, help="打印前几个")
    ap.add_argument("--out", default=None, help="结果写到哪个 json")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = db.load_config(a.config)

    cache = ac.Cache(cfg)
    ctx = cache.get(f"ctx_{a.rp}")
    if ctx is None:
        print(f"盘上没有情景 {a.rp} 的上下文（缓存目录 {cache.dir.name}）。")
        print("先启动一次服务（python -m swbrain.serve）让它把该情景算出来，或换 --rp。")
        return 2

    if a.assign:
        cfg = {**cfg, "ponds": {**(cfg.get("ponds") or {}), "assign_by": a.assign}}
    res = plan(ctx, cfg, budget_aed=a.budget, zone=a.zone, log=print)
    s = res["summary"]
    print(f"\n求解{res['status']}：{res['n_packages']} 个方案包 / {res['n_vars']} 个 0-1 变量，"
          f"MILP {res['ms_milp']:.0f} ms，全程 {res['ms']:,} ms")
    if not s["n_ponds"]:
        print("没选出方案。")
        return 0
    print(f"\n选中 {s['n_ponds']} 个塘：库容合计 {s['volume_m3']:,.0f} m³"
          f"（其中开挖 {s['excavate_m3']:,.0f} m³），造价 {s['cost_aed']:,.0f} AED")
    print(f"  雨峰 {s['peak_window_min']:.0f} min 内拦下 {s['captured_in_peak_m3']:,.0f} m³，"
          f"覆盖 {s['points_served']} 个积水点，积水时长少 {s['flood_h_saved']:,.1f} h")
    if s.get("aed_per_flood_h"):
        print(f"  单位效果成本 {s['aed_per_flood_h']:,.0f} AED / 积水小时")
    print(f"  进水方式：{s['conveyance_mix']}，占地合计 {s['footprint_m2']:,.0f} m²"
          + (f"，其中 {s['n_capped_by_land']} 个受占地上限" if s["n_capped_by_land"] else ""))
    nv = s["points_never_drains"]
    if nv:
        print(f"  ⚠ 被服务的 {s['points_served']} 个点里有 {nv} 个来自世界模型（过程线只涨不退，"
              f"基线整个视界都淹），这些点上的「省时长」是上限值，不是实测值")
    print(f"\n前 {a.top} 个：")
    print(f"{'塘位':<24}{'点':>3}{'库容 m³':>11}{'进水 m³/h':>11}"
          f"{'雨峰拦截 m³':>13}{'利用率':>7}{'省时长 h':>10}{'造价 AED':>12}  方案")
    print("-" * 122)
    for z in res["chosen"][:a.top]:
        print(f"{str(z['site']['site_id'])[:23]:<24}{z['n_points']:>3}{z['volume_m3']:>11,.0f}"
              f"{z['capacity_m3h']:>11,.0f}{z['captured_in_peak_m3']:>13,.0f}"
              f"{z['utilisation'] * 100:>6.0f}%{z['flood_h_saved']:>10,.1f}{z['cost_aed']:>12,.0f}  {z['label']}")
    if a.out:
        p = Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\n写到 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
