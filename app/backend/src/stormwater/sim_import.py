"""统一的交付登记入口：按交付形状自动选 `sim_sww` / `sim_city` / `sim_c1`，再抽取并登记成仿真任务。

NAS 的 `runtime/out/交付/` 下三种形状并存，各有各的抽取口径，不能混用：

| 形状 | 例 | 判据 | 走 |
|---|---|---|---|
| 旧 .sww 交付 | `Mussafah_00_61mm2h` | 态目录下 `二维/` 有 `*_二维水深时序.sww` 且 `二维/耦合的一维/` 在 | `sim_sww`（15 m 规则网格切的三角网，每格 4 个三角形） |
| 全市一二维 | `全市_61mm2h`；`ADM_City_61mm1h_100m_after` | 目录名以「全市」开头；或态目录下有 `1D/` + `2D/*_2d.npz`（英文布局） | `sim_city`（中文布局 250 m / 英文布局 100 m 落图格网） |
| 一二维 npz 交付 | `C1_模型版_61mm2h_5m`、`Mussafah_00_模型版_61mm2h`、`YAS_模型版_…` | 其余 | `sim_c1`（C1 的 `一二维/`；或 `一维/ + 二维/` 加 `work/c2d` 里的 npz） |
| 原生计算结果 | `原生计算结果_可拷贝`（AAM，SWMM .out/.rpt + ANUGA .sww） | 有 `请先阅读_原生结果说明.txt` + `配套参数与标识/` + `L1/*_2D.sww`（目录名不要求是交付形状） | `sim_native`（100 m 三角网聚到 250 m 落图格网） |

用法（容器里 / 本机同）：

    PYTHONPATH=src python -m stormwater.sim_import <交付目录> --by ZN [--c2d <work/c2d>] [--state L1 --state L2]
    PYTHONPATH=src python -m stormwater.sim_import <交付目录> --kind                    # 只打印判定结果，不抽取不登记

交付目录名带不是「片区_雨深mm历时h[_网格m]」形状的后缀（如 `…61mm2h.bak-20261005`）的是备份，不会被识别，要登记请用原目录。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .sim_sww import SwwError, parse_delivery_name

KINDS = {"rda": "sim_rda（RD-A 全套成果）", "native": "sim_native（原生计算结果：ANUGA .sww + SWMM .rpt）", "sww": "sim_sww（旧 .sww 交付）", "city": "sim_city（全市一二维）", "c1": "sim_c1（一二维 npz 交付）"}


def detect(root: Path) -> str:
    """交付形状：`sww` | `city` | `c1`。名字不是交付形状直接报错（备份目录等）。"""
    from . import sim_native, sim_rda

    if sim_rda.is_rda(root):
        return "rda"
    if sim_native.detect(root):
        return "native"
    region = parse_delivery_name(root.name)[0]
    if region.startswith("全市"):
        return "city"
    from . import sim_city
    if sim_city.layout_of(root) == "adm":      # 阿布扎比全市改造后数据：英文目录 1D/ + 2D/*_2d.npz，一次抽两态
        return "city"
    for sd in sorted(p for p in root.iterdir() if p.is_dir() and re.match(r"L\d", p.name)):
        d2 = sd / "二维"
        if d2.is_dir() and (d2 / "耦合的一维").is_dir() and any(
                p for p in d2.glob("*_二维水深时序.sww") if not p.name.startswith("._")):
            return "sww"
    return "c1"


def _import_any(root: Path, by: str | None = None, only: list[str] | None = None, c2d: Path | None = None) -> tuple[str, int]:
    root = root.resolve()
    kind = detect(root)
    if kind == "rda":
        from . import sim_rda
        return kind, sim_rda.import_all(root, by, only)
    if kind == "native":
        from . import sim_native
        return kind, sim_native.import_all(root, by, only)
    if kind == "sww":
        from . import sim_sww
        return kind, sim_sww.import_all(root, by, only)
    if kind == "city":
        from . import sim_city
        return kind, sim_city.import_all(root, by, only)
    from . import sim_c1
    return kind, sim_c1.import_all(root, by, only, c2d)


def import_any(root: Path, by: str | None = None, only: list[str] | None = None, c2d: Path | None = None) -> tuple[str, int]:
    kind, run_id = _import_any(root, by, only, c2d)
    _copy_ponding_ge(root.resolve(), run_id)
    return kind, run_id


def _copy_ponding_ge(root: Path, run_id: int) -> None:
    """交付根上由 engine 写的附属文件带到抽取结果目录：ponding_ge.json（「需要排 → 可以排 → 排完剩余」）、params.json（「推演参数」）、outputs.json（「成果数据位置」）。没有就不动。"""
    names = [n for n in ("ponding_ge.json", "params.json", "outputs.json", "pipes.geojson") if (root / n).is_file()]
    if not names:
        return
    import shutil

    from . import simulation as sim
    from .sim_results import DATA_ROOT, _posix
    with sim.connect() as conn:
        row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row and row["output_dir"]:
        dst = (DATA_ROOT / _posix(row["output_dir"])).resolve()
        if dst.is_dir() and DATA_ROOT.resolve() in dst.parents:
            for n in names:
                shutil.copyfile(root / n, dst / n)
                print(f"{n} → {dst}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", type=Path)
    ap.add_argument("--by", help="操作人缩写，如 ZN")
    ap.add_argument("--state", action="append", help="只处理这几态（可重复，如 L1）")
    ap.add_argument("--c2d", type=Path, help="work/c2d（一二维 npz 交付里没有 npz 时用；不给就从交付往上找 <主机>/work/c2d）")
    ap.add_argument("--kind", action="store_true", help="只打印判定的交付形状，不抽取不登记")
    a = ap.parse_args()
    try:
        if a.kind:
            print(f"{a.path.name}: {KINDS[detect(a.path.resolve())]}")
            return
        kind, run_id = import_any(a.path, a.by, a.state, a.c2d)
    except SwwError as e:
        raise SystemExit(f"✗ {e}") from e
    print(f"已登记为仿真 #{run_id}（{KINDS[kind]}）")


if __name__ == "__main__":
    main()
