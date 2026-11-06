# -*- coding: utf-8 -*-
"""1D→2D 耦合的左半边：抽节点溢流 Q(t)，按构筑物形式处理地形，备好 ANUGA 的输入。

**挖不挖 DTM，由「塘被表达在哪个模型里」决定**，不由塘顶高程决定。

判据只有一条：一份水在同一时刻只能属于一个控制体。一维蓄水节点的 A_s(h) 与二维洼地的
∬(η−z)dA 是**同一个物理库容**的两种写法，两边都有就是重复计量——质量守恒的错误。

  pond_in_2d=False（塘留在一维）
      DEM **不许挖**。筑堤式的把**整个轮廓**抬到堰顶做成实心障碍：既表达它对地表漫流的
      阻挡，又保证二维里它一滴水都存不住。地下箱涵地面没变，原样不动。
  pond_in_2d=True（塘移进二维）
      一维里必须先**删掉**这个蓄水节点，DEM 才可以挖到塘底。进出水变成 1D↔2D 的交换口，
      必须配双向耦合（tools/couple2way.py），否则水进得去出不来。

本项目三个汇水区的塘全部留在一维，理由不是图省事：它们离积水点 1.3–8 km，街上的水漫不过去；
客户模型没有 [SUBCATCHMENTS]，塘自己那片汇水的产流在模型里根本不存在；进水只有专用管或泵。
这些塘与地表**没有模型看得见的交互**，方式二才是物理上对的表达。

用法：python tools/couple_2d.py <汇水区> <态 L1|L2|L2B|L2L>
"""
import json, os, pathlib, re, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import OUT, WORK

SUFFIX = {"L1": "_baseline", "L2": "", "L2P": "_L2v", "L2B": "_L2box", "L2L": "_L2lag",
          "L2AB": "_L2AB"}      # L2+A+B：Parsons（交付模型原件）视为既成事实，其上再加本系统箱涵（tools/l2ab.py）
# 场景标签：空＝原来那套 24 小时雨；设成 _R61D2 就读 61 mm/2 h 的那套。
# 输出也带上它，老结果不被覆盖。
SCEN = os.environ.get("C2D_SCEN", "")
PAD_M = 600.0


def sect(p, wanted):
    out = {w: [] for w in wanted}
    cur = None
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.strip()
            if s.startswith("["):
                cur = s.split()[0].strip("[]").upper(); continue
            if cur in out and s and not s.startswith(";"):
                out[cur].append(s)
    return out


def overflow(stem: pathlib.Path, log=print):
    """逐节点溢流过程线。索引映射必须验过才往下走（见下方幽灵节点判据）。"""
    inp, rpt_f, out_f = (stem.with_suffix(e) for e in (".inp", ".rpt", ".out"))
    coords = {}
    for r in sect(inp, ["COORDINATES"])["COORDINATES"]:
        f = r.split()
        if len(f) >= 3:
            try:
                coords[f[0]] = (float(f[1]), float(f[2]))
            except ValueError:
                pass
    rpt = rpt_f.read_text(encoding="utf-8", errors="replace")
    blk = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", rpt, re.S)
    known = {}
    if blk:
        for ln in blk.group(1).splitlines():
            c = ln.split()
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                try:
                    known[c[0]] = float(c[-2]) * 1000.0
                except ValueError:
                    pass
    from swmm.toolkit import output
    from swmm.toolkit.shared_enum import ElementType, NodeAttribute, Time
    h = output.init(); output.open(h, str(out_f))
    n_node = output.get_proj_size(h)[1]
    n_t = output.get_times(h, Time.NUM_PERIODS.value)
    dt_s = output.get_times(h, Time.REPORT_STEP.value)
    # 这几个接口要传枚举**对象**：传 .value 会报 Error 423 或静默取到别的节点
    names = [output.get_elem_name(h, ElementType.NODE, i) for i in range(n_node)]
    series = {}
    for i, nm in enumerate(names):
        q = np.asarray(output.get_node_series(h, i, NodeAttribute.FLOODING_LOSSES, 0, n_t),
                       dtype=float)
        if q.max() > 0:
            series[nm] = q
    output.close(h)
    # 唯一能验映射的判据：.rpt 说从没溢流过的节点，序列里不该有数
    ghost = [nm for nm in series if nm not in known]
    if ghost:
        raise SystemExit(f"索引映射错位，幽灵节点 {ghost[:8]}")
    log(f"  索引映射校验通过（.rpt 溢流节点 {len(known)}，序列有数 {len(series)}，幽灵 0）")
    # 逐点按 .rpt 的精确体积缩放：5 min 采样会系统性低估，保形状、对总量
    for nm, q in series.items():
        cur = float(q.sum() * dt_s / 1000.0)
        if known.get(nm, 0) > 0 and cur > 0:
            series[nm] = q * (known[nm] / cur)
    keys = sorted(k for k in series if k in coords
                  and float(series[k].sum() * dt_s / 1000.0) > 1.0)
    Q = np.vstack([series[k] for k in keys]); XY = np.array([coords[k] for k in keys])
    tot = float(Q.sum() * dt_s / 1000.0)
    log(f"  溢流 >1 m³ 的节点 {len(keys)} 个，累计 {tot:,.0f} m³"
        f"（.rpt 合计 {sum(known.values()):,.0f} m³）")
    return Q, XY, np.array(keys), float(dt_s), tot


def ponds_for(name: str, state: str, cfg) -> list[dict]:
    """这一态有哪些塘、各自的塘底/塘顶/地面。回不出来就返回空（防线1 本来就没有）。"""
    if state == "L1":
        return []
    if state == "L2AB":                                   # Parsons 的塘 + 本系统的箱涵，两套都要
        return ponds_for(name, "L2", cfg) + ponds_for(name, "L2B_AB", cfg)
    if state in ("L2B", "L2L", "L2B_AB"):
        if state == "L2B_AB":
            state = "L2AB"
        # 换了降雨场景就要读那个场景的塘位（Mussafah.L2B.R61D2.ponds.geojson）。
        # 原先只读不带标签的那份（24 小时雨的塘位），打印出来的塘底/地面是旧设计的。
        f = OUT / f"{name}.{state}{'.' + SCEN.lstrip('_') if SCEN else ''}.ponds.geojson"
        if not f.exists():
            f = OUT / f"{name}.{state}.ponds.geojson"
        if not f.exists():
            return []
        return [{"geometry": x["geometry"], **x["properties"]}
                for x in json.loads(f.read_text(encoding="utf-8"))["features"]
                if x.get("geometry")]
    # L2：客户的塘。轮廓在底图里，塘底/塘深在 .inp 的 [STORAGE]
    from swbrain import threelines as T
    net = T.network(cfg, name)
    stor = {}
    for r in sect(WORK / f"{name}.inp", ["STORAGE"])["STORAGE"]:
        c = r.split()
        try:
            stor[c[0]] = (float(c[1]), float(c[2]))
        except (IndexError, ValueError):
            pass
    out = []
    for ft in net.get("features", []):
        p = ft.get("properties") or {}
        if p.get("layer") != "pond":
            continue
        lab = str(p.get("label") or "")
        hit = stor.get(lab) or next((v for k, v in stor.items()
                                     if k.replace("-", "") == lab.replace("-", "")), None)
        if hit is None:
            continue
        out.append({"geometry": ft["geometry"], "id": lab,
                    "bottom_m": hit[0], "top_m": hit[0] + hit[1]})
    return out


def build_dem(cfg, name, state, bbox, ponds, pond_in_2d: bool = False, log=print):
    """裁 DTM，按**塘在哪个模型里**改地形。返回 (dem, transform, crs, nodata, 各塘的处理)。"""
    import rasterio
    from rasterio.features import rasterize
    from rasterio.windows import from_bounds
    from shapely.geometry import shape
    from shapely.ops import transform as reproj
    from pyproj import Transformer
    from swbrain import terrain
    x0, y0, x1, y1 = bbox
    with terrain.open_dtm(cfg) as ds:
        win = from_bounds(x0, y0, x1, y1, ds.transform)
        dem = ds.read(1, window=win).astype("float32")
        tr = ds.window_transform(win)
        crs, nod = ds.crs, ds.nodata
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform
    how = []
    for p in ponds:
        g = reproj(to_utm, shape(p["geometry"]))
        m = rasterize([g], out_shape=dem.shape, transform=tr, fill=0,
                      default_value=1, all_touched=True).astype(bool)
        if not m.any():
            continue
        gnd = float(np.median(dem[m]))
        bot, top = float(p["bottom_m"]), float(p["top_m"])
        if pond_in_2d:
            # 方式一：塘的库容交给二维。前提是一维里这个蓄水节点已经删掉，否则重复计量
            dem[m] = bot
            how.append((p.get("id"), "挖（塘在二维）", gnd, bot, top, int(m.sum())))
        elif top > gnd + 0.30:
            # 筑堤式且塘在一维：整个轮廓抬到堰顶做成**实心障碍**。
            # 若只抬一圈围堰、内部填到塘底，二维里就又成了一个洼地——
            # 虽然外面的水漫不进去，但那是个随时会被踩中的重复计量陷阱。
            dem[m] = top
            how.append((p.get("id"), "垒成实心障碍", gnd, bot, top, int(m.sum())))
        else:
            # 地下箱涵、或塘顶不高出地面且留在一维：地面没变
            how.append((p.get("id"), "不动（塘在一维）", gnd, bot, top, int(m.sum())))
    return dem, tr, crs, nod, how


if __name__ == "__main__":
    from swbrain import db
    name = sys.argv[1] if len(sys.argv) > 1 else "Mussafah"
    state = sys.argv[2] if len(sys.argv) > 2 else "L1"
    cfg = db.load_config(None)
    stem = WORK / f"{name}{SUFFIX[state]}{SCEN}"
    print(f"{name} · {state}（{stem.name}）")
    Q, XY, keys, dt_s, tot = overflow(stem)
    bbox = (XY[:, 0].min() - PAD_M, XY[:, 1].min() - PAD_M,
            XY[:, 0].max() + PAD_M, XY[:, 1].max() + PAD_M)
    ponds = ponds_for(name, state, cfg)
    in2d = os.environ.get("POND_IN_2D", "").lower() in ("1", "true", "yes")
    dem, tr, crs, nod, how = build_dem(cfg, name, state, bbox, ponds, pond_in_2d=in2d)
    if how:
        print(f"  {'塘':<10}{'处理':>5}{'地面':>8}{'塘底':>8}{'塘顶':>8}{'格数':>7}")
        for pid, act, gnd, bot, top, n in how:
            print(f"  {str(pid):<10}{act:>5}{gnd:>8.2f}{bot:>8.2f}{top:>8.2f}{n:>7,}")
    else:
        print("  这一态没有塘，地形原样不动")
    print(f"  塘的表达：{'二维（DEM 已挖，一维须无此蓄水节点）' if in2d else '一维（DEM 不挖）'}")
    import rasterio
    d2 = pathlib.Path(WORK) / "c2d"; d2.mkdir(exist_ok=True)
    tag = f"{name}_{state}{SCEN}"
    tif = d2 / f"dem_{tag}.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=dem.shape[0], width=dem.shape[1],
                       count=1, dtype="float32", crs=crs, transform=tr, nodata=nod,
                       compress="lzw") as dst:
        dst.write(dem, 1)
    np.savez_compressed(d2 / f"overflow_{tag}.npz", Q=Q, XY=XY, names=keys, dt_s=dt_s)
    qs = Q.sum(axis=0) * 3.6
    ip = int(np.argmax(qs)); nz = np.nonzero(qs > qs.max() * 0.01)[0]
    json.dump({"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3],
               "dt_s": dt_s, "n_t": int(Q.shape[1]), "total_m3": tot,
               "peak_h": ip * dt_s / 3600, "state": state,
               "window_h": [float(nz[0] * dt_s / 3600), float(nz[-1] * dt_s / 3600)],
               "ponds": [{"id": a, "action": b, "ground": c, "bottom": d, "top": e}
                         for a, b, c, d, e, _ in how]},
              open(d2 / f"coupling_{tag}.json", "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)   # 必须显式 utf-8：Windows 默认写 GBK，容器里读不了
    print(f"  DEM {dem.shape[1]}×{dem.shape[0]} 格 → {tif.name}；"
          f"峰值 {qs[ip]:,.0f} m³/h 在第 {ip * dt_s / 3600:.2f} h")
