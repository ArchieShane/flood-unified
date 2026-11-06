"""造一份「假」的服务器一二维交付（几千个三角单元），只用来验证流程与格式，数值没有任何物理意义。

形状照 `data/temp260929/C1_61mm2h`（npz 的键 / dtype、耦合运行记录、.inp / .rpt、设施GIS）；
态数、网格、帧数、是否带 uh/vh、是否压缩都可调，供 sim_c1 / sim_probe 的测试共用。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

#: 态 → 目录名（前缀与 sim_c1.STATES 对得上）
DIRS = {
    "L1": "L1_现状管网+现状泵站",
    "L2": "L2_加OP-02移动泵",
    "L2+planned": "L2+planned_Parsons方案",
    "L2+suggested": "L2+suggested_v3_按塘余量分配",
}
X0, Y0 = 250000.0, 2700000.0      # UTM 40N 随便取一处


def make_npz(path: Path, *, nx: int = 40, ny: int = 40, cell: float = 15.0, frames: int = 12, step_min: float = 5.0,
             speed: bool = True, compressed: bool = True, seed: int = 0) -> dict:
    """规则网格每格切 2 个三角：n_tri = 2·nx·ny。"""
    rng = np.random.default_rng(seed)
    cx, cy = np.meshgrid((np.arange(nx) + 0.5) * cell, (np.arange(ny) + 0.5) * cell)
    xy = np.concatenate([np.stack([cx.ravel() - cell / 6, cy.ravel() - cell / 6], 1),
                         np.stack([cx.ravel() + cell / 6, cy.ravel() + cell / 6], 1)]) + [X0, Y0]
    n = len(xy)
    area = np.full(n, cell * cell / 2, np.float32)
    elev = (2.0 + 0.002 * (xy[:, 0] - X0) + rng.normal(0, 0.02, n)).astype(np.float32)
    shape = np.exp(-(((xy[:, 0] - X0 - nx * cell / 2) / (nx * cell / 4)) ** 2
                     + ((xy[:, 1] - Y0 - ny * cell / 2) / (ny * cell / 4)) ** 2))
    t_h = (np.arange(frames) + 1) * step_min / 60
    wave = np.sin(np.pi * np.clip(np.arange(frames) / max(frames - 1, 1), 0, 1))
    fr = (shape[None, :] * wave[:, None] * 0.8).astype(np.float16)
    z = {"xy": xy.astype(np.float32), "area": area, "elev": elev, "hmax": fr.max(0).astype(np.float32),
         "frames": fr, "t_h": t_h, "rain_mask": rng.random(n) < 0.3}
    if speed:
        z["uh"] = (fr * 0.5).astype(np.float16)
        z["vh"] = (fr * -0.25).astype(np.float16)
    path.parent.mkdir(parents=True, exist_ok=True)
    (np.savez_compressed if compressed else np.savez)(path, **z)
    return {"n_tri": n, "nx": nx, "ny": ny, "cell": cell, "frames": frames}


def make_inp(path: Path, nx: int, ny: int, cell: float) -> None:
    lines = ["[OPTIONS]", "FLOW_UNITS LPS", "", "[OUTFALLS]", ";;Name Elev Type Gate", "OF1 1.0 FREE NO", "",
             "[COORDINATES]"]
    for k in range(5):
        lines.append(f"J{k} {X0 + (k + 1) * nx * cell / 6:.1f} {Y0 + ny * cell / 2:.1f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_state(root: Path, state: str, *, dirname: str | None = None, res_m: float = 15.0, end_h: float | None = None,
               frames: int = 12, facilities: bool = False, speed: bool = True, compressed: bool = True,
               nx: int = 40, ny: int = 40, cell: float = 15.0) -> Path:
    sd = root / (dirname or DIRS[state])
    d2 = sd / "一二维"
    tag = state.replace("+", "_")
    info = make_npz(d2 / f"{tag}_二维水深帧与最大水深.npz", nx=nx, ny=ny, cell=cell, frames=frames, speed=speed,
                    compressed=compressed)
    end_h = end_h if end_h is not None else frames * 5 / 60
    rec = {"tag": f"FAKE_{tag}", "res_m": res_m, "dt_c": 60.0, "n_tri": info["n_tri"], "n_nodes": 5, "end_h": end_h,
           "acc_m3": {"rain": 1000.0, "1to2": 800.0, "2to1": 100.0}, "final_vol_m3": 1500.0, "boundary_out_m3": 0.0,
           "max_wet_m2": 5000.0, "max_wet_t_h": 1.0,
           "series": [{"t_h": (i + 1) * 5 / 60, "acc_1to2": 10.0 * (i + 1)} for i in range(frames)]}
    (d2 / f"{tag}_耦合运行记录.json").write_text(json.dumps(rec), encoding="utf-8")
    (d2 / "耦合的一维").mkdir(exist_ok=True)
    make_inp(d2 / "耦合的一维" / f"{tag}_耦合一维模型.inp", nx, ny, cell)
    (d2 / "耦合的一维" / f"{tag}_耦合一维报告.rpt").write_text("fake rpt\n", encoding="utf-8")
    if facilities:
        g = sd / "设施GIS"
        g.mkdir(exist_ok=True)
        for stem in ("蓄水池_规划塘", "泵站与泵点", "排水去向"):
            (g / f"{stem}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [54.5, 24.4]}, "properties": {}}]}),
                encoding="utf-8")
    return sd


def make_delivery(parent: Path, name: str = "FAKE_61mm2h", states: tuple[str, ...] = ("L1", "L2"), **kw) -> Path:
    root = parent / name
    root.mkdir(parents=True)
    for st in states:
        make_state(root, st, **kw)
    return root


def make_region_stats(root: Path, states: list[str], ids: list[str], *, union: str | None = None) -> Path:
    """区域交付（rg_post.py / ms_post.py）的 `对比_缺口分析/统计值.csv`：区域 / 口径 / 指标 / 各态（表头是态 key）。
    区域名 `ADM-86` 不补零；并集行叫 `<N> 个热点`；另带水量对账行（不该被当成积水点）。数值是假的。"""
    import csv

    names = {"L1": "L1", "L2": "L2", "L2+planned": "L2+planned", "L2+suggested": "L2+suggested"}
    rows = [["区域", "口径", "指标"] + [names[s] for s in states]]
    mets = (("最大水深 m", 1.0), ("积水面积 ≥0.10 m（ha）", 10.0), ("积水面积 ≥0.30 m（ha）", 3.0),
            ("最大积水量 m³", 5000.0), ("最大积水量出现时刻 h", 2.0))
    for k, zone in enumerate([*ids, union or f"{len(ids)} 个热点", "全域"]):
        for m, v in mets:
            rows.append([zone, "全域" if zone == "全域" else "热点 200 m", m] + [round(v * (k + 1) / (i + 1), 3) for i in range(len(states))])
    rows.append(["全域", "水量对账 m³", "1D→2D"] + [123] * len(states))
    p = root / "对比_缺口分析" / "统计值.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    return p


def make_ad_stats(root: Path, ids: list[str], *, region: str = "MI-8") -> None:
    """AD 区域交付（30 m）的积水点与统计：`积水点/积水点结果表.csv`、`积水点/积水点图层.geojson`（WGS84 点）、
    `统计/汇水区汇总.csv`。列名照 MI-8 的真实交付抄；数值是假的。"""
    import csv

    (root / "积水点").mkdir(parents=True, exist_ok=True)
    (root / "统计").mkdir(parents=True, exist_ok=True)
    sfx = ["200 m 内最大水深 m", "最近单元水深 m", "积水面积 ≥0.10 m (ha)", "积水面积 ≥0.30 m (ha)", "最大积水量 m³"]
    hdr = ["积水点", "汇水区", "位置", *[f"{st} {c}" for st in ("L1", "L2") for c in sfx]]
    feats = []
    with (root / "积水点" / "积水点结果表.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for k, h in enumerate(ids):
            row = {"积水点": h, "汇水区": region, "位置": f"loc{k}"}
            for j, st in enumerate(("L1", "L2")):                       # L2 的数是 L1 的一半，便于断言取对了列
                for c, v in zip(sfx, (0.3 * (k + 1), 0.01, 0.5 * (k + 1), 0.1 * (k + 1), 1000.0 * (k + 1))):
                    row[f"{st} {c}"] = round(v / (j + 1), 3)
            w.writerow([row[c] for c in hdr])
            feats.append({"type": "Feature", "properties": {"hotspot_id": h, "area": "Zayed", "location": f"loc{k}"},
                          "geometry": {"type": "Point", "coordinates": [54.5 + k * 0.01, 24.3]}})
    (root / "积水点" / "积水点图层.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}),
                                                    encoding="utf-8")
    with (root / "统计" / "汇水区汇总.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["区域", "指标", "L1", "L2"])
        for m, a, b in (("全域最大水深 m", 2.851, 2.801), ("积水面积 ≥0.10 m（ha）", 1996.99, 1989.03),
                        ("积水面积 ≥0.30 m（ha）", 382.21, 381.75), ("最大积水量 m³", 5043498, 5017621),
                        ("最大积水量出现时刻 h", 1.5, 1.42)):
            w.writerow([region, m, a, b])


# ---- 「模型版」交付形状（服务器 ms_post.py / rg_post.py 的产物）：npz 不在交付里，在 <主机>/work/c2d ----

MV_DIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄", "L2+planned": "L2+planned", "L2+suggested": "L2+suggested"}
MV_KEYS = {"L1": "L1", "L2": "L2", "L2+planned": "L2P", "L2+suggested": "L2S3"}


def make_model_delivery(host: Path, delivery: str = "Mussafah_00_模型版_61mm2h", name: str = "Mussafah_00_M61H2",
                        states: tuple[str, ...] = ("L1", "L2"), *, frames: int = 12, with_tag: bool = True,
                        direct_rain: bool = False, res_m: float | None = 15.0, npz_in_delivery: bool = False) -> Path:
    """`<host>/runtime/out/交付/<delivery>/<态>/{一维,二维}/…` + `<host>/work/c2d/<name>_<态记号>_2d.npz`。
    二维目录里只放运行记录和一个假 .sww（占位，不会被读）。
    `npz_in_delivery=True`：npz 改放进 `<态>/二维/`（30 m 区域交付 `AD_<区>_M61H1_L1_2d.npz` 的形状），work/c2d 不建。"""
    root = host / "runtime" / "out" / "交付" / delivery
    c2d = host / "work" / "c2d"
    if not npz_in_delivery:
        c2d.mkdir(parents=True, exist_ok=True)
    for st in states:
        key = MV_KEYS[st]
        d1, d2 = root / MV_DIRS[st] / "一维", root / MV_DIRS[st] / "二维"
        d1.mkdir(parents=True), d2.mkdir(parents=True)
        make_npz((d2 if npz_in_delivery else c2d) / f"{name}_{key}_2d.npz", frames=frames)
        make_inp(d1 / f"{name}_{key}.inp", 40, 40, 15.0)
        (d1 / f"{name}_{key}_一维报告.rpt").write_text("fake rpt\n", encoding="utf-8")
        (d2 / f"{name}_{key}.sww").write_bytes(b"CDF\x01")
        rec = {"n_tri": 3200, "end_h": frames * 5 / 60, "acc_m3": {"1to2": 800.0, "2to1": 100.0, "rain": 500.0 if direct_rain else 0.0},
               "final_vol_m3": 700.0, "boundary_out_m3": 0.0, "wall_s": 10.0,
               "series": [{"t_h": (i + 1) * 5 / 60, "acc_1to2": 10.0 * (i + 1)} for i in range(frames)]}
        if with_tag:
            rec["tag"] = f"{name}_{key}"
        if res_m:
            rec["res_m"] = res_m
        (d2 / f"{key}_耦合运行记录.json").write_text(json.dumps(rec), encoding="utf-8")
    return root
