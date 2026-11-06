# -*- coding: utf-8 -*-
"""水自己往哪儿汇？——一维答不了、二维才能答的那个问题。

一维模型里水从井盖冒出来就停在那个节点上（ALLOW_PONDING、每节点一个占位面积），
不漫流、不顺街走、不往地形低处汇。所以在一维里，水进塘的**唯一可建模途径就是泵**。
Parsons 全套方案配 69 台 12 寸泵，一部分原因在这儿——不是工程偏好，是模型表达能力。

有了二维就多一个选项：把塘放在水自己要去的地方，让它重力漫流进塘，不用泵。
这个脚本从二维演算结果里把「水自然积聚的洼地」找出来，按积水量排序，
再对上用地台账，回答：**哪些天然汇水点既能收到水、又挖得下去**。

判据与边界：
  · 积水区按 0.05 m 水深阈值取连通域（低于这个深度算薄层过流，不是积水）
  · 只统计演算窗口内的**最大**积水状态，不是末态
  · 二维的水源只有一维的节点溢流，**没有直接降雨、没有坡面产流**
    （客户模型没有 [SUBCATCHMENTS]），所以这里的量是「管网溢出来之后往哪去」，
    不是「这片地一共来多少水」。要后者得先有产流模型。

用法：python tools/natural_sinks.py <汇水区> [态=L1] [水深阈值 m]
"""
import json, pathlib, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import OUT, WORK

H_MIN = 0.05        # 水深阈值 m
CELL = 15.0         # 归一化栅格，和二维网格同量级


def load(name, state):
    f = WORK / "c2d" / f"anuga_{name}_{state}.npz"
    if not f.exists():
        raise SystemExit(f"没有 {f.name}，先跑二维")
    d = np.load(f, allow_pickle=True)
    return d["h"], d["cx"], d["cy"], d["areas"]


def sinks(h, cx, cy, areas, hmin=H_MIN, cell=CELL):
    """把三角形形心的水深栅格化，取连通域 = 一个个天然积水洼地。"""
    from scipy import ndimage
    x0, y0 = cx.min(), cy.min()
    nx = int((cx.max() - x0) / cell) + 2
    ny = int((cy.max() - y0) / cell) + 2
    ci = np.clip(((cx - x0) / cell).astype(int), 0, nx - 1)
    ri = np.clip(((cy.max() - cy) / cell).astype(int), 0, ny - 1)
    vol = np.zeros((ny, nx)); dep = np.zeros((ny, nx))
    np.add.at(vol, (ri, ci), h * areas)
    np.maximum.at(dep, (ri, ci), h)
    lab, n = ndimage.label(dep >= hmin)
    out = []
    for k in range(1, n + 1):
        m = lab == k
        v = float(vol[m].sum())
        if v < 20.0:                     # 20 m³ 以下不值一提
            continue
        rr, cc = np.nonzero(m)
        w = vol[m]
        out.append({"vol_m3": v, "area_m2": float(m.sum() * cell * cell),
                    "hmax_m": float(dep[m].max()),
                    "x": float(x0 + (cc @ w) / max(w.sum(), 1e-9) * cell),
                    "y": float(cy.max() - (rr @ w) / max(w.sum(), 1e-9) * cell)})
    out.sort(key=lambda z: -z["vol_m3"])
    return out


if __name__ == "__main__":
    from swbrain import db, landuse as LU
    name = sys.argv[1] if len(sys.argv) > 1 else "Mussafah"
    state = sys.argv[2] if len(sys.argv) > 2 else "L1"
    hmin = float(sys.argv[3]) if len(sys.argv) > 3 else H_MIN
    cfg = db.load_config(None)
    h, cx, cy, areas = load(name, state)
    ss = sinks(h, cx, cy, areas, hmin)
    tot = sum(s["vol_m3"] for s in ss)
    print(f"{name} · {state}：二维里水自然积聚成 {len(ss)} 个洼地，合计 {tot:,.0f} m³"
          f"（水深阈值 {hmin:g} m）\n")
    xy, ar, ft = LU.load_plots(cfg, name)
    print(f"{'#':>3}{'积水量 m³':>11}{'范围 m²':>10}{'最大水深 m':>11}"
          f"{'离最近合规地块':>15}{'够不够挖':>10}")
    for i, s in enumerate(ss[:15], 1):
        if len(xy):
            d = float(np.min(np.hypot(xy[:, 0] - s["x"], xy[:, 1] - s["y"])))
            near = f"{d:,.0f} m"
        else:
            near = "台账无地块"
        # 就地挖一个 2 m 深的塘，要多大占地才装得下这一处的水
        need = s["vol_m3"] / 2.0
        fits = "就地 %.0f m²" % need
        print(f"{i:>3}{s['vol_m3']:>11,.0f}{s['area_m2']:>10,.0f}{s['hmax_m']:>11.2f}"
              f"{near:>15}{fits:>10}")
    top = ss[:15]
    print(f"\n前 {len(top)} 个洼地占全部地表积水的 "
          f"{sum(s['vol_m3'] for s in top) / max(tot, 1e-9) * 100:.0f}%")
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [s["x"], s["y"]]},
         "properties": {"rank": i, "vol_m3": round(s["vol_m3"]),
                        "area_m2": round(s["area_m2"]), "hmax_m": round(s["hmax_m"], 2)}}
        for i, s in enumerate(ss[:50], 1)]}
    f = OUT / f"{name}.{state}.sinks.geojson"
    f.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    print(f"→ {f.name}（UTM 40N 坐标，前 50 个）")
