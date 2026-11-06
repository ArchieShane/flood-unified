# 由 tools/rg_run_15m.py 生成 tools/ad_run_30m.py（逐处替换，替换不到就报错，免得悄悄漏改）
import pathlib, re

T = pathlib.Path(r"C:\freedo\zn-260930\thinking-drainage-system\tools")
s = (T / "rg_run_15m.py").read_text(encoding="utf-8")


def sub(old, new, count=1):
    global s
    assert s.count(old) >= 1, f"找不到：{old[:70]!r}"
    s = s.replace(old, new) if count == 0 else s.replace(old, new, count)


# ---- 文档串
a = s.index('"""'); b = s.index('"""', a + 3) + 3
DOC = '''"""ADM 汇水区 · 二维 30 m 网格 · 1 h 60 mm · L1 / L2（2026-10-15）。

14 个汇水区（客户 10-15 计划表；区域代号见 ad_regions.py）：MI8 / RD2 / YASO / RB1 / RBE1 / MBZ / RD14 / RB3 / RDA / RDF / RDC / QAB1 / RDI / RDG。
情景名 <区域>_M60H1，如 MI8_M60H1、RD2_M60H1；雨量 60 mm、历时 1 h（客户表写的是 60 mm，不是项目里常用的 61 mm），每区 2 个态（L1 / L2）。

口径（同 rg_run_15m.py 三区域 15 m 版，只改下面几处）：
  计算域   客户汇水区图层（KMZ）里那一个多边形（cases/ad/<区>_domain.geojson），不外扩；四周自由出流，域外地表来水不算
  一维管网 全市一维（客户 GDB 重建），触及计算域的连通块整块取（域外部分只算一维，冒出的水记为损失）
  L1 / L2  L1 = 既有管网 + 计算域内现状泵站；L2 = L1 + 已建成塘（RD14 = Baniyas Lake、RD2 = SW_SCI_MSH44_PND_42；MI8 里那座无库容无进水管、全市 L2 也没建，其余区没有塘，L2 同 L1）
  产流     SCS CN 82、Tc 30 min（全市值）；雨型取客户 Mussafah 模型里 1 h 的设计雨型、等比放大到 60 mm；模拟终止 = 历时 + 4 h = 5 h
  二维     couple2way_v2，30 m（单元面积上限 300 m²），地形：DTM 先按 30 m 取块平均（写 c2d/dem_<名>.tif，C2D_DEM_RAW=1 直接读），曼宁 0.030，交换步 60 s
  成果     不进 swbrain 的 out/ 目录，post 步骤直接写到 NAS：$AD_RESULT_ROOT/$AD_HOST/<区域目录>_60mm1h_30m/（含积水点结果表与图层、.sww）；
           .sww → shp + dat 由服务器脚本（ad_30m_server*.sh）在推演全部完成后自动转换，放在 result 同级的 shp_dat/ 下

文件前缀 AD_<情景>（如 AD_MI8_M60H1_L1.inp），计算容器 adpipe_*，和 C1 / Mussafah / 三区域 15 m 的互不干扰。

用法（容器里 `python tools/ad_run_30m.py ...`）：
  python tools/ad_run_30m.py check-patches
  python tools/ad_run_30m.py check   --tag RDA_M60H1
  python tools/ad_run_30m.py plan    --tag RDA_M60H1
  python tools/ad_run_30m.py run prep                        --tag RDA_M60H1
  python tools/ad_run_30m.py run build:RDA_M60H1             --tag RDA_M60H1
  python tools/ad_run_30m.py run compute:RDA_M60H1:L1,L2     --tag RDA_M60H1
  python tools/ad_run_30m.py run post:RDA_M60H1 [--force]    --tag RDA_M60H1
多个区一起排队用 c1_batch.py --runner ad_run_30m.py（服务器上用 ad_30m_server{1,2,3}.sh）。
"""'''
s = s[:a] + DOC + s[b:]

sub("from rg_regions import CASES, REGIONS  # noqa: E402", "from ad_regions import CASES, REGIONS  # noqa: E402")
sub('PREFIX = "RG"                  # 文件名前缀：RG_<情景>_<态>', 'PREFIX = "AD"                  # 文件名前缀：AD_<情景>_<态>')
sub('CONTAINER = "rgpipe"           # 计算容器名：rgpipe_<情景>_<态>', 'CONTAINER = "adpipe"           # 计算容器名：adpipe_<情景>_<态>')
sub('STEP_LOG = "rg"                # 调度器的步骤日志：logs/rg_<步骤>.log', 'STEP_LOG = "ad"                # 调度器的步骤日志：logs/ad_<步骤>.log')
sub("RES_M = 15\n", "RES_M = 30\n")
sub("RAINS = [(33, 1), (33, 2), (33, 24), (61, 1), (61, 2), (61, 24), (96, 1), (96, 2), (96, 24)]", "RAINS = [(60, 1)]")
sub('BATCH_PRE = {"desc": "三区域准备（雨型来源 Mussafah_baseline.inp、耦合程序、读入检查）", "steps": ["prep"],\n             "outs": [P.WORK / "Mussafah_baseline.inp", P.C2D / "couple2way_v2.py", P.C2D / "npz_to_sww.py", P.C2D / "_chk_inp.py"]}',
    '''# 成果直接写 NAS：$AD_RESULT_ROOT/$AD_HOST/<区域目录>_60mm1h_30m/；NAS 的宿主机路径也挂进 post 的容器（同路径）
RESULT_ROOT = os.environ.get("AD_RESULT_ROOT", "/media/pop-fs/flood_data/result")
AD_HOST = os.environ.get("AD_HOST", "")
NAS_BASE = str(pathlib.PurePosixPath(RESULT_ROOT).parent)
BATCH_PRE = {"desc": "14 个汇水区准备（雨型来源 Mussafah_baseline.inp、耦合程序、后处理脚本、案例数据、读入检查）", "steps": ["prep"],
             "outs": [P.WORK / "Mussafah_baseline.inp", P.C2D / "couple2way_v2.py", P.C2D / "npz_to_sww.py", P.C2D / "_chk_inp.py", P.C2D / "ad_post.py", P.C2D / "ad_cases" / "hotspots_ad.json"]}''')
sub('def post_out(t: str) -> pathlib.Path:\n    return ROOT / "out" / f"rg_stats_{t}.json"', 'def post_out(t: str) -> pathlib.Path:\n    return P.C2D / f"ad_stats_{t}.json"          # 后处理在 anuga-swmm 容器里跑，只看得到 /work，标记文件放这里')
sub('cache = P.C2D / f"rg_net_{reg}.pkl"', 'cache = P.C2D / f"ad_net_{reg}.pkl"')

# ---- prep：多拷 ad_post.py 与案例数据
sub('for s, d in ((HERE / "couple2way_v2.py", C / "couple2way_v2.py"), (HERE / "npz_to_sww.py", C / "npz_to_sww.py"), (HERE / "chk_inp.py", C / "_chk_inp.py")):',
    'for s, d in ((HERE / "couple2way_v2.py", C / "couple2way_v2.py"), (HERE / "npz_to_sww.py", C / "npz_to_sww.py"), (HERE / "chk_inp.py", C / "_chk_inp.py"),\n                 (HERE / "ad_post.py", C / "ad_post.py")):')
sub('''        if not d.exists():
            print(f"  cp {s} → {d}")
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(s, d)
    for reg in REGIONS:''', '''        if not d.exists() or d.name == "ad_post.py":            # ad_post.py 每次覆盖，改了脚本不用手工删
            print(f"  cp {s} → {d}")
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(s, d)
    if not dry:                                                 # 后处理要的案例数据（计算域 / 泵站 / 塘 / 积水点）放进 /work/c2d/ad_cases/
        (C / "ad_cases").mkdir(parents=True, exist_ok=True)
        for f in CASES.glob("*"):
            shutil.copy2(f, C / "ad_cases" / f.name)
    for reg in REGIONS:''')
sub('''                raise SystemExit(f"缺 {f}：在开发机上跑 python tools/rg_regions.py 生成，随上传包带上")''',
    '''                raise SystemExit(f"缺 {f}：在开发机上跑 python tools/ad_regions.py 生成，随上传包带上")
    if not (CASES / "hotspots_ad.json").exists():
        raise SystemExit(f"缺 {CASES / 'hotspots_ad.json'}：在开发机上跑 python tools/ad_regions.py（不带参数）生成")''')

# ---- write_dem：先按 30 m 取块平均（同 city_run_250m）
a = s.index("def write_dem(name: str, D):"); b = s.index("def build(t: str, dry: bool)")
s = s[:a] + '''def write_dem(name: str, D):
    """DTM（现为 50 cm）→ 计算域外扩 50 m 的包络内按 RES_M 取块平均 → c2d/dem_<名>.tif；返回 (形状, 原 DTM 分辨率)。
    耦合程序有 C2D_DEM_RAW=1，直接读这份已平均好的栅格；不把 50 cm 原值裁给 250 km²（几十 GB）。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    from affine import Affine
    import datapaths
    X0, Y0, X1, Y1 = D.bounds
    with rasterio.open(datapaths.get("dtm")) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        f = RES_M / ds.res[0]
        Hh, Ww = max(int(win.height / f), 1), max(int(win.width / f), 1)
        print(f"  DTM {ds.res[0]:g} m：窗口 {win.width}×{win.height} 像素 → 取平均成 {Ww}×{Hh} 格 @ {RES_M} m", flush=True)
        z = ds.read(1, window=win, out_shape=(Hh, Ww), resampling=Resampling.average).astype("float32")
        tr = ds.window_transform(win) * Affine.scale(win.width / Ww, win.height / Hh)
        prof = ds.profile.copy(); prof.update(width=Ww, height=Hh, transform=tr, compress="deflate", dtype="float32", tiled=False)
        prof.pop("blockxsize", None); prof.pop("blockysize", None)
        with rasterio.open(P.C2D / f"dem_{name}.tif", "w", **prof) as w:
            w.write(z, 1)
        return z.shape, ds.res[0]


''' + s[b:]

# ---- build：SWMM 线程数、输入记录位置、标题
sub('txt = out.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", "THREADS  4", 1)',
    'txt = out.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", f"THREADS  {int(os.environ.get(\'AD_SWMM_THREADS\', \'8\'))}", 1)')
sub("（tools/rg_run_15m.py）", "（tools/ad_run_30m.py）")
sub('''    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / f"rg_inputs_{t}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")''',
    '''    info["dem_res_m"] = RES_M
    (C / f"ad_inputs_{t}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")      # 后处理（anuga-swmm 容器）读这份''')

# ---- 后处理步骤：.sww → ad_post（直接写 NAS）
a = s.index('        "post": (f"post:{t}"'); b = s.index("    }\n\n\ndef all_steps")
s = s[:a] + '''        "post": (f"post:{t}", f"{t}：.sww、积水点结果表与图层、交付目录（直接写 NAS：{RESULT_ROOT}/<主机>/，{rain} mm / {dur} h）", [post_out(t)],
                 [*[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", name, s, RES_M, threads=8) for s in STATES],
                  dock_nas("python", "-u", "/work/c2d/ad_post.py", "--tag", t, "--res", RES_M, threads=8)]),
''' + s[b:]
sub("# ------------------------------------------------------------------ 步骤表：(id, 说明, 产物, 命令列表)",
    '''def dock_nas(*cmd, threads=8):
    """anuga-swmm 容器，多挂 NAS（宿主机路径同名挂进去）并带上主机名；成果直接写 $AD_RESULT_ROOT/$AD_HOST/。"""
    if not AD_HOST:
        raise SystemExit("环境变量 AD_HOST（主机名）没设：用 ad_30m_server*.sh 启动，别直接跑 python")
    return ["docker", "run", "--rm", "-e", f"OMP_NUM_THREADS={threads}", "-e", f"AD_RESULT_ROOT={RESULT_ROOT}", "-e", f"AD_HOST={AD_HOST}",
            "-v", f"{P.HOST_WORK}:/work", "-v", f"{NAS_BASE}:{NAS_BASE}", P.IMAGE, *map(str, cmd)]


# ------------------------------------------------------------------ 步骤表：(id, 说明, 产物, 命令列表)''')

# ---- check_patches / check 里的文件清单与措辞
sub('''    for f in ("pilot_frames.py", "npz_to_tif.py", "rg_post.py", "runoff.py", "build_city1d.py", "build_pilot.py", "strip_baseline.py", "couple2way_v2.py", "npz_to_sww.py", "chk_inp.py"):''',
    '''    for f in ("ad_post.py", "ad_regions.py", "runoff.py", "build_city1d.py", "build_pilot.py", "strip_baseline.py", "couple2way_v2.py", "npz_to_sww.py", "chk_inp.py"):''')
sub('''    print("文件都在（本 runner 不替换别的脚本的源码，没有替换点）" if not bad else f"有 {bad} 个文件不在")''',
    '''    ok = (CASES / "hotspots_ad.json").exists(); bad += not ok
    print(("  ✓ " if ok else "  ✗ ") + "cases/ad/hotspots_ad.json" + ("" if ok else " 不在"))
    print("文件都在（本 runner 不替换别的脚本的源码，没有替换点）" if not bad else f"有 {bad} 个文件不在")''')
sub('''for lab, p in (("GDB", datapaths.get("gdb_dir")), ("DTM", datapaths.get("dtm")), ("雨型来源", P.WORK / "Mussafah_baseline.inp"),
                   ("耦合程序", P.C2D / "couple2way_v2.py"), ("读入检查", P.C2D / "_chk_inp.py"), (".sww 转换", P.C2D / "npz_to_sww.py")):''',
    '''for lab, p in (("GDB", datapaths.get("gdb_dir")), ("DTM", datapaths.get("dtm")), ("雨型来源", P.WORK / "Mussafah_baseline.inp"),
                   ("耦合程序", P.C2D / "couple2way_v2.py"), ("读入检查", P.C2D / "_chk_inp.py"), (".sww 转换", P.C2D / "npz_to_sww.py"), ("后处理", P.C2D / "ad_post.py"),
                   ("NAS 结果目录", pathlib.Path(RESULT_ROOT))):''')
s = s.replace("rg_post", "ad_post").replace("rg_regions", "ad_regions").replace("rg_run_15m", "ad_run_30m").replace("三区域准备", "14 个汇水区准备")
# 注意：compute_step 的缺省线程在服务器脚本里用 C2D_G5_THREADS 设
(T / "ad_run_30m.py").write_text(s, encoding="utf-8", newline="\n")
print("写出", T / "ad_run_30m.py", len(s.splitlines()), "行")
