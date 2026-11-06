# -*- coding: utf-8 -*-
"""C1 热点群 1 h / 33 mm（tag M33H1，模型版计算域）一条命令跑完：建模 → 三态 → 设计 L2+suggested v3 → 后处理 → 交付。

不改 tools/ 里任何已有脚本：
  · 计算段（建模、耦合、设计）：运行时往 c1_pipeline.TAGS 里加 M33H1，其余照用 c1_pipeline 的步骤表；
  · 后处理段：c1_post / export_delivery_c1 / c1_facility_gis / c1_hs_series / c1_hyetograph 把「2 h」和 int(tag[1:]) 写死在源码里，
    这里读它们的源码、在内存里替换几处再执行，磁盘上的原文件不动（替换点对不上就报错，不会悄悄跑偏）。
雨型取客户 Mussafah 模型的 1 h 设计雨型里名字带 CC 的那条（10 年一遇含气候变化），放大到 33 mm；模拟 5 h（历时 + 4 h，参数 E17）。

用法（要用项目 .venv 的 Python，系统 Python 没有 geopandas / swmm.toolkit）：
  .venv/Scripts/python.exe tools/c1_run_33mm1h.py            # = run all，已有产物的步骤自动跳过
  .venv/Scripts/python.exe tools/c1_run_33mm1h.py plan       # 只列命令
  .venv/Scripts/python.exe tools/c1_run_33mm1h.py check
  .venv/Scripts/python.exe tools/c1_run_33mm1h.py run post:M33H1 [--force]
产物：out/交付/C1_模型版_33mm1h/、console/data/flood2d_c1_m33h1/、out/c1_stats_M33H1.*（与 M61 的文件名互不相干，不覆盖）。
"""
from __future__ import annotations

import pathlib
import re
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TAG = "M33H1"
RAIN_MM, DUR_H = 33, 1


def parse(tag: str) -> tuple[int, int]:
    """情景名 → (雨量 mm, 历时 h)：M61 → (61, 2)；M33H1 → (33, 1)；尾部 H<n> = 历时，缺省 2 h。"""
    m = re.fullmatch(r"[A-Z](\d+)(?:H(\d+))?", tag)
    if not m:
        raise ValueError(f"看不懂的情景名：{tag}")
    return int(m.group(1)), int(m.group(2) or 2)


# 源码替换表：(原文, 新文, 应出现次数)。次数对不上就停。
PATCHES = {
    "c1_post.py": [
        ('r = 61 if tag.startswith("D") else int(tag[1:]); name', "r, _DUR = _parse(tag); name", 1),
        ('f"{r}mm/2h", "--stats-tag", f"{tag}D2"', 'f"{r}mm/{_DUR}h", "--stats-tag", f"{tag}D{_DUR}"', 1),
        ('"dur_h": 24 if tag.startswith("D") else 2,', '"dur_h": _DUR,', 1),
    ],
    "export_delivery_c1.py": [
        ("r = 61 if DS else int(tag[1:])", "r, _DUR = _parse(tag)", 1),
        ('f"C1_模型版_{r}mm2h"', 'f"C1_模型版_{r}mm{_DUR}h"', 1),
        ("f'{r} mm / 2 h'", "f'{r} mm / {_DUR} h'", 3),
    ],
    "c1_facility_gis.py": [
        ('("R61", "R96", "U61", "D24", "M61")', '("M33H1",)', 1),            # 只处理这一个 tag，不重写别的场景的设施图层
        ('r = 61 if tag.startswith("D") else int(tag[1:])', "r, _DUR = _parse(tag)", 1),
        ('f"C1_模型版_{r}mm2h"', 'f"C1_模型版_{r}mm{_DUR}h"', 1),
        ("{r} mm / 2 h · 设施空间图层", "{r} mm / {_DUR} h · 设施空间图层", 1),
    ],
    "c1_hs_series.py": [
        ('("R61", "R96", "U61", "D24", "M61")', '("R61", "R96", "U61", "D24", "M61", "M33H1")', 1),
    ],
    "c1_hyetograph.py": [
        ('"D24": (61.34, 24.0)}', '"D24": (61.34, 24.0), "M33H1": (33.0, 1.0)}', 1),
        ("DT_OUT = {2.0: 5, 24.0: 30}", "DT_OUT = {1.0: 5, 2.0: 5, 24.0: 30}", 1),
    ],
}


def load_patched(name: str, as_main: bool, argv: list[str] | None = None):
    """读 tools/<name> 的源码，按 PATCHES 在内存里替换后执行；as_main = 当作 __main__ 跑。"""
    path = HERE / name
    src = path.read_text(encoding="utf-8")
    for old, new, n in PATCHES.get(name, []):
        if src.count(old) != n:
            raise SystemExit(f"{name}：找不到要替换的原文（应有 {n} 处，实际 {src.count(old)} 处）：{old!r}\n"
                             f"  这个脚本被改过，请更新 c1_run_33mm1h.py 里的替换表。")
        src = src.replace(old, new)
    mod = types.ModuleType("__main__" if as_main else path.stem)
    mod.__dict__.update(__file__=str(path), _parse=parse)
    if argv is not None:
        sys.argv = [str(path), *argv]
    sys.modules[mod.__name__] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


def run_patched(name: str, argv: list[str]) -> int:
    if name == "export_delivery_c1.py":                 # 它在进程内 import c1_facility_gis：先把改过的版本放进去
        load_patched("c1_facility_gis.py", as_main=False)
    load_patched(name, as_main=True, argv=argv)
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--exec":       # 内部：由后处理步骤调用
        return run_patched(sys.argv[2], sys.argv[3:])
    sys.stdout.reconfigure(encoding="utf-8")
    import c1_pipeline as P

    P.TAGS[TAG] = dict(rain=RAIN_MM, dur=DUR_H, end=DUR_H + 4, domain="out/units_from_models/c1_model_domain.geojson")
    me = str(pathlib.Path(__file__).resolve())

    def post_step(t):
        st = P.STATES3 + ["L2S3"]
        x = lambda script, *a: [P.PY, me, "--exec", script, *a]
        return (f"post:{t}", f"{t}：后处理、系统数据、交付目录（33 mm / 1 h）", [P.ROOT / f"out/c1_stats_{t}.json"],
                [x("c1_post.py", "--tag", t), P.py("c1_l2s_side_effects.py", "--tag", t, "--state", "L2S3"), P.py("npz_to_tif.py", f"C1_{t}", *st),
                 *[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", f"C1_{t}", s, 15, threads=4) for s in st],
                 x("c1_hs_series.py"), x("c1_hyetograph.py"), P.py("c1_map_layers.py"), x("export_delivery_c1.py", "--tag", t)])

    P.post_step = post_step                                 # all_steps / parse 都按模块全局名查，替换后两处都生效
    argv = sys.argv[1:] or ["run", "all"]
    if argv[0] not in ("check", "plan", "run", "selftest"):
        argv = ["run", *argv]
    if "--tag" not in argv:
        argv += ["--tag", TAG]
    return P.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
