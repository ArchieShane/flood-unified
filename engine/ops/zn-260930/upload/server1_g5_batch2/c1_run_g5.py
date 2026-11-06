# -*- coding: utf-8 -*-
"""C1 热点群 · 模型版计算域 · 二维 5 m 网格 · 多场雨一条命令跑完（建模 → 三态 → 设计 L2+suggested v3 → 后处理 → 交付）。

和 M61 / M33H1 / M96H1 等 15 m 版本的区别只有二维网格：15 m → 5 m（单元面积上限 5² / 3 ≈ 8.3 m²）。
地形取配置里 [terrain].dtm_path 指的 DTM 原值（C2D_DEM_RAW=1）：要用 50 cm DTM，就把 config.toml 的 dtm_path 指向它。
情景名 = 原情景 + G5，输出文件名都带这个后缀，不覆盖 15 m 的结果：

  M61G5     61 mm / 2 h，模拟 6 h
  M33H1G5   33 mm / 1 h，模拟 5 h
  M96H24G5  96 mm / 24 h，模拟 28 h（24 h 雨型取客户 Mussafah 模型里的 SCS II 型，等比放大到 96 mm）
  M32H1G5   32 mm / 1 h，模拟 5 h；M32H2G5   32 mm / 2 h，模拟 6 h（服务器 1，推演计划表 v27 5m格网 第 1–8 行，tools/chains/c1_g5_server1.sh）
  M32H24G5  32 mm / 24 h，模拟 28 h；M61H1G5   61 mm / 1 h，模拟 5 h（服务器 1，推演计划表 v29 5m格网 第 9–16 行）
  M61H24G5  61 mm / 24 h，模拟 28 h（服务器 1，v29 5m格网 第 21–24 行，排在上面两场后面补空槽）
  M96H1G5   96 mm / 1 h，模拟 5 h；M96H2G5   96 mm / 2 h，模拟 6 h（服务器 1，v30 5m格网 第 25–32 行；M96H24G5 是第 33–36 行）
    2026-10-10 起服务器 1 一次排完 C1 剩下的六场雨（tools/chains/c1_g5_server1.sh 缺省 TAGS），前面的态算完、槽空出来就接着开
  1 h 雨型取客户 Mussafah 模型里名字带 CC 的那条、24 h 取 SCS II 型，等比放大（同 c1_run_15m.py 的 M61H1 / M32H24）

不改 tools/ 里任何已有脚本（同 c1_run_33mm1h.py）：
  · 计算段：运行时往 c1_pipeline.TAGS 里加这些情景，并把写死 15 的建模 / 耦合 / .sww 步骤换成 5；
  · build_c1 / 后处理段：读源码、在内存里替换几处再执行，替换点对不上就报错，不会悄悄跑偏。

用法（容器里 `python tools/c1_run_g5.py ...`，源码方式用项目 .venv 的 Python）：
  python tools/c1_run_g5.py plan  --tag M33H1G5
  python tools/c1_run_g5.py run   build:M33H1G5
  python tools/c1_run_g5.py run   compute:M33H1G5:L1,L2,L2P
  python tools/c1_run_g5.py run   design:M33H1G5
  python tools/c1_run_g5.py run   compute:M33H1G5:L2S3
  python tools/c1_run_g5.py run   post:M33H1G5 [--force]
环境变量 C2D_G5_THREADS：每个二维计算容器的线程数（默认 8；长历时的 96 mm / 24 h 可以加大）。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RES_M = 5
DOMAIN = "out/units_from_models/c1_model_domain.geojson"
# 情景名 → (雨量 mm, 历时 h)；模拟终止 = 历时 + 4 h（参数 E17）
SCEN = {"M61G5": (61, 2), "M33H1G5": (33, 1), "M96H24G5": (96, 24), "M32H1G5": (32, 1), "M32H2G5": (32, 2),
        "M32H24G5": (32, 24), "M61H1G5": (61, 1), "M61H24G5": (61, 24), "M96H1G5": (96, 1), "M96H2G5": (96, 2)}


def parse(tag: str) -> tuple[int, int]:
    """情景名 → (雨量 mm, 历时 h)：M61G5 → (61, 2)；M33H1G5 → (33, 1)；M96H24G5 → (96, 24)。H<n> = 历时，缺省 2 h。"""
    m = re.fullmatch(r"[A-Z](\d+)(?:H(\d+))?(?:G\d+)?", tag)
    if not m:
        raise ValueError(f"看不懂的情景名：{tag}")
    return int(m.group(1)), int(m.group(2) or 2)


def suffix(tag: str) -> str:
    """交付目录名后缀：5 m 版带 _5m，免得和 15 m 版的同名目录（如 C1_模型版_33mm1h）互相覆盖。"""
    return "_5m" if re.search(r"G\d+$", tag) else ""


_ALL = ", ".join(f'"{t}"' for t in SCEN)
PATCHES = {
    "build_c1.py": [("RES = 15.0", f"RES = {RES_M}.0", 1)],
    "c1_post.py": [
        ('r = 61 if tag.startswith("D") else int(tag[1:]); name', "r, _DUR = _parse(tag); name", 1),
        ('f"{r}mm/2h", "--stats-tag", f"{tag}D2"', 'f"{r}mm/{_DUR}h", "--stats-tag", f"{tag}D{_DUR}"', 1),
        ('"dur_h": 24 if tag.startswith("D") else 2,', '"dur_h": _DUR,', 1),
    ],
    "export_delivery_c1.py": [
        ("r = 61 if DS else int(tag[1:])", "r, _DUR = _parse(tag)", 1),
        ('f"C1_模型版_{r}mm2h"', 'f"C1_模型版_{r}mm{_DUR}h{_SUF(tag)}"', 1),
        ("f'{r} mm / 2 h'", "f'{r} mm / {_DUR} h'", 3),
        ("15 m GeoTIFF", f"{RES_M} m GeoTIFF", 1),
    ],
    "c1_facility_gis.py": [
        ('("R61", "R96", "U61", "D24", "M61")', f"({_ALL},)", 1),          # 只处理这几个 tag，不重写别的场景的设施图层
        ('r = 61 if tag.startswith("D") else int(tag[1:])', "r, _DUR = _parse(tag)", 1),
        ('f"C1_模型版_{r}mm2h"', 'f"C1_模型版_{r}mm{_DUR}h{_SUF(tag)}"', 1),
        ("{r} mm / 2 h · 设施空间图层", "{r} mm / {_DUR} h · 设施空间图层", 1),
    ],
    "c1_hs_series.py": [
        ('("R61", "R96", "U61", "D24", "M61")', f'("R61", "R96", "U61", "D24", "M61", {_ALL})', 1),
    ],
    "c1_hyetograph.py": [
        ('"D24": (61.34, 24.0)}', '"D24": (61.34, 24.0), ' + ", ".join(f'"{t}": ({p}.0, {d}.0)' for t, (p, d) in SCEN.items()) + "}", 1),
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
                             f"  这个脚本和仓库里的不一样，请把这条报错发给开发者更新 c1_run_g5.py 里的替换表。")
        src = src.replace(old, new)
    mod = types.ModuleType("__main__" if as_main else path.stem)
    mod.__dict__.update(__file__=str(path), _parse=parse, _SUF=suffix)
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


def check_patches() -> int:
    """只验证替换点都对得上，不执行任何东西。"""
    bad = 0
    for name, ps in PATCHES.items():
        src = (HERE / name).read_text(encoding="utf-8")
        for old, _, n in ps:
            ok = src.count(old) == n
            bad += not ok
            print(("  ✓ " if ok else "  ✗ ") + f"{name}: {old[:50]!r}（应 {n} 处，实际 {src.count(old)} 处）")
    print("替换点全部对得上" if not bad else f"有 {bad} 处对不上")
    return 1 if bad else 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--exec":       # 内部：由建模 / 后处理步骤调用
        return run_patched(sys.argv[2], sys.argv[3:])
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["check-patches"]:
        return check_patches()
    import c1_pipeline as P

    for t, (rain, dur) in SCEN.items():
        P.TAGS[t] = dict(rain=rain, dur=dur, end=dur + 4, domain=DOMAIN)
    threads = int(os.environ.get("C2D_G5_THREADS", "8"))
    me = str(pathlib.Path(__file__).resolve())
    x = lambda script, *a: [P.PY, me, "--exec", script, *map(str, a)]
    orig = {k: getattr(P, k) for k in ("build_step", "build_l1_step", "compute_step", "post_step")}

    def build_step(t):
        if t not in SCEN:
            return orig["build_step"](t)
        c, r = P.TAGS[t], P.int_rain(t)
        return (f"build:{t}", f"{t}：建 L1 / L2 / L2+planned（{RES_M} m 网格）",
                [P.WORK / f"C1_{t}_{s}.inp" for s in P.STATES3] + [P.C2D / f"pilot_C1_{t}_{s}.json" for s in P.STATES3],
                [x("build_c1.py", *P.rain_args(t), "--domain", str(P.ROOT / c["domain"])),
                 P.py("build_c1_l2.py", "--rain", r, "--tag", t),
                 P.py("build_c1_l2p.py", "--rain", r, "--tag", t, "--l2tag", t),
                 P.dock("python", "/work/c2d/_chk_inp.py", *[f"C1_{t}_{s}" for s in P.STATES3], threads=1)])

    def build_l1_step(t):
        if t not in SCEN:
            return orig["build_l1_step"](t)
        c = P.TAGS[t]
        return (f"build:{t}:L1", f"{t}：只建 L1", [P.WORK / f"C1_{t}_L1.inp"],
                [x("build_c1.py", *P.rain_args(t), "--domain", str(P.ROOT / c["domain"])),
                 P.dock("python", "/work/c2d/_chk_inp.py", f"C1_{t}_L1", threads=1)])

    def compute_step(t, states):
        step = orig["compute_step"](t, states)
        if t not in SCEN:
            return step
        orig_dock, inner = P.dock, step[3][0]

        def dock5(*cmd, **kw):          # 原函数把 15 和线程数写死在 docker 命令里：执行期间改成 5 和 C2D_G5_THREADS
            cmd = [re.sub(r"(couple2way_v2\.py C1_\S+ \S+) 15 ", rf"\1 {RES_M} ", c) if isinstance(c, str) else c for c in cmd]
            full = orig_dock(*cmd, **kw)
            if any("couple2way_v2.py" in c for c in full):       # OMP_NUM_THREADS 是 dock() 拼进命令的，要在返回后改
                full = [f"OMP_NUM_THREADS={threads}" if c.startswith("OMP_NUM_THREADS=") else c for c in full]
            return full

        def run(dry):
            P.dock = dock5
            try:
                inner(dry)
            finally:
                P.dock = orig_dock
        return (step[0], step[1] + f"（{RES_M} m 网格，{threads} 线程）", step[2], [run])

    def post_step(t):
        if t not in SCEN:
            return orig["post_step"](t)
        st = P.STATES3 + ["L2S3"]
        return (f"post:{t}", f"{t}：后处理、系统数据、交付目录（{RES_M} m 网格）", [P.ROOT / f"out/c1_stats_{t}.json"],
                [x("c1_post.py", "--tag", t), P.py("c1_l2s_side_effects.py", "--tag", t, "--state", "L2S3"), P.py("npz_to_tif.py", f"C1_{t}", *st),
                 *[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", f"C1_{t}", s, RES_M, threads=4) for s in st],
                 x("c1_hs_series.py"), x("c1_hyetograph.py"), P.py("c1_map_layers.py"), x("export_delivery_c1.py", "--tag", t)])

    P.build_step, P.build_l1_step, P.compute_step, P.post_step = build_step, build_l1_step, compute_step, post_step
    argv = sys.argv[1:] or ["run", "all"]
    if argv[0] not in ("check", "plan", "run", "selftest"):
        argv = ["run", *argv]
    if "--tag" not in argv:
        raise SystemExit(f"请用 --tag 指定情景：{' / '.join(SCEN)}")
    return P.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
