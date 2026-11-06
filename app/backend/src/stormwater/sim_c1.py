"""把「C1 热点群」四态一二维交付抽成仿真系统认的交付形状，再登记成一个多态仿真任务。

交付是 `data/temp260929/C1_61mm2h/`（2026-09-29，算力侧「C1 热点群 · 61 mm / 2 h」，在线报告
https://claude.ai/artifact/NZKLeppNDkvRx593PSjrYJ ）：

    C1_61mm2h/
        readme.md  计算设置.md                          四态口径与全部输入参数、局限
        L1_现状管网+现状泵站/一二维/L1_二维水深帧与最大水深.npz   875,510 个三角单元：xy / area / elev / hmax /
                                                       frames(72) / uh / vh / t_h / rain_mask
        L1_…/一二维/L1_耦合运行记录.json                逐 5 min 水量、1D↔2D 交换累计、直接落地的雨
        L1_…/一二维/耦合的一维/*.rpt|inp                与二维同进程耦合的 SWMM（9,211 个节点）
        L1_…/设施.csv  摘要.md                          这一态包含的设施（WGS84）与指标
        L2_加OP-02移动泵/  L2+planned_Parsons方案/  L2+suggested_转输与地面取水泵/   同 L1 的结构
        统计值/C1统计值.xlsx                            各热点与全域的积水统计（每项带口径）

npz 与全市（`sim_city.py`）同一个格式，所以抽取走同一套：非结构三角网 → 按面积平均到规则格，最大水深 / 积水面积按三角
单元统计，地表**直接受雨**（二维直接降雨 659,883 m³），二维从 0 h 起算、帧 t = 5 … 360 min。和全市不同的地方：

- 计算域只有 42.8 km²（8 个热点汇水区外扩 300 m），落图格网取 **15 m**（= 模型标称网格，格内约 4–5 个三角单元），
  不是 250 m。
- npz 带 `uh / vh`（单宽流量），格内最大流速照 `sim_sww` 的口径（Σ动量×面积 / Σ水深×面积）写进最大水深图层的
  `maximum_speed_m_s`，`sim_ponding` 不再亮 `no_speed`。
- 四态都是**真算**的（没有 ≡ L1 的硬链，`same_as` 为空）：
  L1 现状管网 + 6 座现状泵站 → L2 加 OP-02 的 23 台移动泵 → L2+planned Parsons 方案（27 座固定泵站 + 12 座 1 m 围堤塘）
  → L2+suggested 在 L2+planned 上加转输 / 地面取水泵。
  L2+planned 用固定泵站取代了同址的 22 台移动泵（只留 MP-16），所以它的 `parent` 是 L1 不是 L2；
  「新增调蓄」（储水设施落图 / 效果分析）都按这个父态比。
- 没有 `五态统计值.csv` 那样的逐态对账表，不写 `analysis/state_stats.csv`（`sim_perf` 缺了不报错，只是没有
  「系统储水能力」一行的交付原值）。
- 耦合 SWMM 的 .rpt / .inp 各四十 / 十七 MB，硬链到输出目录（同卷零拷贝），不另拷。

任务记最高一道（第二道规划），操作人按用户要求记 AR（`simulation.PROTECTED_OPERATORS`，不能删）。

用法：

    PYTHONPATH=src .venv/bin/python -m stormwater.sim_c1 import data/temp260929/C1_61mm2h --by AR
    PYTHONPATH=src .venv/bin/python -m stormwater.sim_c1 extract data/temp260929/C1_61mm2h   # 只抽不登记
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from .i18n import EXTENT_EN, en_label
from .sim_city import _extract_grid, _link, _overflow_series
from .sim_sww import (DATA_ROOT, DELIVERY_WET_M, LEGACY_RES_M, OUT_ROOT, WET_M, SwwError, _cell_polys, _dump,
                      _node_coords, _one, _outfalls, is_projected, norm_geom, output_key,
                      parse_delivery_name)

SCHEMA = "flood_management.c1_state_delivery.v1"
LAND_CELL_M = 15.0        # 落图格网的下限：模型网格 ≥ 15 m 时 = 模型标称网格，更细的（5 m）仍落 15 m，免得逐帧 geojson 爆炸
REPORT_URL = "https://claude.ai/artifact/NZKLeppNDkvRx593PSjrYJ"
EXTENT = "C1"

#: 态 → (子目录前缀, 防线, 中文名)；中文名改了要同步 i18n.EN
STATES = {
    "L1": ("L1_", "line1", "Existing network + existing pump stations"),
    "L2": ("L2_", "line2_current", "Plus OP-02 mobile pumps"),
    "L2+planned": ("L2+planned", "line2_planned", "Parsons scheme"),
    "L2+suggested": ("L2+suggested", "line2_planned", "Transfer and surface-intake pumps"),
}
COLUMNS = {"L1": "line1", "L2": "line2_current", "L2+planned": "line2_planned",
           "L2+suggested": "line2_suggested"}
PARENT = {"L1": None, "L2": "L1", "L2+planned": "L1", "L2+suggested": "L2+planned"}

#: 每态一句话口径（进 claim_boundary）
_NOTE = {
    "L1": "客户 GDB 重建的现状管网 + 6 座现状泵站",
    "L2": "L1 + Parsons OP-02 的 23 台 12 英寸移动泵（雨前布设，排到下游不溢流的井）",
    "L2+planned": "L1 + Parsons PDF 版方案：27 座固定泵站（只开主用）+ 12 座 1 m 围堤塘（443,274 m³），保留 MP-16",
    "L2+suggested": "L2+planned + 按 L2+planned 残余积水设计的转输 / 地面取水泵（挡水土堤方案已弃用）",
}


#: C1 自己的局限（计算设置.md），别的片区不套用
_C1_ONLY = ("ADM-90 的地表汇水区碰到窗口边未收敛、计算域外的来水算不进，"
            "计算域外的管网只算一维、冒出的水记为损失；地道线性调蓄未建。")


def _label(root: Path, state: str) -> str:
    """态的展示名。C1 用 `STATES` 里已翻译的固定名；别的片区各态的措施不一定同名（Yas / Al Bateen / Bani Yas 的 L2 是什么
    交付没说死），所以用交付目录名里前缀后面的那段，不替交付方编名字。"""
    if extent_of(root) == "C1":
        return state_label(state)
    tail = _state_dir(root, state).name.split("_", 1)
    return f"{state} {en_label(tail[1])}" if len(tail) > 1 else state


#: RD-A 全套成果各态的口径（来自交付 README：设施逐级累加，每一态含前面各态的全部设施）
_RDA_NOTE = {
    "L1": "客户 SewerGEMS RD-A 模型（换算公制）现状管网 + 3 座现状泵站",
    "L2": "L1 + 雨前预置的 15 台移动泵（热点表 Plan A）",
    "L2+planned": "L2 + 客户计划的 2 台泵（客户 SewerGEMS 情景 Temporary Intervention）",
    "L2+suggested": "L2+planned + 推荐方案（当前方案瘦身版，2026-10-27 交付）：53 项措施（地下调蓄池 / 泵站 → 新建排海口 / 地面取水泵站），"
                    "位置已按 WA-726 与空间约束筛过，造价为占位单价、不是概算",
}


def _state_note(state: str, dir_name: str, extent: str = "C1") -> str:
    """态的口径一句话。L2+suggested 的 `_NOTE` 是 v2（转输 / 地面取水泵）的写法；交付换成 v3（按塘余量分配）后
    措施清单变了，这里不沿用 v2 的描述，只写到父态与方案名，细节让读者去看交付的计算设置.md。"""
    if extent == "RDA":
        return _RDA_NOTE[state]
    if extent != "C1":
        return f"交付目录 {dir_name}，措施清单以交付的计算设置.md 为准，本系统未复核"
    if state == "L2+suggested" and re.search(r"v([3-9]|\d{2,})", dir_name):
        return f"L2+planned + 交付 {dir_name.split('_', 1)[-1]}（措施清单以交付的计算设置.md 为准，本系统未复核）"
    return _NOTE[state]


def state_label(state: str) -> str:
    return f"{state} {STATES[state][2]}"


def _state_dir(root: Path, state: str) -> Path | None:
    """按前缀找态目录（`L2+suggested` 同时命中 `L2+suggested_转输…` 与 `L2+suggested_v3_按塘余量分配`）。
    没有返回 None；同一态有两个目录说不清用哪个，报错而不是悄悄挑一个。"""
    hits = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(STATES[state][0]))
    if len(hits) > 1:   # 交付里带「中间步」的是过程态（如 L2+suggested_v4中间步），不参与登记
        hits = [p for p in hits if "中间步" not in p.name] or hits
    if len(hits) > 1:
        raise SwwError(f"{root} 下 {state} 有多个目录：{[p.name for p in hits]}，只留一个")
    return hits[0] if hits else None


def present_states(root: Path) -> list[str]:
    """交付里实际有目录的态（按 STATES 的先后）。"""
    return [st for st in STATES if _state_dir(root, st) is not None]


#: npz 文件名里的态记号（`work/c2d/<名>_<记号>_2d.npz`；服务器脚本里 L2P = L2+planned、L2S3 = L2+suggested）
C2D_KEYS = {"L1": "L1", "L2": "L2", "L2+planned": "L2P", "L2+suggested": "L2S3"}


class StateFiles(NamedTuple):
    npz: Path
    rec: Path
    rpt: Path
    inp: Path
    layout: str          # "一二维"（C1 交付：npz 随交付）| "模型版"（Mussafah / 区域交付：npz 在 work/c2d）


def find_c2d(root: Path) -> Path | None:
    """交付目录往上找 `<主机>/work/c2d`（NAS 形状 `result/<主机>/runtime/out/交付/<交付>` 与 `result/<主机>/work/c2d`）。"""
    for p in root.parents:
        if (p / "work" / "c2d").is_dir():
            return p / "work" / "c2d"
    return None


def _find_npz(c2d: Path | None, state: str, rec: dict[str, Any], inp: Path | None) -> Path:
    """在 work/c2d 里找这一态的 npz。候选名依次：运行记录的 tag、交付里 .inp 的文件名（服务器脚本把
    `<名>_<态>.inp` 原名拷进交付）；都不在时按 `*_<态记号>_2d.npz` 唯一匹配，多个就要人指定（--c2d 指到只含这一场的目录）。"""
    if c2d is None or not c2d.is_dir():
        raise SwwError("交付里没有 npz，且找不到 work/c2d（用 --c2d 指到存放 *_2d.npz 的目录）")
    stems = [t for t in (rec.get("tag"), inp.stem if inp else None) if t]
    for stem in stems:
        p = c2d / f"{stem}_2d.npz"
        if p.is_file():
            return p
    hits = sorted(p for p in c2d.glob(f"*_{C2D_KEYS[state]}_2d.npz") if not p.name.startswith("._"))
    if len(hits) == 1:
        return hits[0]
    raise SwwError(f"{c2d} 下找不到 {state} 的 npz（试过 {[s + '_2d.npz' for s in stems]}；"
                   f"按 *_{C2D_KEYS[state]}_2d.npz 匹配到 {len(hits)} 个）")


def state_files(root: Path, state: str, c2d: Path | None = None) -> StateFiles:
    """一个态要的四个文件。两种交付形状：
    - `<态>/一二维/`（C1，`export_delivery_c1.py`）：运行记录 / 耦合的一维 / npz 都在里面；npz 缺了（`--no_big` 导出）退回 work/c2d；
    - `<态>/一维/` + `<态>/二维/`（Mussafah 模型版 `ms_post.py`、区域 `rg_post.py`）：二维只有 .sww，npz 在 work/c2d。"""
    sd = _state_dir(root, state)
    if sd is None:
        raise SwwError(f"{root} 下没有 {state} 的目录")
    if (sd / "一二维").is_dir():
        d2, layout = sd / "一二维", "一二维"
        # C1 的 SWMM 在「耦合的一维/」里；RDA 全套成果把 .inp / .rpt 直接放在「一二维/」里
        d1 = d2 / "耦合的一维" if (d2 / "耦合的一维").is_dir() else d2
    elif (sd / "二维").is_dir() and (sd / "一维").is_dir():
        d2, d1, layout = sd / "二维", sd / "一维", "模型版"
    else:
        raise SwwError(f"{sd.name} 里既没有「一二维/」也没有「一维/ + 二维/」")
    rec_p = _one(d2, "*_耦合运行记录.json" if any(d2.glob("*_耦合运行记录.json")) else "*_耦合交换记录.json")
    rec = json.loads(rec_p.read_text(encoding="utf-8"))
    rpt, inp = _one(d1, "*.rpt"), _one(d1, "*.inp")
    npzs = sorted(p for p in d2.glob("*_二维水深帧与最大水深.npz") if not p.name.startswith("._"))
    if not npzs:        # RDA 全套成果：<名>_<态记号>_二维结果.npz（同一组键）
        npzs = sorted(p for p in d2.glob("*_二维结果.npz") if not p.name.startswith("._"))
    if not npzs and layout == "模型版":
        # 区域交付把 npz 直接放进 <态>/二维/，文件名是 <运行记录 tag>_2d.npz（如 AD_MI8_M61H1_L1_2d.npz）
        npzs = sorted(p for p in d2.glob("*_2d.npz") if not p.name.startswith("._"))
    if len(npzs) == 1:
        npz = npzs[0]
    elif npzs:
        raise SwwError(f"{d2} 下 *_二维水深帧与最大水深.npz 有 {len(npzs)} 个，需恰好一个")
    else:
        npz = _find_npz(c2d or find_c2d(root), state, rec, inp if layout == "模型版" else None)
    return StateFiles(npz, rec_p, rpt, inp, layout)


def _state_record(root: Path, state: str) -> dict[str, Any] | None:
    sd = _state_dir(root, state)
    for d2 in (sd / "一二维", sd / "二维"):
        recs = sorted(p for p in [*d2.glob("*_耦合运行记录.json"), *d2.glob("*_耦合交换记录.json")]
                      if not p.name.startswith("._")) if d2.is_dir() else []
        if recs:
            return json.loads(recs[0].read_text(encoding="utf-8"))
    return None


def delivery_res(root: Path) -> float:
    """模型网格（m）：取各态耦合运行记录的 `res_m`，各态不一致报错；记录都没写则取目录名里标的，再退回 15 m。"""
    found = set()
    for st in present_states(root):
        rec = _state_record(root, st)
        if rec:
            v = rec.get("res_m")
            if v:
                found.add(float(v))
    if len(found) > 1:
        raise SwwError(f"{root.name} 各态的 res_m 不一致：{sorted(found)}")
    return found.pop() if found else (parse_delivery_name(root.name)[3] or LEGACY_RES_M)


#: 目录名里区分「同一片区的不同口径」的标记，不算范围名的一部分
_VARIANT_TAGS = ("模型版", "客户单元", "设计暴雨")


def extent_of(root: Path) -> str:
    """任务的范围名：目录名里片区段去掉口径标记（`C1_模型版_61mm2h_5m` → `C1`，`Mussafah_00_模型版_61mm2h` → `Mussafah_00`，
    `YAS_61mm2h` → `YAS`）。"""
    toks = [t for t in parse_delivery_name(root.name)[0].split("_") if t not in _VARIANT_TAGS]
    name = "_".join(toks) or EXTENT
    return EXTENT_EN.get(name, name)


def extract_state(root: Path, state: str, out: Path, c2d: Path | None = None) -> dict[str, Any]:
    region, depth_mm, dur_h, _ = parse_delivery_name(root.name)
    extent = extent_of(root)
    label = _label(root, state)
    sd = _state_dir(root, state)
    if sd is None:
        raise SwwError(f"{root} 下没有 {state} 的目录")
    fl = state_files(root, state, c2d)
    npz, rpt, inp = fl.npz, fl.rpt, fl.inp
    rec = json.loads(fl.rec.read_text(encoding="utf-8"))
    acc = rec.get("acc_m3") or {}
    direct_rain = float(acc.get("rain") or 0) > 0       # 地表直接受雨（C1 / 全市）；Mussafah 式为 0：来水全是井口溢流

    res = float(rec.get("res_m") or LEGACY_RES_M)
    g = _extract_grid(npz, max(res, LAND_CELL_M))
    out.mkdir(parents=True, exist_ok=True)
    (out / "temporal_snapshots").mkdir(exist_ok=True)
    nc, cs = g["nc"], g["cs"]
    cell_a = cs * cs

    ids = np.where(g["maxd"] >= WET_M)[0]
    feats = []
    for cid, ring in zip(ids.tolist(), _cell_polys(g, ids)):
        props = {
            "cell_id": cid,
            "depth_m": round(float(g["maxd"][cid]), 4),
            "maximum_depth_m": round(float(g["maxd"][cid]), 4),
            "maximum_depth_time_minutes": round(float(g["peak_min"][cid]), 2),
            "final_depth_m": round(float(g["final"][cid]), 4),
            "model_cell_size_m": cs,
        }
        if g["maxsp"] is not None:
            props["maximum_speed_m_s"] = round(float(g["maxsp"][cid]), 3)
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props})
    _dump(out / "maximum_depth_wgs84.geojson",
          {"type": "FeatureCollection", "name": f"{region}_{state}_maximum_depth", "features": feats})

    snaps = []
    for i, (ts, d) in enumerate(g["frames"]):
        w = np.where(d >= WET_M)[0]
        name = f"temporal_snapshots/surface_depth_t{i:03d}.geojson"
        _dump(out / name, {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None,
                          "properties": {"cell_id": int(c), "depth_m": round(float(d[c]), 4)}} for c in w],
            "metadata": {"time_index": i, "time_seconds": ts, "time_minutes": ts / 60},
        })
        snaps.append({"index": i, "time_seconds": ts, "time_minutes": ts / 60, "path": name,
                      "feature_count": int(len(w))})
    _dump(out / "temporal_snapshots" / "manifest.json", {"schema": SCHEMA, "snapshots": snaps})
    snap_mb = sum(f.stat().st_size for f in (out / "temporal_snapshots").glob("surface_depth_t*.geojson")) / 2**20
    print(f"  {state}：逐帧 geojson {len(snaps)} 个、共 {snap_mb:,.0f} MB（落图格 {cs:g} m）")
    _dump(out / "surface_series.json", {
        "schema": SCHEMA, "wet_threshold_m": DELIVERY_WET_M,
        "note": "逐 5 min 帧、按三角单元（npz）：wet_area_m2 = 水深 ≥ 阈值的单元面积之和，max_depth_m = 该帧单元最大水深，"
                "volume_m3 = Σ 水深 × 单元面积；time_minutes 从降雨起算。" + ("地表直接受雨，面积含雨水膜" if direct_rain else "地表不直接受雨"),
        **g["series"],
        "overflow": _overflow_series(rec),
    })

    has = g["has"].reshape(g["nr"], nc)
    np.savez_compressed(
        out / "terrain_grid.npz",
        values=np.nan_to_num(g["dem"], nan=0.0).reshape(g["nr"], nc),
        transform=np.array([cs, 0.0, g["x0"], 0.0, -cs, g["ytop"]]),
        land_mask=has, permanent_water_mask=np.zeros_like(has), source=np.array("sww"),
    )

    coords = _node_coords(inp)
    binds = []
    for k, (nid, (xx, yy)) in enumerate(coords.items()):
        c = int(np.floor((xx - g["x0"]) / cs))
        r = int(np.floor((g["ytop"] - yy) / cs))
        if 0 <= r < g["nr"] and 0 <= c < nc:
            binds.append(f'{{"swmm_node_id": {json.dumps(nid)}, "swmm_node_index": {k}, "anuga_cell_index": {r * nc + c}}}')
    (out / "node_cell_binding.json").write_text(
        '{"schema": "' + SCHEMA + '", "note": "' + f"节点坐标取耦合 SWMM 的 [COORDINATES]，落到 {cs:g} m 格"
        + '", "bindings": [\n' + ",\n".join(binds) + "\n]}\n", encoding="utf-8")
    _link(rpt, out / "swmm_coupled.rpt")
    _link(inp, out / "coupled_scenario.inp")

    outf = _outfalls(inp)
    _dump(out / "scenario.json", {
        "scope": "hotspot_cluster",
        # 有管网覆盖的地面按客户汇水区 CN 82 / Tc 30 min 产流注入一维；离井 > 46 m 的地面净雨直接下在二维上
        "spatial_pattern": "Client catchments + direct rain on 2D" if direct_rain else "Per-node inflows (client model)",
        "rainfall_profile": {"profile_id": f"R{depth_mm:g}D{dur_h:g}",
                             "provenance": {"temporal_assumption": "client 2 h design storm"}},
        "outfall_mode": "free",
        "outfall_level_m": None,
        "outfalls": [{"name": o[0], "type": o[1], "stage_m": o[2]} for o in outf],
        "start_time": None,
    })

    acc = rec.get("acc_m3") or {}
    summary = {
        "schema": SCHEMA,
        "run_id": f"{root.name}/{state}",
        "scenario_label": f"{region} · {depth_mm:g} mm / {dur_h:g} h · {label}",
        "state": state,
        "status": "completed",
        "solver": "ANUGA 2D + SWMM 5.2",
        "claim_boundary": (
            f"{label}：{_state_note(state, sd.name, extent)}。SWMM 与 ANUGA 双向耦合（{rec.get('dt_c', 60):g} s 交换），"
            f"非结构三角网 {g['n_tri']:,} 个单元（模型网格 {res:g} m）、{'5 m ' if extent == 'C1' else ''}DTM 原值，"
            f"计算域 {g['domain_m2'] / 1e6:.1f} km²（{'热点汇水区外扩、' if extent == 'C1' else ''}自由出流），"
            f"演算 0 → {g['end_min'] / 60:g} h；"
            + ("离井 > 46 m 的地面直接受雨。" if direct_rain else "地表不直接受雨，来水全是井口溢流。")
            + "糙率全域未分下垫面；交付方未标定、未经工程认可。"
            + (_C1_ONLY if extent == "C1" else "其余口径与局限见交付的计算设置.md。")
            + f"页面格网是三角单元按面积平均到 {cs:g} m，最大水深 / 积水面积按三角单元统计。"
            + (f"本态交付目录 {sd.name}。" if "_v" in sd.name else "")
        ),
        "source": {"delivery": str(root.relative_to(DATA_ROOT)) if DATA_ROOT in root.parents else str(root),
                   "state_dir": sd.name, "native_npz": npz.name, "coupling_record": rec.get("tag"),
                   "report_url": REPORT_URL if extent == "C1" else None},
        "domain": {
            "bounds_epsg32640": g["bounds"],
            "area_m2": round(g["domain_m2"], 1),
            "cell_size_m": cs,
            "rectangular_cells": g["nr"] * nc,
            "active_land_cells": int(g["has"].sum()),
            "triangle_count": g["n_tri"],
            "coupling_start_minutes": 0.0,
            "simulation_duration_minutes": g["end_min"],
            "simulation_duration_hours": g["end_min"] / 60,
            "output_step_minutes": 5.0,
        },
        "results": {
            "maximum_depth_m": round(g["tri_hmax"], 4),
            "maximum_depth_xy_epsg32640": g["tri_hmax_xy"],
            "peak_inundated_area_m2": round(g["tri_wet_max_m2"], 1),
            "peak_inundated_area_minutes": g["tri_wet_max_min"],
            "peak_inundated_threshold_m": DELIVERY_WET_M,
            "maximum_depth_cell_m": round(float(g["maxd"].max()), 4),
            "maximum_depth_triangle_m": round(g["tri_hmax"], 4),
            "inundated_cells_ge_0_01m": int(len(ids)),
            "inundated_area_ge_0_01m2": float(len(ids) * cell_a),
            "inundated_area_ge_0_05m2": float((g["maxd"] >= 0.05).sum() * cell_a),
            "final_inundated_cell_count_ge_0_01m": int((g["final"] >= WET_M).sum()),
            "final_inundated_area_ge_0_01m2": float((g["final"] >= WET_M).sum() * cell_a),
            # 交付口径（三角单元），只为与耦合记录 / 统计值对账，页面卡片不用
            "delivery_ever_wet_area_ge_0_02m2": round(g["tri_ever"][0.02], 1),
            "delivery_max_area_ge_0_10m2": round(g["tri_ever"][0.10], 1),
            "delivery_max_area_ge_0_30m2": round(g["tri_ever"][0.30], 1),
            "delivery_max_area_ge_0_80m2": round(g["tri_ever"][0.80], 1),
            "delivery_wet_record_max_m2": rec.get("max_wet_m2"),
            "peak_surface_volume_m3": g["tri_vol_max_m3"],
            "final_surface_volume_m3": g["tri_vol_end_m3"],
            "minimum_published_depth_m": WET_M,
        },
        "coupling": {
            "coupling_mode": "bidirectional",
            "coupled_node_count": rec.get("n_nodes"),
            "exchange_step_seconds": rec.get("dt_c"),
            "total_swmm_to_anuga_m3": acc.get("1to2"),
            "total_anuga_to_swmm_m3": acc.get("2to1"),
            "boundary_outflow_m3": rec.get("boundary_out_m3"),
        },
        "model_configuration": {
            "coupling_mode": "bidirectional",
            "return_period_years": None,
            "rainfall_total_mm": depth_mm,
            "rainfall_duration_minutes": dur_h * 60,
            "exchange_quantity": "node overflow to surface and surface return to node (bidirectional)",
            "dynamic_head_feedback": True,
            "simulation_duration_minutes": g["end_min"],
            "output_interval_minutes": 5.0,
            "model_cell_size_m": res,
            "terrain_product": f"customer {'5 m ' if extent == 'C1' else ''}DTM (raw), unstructured mesh (mean {g['domain_m2'] / g['n_tri']:.0f} m²)",
            # 直接落在二维地面的净雨（耦合记录 acc_m3.rain）：> 0，sim_ponding 不亮 no_surface_rain
            "surface_rainfall_inflow_m3": acc.get("rain"),
            "surface_rain_cell_fraction": g["rain_mask_fraction"],
        },
        "outputs": {
            "maximum_depth": "maximum_depth_wgs84.geojson",
            "timeline_manifest": "temporal_snapshots/manifest.json",
            "swmm_report": "swmm_coupled.rpt",
            "swmm_input": "coupled_scenario.inp",
            "coupling_receipt": "node_cell_binding.json",
            "surface_series": "surface_series.json",
        },
    }
    _dump(out / "delivery_summary.json", summary)
    print(f"  {state}：{len(ids):,} 格 ≥ {WET_M} m｜最大水深 {g['tri_hmax']:.2f} m｜"
          f"同刻 ≥ 0.02 m 最大 {g['tri_wet_max_m2'] / 1e4:,.0f} ha @ {g['tri_wet_max_min']:g} min"
          f"（记录 {(rec.get('max_wet_m2') or 0) / 1e4:,.0f} ha）｜≥0.30 m 全程 {g['tri_ever'][0.30] / 1e4:,.2f} ha")
    return summary


#: 统计值表里「口径」列 → 协议 key；指标名 → 协议 key
_SCOPES = {"热点汇水区": "catchment", "热点 200 m": "near_200m",
           "热点汇水区（并集）": "catchment"}
_METRICS = {"最大水深 m": "max_depth_m", "积水面积 ≥0.10 m（ha）": "area_ge_0_10_ha",
            "积水面积 ≥0.30 m（ha）": "area_ge_0_30_ha", "最大积水量 m³": "max_volume_m3",
            "最大积水量出现时刻 h": "max_volume_h"}
_TOTAL_ROW = "C1 八个热点"
#: 「全域」行的口径 → 协议 key（管网服务区 / 直接降雨区 / 全域）
_ZONES = {"管网服务区": "network_zone", "直接降雨区": "direct_rain_zone", "全域": "domain"}


def _utm_to_ll(c: Any) -> Any:
    """EPSG:32640 的坐标嵌套 → 经纬度（落图用，只给米制写成了 GeoJSON 的交付兜底）。"""
    if c and isinstance(c[0], (int, float)):
        from .rda import _utm40_to_lonlat

        lo, la = _utm40_to_lonlat(np.array([c[0]], float), np.array([c[1]], float))
        return [float(lo[0]), float(la[0])]
    return [_utm_to_ll(x) for x in c]


def _round_coords(c: Any) -> Any:
    return [round(c[0], 5), round(c[1], 5)] if c and isinstance(c[0], (int, float)) else [_round_coords(x) for x in c]


def _adm(h: Any) -> str | None:
    """热点编号归一：`ADM-21` / `ADM-021` → `ADM-21`；台账的 uid 补零、统计值表不补，按数字对。
    前缀（ADM / AAM / DRM）必须带着：三个台账的 `hotspot_id_raw` 会重复，只按数字会串点。"""
    m = re.fullmatch(r"(ADM|AAM|DRM)-0*(\d+)", str(h).strip())
    return f"{m.group(1)}-{int(m.group(2))}" if m else None


def _is_total(area: str) -> bool:
    return area == _TOTAL_ROW or re.fullmatch(r"\d+ 个热点", area) is not None


#: 长表（RB-01 交付 `统计值/统计值.csv`：一行一个「态 × 范围」，指标是列）的列 → 宽表的「指标」
_LONG_METRICS = {"最大水深 m": "最大水深 m", "≥0.10 m 面积 ha": "积水面积 ≥0.10 m（ha）",
                 "≥0.30 m 面积 ha": "积水面积 ≥0.30 m（ha）", "最大积水量 m³": "最大积水量 m³"}
_LONG_STATES = (("L1", "L1"), ("L2+planned", "L2+planned"), ("L2+suggested", "L2+suggested"), ("L2 ", "L2"))


def _stats_rows_long(path: Path) -> tuple[list[str], list[tuple]] | None:
    """长表 → 与 xlsx 同形的宽表（区域 / 口径 / 指标 / 各态）。态名按前缀归到 STATES 的键；
    带「中间步」的过程态不要（与 `_state_dir` 同口径）；「全域（塘外）」当全域行，「ADM-n 200 m 内」当热点 200 m。"""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cells: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in rows:
        label = r["态"]
        if "中间步" in label:
            continue
        st = next((k for pre, k in _LONG_STATES if label.startswith(pre)), None)
        if st is None:
            continue
        rng = r["范围"]
        if rng.startswith("全域"):
            area, scope = "全域", "全域"
        elif (m := re.fullmatch(r"((?:ADM|AAM|DRM)-\d+) 200 m 内", rng)):
            area, scope = m.group(1), "热点 200 m"
        else:
            continue
        for col, metric in _LONG_METRICS.items():
            if r.get(col) not in (None, ""):
                cells.setdefault((area, scope, metric), {})[st] = r[col]
    states = ["L1", "L2", "L2+planned", "L2+suggested"]
    hdr = ["区域", "口径", "指标", *states]
    return hdr, [(a, sc, mt, *[c.get(st, "") for st in states]) for (a, sc, mt), c in cells.items()]


#: AD 区域交付（30 m）：`积水点/积水点结果表.csv` 的列后缀（前面是「L1 」「L2 」）→ 宽表的「指标」
_AD_HOTSPOT_COLS = {"200 m 内最大水深 m": "最大水深 m", "积水面积 ≥0.10 m (ha)": "积水面积 ≥0.10 m（ha）",
                    "积水面积 ≥0.30 m (ha)": "积水面积 ≥0.30 m（ha）", "最大积水量 m³": "最大积水量 m³"}
#: `统计/汇水区汇总.csv` 的指标名 → 宽表的「指标」（别的行原名就是 _METRICS 里的）
_AD_ZONE_METRICS = {"全域最大水深 m": "最大水深 m"}


def _stats_rows_ad(root: Path) -> tuple[list[str], list[tuple]] | None:
    """AD 区域交付（MI-8 / RB-1 / RD-* 等 30 m 交付）→ 与 xlsx 同形的宽表（区域 / 口径 / 指标 / 各态）：
    - `积水点/积水点结果表.csv`：一行一个热点，列「<态> 200 m 内最大水深 m」等 → 「热点 200 m」口径；
    - `统计/汇水区汇总.csv`（区域 / 指标 / 各态）：整个片区一行一指标 → 「全域」口径。
    这批交付没有汇水区面，「汇水区」就是整个片区（`公共/计算域.geojson`），所以没有「热点汇水区」口径。
    态只有 L1 / L2（表里有几个就读几个）。"""
    states = ["L1", "L2", "L2+planned", "L2+suggested"]
    cells: dict[tuple[str, str, str], dict[str, str]] = {}
    hot_p = root / "积水点" / "积水点结果表.csv"
    if not hot_p.is_file():
        return None
    with hot_p.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            area = _adm(r.get("积水点"))
            if area is None:
                continue
            for st in states:
                for suffix, metric in _AD_HOTSPOT_COLS.items():
                    v = r.get(f"{st} {suffix}")
                    if v not in (None, ""):
                        cells.setdefault((area, "热点 200 m", metric), {})[st] = v
    zone_p = root / "统计" / "汇水区汇总.csv"
    if zone_p.is_file():
        with zone_p.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                metric = _AD_ZONE_METRICS.get(r.get("指标", ""), r.get("指标", ""))
                for st in states:
                    if r.get(st) not in (None, ""):
                        cells.setdefault(("全域", "全域", metric), {})[st] = r[st]
    if not cells:
        return None
    hdr = ["区域", "口径", "指标", *states]
    return hdr, [(a, sc, mt, *[c.get(st, "") for st in states]) for (a, sc, mt), c in cells.items()]


def _merge_stats(base: tuple[list[str], list[tuple]], extra: tuple[list[str], list[tuple]]) -> tuple[list[str], list[tuple]]:
    """把 extra 的行并进 base（按 base 的表头对齐各态列；同一「区域 / 口径 / 指标」以 base 为准）。"""
    hdr, rows = base
    seen = {(str(r[0]), r[1], r[2]) for r in rows if len(r) >= 3}
    idx = {name: i for i, name in enumerate(extra[0])}
    out = list(rows)
    for r in extra[1]:
        if (str(r[0]), r[1], r[2]) in seen:
            continue
        out.append(tuple(r[idx[c]] if c in idx and idx[c] < len(r) else "" for c in hdr))
    return hdr, out


def _stats_rows(root: Path) -> tuple[list[str], list[tuple]] | None:
    """交付的逐态统计值表 → (表头, 行)。几种形状，列都是「区域 / 口径 / 指标 / 各态」：
    C1 是 `统计值/*统计值.xlsx`（工作表「统计值」）；Mussafah 模型版与 Yas / Al Bateen / Bani Yas 是
    `对比_缺口分析/统计值.csv`（utf-8-sig）；AD 区域交付是 `积水点/积水点结果表.csv` + `统计/汇水区汇总.csv`
    （见 `_stats_rows_ad`）。都没有返回 None。
    艾因这类交付的 `对比_缺口分析/统计值.csv` 只有全域汇总、表头是「口径 / 指标 / 各态」（没有区域列），
    逐热点的数在 `积水点/积水点结果表.csv`：两张表合并，不能只取前一张。"""
    for x in sorted(p for p in (root / "统计值").glob("*统计值.xlsx") if not p.name.startswith(("._", "~$"))):
        import openpyxl

        ws = openpyxl.load_workbook(x, data_only=True)["统计值"]
        it = ws.iter_rows(values_only=True)
        return [str(c) for c in next(it)], list(it)
    long_p = root / "统计值" / "统计值.csv"
    if long_p.is_file():
        return _stats_rows_long(long_p)
    csv_p = root / "对比_缺口分析" / "统计值.csv"
    if csv_p.is_file():
        with csv_p.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        hdr, body = rows[0], [tuple(r) for r in rows[1:]]
        if hdr and hdr[0] == "口径":      # 没有区域列：每行都是全域口径
            hdr, body = ["区域", *hdr], [("全域", *r) for r in body]
        base = (hdr, body)
        ad = _stats_rows_ad(root)
        return _merge_stats(base, ad) if ad else base
    return _stats_rows_ad(root)


def _ledger_points(root: Path) -> dict[str, dict[str, Any]]:
    """热点坐标：优先台账 `hotspots_845.csv`（只取 ADM）；没有台账就取交付自带的 `公共/热点.geojson`（WGS84 点）。"""
    ledger = DATA_ROOT / "processed" / "hotspots_845.csv"
    out: dict[str, dict[str, Any]] = {}
    if ledger.is_file():
        with ledger.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                k = _adm(r["uid"])
                if k:
                    out[k] = {"district": r["area"], "location": r["location"],
                              "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6)}
        # AD 区域交付自带 `积水点/积水点图层.geojson`（WGS84 点）：台账里没有的热点用它补，不覆盖台账
        own = root / "积水点" / "积水点图层.geojson"
        if own.is_file():
            for ft in json.loads(own.read_text(encoding="utf-8")).get("features", []):
                pr, geom = ft["properties"], ft.get("geometry") or {}
                k = _adm(pr.get("hotspot_id"))
                if k and k not in out and geom.get("type") == "Point":
                    out[k] = {"district": pr.get("area"), "location": pr.get("location"),
                              "lat": round(geom["coordinates"][1], 6), "lon": round(geom["coordinates"][0], 6)}
        return out
    gs = sorted((root / "公共").glob("*热点.geojson"))   # 热点.geojson / 6个热点.geojson
    if not gs and (root / "积水点" / "积水点图层.geojson").is_file():   # AD 区域交付
        gs = [root / "积水点" / "积水点图层.geojson"]
    g = gs[0] if gs else root / "公共" / "热点.geojson"
    if g.is_file():
        for ft in json.loads(g.read_text(encoding="utf-8")).get("features", []):
            pr, geom = ft["properties"], ft.get("geometry") or {}
            k = _adm(pr.get("hotspot_id"))
            if k and geom.get("type") == "Point":
                out[k] = {"district": pr.get("area"), "location": pr.get("location"),
                          "lat": round(geom["coordinates"][1], 6), "lon": round(geom["coordinates"][0], 6)}
    return out


def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_region(x: float, y: float, region: dict[str, Any]) -> bool:
    for ft in region.get("features", []):
        g = ft["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for rings in polys:
            if _in_ring(x, y, rings[0]) and not any(_in_ring(x, y, h) for h in rings[1:]):
                return True
    return False


def ledger_adm_points() -> list[dict[str, Any]]:
    """台账全部 ADM 积水点（不分区域），坐标缺失的跳过；全市任务补「域外」点用。"""
    ledger = DATA_ROOT / "processed" / "hotspots_845.csv"
    if not ledger.is_file():
        return []
    out = []
    with ledger.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                lat, lon = round(float(r["lat"]), 6), round(float(r["lon"]), 6)
            except (TypeError, ValueError):
                continue
            if (_adm(r["uid"]) or "").startswith("ADM-"):
                out.append({"id": _adm(r["uid"]), "district": r["area"], "location": r["location"],
                            "criticality": r.get("criticality"), "lat": lat, "lon": lon})
    return out


def region_ledger_points(region: dict[str, Any]) -> list[dict[str, Any]]:
    """片区汇水区内台账登记的全部积水点（只取 ADM，与 `_ledger_points` 同口径）。
    「交付的积水点」只是统计表列出的那几个；这里给整片区的台账点，让页面能把两层分开看。
    没有台账 CSV、或片区没有汇水区面，返回空表（不拿交付自带的几个点冒充全部）。"""
    ledger = DATA_ROOT / "processed" / "hotspots_845.csv"
    if not ledger.is_file() or not region.get("features"):
        return []
    out = []
    with ledger.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            k = _adm(r["uid"])
            try:
                lat, lon = round(float(r["lat"]), 6), round(float(r["lon"]), 6)
            except (TypeError, ValueError):
                continue
            if k and _in_region(lon, lat, region):
                out.append({"id": k, "district": r["area"], "location": r["location"], "lat": lat, "lon": lon})
    return sorted(out, key=lambda p: int(p["id"].split("-")[1]))


#: 片区汇水区面（每个交付自己的）：C1 优先取客户模型子汇水区并集（模型版），其余取 `公共/计算域.geojson`
#: （Yas / Al Bateen / Bani Yas 是客户汇水区图层里的那一个多边形，Mussafah 是客户模型汇水区并集）
def region_catchment(root: Path) -> dict[str, Any]:
    cands = sorted((root / "公共").glob("*汇水区_模型版/*汇水区_模型版.geojson")) + [root / "公共" / "计算域.geojson"]
    for f in cands:
        if not f.is_file():
            continue
        feats = []
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            g = norm_geom(ft.get("geometry") or {})
            if g.get("type") in ("Polygon", "MultiPolygon") and is_projected(g):
                g = {**g, "coordinates": _utm_to_ll(g["coordinates"])}
            if g.get("type") in ("Polygon", "MultiPolygon"):
                pr = ft.get("properties") or {}
                feats.append({"type": "Feature", "properties": {"name": pr.get("name") or pr.get("id") or root.name,
                                                                "area_km2": pr.get("area_km2"), "source": f.name},
                              "geometry": {"type": g["type"], "coordinates": _round_coords(g["coordinates"])}})
        if feats:
            return {"type": "FeatureCollection", "features": feats}
    return {"type": "FeatureCollection", "features": []}


def extract_hotspots(root: Path, base: Path, states: list[str]) -> Path | None:
    """交付的积水点（页面叫「积水点」，每个片区都有）：坐标取台账（见 `_ledger_points`），逐态数取交付的统计值表
    （见 `_stats_rows`）的「热点汇水区」/「热点 200 m」/ 全域各口径（水量对账等行不在这里）。
    汇水区面只有 C1 交付带（`公共/8个热点的地表与管网汇水区.geojson`），别的片区没有，此时只出 200 m 口径。
    缺统计值表、或坐标一个都对不上就不写、返回 None。"""
    st_rows = _stats_rows(root)
    pts = _ledger_points(root)
    if st_rows is None or not pts:
        return None
    hdr, rows = st_rows
    cols = {st: hdr.index(st) for st in states if st in hdr}
    stats: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    total_name = None
    for r in rows:
        if len(r) < 3 or not r[0]:
            continue
        area, scope, metric = str(r[0]), r[1], r[2]
        total = _is_total(area)
        if total:
            total_name = area
        key = _ZONES.get(scope) if area == "全域" else _SCOPES.get(scope)
        if key is None or metric not in _METRICS:
            continue
        name = area if (area == "全域" or total) else _adm(area)
        if name is None:
            continue
        for st, i in cols.items():
            if i >= len(r) or r[i] in (None, ""):
                continue
            try:
                stats.setdefault(name, {}).setdefault(st, {}).setdefault(key, {})[_METRICS[metric]] = float(r[i])
            except ValueError:
                continue
    cat: dict[str, dict[str, Any]] = {}
    feats = []
    geo = root / "公共" / "8个热点的地表与管网汇水区.geojson"
    if geo.is_file():
        for ft in json.loads(geo.read_text(encoding="utf-8"))["features"]:
            pr = ft["properties"]
            cat.setdefault(pr["hotspot_id"], {})["surface" if pr["kind"] == "地表" else "network"] = pr["area_ha"]
            if pr.get("unconverged"):
                cat[pr["hotspot_id"]]["unconverged"] = True
            feats.append({"type": "Feature", "properties": {"hotspot_id": pr["hotspot_id"],
                                                            "kind": "surface" if pr["kind"] == "地表" else "network",
                                                            "area_ha": pr["area_ha"],
                                                            "unconverged": bool(pr.get("unconverged"))},
                          "geometry": {"type": ft["geometry"]["type"],
                                       "coordinates": _round_coords(ft["geometry"]["coordinates"])}})
    ids = sorted((h for h in stats if h in pts), key=lambda x: int(x.split("-")[1]))
    if not ids:
        return None
    points = [{"id": h, **pts[h], "catchment_ha": cat.get(h, {}), "stats": stats[h]} for h in ids]
    out = base / "hotspots.json"
    _dump(out, {"schema": SCHEMA, "note": f"交付的 {len(points)} 个热点（积水点）：逐态数取自交付的统计值表",
                "region_catchment": region_catchment(root),
                "states": list(cols), "points": points, "total": stats.get(total_name, {}) if total_name else {},
                "zones": stats.get("全域", {}), "catchments": {"type": "FeatureCollection", "features": feats}})
    print(f"  积水点 {len(points)} 个 → {out.name}" + ("" if feats else "（交付无汇水区面，只有 200 m / 全域口径）"))
    return out


# 设施 GIS：服务器交付放在 `<态目录>/设施GIS/`；2026-09-29 的 C1 样例放在交付旁边的 planned/ sugg/（与 C1_61mm2h 同级）
FACILITY_SIBLINGS = {"L2+planned": "planned", "L2+suggested": "sugg"}
FACILITY_LAYERS = {"ponds": "蓄水池_规划塘", "pumps": "泵站与泵点", "outlets": "排水去向", "intake": "地面取水片",
                   "gravity": "重力管（井→新塘）"}
#: 同一类设施在不同交付里的别名（RB-01 交付：塘 / 泵（吸水井→排放点））
FACILITY_ALIASES = {"ponds": ["塘"], "pumps": ["泵（吸水井→排放点）"]}


def facility_dir(root: Path, state: str) -> Path | None:
    sd = _state_dir(root, state)
    if sd is not None and (sd / "设施GIS").is_dir():
        return sd / "设施GIS"
    sub = FACILITY_SIBLINGS.get(state)
    return root.parent / sub / "设施GIS" if sub and (root.parent / sub / "设施GIS").is_dir() else None


def extract_facilities(root: Path, base: Path) -> None:
    """把各态的 `设施GIS/*.geojson`（GeoJSON 已是 WGS84）收成各态的 facilities.json（落图用，`/layers/facilities`）。"""
    for st in present_states(root):
        src = facility_dir(root, st)
        if src is None:
            continue
        layers = {}
        for key, stem in FACILITY_LAYERS.items():
            f = next((src / f"{n}.geojson" for n in [stem, *FACILITY_ALIASES.get(key, [])]
                      if (src / f"{n}.geojson").is_file()), None)
            if f is not None:
                layers[key] = json.loads(f.read_text(encoding="utf-8"))
        out = base / st.replace("+", "_") / "facilities.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        _dump(out, {"state": st, "layers": layers})
        print(f"  设施 GIS {st}（{src.parent.name}）: " + " / ".join(f"{k} {len(v['features'])}" for k, v in layers.items()))


def extract(root: Path, only: list[str] | None = None, c2d: Path | None = None) -> Path:
    root = root.resolve()
    base = OUT_ROOT / output_key(root, delivery_res(root))
    for state in present_states(root):
        if only and state not in only:
            continue
        extract_state(root, state, base / state.replace("+", "_"), c2d)
    present = [st for st in STATES if (base / st.replace("+", "_") / "delivery_summary.json").is_file()]
    extract_facilities(root, base)
    extract_hotspots(root, base, present)
    _dump(base / "states.json", {
        "schema": "flood_management.sww_states.v1",
        "delivery": root.name,
        "default": "L1" if "L1" in present else present[0],
        "states": [{"key": st, "label": _label(root, st), "defence_line_key": STATES[st][1],
                    "column": COLUMNS[st], "dir": st.replace("+", "_"),
                    "parent": PARENT[st] if PARENT[st] in present else None,
                    "same_as": None} for st in present],
    })
    hp = base / "hotspots.json"
    if hp.is_file():        # 口径切换要按态（要 states.json、各态的 .inp / .rpt / 溢水点）拆指标，所以放在最后
        from . import sim_scope

        hot = json.loads(hp.read_text(encoding="utf-8"))
        hot["scopes"] = sim_scope.compute(root, base, hot)
        _dump(hp, hot)
        print(f"  口径 {len(hot['scopes'])} 个 → hotspots.json")
    return base


def import_all(root: Path, by: str | None = None, only: list[str] | None = None, c2d: Path | None = None) -> int:
    from . import sim_results
    from . import simulation as sim

    base = extract(root, only, c2d)
    keys = [s["key"] for s in json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]]
    top = max((STATES[k][1] for k in keys), key=sim.LINE_ORDER.index)
    with sim.connect() as conn:
        return sim_results.import_delivery(conn, base, by, line_key=top, extent=extent_of(root),
                                           variant_label=f"{len(keys)} states: {' / '.join(keys)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("extract", "只从 npz 抽出交付形状"), ("import", "抽取并登记为仿真任务")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("path", type=Path)
        p.add_argument("--state", action="append", choices=list(STATES), help="只处理这几态（可重复）")
        p.add_argument("--c2d", type=Path, help="存放 <名>_<态>_2d.npz 的目录（work/c2d）；不给就从交付往上找 <主机>/work/c2d")
        if name == "import":
            p.add_argument("--by", help="操作人缩写，如 AR")
    args = ap.parse_args()
    if args.cmd == "extract":
        extract(args.path, args.state, args.c2d)
        return
    print(f"已登记为仿真 #{import_all(args.path, args.by, args.state, args.c2d)}")


if __name__ == "__main__":
    main()
