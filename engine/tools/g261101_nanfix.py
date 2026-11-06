# -*- coding: utf-8 -*-
"""261101 新区域：二维结果里个别单元出现 NaN 的善后（不改 couple2way_v2.py / region_post.py）。

现象（2026-11-02，Musaffah RD09 的 19 个算例）：二维计算到某一刻（如 6.75 h）有极少数单元（5 个，6–49 m²，排成一条直线，地面 4.8 m，前一帧是干的）的水深变成 NaN，
之后一直是这 5 个，不扩散；全域水量统计、最深水深、边界出流实测累计都变成 NaN，后处理 `round(nan)` 报 ValueError，成果出不来。
做法：二维结果文件（npz + 运行记录 json）被读取之前先检查，有 NaN 就就地修复——
  · frames / hmax / uh / vh 里的 NaN 置 0（这些单元按干处理；hmax 取它出 NaN 之前最大的有限值）；
  · 运行记录 twoway_<tg>.json：series 里的 vol_m3 / hmax_m 用修复后的帧重算，final_vol_m3 取最后一帧的水量，boundary_out_m3（余项）= 净入 − 域内留存，
    hmax_m / wet_ever_m2 用修复后的 hmax 重算；实测边界出流和二维闭合误差（累计量，NaN 之后不可恢复）置 null；
  · 记一笔 `nan_fix`（哪些单元、第一次出现的时刻、位置）进 json，页面「推演参数」/ outputs 可追溯。
NaN 单元超过 0.1 %（至少 50 个）说明是计算本身发散，不修复，直接报错。修复是幂等的（修过的文件再读不会再动）。
用 install() 装进 numpy.load；run_region_g.py 启动时调用。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

_load = np.load
DRY = 0.02


def fix_pair(npz_path: Path, log=print) -> bool:
    """修复 <tg>_2d.npz 和同目录 twoway_<tg>.json；有改动返回 True。"""
    npz_path = Path(npz_path)
    tg = npz_path.name[: -len("_2d.npz")]
    js_path = npz_path.with_name(f"twoway_{tg}.json")
    z = _load(npz_path)
    d = {k: z[k] for k in z.files}
    z.close()
    fr = d["frames"]
    bad = ~np.isfinite(fr.astype("float32"))
    if not bad.any() and np.isfinite(d["hmax"]).all():
        return False
    area = d["area"].astype(np.float64)
    cells = np.flatnonzero(bad.any(axis=0) | ~np.isfinite(d["hmax"]))
    if len(cells) > max(50, int(0.001 * len(area))):
        raise RuntimeError(f"{npz_path.name}：{len(cells)} 个单元出现 NaN / inf（占 {len(cells) / len(area) * 100:.2f} %），超过 0.1 %，是计算发散，不修复")
    t_h = np.asarray(d["t_h"], float)
    first = int(np.flatnonzero(bad.any(axis=1))[0]) if bad.any() else None
    # hmax：NaN 之前这些单元的最大有限值
    fin = np.where(bad, -np.inf, fr.astype("float32"))
    hm_fix = np.maximum(fin[:, cells].max(axis=0), 0.0) if len(cells) else np.zeros(0)
    d["hmax"] = d["hmax"].astype("float32").copy()
    d["hmax"][cells] = hm_fix
    fr[bad] = 0
    d["frames"] = fr
    for k in ("uh", "vh"):
        if k in d:
            m = ~np.isfinite(d[k].astype("float32"))
            if m.any():
                d[k][m] = 0
    tmp = npz_path.with_name(npz_path.stem + f".{os.getpid()}.tmp.npz")
    np.savez_compressed(tmp, **d)
    os.replace(tmp, npz_path)
    # ---- 运行记录
    tw = json.loads(js_path.read_text(encoding="utf-8"))
    vol = np.array([float((np.asarray(f, dtype=np.float64) * area).sum()) for f in d["frames"]])
    hmx = np.array([float(np.asarray(f, dtype=np.float32).max()) for f in d["frames"]])
    for row in tw.get("series", []):
        i = int(np.argmin(np.abs(t_h - float(row["t_h"]))))
        for key, v in (("vol_m3", vol[i]), ("hmax_m", hmx[i])):
            x = row.get(key)
            if x is None or not np.isfinite(x):
                row[key] = float(v)
    acc = tw["acc_m3"]
    net = acc["1to2"] + acc["rain"] - acc["2to1"] - acc["pond"] - acc.get("pump_out", 0.0) - acc.get("pump_pond", 0.0) - acc.get("infil", 0.0)
    tw["final_vol_m3"] = float(vol[-1])
    tw["boundary_out_m3"] = float(net - vol[-1])
    tw["boundary_out_measured_m3"] = None
    tw["closure_err_2d"] = None
    tw["hmax_m"] = float(d["hmax"].max())
    tw["wet_ever_m2"] = float(area[d["hmax"] > DRY].sum())
    tw["nan_fix"] = {"cells": [{"index": int(c), "x": float(d["xy"][c][0]), "y": float(d["xy"][c][1]), "area_m2": float(area[c]), "ground_m": float(d["elev"][c])} for c in cells[:20]],
                     "n_cells": int(len(cells)), "first_nan_t_h": float(t_h[first]) if first is not None else None,
                     "note": "二维计算中这些单元的水深变成 NaN（单元很小、地面高程正常、之前是干的，之后一直只有这几个，没有扩散）；已把它们的水深置 0（按干处理），hmax 取 NaN 之前的最大值。"
                             "实测边界出流是累计量，NaN 之后不可恢复，已置 null；边界出流（余项）= 净入 − 域内留存 照常给出。"}
    js_path.write_text(json.dumps(tw, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  ⚠ 二维结果 NaN 善后：{len(cells)} 个单元（第一次出现 t = {t_h[first] if first is not None else '?'} h）已置 0；记录写进 {js_path.name} 的 nan_fix")
    return True


def _load_patched(file, *a, **kw):
    try:
        p = Path(str(file))
        if p.name.endswith("_2d.npz") and p.is_file():
            fix_pair(p)
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"  （NaN 善后检查没做成：{type(e).__name__}: {e}）", flush=True)
    return _load(file, *a, **kw)


def install():
    np.load = _load_patched
