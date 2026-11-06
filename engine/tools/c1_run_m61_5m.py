# -*- coding: utf-8 -*-
"""C1 热点群 61 mm / 2 h、模型版计算域、二维 5 m 网格（tag M61G5）一条命令跑完。

和 M61 的区别只有二维网格：15 m → 5 m（单元面积上限 5² / 3 ≈ 8.3 m²，三角单元数约 15 m 的 9 倍）。
一维、雨、计算域、设施布置都与 M61 相同。不改 tools/ 里任何已有脚本：
  · 计算段：运行时往 c1_pipeline.TAGS 里加 M61G5，并把写死 15 的建模 / 耦合 / .sww 步骤换成 5；
  · build_c1 / 后处理段：读源码、在内存里替换几处再执行（同 c1_run_33mm1h.py），替换点对不上就报错。

用法（要用项目 .venv 的 Python）：
  .venv/Scripts/python.exe tools/c1_run_m61_5m.py plan
  .venv/Scripts/python.exe tools/c1_run_m61_5m.py run build:M61G5
  .venv/Scripts/python.exe tools/c1_run_m61_5m.py run compute:M61G5:L1      # 先只算一个态试内存和耗时
产物：out/交付/C1_模型版_61mm2h_5m/ 等，文件名都带 M61G5，不覆盖 M61。
"""
from __future__ import annotations

import pathlib
import re
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TAG = "M61G5"
RES_M = 5


def parse(tag: str) -> tuple[int, int]:
    """情景名 → (雨量 mm, 历时 h)：M61G5 → (61, 2)。"""
    m = re.fullmatch(r"[A-Z](\d+)(?:H(\d+))?(?:G\d+)?", tag)
    if not m:
        raise ValueError(f"看不懂的情景名：{tag}")
    return int(m.group(1)), int(m.group(2) or 2)


PATCHES = {
    "build_c1.py": [("RES = 15.0", f"RES = {RES_M}.0", 1)],
    "c1_post.py": [
        ('r = 61 if tag.startswith("D") else int(tag[1:]); name', "r, _DUR = _parse(tag); name", 1),
    ],
    "export_delivery_c1.py": [
        ("r = 61 if DS else int(tag[1:])", "r, _DUR = _parse(tag)", 1),
        ('f"C1_模型版_{r}mm2h"', 'f"C1_模型版_{r}mm2h_5m"', 1),      # 不带后缀会写进 15 m 版的 C1_模型版_61mm2h（2026-10-08 服务器 1 上踩过）
        ("15 m GeoTIFF", f"{RES_M} m GeoTIFF", 1),
    ],
    "c1_facility_gis.py": [
        ('("R61", "R96", "U61", "D24", "M61")', f'("{TAG}",)', 1),
        ('r = 61 if tag.startswith("D") else int(tag[1:])', "r, _DUR = _parse(tag)", 1),
        ('f"C1_模型版_{r}mm2h"', 'f"C1_模型版_{r}mm2h_5m"', 1),
    ],
    "c1_hs_series.py": [
        ('("R61", "R96", "U61", "D24", "M61")', f'("R61", "R96", "U61", "D24", "M61", "{TAG}")', 1),
    ],
    "c1_hyetograph.py": [
        ('"D24": (61.34, 24.0)}', f'"D24": (61.34, 24.0), "{TAG}": (61.0, 2.0)}}', 1),
    ],
}


def load_patched(name: str, as_main: bool, argv: list[str] | None = None):
    path = HERE / name
    src = path.read_text(encoding="utf-8")
    for old, new, n in PATCHES.get(name, []):
        if src.count(old) != n:
            raise SystemExit(f"{name}：找不到要替换的原文（应有 {n} 处，实际 {src.count(old)} 处）：{old!r}\n"
                             f"  这个脚本被改过，请更新 c1_run_m61_5m.py 里的替换表。")
        src = src.replace(old, new)
    mod = types.ModuleType("__main__" if as_main else path.stem)
    mod.__dict__.update(__file__=str(path), _parse=parse)
    if argv is not None:
        sys.argv = [str(path), *argv]
    sys.modules[mod.__name__] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


def run_patched(name: str, argv: list[str]) -> int:
    if name == "export_delivery_c1.py":
        load_patched("c1_facility_gis.py", as_main=False)
    load_patched(name, as_main=True, argv=argv)
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--exec":
        return run_patched(sys.argv[2], sys.argv[3:])
    sys.stdout.reconfigure(encoding="utf-8")
    import c1_pipeline as P

    P.TAGS[TAG] = dict(rain=61, dur=2, end=6, domain="out/units_from_models/c1_model_domain.geojson")
    me = str(pathlib.Path(__file__).resolve())
    x = lambda script, *a: [P.PY, me, "--exec", script, *map(str, a)]

    def build_step(t):
        if t != TAG:
            return P._orig["build_step"](t)
        c, r = P.TAGS[t], P.int_rain(t)
        dom = ["--domain", str(P.ROOT / c["domain"])] if c["domain"] else []
        return (f"build:{t}", f"{t}：建 L1 / L2 / L2+planned（{RES_M} m 网格）",
                [P.WORK / f"C1_{t}_{s}.inp" for s in P.STATES3] + [P.C2D / f"pilot_C1_{t}_{s}.json" for s in P.STATES3],
                [x("build_c1.py", *P.rain_args(t), *dom), P.py("build_c1_l2.py", "--rain", r, "--tag", t),
                 P.py("build_c1_l2p.py", "--rain", r, "--tag", t, "--l2tag", t),
                 P.dock("python", "/work/c2d/_chk_inp.py", *[f"C1_{t}_{s}" for s in P.STATES3], threads=1)])

    def build_l1_step(t):
        if t != TAG:
            return P._orig["build_l1_step"](t)
        c = P.TAGS[t]
        dom = ["--domain", str(P.ROOT / c["domain"])] if c["domain"] else []
        return (f"build:{t}:L1", f"{t}：只建 L1", [P.WORK / f"C1_{t}_L1.inp"],
                [x("build_c1.py", *P.rain_args(t), *dom), P.dock("python", "/work/c2d/_chk_inp.py", f"C1_{t}_L1", threads=1)])

    def compute_step(t, states):
        if t != TAG:
            return P._orig["compute_step"](t, states)
        # 原函数把 15 写死在 docker 命令字符串里：执行期间拦截 dock，把 couple2way_v2 命令里的 " 15 " 换成 5
        orig_dock = P.dock

        def dock5(*cmd, **kw):
            cmd = [re.sub(r"(couple2way_v2\.py C1_\S+ \S+) 15 ", rf"\1 {RES_M} ", c) if isinstance(c, str) else c for c in cmd]
            return orig_dock(*cmd, **kw)

        step = P._orig["compute_step"](t, states)
        inner = step[3][0]

        def run(dry):
            P.dock = dock5
            try:
                inner(dry)
            finally:
                P.dock = orig_dock
        return (step[0], step[1] + f"（{RES_M} m 网格）", step[2], [run])

    def post_step(t):
        if t != TAG:
            return P._orig["post_step"](t)
        st = P.STATES3 + ["L2S3"]
        return (f"post:{t}", f"{t}：后处理、系统数据、交付目录（{RES_M} m 网格）", [P.ROOT / f"out/c1_stats_{t}.json"],
                [x("c1_post.py", "--tag", t), P.py("c1_l2s_side_effects.py", "--tag", t, "--state", "L2S3"), P.py("npz_to_tif.py", f"C1_{t}", *st),
                 *[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", f"C1_{t}", s, RES_M, threads=4) for s in st],
                 x("c1_hs_series.py"), x("c1_hyetograph.py"), P.py("c1_map_layers.py"), x("export_delivery_c1.py", "--tag", t)])

    P._orig = {k: getattr(P, k) for k in ("build_step", "build_l1_step", "compute_step", "post_step")}
    P.build_step, P.build_l1_step, P.compute_step, P.post_step = build_step, build_l1_step, compute_step, post_step
    argv = sys.argv[1:] or ["run", "all"]
    if argv[0] not in ("check", "plan", "run", "selftest"):
        argv = ["run", *argv]
    if "--tag" not in argv:
        argv += ["--tag", TAG]
    return P.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
