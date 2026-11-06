# -*- coding: utf-8 -*-
"""C1 热点群一二维计算的全流程：检查数据、按顺序逐步执行（2026-09-30，给换机器复现用）。

    python tools/c1_pipeline.py selftest              # 先做：合成算例 2 分钟跑通一二维全链路并自动判定（不要客户数据，= tools/demo_case.py）
    python tools/c1_pipeline.py check                 # 数据、环境、每一步的产物在不在（只读，不改任何东西）
    python tools/c1_pipeline.py plan  [--tag M61]     # 列出全部步骤和实际要执行的命令
    python tools/c1_pipeline.py run   <步骤…>         # 执行指定步骤，如 prep  或  build:M61 compute:M61:L1,L2,L2P
    python tools/c1_pipeline.py run   all --tag M61   # 从头到尾（准备 → R96 L1 → M61 四个态 → 后处理与交付），缺什么做什么
    加 --force 重做已有产物的步骤；加 --dry 只打印命令
    计算域：--domain 面文件 [--where "NAME='X'"] [--layer 图层] [--buffer m] [--multi largest] [--rain mm --dur h]，
        如 run all --tag A61 --domain D:/数据/行政区.shp --where "NAME='Mussafah'" --buffer 300（--tag 用新名字，产物不和已有情景混）
    网格：--res 计算网（缺省 15 m）、--tif-res GeoTIFF、--sww-res .sww（后两个缺省 = 计算网），如 run post:M61 --force --tif-res 5 --sww-res 10

步骤与依赖（详见 README「一二维热点群计算」与 .claude/skills/hotspot-1d2d-states/）：
  prep:*        客户数据 → 中间数据（热点、设计雨型、全市一维、热点汇水区、Parsons 设施、现状泵站、模型版汇水区、手工记录）
  build:<tag>   建 L1 / L2 / L2+planned 三个态的模型，读入检查
  compute:<tag>:<态,…>   docker anuga-swmm 并行算一二维（阻塞到算完；每个态 6 h 雨 ≈ 3–4 h 墙钟）
  design:<tag>  按 L2+planned 的结果设计 L2+suggested v3
  post:<tag>    后处理（统计、GeoTIFF、.sww、过程线）与交付目录
**隐含依赖**：L2 / L2+planned 的接收井按 C1 **R96 L1** 的一维结果选（两场雨布置一致），所以任何 tag 建 L2 之前都要先算完 R96 的 L1。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import datapaths  # noqa: E402

WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
C2D = WORK / "c2d"
# 部署包里本脚本跑在系统服务容器内：起计算容器时 -v 要给**宿主机**上的路径（容器里的 /work 对宿主机的 docker 没有意义）
HOST_WORK = os.environ.get("SWBRAIN_SWMM_WORK_HOST") or WORK.as_posix()
IMAGE = "anuga-swmm:1.0"
PY = sys.executable
HS8 = ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"]
STATES3 = ["L1", "L2", "L2P"]
# 三套网格尺寸（m），命令行 --res / --tif-res / --sww-res 改：计算网（ANUGA 三角网）、GeoTIFF 栅格、.sww 输出三角网。
# 后两个缺省 = 计算网；比计算网细 = 插值（更平滑，不多出信息），比计算网粗 = 聚合
GRID = {"res": 15.0, "tif": None, "sww": None}
# tag → 雨量（mm）、历时（h）、模拟终止（h）、计算域面文件（None = 8 个热点汇水区外扩 300 m）
TAGS = {
    "R61": dict(rain=61, dur=2, end=6, domain=None),
    "R96": dict(rain=96, dur=2, end=6, domain=None),
    "M61": dict(rain=61, dur=2, end=6, domain="out/units_from_models/c1_model_domain.geojson"),
    "D24": dict(rain=61.34, dur=24, end=28, domain=None),
    # U61 的计算域来自照片描图（手描，已被模型版取代），不能从原始数据重建；有那个面文件才能跑
    "U61": dict(rain=61, dur=2, end=6, domain="out/交付/C1汇水区_客户单元/C1_汇水区_合并.shp"),
}
PRESETS = tuple(TAGS)          # 预置情景（C1 工程的几种计算域与雨）；其余运行名都是自定义运行（任意范围面、改过的网格 / 雨）


def is_preset(t):
    return t in PRESETS


def env():
    e = dict(os.environ, SWBRAIN_SWMM_WORK=str(WORK), PYTHONIOENCODING="utf-8")
    try:
        import rasterio
        pj = pathlib.Path(rasterio.__file__).parent / "proj_data"
        if pj.exists():
            e.setdefault("PROJ_DATA", str(pj))           # 系统里有两套 PROJ 时 rasterio 会报 CRSError
    except ImportError:
        pass
    return e


def py(script, *args):
    return [PY, str(HERE / script) if script.endswith(".py") else script, *map(str, args)]


def dock(*cmd, name=None, threads=6, detach=False):
    return ["docker", "run", "--rm", *(["-d", "--name", name] if detach else []), "-e", f"OMP_NUM_THREADS={threads}",
            "-e", "C2D_DEM_RAW=1", "-e", "C2D_SAVE_UV=1", "-v", f"{HOST_WORK}:/work", IMAGE, *map(str, cmd)]


def rain_args(t):
    c = TAGS[t]
    return ["--rain", c["rain"], "--tag", t] + (["--dur", c["dur"]] if c["dur"] != 2 else [])


def int_rain(t):
    return int(round(TAGS[t]["rain"]))


# ------------------------------------------------------------------ 步骤表：(id, 说明, 产物, 命令列表)
def copy_step(pairs):
    def f(dry):
        for s, d in pairs:
            print(f"  cp {s} → {d}")
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(s, d)
    return f


def prep_steps():
    W = WORK
    return [
        ("prep:hotspots", "客户热点工作簿 + KMZ → 热点位置", [ROOT / "console/data/hotspots.geojson"],
         [[PY, "-m", "swbrain.hotspots", "--geojson", str(ROOT / "console/data/hotspots.geojson")]]),
        ("prep:rain", "客户 Mussafah_00.inp → 剥掉治理措施 → 设计雨型来源 Mussafah_baseline.inp", [W / "Mussafah_baseline.inp"],
         [copy_step([(datapaths.get("client_swmm_export") / "Mussafah_00.inp", W / "Mussafah.inp")]), py("strip_baseline.py")]),
        ("prep:city1d", "GDB + DTM → 全市一维重建（8 份）并算 61 mm / 2 h（热点管网汇水区、现状泵站用）",
         [W / f"City_L1_v2_p{i}.rpt" for i in range(1, 9)],
         [py("build_city1d.py", "L1", "--tag", "_v2", "--parts", 8, "--sim", 6), py("run_city1d.py", "L1", "--tag", "_v2", "--parts", 8)]),
        ("prep:catch", "8 个热点的地表 + 管网汇水区（原计算域的底图）", [ROOT / "console/data/hotspot_catch_C1.geojson"],
         [py("hotspot_catchments.py", *HS8, "--tag", "_C1")]),
        ("prep:parsons", "Parsons 关键热点 SHP + 图纸 → 规划塘 / 泵 / 管", [ROOT / "console/data/parsons_crit.geojson"],
         [py("import_parsons_crit.py")]),
        ("prep:design", "Parsons C1 图纸 → 27 座泵站参数与落点、OP-02 移动泵点",
         [ROOT / "out/c1_design_params.json", ROOT / "console/data/c1_pumps.geojson", ROOT / "console/data/c1_op02_pumps.geojson"],
         [py("extract_c1_design.py"), py("extract_c1_op02.py")]),
        ("prep:pumps", "原计算域 + 现状泵站（GDB PS_PUMP、压力管、Parsons 导出）", [ROOT / "out/c1_domain.geojson", ROOT / "out/c1_existing_pumps.json"],
         [py("c1_existing_pumps.py")]),
        ("prep:units", "客户 22 个模型 → 汇水区单元与 C1 模型版计算域", [ROOT / "out/units_from_models/c1_model_domain.geojson"],
         [py("units_from_models.py"), py("model_units_build.py")]),             # 前者出 B_C 等的出水口分区，后者要读
        ("prep:records", "入库的手工记录（参数确认、PDF 上的 3 个新建出水口）→ out/", [ROOT / "out/c1_params_answers.json", ROOT / "out/c1_proposed_outfalls.json"],
         [copy_step([(ROOT / "cases/C1/params_answers.json", ROOT / "out/c1_params_answers.json"),
                     (ROOT / "cases/C1/proposed_outfalls.json", ROOT / "out/c1_proposed_outfalls.json")])]),
        ("prep:runtime", "耦合程序与读入检查拷进工作目录（容器里从 /work/c2d 跑）", [C2D / "couple2way_v2.py", C2D / "npz_to_sww.py", C2D / "_chk_inp.py"],
         [copy_step([(HERE / "couple2way_v2.py", C2D / "couple2way_v2.py"), (HERE / "npz_to_sww.py", C2D / "npz_to_sww.py"),
                     (HERE / "chk_inp.py", C2D / "_chk_inp.py")])]),
    ]


def dom_args(t):
    c = TAGS[t]
    if not c.get("domain"):
        return []
    return (["--domain", str(ROOT / c["domain"])] +(["--where", c["where"]] if c.get("where") else [])
            + (["--layer", c["layer"]] if c.get("layer") else []) + (["--buffer", c["buffer"]] if c.get("buffer") else [])
            + (["--multi", c["multi"]] if c.get("multi") else []))


def build_step(t):
    if not is_preset(t) and TAGS[t].get("domain") and not TAGS[t].get("base"):
        raise SystemExit(f"  ! {t} 是任意范围的运行，只能建 L1（build:{t}:L1）：L2 / L2+planned 的设施是 C1 工程方案的，只在 C1 的计算域里有")
    r = int_rain(t)
    dom = dom_args(t)
    return (f"build:{t}", f"{t}：建 L1 / L2 / L2+planned（L2 起要 R96 L1 的结果）", [WORK / f"C1_{t}_{s}.inp" for s in STATES3] + [C2D / f"pilot_C1_{t}_{s}.json" for s in STATES3],
            [py("build_c1.py", *rain_args(t), *dom, "--res", f"{GRID['res']:g}"), py("build_c1_l2.py", "--rain", r, "--tag", t), py("build_c1_l2p.py", "--rain", r, "--tag", t, "--l2tag", t),
             dock("python", "/work/c2d/_chk_inp.py", *[f"C1_{t}_{s}" for s in STATES3], threads=1)])


def build_l1_step(t):
    dom = dom_args(t)
    return (f"build:{t}:L1", f"{t}：只建 L1", [WORK / f"C1_{t}_L1.inp"],
            [py("build_c1.py", *rain_args(t), *dom, "--res", f"{GRID['res']:g}"),dock("python", "/work/c2d/_chk_inp.py", f"C1_{t}_L1", threads=1)])


def compute_step(t, states):
    def f(dry):
        (C2D / "logs").mkdir(parents=True, exist_ok=True)
        thr = max(2, min(8, (os.cpu_count() or 8) // max(len(states), 1) - 1))
        names = []
        for s in states:
            n = f"c1pipe_{t}_{s}"
            cmd = dock("sh", "-c", f"python -u /work/c2d/couple2way_v2.py C1_{t} {s} {GRID['res']:g} {TAGS[t]['end']} 60 > /work/c2d/logs/C1_{t}_{s}.log 2>&1",
                       name=n, threads=thr, detach=True)
            print("  " + " ".join(cmd))
            if not dry:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL); names.append(n)
        while names and not dry:
            time.sleep(60)
            live = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
            for s in states:
                lg = C2D / "logs" / f"C1_{t}_{s}.log"
                tail = lg.read_text(encoding="utf-8", errors="replace").replace("\r", "\n").strip().splitlines()[-1:] if lg.exists() else ["(还没有日志)"]
                print(f"  [{time.strftime('%H:%M')}] {s}: {tail[0][:110] if tail else ''}")
            names = [n for n in names if n in live]
        bad = [s for s in states if not (C2D / f"twoway_C1_{t}_{s}.json").exists()]
        if bad and not dry:
            raise SystemExit(f"  ! {t} {bad} 容器已退出但没有结果，看 {C2D / 'logs'}")
    return (f"compute:{t}:{','.join(states)}", f"{t}：一二维耦合计算 {' / '.join(states)}（并行，阻塞到算完）",
            [C2D / f"twoway_C1_{t}_{s}.json" for s in states] + [C2D / f"C1_{t}_{s}_2d.npz" for s in states], [f])


def design_step(t):
    return (f"design:{t}", f"{t}：按 L2+planned 的结果设计 L2+suggested v3", [WORK / f"C1_{t}_L2S3.inp"],
            [py("c1_design_l2s3.py", "--rain", int_rain(t), "--tag", t, "--base", "L2P"), dock("python", "/work/c2d/_chk_inp.py", f"C1_{t}_L2S3", threads=1)])


def post_generic_step(t):
    """自定义运行的后处理：只处理算完的态（通常只有 L1）——逐帧图、统计、GeoTIFF、.sww、积水点、飞渡 DTS。
    C1 那套（热点汇水区统计、设施、差值、交付目录）绑在 C1 工程上，不适用于任意范围。"""
    def f(dry):
        st = [s for s in STATES3 + ["L2S3"] if (C2D / f"C1_{t}_{s}_2d.npz").exists()]
        if dry and not st:
            st = ["L1"]
        if not st:
            raise SystemExit(f"  ! {t}：没有算完的态")
        c = TAGS[t]
        cmds = [py("pilot_frames.py", "--name", f"C1_{t}", "--states", ",".join(st), "--out", f"flood2d_{t.lower()}",
                   "--region", f"{t}（{(c.get('domain') or c.get('base') or '').rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}）",
                   "--scen", f"{c['rain']:g}mm/{c['dur']:g}h", "--stats-tag", "stats"),
                py("npz_to_tif.py", f"C1_{t}", *st, *(["--cell", GRID["tif"]] if GRID["tif"] else [])),
                *[dock("python", "-u", "/work/c2d/npz_to_sww.py", f"C1_{t}", s, f"{GRID['res']:g}",
                       *(["--out-res", f"{GRID['sww']:g}"] if GRID["sww"] else []), threads=4) for s in st],
                py("ponding_points.py", "--name", f"C1_{t}", "--states", ",".join(st)),   # 二维模拟积水点（缺省阈值，系统 /api/ponding 直接读缓存）
                py("dts_export.py", "--run", f"C1_{t}", "--states", ",".join(st))]       # 飞渡 DTS：out/交付/C1_<t>/<态>/飞渡DTS/（要在 npz_to_sww 之后）
        for cmd in cmds:
            print("  $ " + " ".join(map(str, cmd)))
            if not dry and subprocess.run(cmd, cwd=ROOT, env=env()).returncode:
                raise SystemExit(f"  ! 失败：{cmd[1] if len(cmd) > 1 else cmd}")
    return (f"post:{t}", f"{t}：后处理（逐帧图、统计、GeoTIFF、.sww）", [ROOT / f"out/pilot_C1_{t}_stats.json"], [f])


def post_step(t):
    if not is_preset(t):
        return post_generic_step(t)
    st = STATES3 + ["L2S3"]
    return (f"post:{t}", f"{t}：后处理、系统数据、交付目录", [ROOT / f"out/c1_stats_{t}.json"],
            [py("c1_post.py", "--tag", t), py("c1_l2s_side_effects.py", "--tag", t, "--state", "L2S3"), py("npz_to_tif.py", f"C1_{t}", *st, *(["--cell", GRID["tif"]] if GRID["tif"] else [])),
             *[dock("python", "-u", "/work/c2d/npz_to_sww.py", f"C1_{t}", s, f"{GRID['res']:g}",
                    *(["--out-res", f"{GRID['sww']:g}"] if GRID["sww"] else []), threads=4) for s in st],
             py("c1_hs_series.py"), py("c1_hyetograph.py"), py("c1_map_layers.py"), py("ponding_points.py", "--name", f"C1_{t}"),
             py("export_delivery_c1.py", "--tag", t)])


def all_steps(t):
    s = prep_steps()
    if not is_preset(t) and TAGS[t].get("domain"):             # 任意范围：只有 L1（L2 起是 C1 工程方案的设施）
        return s + [build_l1_step(t), compute_step(t, ["L1"]), post_step(t)]
    if t != "R96":
        s += [build_l1_step("R96"), compute_step("R96", ["L1"])]
    s += [build_step(t), compute_step(t, STATES3), design_step(t), compute_step(t, ["L2S3"]), post_step(t)]
    return s


def parse(spec):
    if spec == "prep":
        return prep_steps()
    for s in prep_steps():
        if s[0] == spec:
            return [s]
    k = spec.split(":")
    if k[0] == "build" and len(k) == 3 and k[2] == "L1":
        return [build_l1_step(k[1])]
    if k[0] in ("build", "design", "post") and len(k) == 2:
        return [{"build": build_step, "design": design_step, "post": post_step}[k[0]](k[1])]
    if k[0] == "compute" and len(k) == 3:
        return [compute_step(k[1], k[2].split(","))]
    raise SystemExit(f"不认识的步骤：{spec}（见 plan）")


def done(outs):
    return all(pathlib.Path(o).exists() for o in outs)


# ------------------------------------------------------------------ check
DATA_NEED = {"gdb_zip": "", "gdb_dir": "", "dtm": "", "hotspot_xlsx_dir": "", "hotspot_kmz": "",
             "client_models_zip": "", "client_swmm_export": "/Mussafah_00.inp", "parsons_root": "/Critical Hotspots/PDF/CRITICAL HOTSPOT-C1_WITH MECHANICAL DATA.pdf"}
PY_NEED = ("geopandas", "rasterio", "pyogrio", "shapely", "scipy", "pyflwdir", "pymupdf", "swmm.toolkit", "matplotlib")


def status(t: str = "M61", modules: bool = True) -> dict:
    """结构化的检查结果（check 打印它；数据质检智能体 qa_data 动作直接用它）。只读。"""
    data = []
    for k, sub in DATA_NEED.items():
        p = datapaths.get(k)
        q = pathlib.Path(str(p) + sub) if p else None
        data.append({"key": k, "path": str(q) if q else None, "ok": bool(q and q.exists())})
    env_ = [{"item": "工作目录无中文", "detail": str(WORK), "ok": not any(ord(ch) > 127 for ch in str(WORK))}]
    try:
        img = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        img = False
    env_.append({"item": f"docker 镜像 {IMAGE}", "detail": "" if img else "docker build -t anuga-swmm:1.0 docker/anuga-swmm", "ok": img})
    if modules:
        import importlib.util
        for m in PY_NEED:
            try:
                ok_m = importlib.util.find_spec(m) is not None
            except ModuleNotFoundError:
                ok_m = False
            env_.append({"item": m, "detail": "" if ok_m else "pip install -r requirements.txt", "ok": ok_m})
    steps = []
    for sid, desc, outs, _ in all_steps(t):
        miss = [str(o) for o in outs if not pathlib.Path(o).exists()]
        steps.append({"step": sid, "desc": desc, "done": not miss, "missing": len(miss), "first_missing": miss[0] if miss else None})
    return {"tag": t, "data": data, "env": env_, "steps": steps, "ok": all(d["ok"] for d in data) and all(e["ok"] for e in env_)}


def check(t):
    st = status(t)
    print("数据（config.toml [data] / [terrain] / [hotspots]，见 docs/C1_资料清单.md）")
    for d in st["data"]:
        print(f"  {'✓' if d['ok'] else '✗'} {d['key']:<20} {d['path']}")
    print("环境")
    for e in st["env"]:
        print(f"  {'✓' if e['ok'] else '✗'} {e['item']}" + (f"（{e['detail']}）" if e["detail"] else ""))
    print(f"步骤（run all --tag {t} 的顺序；✓ = 产物已在）")
    for s in st["steps"]:
        print(f"  {'✓' if s['done'] else '·'} {s['step']:<26} {s['desc']}" + (f"\n{'':32}缺 {s['missing']} 个，如 {s['first_missing']}" if s["missing"] else ""))
    print("\n数据与环境齐了。" if st["ok"] else "\n有 ✗ 项：先补齐（数据向客户要，路径写进 config.toml [data]）。")
    return 0 if st["ok"] else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["check", "plan", "run", "selftest"])
    ap.add_argument("steps", nargs="*")
    ap.add_argument("--tag", default="M61", help=f"情景：{' / '.join(TAGS)}；配 --domain 时可以起新名字")
    g = ap.add_argument_group("自定义计算域（新情景，或覆盖已有情景的计算域）")
    g.add_argument("--domain", help="计算域面文件（行政区 / 汇水区，shp / geojson / gpkg，要带坐标系）")
    g.add_argument("--where", help="按属性筛要素，如 \"NAME='Mussafah'\"")
    g.add_argument("--layer", help="gpkg / gdb 的图层名")
    g.add_argument("--buffer", type=float, help="外扩 m")
    g.add_argument("--multi", choices=["error", "largest"], help="几块不相连时：报错（缺省）/ 只留最大块")
    g.add_argument("--rain", type=float, help="新情景的雨量 mm（缺省 61）")
    g.add_argument("--dur", type=float, help="新情景的降雨历时 h（缺省 2；模拟到历时 + 4 h）")
    g.add_argument("--base", help="以哪个预置情景为底稿（沿用它的计算域与雨，--tag 给新的运行名；改网格 / 雨时用，结果不覆盖预置情景）")
    ap.add_argument("--force", action="store_true", help="产物已在也重做")
    ap.add_argument("--dry", action="store_true", help="只打印命令")
    ap.add_argument("--res", type=float, default=GRID["res"], help="二维计算网格 m（缺省 15）")
    ap.add_argument("--tif-res", type=float, help="GeoTIFF 栅格 m（缺省 = 计算网格）")
    ap.add_argument("--sww-res", type=float, help=".sww 输出三角网 m（缺省 = 计算网格）")
    a = ap.parse_args(argv)
    GRID.update(res=a.res, tif=a.tif_res, sww=a.sww_res)
    if a.base and a.base not in PRESETS:
        ap.error(f"--base 只能是预置情景 {' / '.join(PRESETS)}")
    if a.tag in PRESETS and not (a.domain or a.base) and a.cmd == "run" and a.res != 15.0 and any(s.startswith(("build", "compute", "all")) for s in a.steps):
        ap.error(f"{a.tag} 的结果是 15 m 网格算的；改网格要另起运行名：--tag {a.tag}_r{a.res:g} --base {a.tag} --res {a.res:g}")
    if a.domain or a.base:
        if a.tag in PRESETS:
            ap.error(f"自定义运行（--domain / --base）要用新的运行名，不能用预置情景名 {a.tag}（会覆盖它的结果）")
        c = dict(TAGS[a.base]) if a.base else dict(rain=61, dur=2, end=6, domain=None)
        if a.domain:
            p = pathlib.Path(a.domain)
            c.update(domain=str(p if p.is_absolute() else pathlib.Path.cwd() / p), where=a.where, layer=a.layer, buffer=a.buffer, multi=a.multi)
        if a.rain is not None:
            c["rain"] = a.rain
        if a.dur is not None:
            c.update(dur=a.dur, end=a.dur + 4)
        c["base"] = a.base
        TAGS[a.tag] = c
    elif a.tag not in TAGS:
        ap.error(f"没有情景 {a.tag}（{' / '.join(TAGS)}）；新的运行要配 --domain 或 --base")
    elif a.where or a.layer or a.buffer or a.multi or a.rain is not None or a.dur is not None:
        ap.error("--where / --layer / --buffer / --multi / --rain / --dur 要配 --domain 或 --base 用")
    if a.cmd == "check":
        return check(a.tag)
    if a.cmd == "selftest":                           # 快速验证：合成算例跑通一二维全链路（不要客户数据，约 2 分钟）
        return subprocess.run([PY, str(HERE / "demo_case.py")], cwd=ROOT, env=env()).returncode
    if a.cmd == "run" and not a.dry:                  # 新克隆的仓库没有这些目录（都不入库），脚本不会自己建
        for d in (ROOT / "out", ROOT / "console" / "data", ROOT / "exchange" / "hotspots", C2D / "logs"):
            d.mkdir(parents=True, exist_ok=True)
    steps = all_steps(a.tag) if a.cmd == "plan" or a.steps == ["all"] else [s for sp in a.steps for s in parse(sp)]
    for sid, desc, outs, cmds in steps:
        if a.cmd == "plan":
            print(f"\n[{'✓' if done(outs) else ' '}] {sid} — {desc}")
            for c in cmds:
                print("      " + ("（内置步骤：拷贝文件 / 起容器并等到算完）" if callable(c) else " ".join(map(str, c))))
            continue
        if done(outs) and not a.force:
            print(f"= {sid}：产物已在，跳过（--force 重做）"); continue
        print(f"\n▶ {sid} — {desc}")
        for c in cmds:
            if callable(c):
                c(a.dry); continue
            print("  $ " + " ".join(map(str, c)))
            if not a.dry:
                r = subprocess.run(c, cwd=ROOT, env=env())
                if r.returncode:
                    raise SystemExit(f"  ! 失败（退出码 {r.returncode}）：{sid}")
        if not a.dry and not done(outs):
            raise SystemExit(f"  ! {sid} 跑完了但产物不全：{[str(o) for o in outs if not pathlib.Path(o).exists()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
