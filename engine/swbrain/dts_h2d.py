# -*- coding: utf-8 -*-
"""
ANUGA 二维水动力结果 (.sww) -> DTS Cloud h2d 格式 (shp + RESULTSxxx.dat) 转换器（给飞渡 DTS 用）

系统里的用法（2026-10-18 接入）：交付导出（export_delivery_rb01 / c1）与自定义运行的后处理，
每个态写 <态>/飞渡DTS/（shp/、dat/、h2d_index.json；不出 dat 同名 CSV），见 export_state()。
命令行仍可单独用（根目录 hydro2d_convert.py 或 python -m swbrain.dts_h2d），选项与原脚本一致。

参考格式（DTS Cloud/7.1/SDK/assets/h2d）：
  shp/<name>.shp|shx|dbf|prj|cpg
      - 面要素，每个要素一个三角形（4 个点，首尾闭合，顺时针）
      - 属性字段：ID (N,19,11) 单元编号；Elev (N,19,11) 单元底高程 (m)
  dat/RESULTS001.dat ... RESULTSnnn.dat  每个时刻一个文件
      - 无文件头，定长 28 字节小端二进制记录，按 ID 升序排列：
          int32   ID      单元编号（与 shp 的 ID 字段对应）
          float64 h       水深 (m)，干单元为 0
          float64 u       x 方向流速 (m/s)
          float64 v       y 方向流速 (m/s)

用法示例：
  python hydro2d_convert.py "<结果目录或 .sww 文件>" -o <输出目录> --name abudhabi_z40
  python hydro2d_convert.py <结果目录> -o out --cells-from-geojson       # shp 只保留 maximum_depth geojson 中出现的网格单元
  python hydro2d_convert.py <结果目录> -o out --target-crs EPSG:4547     # 重投影后输出 shp

依赖：numpy, scipy, pyshp, pyproj
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import shapefile  # pyshp
from pyproj import CRS, Transformer
from scipy.io import netcdf_file

# dat 记录结构：int32 ID + 3 × float64，小端，无对齐填充（共 28 字节）
DAT_DTYPE = np.dtype([("id", "<i4"), ("h", "<f8"), ("u", "<f8"), ("v", "<f8")])
assert DAT_DTYPE.itemsize == 28


# --------------------------------------------------------------------------- #
# 读取 sww
# --------------------------------------------------------------------------- #
class SwwMesh:
    """从 ANUGA sww 中取出网格与质心量。所有数组都复制到内存，读完即关闭文件。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        with netcdf_file(str(self.path), "r", mmap=False) as nc:
            v = nc.variables
            self.x = np.array(v["x"][:], dtype=np.float64)
            self.y = np.array(v["y"][:], dtype=np.float64)
            self.volumes = np.array(v["volumes"][:], dtype=np.int64)  # (ntri, 3)
            self.time = np.array(v["time"][:], dtype=np.float64)  # 秒
            # 质心量优先；老版本 sww 没有 *_c 时退化为顶点平均
            if "elevation_c" in v:
                self.elev_c = np.array(v["elevation_c"][:], dtype=np.float64)
            else:
                self.elev_c = np.array(v["elevation"][:], dtype=np.float64)[self.volumes].mean(axis=1)
            has_c = all(k in v for k in ("stage_c", "xmomentum_c", "ymomentum_c"))
            if has_c:
                self.stage_c = np.array(v["stage_c"][:], dtype=np.float64)
                self.xmom_c = np.array(v["xmomentum_c"][:], dtype=np.float64)
                self.ymom_c = np.array(v["ymomentum_c"][:], dtype=np.float64)
            else:
                vol = self.volumes
                self.stage_c = np.array(v["stage"][:], dtype=np.float64)[:, vol].mean(axis=2)
                self.xmom_c = np.array(v["xmomentum"][:], dtype=np.float64)[:, vol].mean(axis=2)
                self.ymom_c = np.array(v["ymomentum"][:], dtype=np.float64)[:, vol].mean(axis=2)
            attrs = {}
            for k, val in nc._attributes.items():
                attrs[k] = val.decode(errors="ignore") if isinstance(val, bytes) else val
            self.attrs = attrs
        # ANUGA 的 xllcorner/yllcorner 偏移（本例为 0）
        self.x += float(self.attrs.get("xllcorner", 0.0) or 0.0)
        self.y += float(self.attrs.get("yllcorner", 0.0) or 0.0)

    @property
    def ntri(self) -> int:
        return self.volumes.shape[0]

    @property
    def nsteps(self) -> int:
        return len(self.time)

    def centroids(self) -> tuple[np.ndarray, np.ndarray]:
        return self.x[self.volumes].mean(axis=1), self.y[self.volumes].mean(axis=1)

    def signed_area(self) -> np.ndarray:
        p = self.volumes
        x, y = self.x, self.y
        return 0.5 * ((x[p[:, 1]] - x[p[:, 0]]) * (y[p[:, 2]] - y[p[:, 0]])
                      - (x[p[:, 2]] - x[p[:, 0]]) * (y[p[:, 1]] - y[p[:, 0]]))

    def depth_velocity(self, step: int, hmin: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """返回第 step 个时刻每个三角形的 (h, u, v)。h<0 截断为 0，h<=hmin 视为干单元、流速置 0。"""
        h = self.stage_c[step] - self.elev_c
        h = np.where(h > 0.0, h, 0.0)
        wet = h > hmin
        u = np.zeros_like(h)
        v = np.zeros_like(h)
        u[wet] = self.xmom_c[step][wet] / h[wet]
        v[wet] = self.ymom_c[step][wet] / h[wet]
        return h, u, v


# --------------------------------------------------------------------------- #
# 网格单元 (cell) 与 geojson cell_id 的对应
# --------------------------------------------------------------------------- #
def load_delivery_summary(result_dir: Path | None) -> dict | None:
    if result_dir is None:
        return None
    p = result_dir / "delivery_summary.json"
    if not p.is_file():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def triangle_cell_ids(mesh: SwwMesh, summary: dict | None) -> np.ndarray:
    """
    按三角形质心落在哪个矩形网格单元里，计算 geojson 中的 cell_id。
    cell_id 采用栅格顺序（左上角为 0，行优先）：cell_id = (ny-1-j) * nx + i
    其中 i 为列号（x 方向），j 为自下而上的行号（y 方向）。
    """
    cx, cy = mesh.centroids()
    if summary and "domain" in summary:
        x0, y0, x1, y1 = summary["domain"]["bounds_epsg32640"]
        cs = float(summary["domain"]["cell_size_m"])
    else:
        # 从网格推断：ANUGA rectangular_cross 每个矩形单元 4 个三角形，面积 = cs^2 / 4
        x0, y0, x1, y1 = mesh.x.min(), mesh.y.min(), mesh.x.max(), mesh.y.max()
        cs = float(np.sqrt(4.0 * np.abs(mesh.signed_area()).mean()))
    nx = int(round((x1 - x0) / cs))
    ny = int(round((y1 - y0) / cs))
    i = np.clip(np.floor((cx - x0) / cs).astype(np.int64), 0, nx - 1)
    j = np.clip(np.floor((cy - y0) / cs).astype(np.int64), 0, ny - 1)
    return (ny - 1 - j) * nx + i


def cell_ids_from_geojson(path: Path) -> set[int]:
    with open(path, encoding="utf-8") as f:
        g = json.load(f)
    return {int(ft["properties"]["cell_id"]) for ft in g.get("features", [])}


# --------------------------------------------------------------------------- #
# 写 shp
# --------------------------------------------------------------------------- #
def esri_wkt(crs: CRS) -> str:
    return crs.to_wkt(version="WKT1_ESRI")


def write_shapefile(mesh: SwwMesh, ids: np.ndarray, keep: np.ndarray, out_base: Path,
                    src_crs: CRS, target_crs: CRS | None, elev: np.ndarray | None = None) -> int:
    """
    写三角形面 shp。keep 为 bool 掩码（保留哪些三角形）。
    elev 为写入 Elev 字段的数组（默认用 sww 质心底高程）。
    三角形按顺时针写入（shapefile 外环规范）。返回写入要素数。
    """
    if elev is None:
        elev = mesh.elev_c
    x, y = mesh.x, mesh.y
    if target_crs is not None and not target_crs.equals(src_crs):
        tr = Transformer.from_crs(src_crs, target_crs, always_xy=True)
        x, y = tr.transform(x, y)
        x, y = np.asarray(x), np.asarray(y)
    vol = mesh.volumes
    # 在源坐标系里判断朝向；逆时针 (面积>0) 的交换后两个顶点变为顺时针
    ccw = mesh.signed_area() > 0
    tri = vol.copy()
    tri[ccw, 1], tri[ccw, 2] = vol[ccw, 2], vol[ccw, 1]

    out_base.parent.mkdir(parents=True, exist_ok=True)
    w = shapefile.Writer(str(out_base), shapeType=shapefile.POLYGON, autoBalance=True)
    w.field("ID", "N", 19, 11)
    w.field("Elev", "N", 19, 11)
    n = 0
    for t in np.flatnonzero(keep):
        p = tri[t]
        ring = [(float(x[p[0]]), float(y[p[0]])),
                (float(x[p[1]]), float(y[p[1]])),
                (float(x[p[2]]), float(y[p[2]])),
                (float(x[p[0]]), float(y[p[0]]))]
        w.poly([ring])
        w.record(int(ids[t]), float(elev[t]))
        n += 1
    w.close()
    crs_out = target_crs if target_crs is not None else src_crs
    out_base.with_suffix(".prj").write_text(esri_wkt(crs_out), encoding="ascii")
    out_base.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
    return n


# --------------------------------------------------------------------------- #
# 写 / 读 dat
# --------------------------------------------------------------------------- #
def write_dat(path: Path, ids: np.ndarray, h: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """写一个时刻的 dat，返回实际写入（已按 ID 排序）的结构化数组。"""
    rec = np.empty(len(ids), dtype=DAT_DTYPE)
    rec["id"] = ids
    rec["h"] = h
    rec["u"] = u
    rec["v"] = v
    rec = rec[np.argsort(rec["id"], kind="stable")]  # 参考文件按 ID 升序
    path.parent.mkdir(parents=True, exist_ok=True)
    rec.tofile(str(path))
    return rec


def read_dat(path: Path) -> np.ndarray:
    """读取 RESULTSxxx.dat，返回结构化数组 (id, h, u, v)。"""
    return np.fromfile(str(path), dtype=DAT_DTYPE)


def write_dat_csv(path: Path, rec: np.ndarray, precision: int = 6) -> None:
    """
    与 dat 同步输出的 CSV 版：首行表头 ID,h,u,v，之后每行一条记录，与 dat 一一对应、顺序相同。
    不含注释行，Excel / pandas 可直接打开。
    """
    cols = np.column_stack([rec["id"].astype(np.float64), rec["h"], rec["u"], rec["v"]])
    np.savetxt(str(path), cols, fmt=["%d", f"%.{precision}f", f"%.{precision}f", f"%.{precision}f"],
               delimiter=",", header="ID,h,u,v", comments="", newline="\n", encoding="utf-8")


def write_index_csv(path: Path, index: list[dict]) -> None:
    """dat 目录下的汇总表：每个 dat 文件对应的时刻、记录数、湿单元数、水深范围。"""
    lines = ["file,step,time_seconds,time_minutes,records,wet_cells,min_depth_m,max_depth_m,min_wet_depth_m"]
    for t in index:
        mw = "" if t["min_wet_depth_m"] is None else f"{t['min_wet_depth_m']:.6f}"
        lines.append(f"{t['file']},{t['step']},{t['time_seconds']:.0f},{t['time_minutes']:.1f},{t['records']},"
                     f"{t['wet_cells']},{t['min_depth_m']:.6f},{t['max_depth_m']:.6f},{mw}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")  # 带 BOM，Excel 打开不乱码


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def find_sww(inp: Path) -> tuple[Path, Path | None]:
    """返回 (sww 路径, 结果目录或 None)。"""
    if inp.is_file():
        return inp, inp.parent
    if inp.is_dir():
        summary = inp / "delivery_summary.json"
        if summary.is_file():
            with open(summary, encoding="utf-8") as f:
                rel = json.load(f).get("outputs", {}).get("native_sww")
            if rel and (inp / rel).is_file():
                return inp / rel, inp
        cands = sorted(inp.glob("*.sww"))
        if cands:
            return cands[0], inp
    raise FileNotFoundError(f"找不到 .sww 文件：{inp}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="结果目录（含 delivery_summary.json / *.sww）或 .sww 文件")
    ap.add_argument("-o", "--out", required=True, help="输出目录，会生成 shp/ 与 dat/ 两个子目录")
    ap.add_argument("--name", default=None, help="shp 文件名（不含扩展名），默认取 sww 文件名")
    ap.add_argument("--src-crs", default=None,
                    help="sww 坐标系，默认读 delivery_summary.json 的 surface.crs，再否则 EPSG:32640")
    ap.add_argument("--target-crs", default=None, help="shp 输出坐标系（如 EPSG:4547），默认与源一致，不重投影")
    ap.add_argument("--id-base", type=int, default=1, help="单元 ID 起始值（默认 1，即 ID = 三角形序号 + 1）")
    ap.add_argument("--hmin", type=float, default=1e-3, help="干湿阈值 (m)，h<=hmin 时流速写 0（默认 0.001）")
    ap.add_argument("--elev-offset", type=float, default=0.0,
                    help="shp 的 Elev 字段整体加上的偏移量 (m)，用于抬高/压低整层高度或做垂直基准换算（默认 0）")
    ap.add_argument("--elev-const", type=float, default=None,
                    help="把所有单元的 Elev 强制设为该常数 (m)，忽略 sww 底高程（例如平铺在固定高度上展示）")
    ap.add_argument("--elev-min", type=float, default=None,
                    help="shp 只保留底高程 >= 该值的三角形 (m)，用于排除海域/永久水面（例如 --elev-min 0.05 只留陆地）")
    ap.add_argument("--cells-from-geojson", action="store_true",
                    help="shp 只保留 maximum_depth_wgs84.geojson 中出现的网格单元对应的三角形（排除永久水面/从未淹没单元）")
    ap.add_argument("--geojson", default=None,
                    help="配合 --cells-from-geojson 指定 geojson 路径，默认取结果目录下的 maximum_depth_wgs84.geojson")
    ap.add_argument("--dat-subset", action="store_true",
                    help="dat 也只写 shp 中保留的单元（默认 dat 覆盖全域，与参考数据一致）")
    ap.add_argument("--drop-dry", action="store_true",
                    help="dat 中不写干单元 (h<=hmin) 记录（参考数据中 THC_*.dat 的风格）")
    ap.add_argument("--skip-initial", action="store_true", help="跳过 t=0 的初始时刻")
    ap.add_argument("--dat-prefix", default="RESULTS", help="dat 文件名前缀（默认 RESULTS，生成 RESULTS001.dat ...）")
    ap.add_argument("--dat-dir", default="dat", help="dat 输出子目录名（默认 dat；配合 --uv-scale 可写成 dat_uv10 等）")
    ap.add_argument("--uv-scale", type=float, default=1.0,
                    help="u、v 流速放大倍数（默认 1，不放大）。仅用于平台箭头/流场显示，水深 h 不变")
    ap.add_argument("--no-shp", action="store_true", help="只生成 dat，不生成 shp")
    ap.add_argument("--no-csv", action="store_true",
                    help="不生成与 dat 同名的 .csv 及 RESULTS_index.csv（默认生成）")
    ap.add_argument("--csv-precision", type=int, default=6, help="csv 中 h/u/v 的小数位数（默认 6）")
    return ap


def main(argv=None) -> int:
    run(build_parser().parse_args(argv))
    return 0


def convert(sww, out, **opts) -> dict:
    """函数式调用：选项名同命令行（下划线），如 convert(sww, out, name="X", no_csv=True)。返回写进 h2d_index.json 的内容。"""
    args = build_parser().parse_args([str(sww), "-o", str(out)])
    for k, v in opts.items():
        if not hasattr(args, k):
            raise TypeError(f"未知选项 {k}")
        setattr(args, k, v)
    return run(args)


def run(args) -> dict:
    inp = Path(args.input)
    sww_path, result_dir = find_sww(inp)
    out = Path(args.out)
    summary = load_delivery_summary(result_dir)

    print(f"[读取] {sww_path}")
    mesh = SwwMesh(sww_path)
    print(f"       三角形 {mesh.ntri}，顶点 {len(mesh.x)}，时刻 {mesh.nsteps} 个："
          f"{mesh.time.min():.0f}s ~ {mesh.time.max():.0f}s")
    print(f"       x {mesh.x.min():.1f}~{mesh.x.max():.1f}, y {mesh.y.min():.1f}~{mesh.y.max():.1f}, "
          f"底高程 {mesh.elev_c.min():.3f}~{mesh.elev_c.max():.3f} m")

    # 坐标系
    src_crs_name = args.src_crs or (summary or {}).get("surface", {}).get("crs") or "EPSG:32640"
    src_crs = CRS.from_user_input(src_crs_name)
    target_crs = CRS.from_user_input(args.target_crs) if args.target_crs else None
    print(f"[坐标] 源 {src_crs.to_string()}" + (f" -> 输出 {target_crs.to_string()}" if target_crs else ""))

    ids = np.arange(mesh.ntri, dtype=np.int64) + args.id_base
    keep = np.ones(mesh.ntri, dtype=bool)
    if args.elev_min is not None:
        keep &= mesh.elev_c >= args.elev_min
        print(f"[筛选] 底高程 >= {args.elev_min} m 的三角形 {keep.sum()} / {mesh.ntri}")
    if args.cells_from_geojson:
        gj = Path(args.geojson) if args.geojson else None
        if gj is None:
            if result_dir is None:
                sys.exit("--cells-from-geojson 需要结果目录或 --geojson")
            rel = (summary or {}).get("outputs", {}).get("maximum_depth", "maximum_depth_wgs84.geojson")
            gj = result_dir / rel
        wanted = cell_ids_from_geojson(gj)
        cell_ids = triangle_cell_ids(mesh, summary)
        keep &= np.isin(cell_ids, np.fromiter(wanted, dtype=np.int64))
        print(f"[筛选] geojson {gj.name} 含 {len(wanted)} 个单元，匹配三角形 {keep.sum()} / {mesh.ntri}")

    # ---- Elev 字段（高度）
    if args.elev_const is not None:
        elev_out = np.full(mesh.ntri, float(args.elev_const))
        print(f"[高度] Elev 强制为常数 {args.elev_const} m")
    else:
        elev_out = mesh.elev_c + args.elev_offset
        if args.elev_offset:
            print(f"[高度] Elev 整体偏移 {args.elev_offset:+} m -> {elev_out.min():.3f}~{elev_out.max():.3f} m")

    # ---- shp
    name = args.name or sww_path.stem
    if not args.no_shp:
        shp_base = out / "shp" / name
        n = write_shapefile(mesh, ids, keep, shp_base, src_crs, target_crs, elev=elev_out)
        print(f"[shp ] {shp_base}.shp  要素 {n}")

    # ---- dat
    dat_dir = out / args.dat_dir
    dat_dir.mkdir(parents=True, exist_ok=True)
    if args.uv_scale != 1.0:
        print(f"[流速] u、v 放大 {args.uv_scale:g} 倍 -> {dat_dir}")
    dat_mask = keep if args.dat_subset else np.ones(mesh.ntri, dtype=bool)
    steps = list(range(mesh.nsteps))
    if args.skip_initial and steps:
        steps = steps[1:]
    index = []
    for k, step in enumerate(steps, start=1):
        h, u, v = mesh.depth_velocity(step, args.hmin)
        if args.uv_scale != 1.0:
            u = u * args.uv_scale
            v = v * args.uv_scale
        m = dat_mask.copy()
        if args.drop_dry:
            m &= h > args.hmin
        fname = f"{args.dat_prefix}{k:03d}.dat"
        rec = write_dat(dat_dir / fname, ids[m], h[m], u[m], v[m])
        n = len(rec)
        if not args.no_csv:
            write_dat_csv((dat_dir / fname).with_suffix(".csv"), rec, args.csv_precision)
        wet = int((h[m] > args.hmin).sum())
        hw = h[m]
        hwet = hw[hw > args.hmin]
        index.append({"file": fname, "step": int(step), "time_seconds": float(mesh.time[step]),
                      "time_minutes": float(mesh.time[step]) / 60.0, "records": int(n), "wet_cells": wet,
                      "max_depth_m": float(hw.max()) if hw.size else 0.0,
                      "min_depth_m": float(hw.min()) if hw.size else 0.0,
                      "min_wet_depth_m": float(hwet.min()) if hwet.size else None})
        print(f"[dat ] {fname}  t={mesh.time[step]:>8.0f}s  记录 {n:>7d}  湿单元 {wet:>7d}  "
              f"水深 {index[-1]['min_depth_m']:.3f}~{index[-1]['max_depth_m']:.3f} m")

    depth_summary = {
        "max_depth_m": max(t["max_depth_m"] for t in index) if index else 0.0,
        "max_depth_file": max(index, key=lambda t: t["max_depth_m"])["file"] if index else None,
        "max_depth_time_minutes": max(index, key=lambda t: t["max_depth_m"])["time_minutes"] if index else None,
        "min_depth_m": min(t["min_depth_m"] for t in index) if index else 0.0,
        "min_wet_depth_m": min((t["min_wet_depth_m"] for t in index if t["min_wet_depth_m"] is not None),
                               default=None),
        "note": "min_depth_m 含干单元(通常为 0)；min_wet_depth_m 为 h>hmin 的湿单元最小水深",
    }

    meta = {
        "source_sww": str(sww_path),
        "run_id": (summary or {}).get("run_id"),
        "crs_shp": (target_crs or src_crs).to_string(),
        "crs_sww": src_crs.to_string(),
        "triangles_total": int(mesh.ntri),
        "triangles_in_shp": int(keep.sum()) if not args.no_shp else 0,
        "id_base": args.id_base,
        "hmin_m": args.hmin,
        "dat_dir": args.dat_dir,
        "uv_scale": args.uv_scale,
        "elev_min_m": args.elev_min,
        "elev_offset_m": args.elev_offset,
        "elev_const_m": args.elev_const,
        "elev_range_m": [float(elev_out.min()), float(elev_out.max())],
        "dat_record": "int32 ID, float64 depth_m, float64 u_m_s, float64 v_m_s (little-endian, 28 bytes)",
        "shp_fields": {"ID": "cell id (= triangle index + id_base)", "Elev": "bed elevation at centroid (m)"},
        "depth_summary": depth_summary,
        "timesteps": index,
    }
    index_name = "h2d_index.json" if args.dat_dir == "dat" else f"h2d_index_{args.dat_dir}.json"
    with open(out / index_name, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    if not args.no_csv:
        write_index_csv(dat_dir / f"{args.dat_prefix}_index.csv", index)
        print(f"[csv ] 每个 dat 已同步生成同名 .csv，汇总表 {dat_dir / (args.dat_prefix + '_index.csv')}")
    print(f"[完成] 输出目录 {out}，时刻索引 {index_name}")
    return meta


# --------------------------------------------------------------------------- #
# 系统接入：交付目录每个态的 飞渡DTS/
# --------------------------------------------------------------------------- #
DTS_DIR = "飞渡DTS"
DTS_MARK = ("<!-- dts:start -->", "<!-- dts:end -->")


def export_state(sww: Path, state_dir: Path, name: str) -> dict | None:
    """把一个态的 .sww 转成 <state_dir>/飞渡DTS/（先清空这个文件夹再写）；没有 .sww 返回 None。"""
    import shutil
    sww = Path(sww)
    if not sww.is_file():
        print(f"  ! 飞渡DTS：没有 {sww.name}，跳过")
        return None
    o = Path(state_dir) / DTS_DIR
    if o.exists():
        shutil.rmtree(o)
    return convert(sww, o, name=name, no_csv=True)


def readme_section(state_dirs: dict) -> str:
    """交付 readme 里的一节：{态的显示名: 文件夹名}"""
    return "\n".join([DTS_MARK[0], "## 飞渡 DTS 数据", "",
                      "每个态的 `飞渡DTS/` 文件夹：二维结果转成飞渡 DTS（DTS Cloud h2d）格式，格式说明见 `飞渡DTS说明.md`。", "",
                      *[f"- `{d}/{DTS_DIR}/`：{nm}" for nm, d in state_dirs.items()], DTS_MARK[1]])


def delivery_doc(rows: list[tuple]) -> str:
    """rows：(态, 三角形数, 时刻数, 起止 s, 最大水深 m, 坐标系)"""
    L = ["# 飞渡 DTS 数据（DTS Cloud h2d 格式）", "",
         "每个态的 `飞渡DTS/` 由该态的 ANUGA 结果（`一二维/*_二维水深与流速时序.sww`）转换而来，生成：`swbrain/dts_h2d.py`。", "",
         "## 文件", "",
         "| 文件 | 内容 |", "|---|---|",
         "| `shp/<运行>_<态>.shp`（.shx .dbf .prj .cpg） | 二维计算网格，每个三角形一个面（顺时针闭合）；字段 `ID` 单元编号、`Elev` 单元形心底高程（m） |",
         "| `dat/RESULTS001.dat` … | 每个时刻一个文件，无文件头，定长 28 字节小端记录，按 ID 升序：int32 `ID`、float64 水深 h（m，干单元 0）、float64 x 向流速 u（m/s）、float64 y 向流速 v（m/s） |",
         "| `h2d_index.json` | 坐标系、ID 规则、干湿阈值；`timesteps` 里每个 dat 对应的时刻（s / min）、记录数、湿单元数、水深范围；全程水深汇总 |", "",
         "## 约定", "",
         "- `ID` = 三角形序号 + 1，shp 与 dat 一一对应；dat 覆盖全部单元（含干单元）。",
         "- 坐标系：与模型一致，EPSG:32640（WGS 84 / UTM 40N），不重投影。",
         "- 干湿阈值 0.001 m：水深 ≤ 0.001 m 的单元流速写 0。",
         "- 时刻从降雨开始计，每 5 分钟一帧（与 `.sww` 相同）。",
         "- 不出 dat 同名 CSV 与 `RESULTS_index.csv`（2026-10-18 定，省盘；时刻索引在 `h2d_index.json`）；需要时用 `python hydro2d_convert.py <.sww> -o <目录>` 单独生成。", "",
         "## 本次", "", "| 态 | 三角形 | 时刻数 | 时间范围 | 全程最大水深 | 坐标系 |", "|---|---|---|---|---|---|"]
    L += [f"| {st} | {n:,} | {k} | {t0:.0f}–{t1:.0f} s | {hm:.2f} m | {crs} |" for st, n, k, t0, t1, hm, crs in rows]
    return "\n".join(L) + "\n"


def export_delivery(run_name: str, sww_dir: Path, state_dirs: dict, root_dir: Path, names: dict | None = None) -> list[tuple]:
    """state_dirs：{态: 交付目录里这个态的文件夹}；sww 取 sww_dir/<运行>_<态>.sww；root_dir 写 飞渡DTS说明.md。返回每个态的摘要行。"""
    rows = []
    for st, d in state_dirs.items():
        m = export_state(Path(sww_dir) / f"{run_name}_{st}.sww", d, f"{run_name}_{st}")
        if m:
            ts = m["timesteps"]
            rows.append(((names or {}).get(st, st), m["triangles_total"], len(ts), ts[0]["time_seconds"] if ts else 0, ts[-1]["time_seconds"] if ts else 0,
                         m["depth_summary"]["max_depth_m"], m["crs_shp"]))
    if rows:
        (Path(root_dir) / "飞渡DTS说明.md").write_text(delivery_doc(rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    sys.exit(main())
