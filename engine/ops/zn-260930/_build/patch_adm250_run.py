import re, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")
f = pathlib.Path(r"C:\freedo\zn-260930\thinking-drainage-system\tools\adm250_run.py")
s = f.read_text(encoding="utf-8")


def rep(old, new):
    global s
    assert s.count(old) >= 1, "找不到：" + old[:70]
    s = s.replace(old, new, 1)


# 1 docstring
i = s.index('"""'); j = s.index('"""', i + 3) + 3
doc = '''"""ADM 全市 · 二维 250 m 网格 · 33 / 61 / 96 mm × 1 / 2 / 24 h（9 场）· 只算 L2（2026-10-19）。

由 ad_run_30m.py（ADM 汇水区 30 m / 5 m 版）改来，口径同 ADM 标准，只改下面几处（用户 10-19 定）：
  计算域   Abu Dhabi 全市 1,234 km²（同 city_run_250m.py：塘 + 塘汇水区 ∪ 管网服务范围，外扩 300 m、取最大一块、去洞），cases/ad250/CITY_domain.geojson；四周自由出流
  一维管网 全市一维（客户 GDB 重建）触及计算域的连通块整块取；现状泵站（域内）+ 域内已建成塘（ponds_all 去重清单，L2 才建）
  态       只算 L2 = 既有管网 + 现状泵站 + 已建成塘；不算 L1，所以没有 L2 - L1 差值与对比
  产流     SCS CN 82、Tc 30 min（全市值，同 ADM 汇水区各批；不用客户子汇水区参数）；雨型取客户 Mussafah 模型里同历时的设计雨型、等比放大；模拟终止 = 历时 + 4 h
  二维     couple2way_v2，250 m（单元面积上限 250²/3 m²），地形：DTM 先按 250 m 取块平均（全市一份 c2d/dem_AX250.tif，9 场共用），曼宁 0.030，交换步 60 s
  成果     post 步骤直接写 NAS：$AD_RESULT_ROOT/$AD_HOST/ADM_City_<雨量>mm<历时>h_250m/（积水点结果表与图层、统计、.sww、.npz、最大水深 tif）
           不做逐帧图 / viewer、不转 shp + dat、不登记 flood-aio

文件前缀 AX_<情景>（如 AX_CITY_M61H2_L2.inp），计算容器 axpipe_*，和 C1 / Mussafah / 三区域 / ADM 各批 / 全市 CT 250 m 的互不干扰。
情景名 CITY_M<雨量>H<历时>。

用法（容器里 `python tools/adm250_run.py ...`）：
  python tools/adm250_run.py check-patches
  python tools/adm250_run.py check   --tag CITY_M61H2
  python tools/adm250_run.py plan    --tag CITY_M61H2
  python tools/adm250_run.py run prep                      --tag CITY_M61H2
  python tools/adm250_run.py run build:CITY_M61H2          --tag CITY_M61H2
  python tools/adm250_run.py run compute:CITY_M61H2:L2     --tag CITY_M61H2
  python tools/adm250_run.py run post:CITY_M61H2 [--force] --tag CITY_M61H2
多场一起排队用 c1_batch.py --runner adm250_run.py（服务器上用 adm_250m_server.sh）。
"""'''
s = s[:i] + doc + s[j:]

# 2 区域元数据：从案例数据读
rep("from ad_regions import CASES, REGIONS  # noqa: E402",
    '''CASES = ROOT / "cases" / "ad250"          # 容器里 /app/cases/ad250（服务器脚本 docker cp 进去）
_HS = CASES / "hotspots_ad.json"
if not _HS.exists():
    raise SystemExit(f"缺 {_HS}：在开发机上跑 .venv/Scripts/python.exe tools/adm250_regions.py 生成 cases/ad250/，随上传包带上")
REGIONS = json.loads(_HS.read_text(encoding="utf-8"))["regions"]      # {"CITY": {title, feat, dir, km2, res, rains, ...}}''')

# 3 常量
rep('PREFIX = "AD"                  # 文件名前缀：AD_<情景>_<态>', 'PREFIX = "AX"                  # 文件名前缀：AX_<情景>_<态>')
rep('CONTAINER = "adpipe"           # 计算容器名：adpipe_<情景>_<态>', 'CONTAINER = "axpipe"           # 计算容器名：axpipe_<情景>_<态>')
rep('STEP_LOG = "ad"                # 调度器的步骤日志：logs/ad_<步骤>.log', 'STEP_LOG = "ax"                # 调度器的步骤日志：logs/ax_<步骤>.log')
rep("RES_M = 30\n", 'RES_M = 250\nGRID = "AX250"                 # 全市共用的 DEM / 网络缓存名\n')
rep('STATES3 = ["L1", "L2"]         # c1_batch：本 runner 只有这两个态', 'STATES3 = ["L2"]               # c1_batch：本 runner 只算 L2')
rep('SNAME = {"L1": "L1", "L2": "L2"}\nDIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄"}', 'SNAME = {"L2": "L2"}\nDIRS = {"L2": "L2_已建成调蓄"}')
s = re.sub(r"RAINS = \[\(61, 1\), \(96, 2\)\][^\n]*", "RAINS = [(33, 1), (33, 2), (33, 24), (61, 1), (61, 2), (61, 24), (96, 1), (96, 2), (96, 24)]      # 9 场", s, count=1)
s = re.sub(r"SCEN = \{f\"\{r\}_M\{mm\}H\{h\}\".*", 'SCEN = {f"{r}_M{mm}H{h}": (mm, h) for r in REGIONS for mm, h in (REGIONS[r].get("rains") or RAINS)}', s, count=1)
rep("ADM 汇水区准备（雨型来源 Mussafah_baseline.inp、耦合程序、后处理脚本、案例数据、读入检查）", "ADM 全市 250 m 准备（雨型来源、耦合程序、后处理脚本、案例数据、全市一维网与计算域缓存、250 m DEM）")
s, n = re.subn(r'"outs": \[P\.WORK / "Mussafah_baseline\.inp".*?\]\}[^\n]*\n', '''"outs": [P.WORK / "Mussafah_baseline.inp", P.C2D / "couple2way_v2.py", P.C2D / "npz_to_sww.py", P.C2D / "_chk_inp.py", P.C2D / "adm250_post.py",
                      P.C2D / "ad250_cases" / "hotspots_ad.json", P.C2D / "ax_net_CITY.pkl", P.C2D / f"dem_{GRID}.tif"]}
''', s, count=1, flags=re.S)
assert n == 1
rep("# 成果直接写 NAS：$AD_RESULT_ROOT/$AD_HOST/<区域目录>_61mm1h_30m/", "# 成果直接写 NAS：$AD_RESULT_ROOT/$AD_HOST/<区域目录>_<雨量>mm<历时>h_250m/")
rep('return P.C2D / f"ad_stats_{t}.json"', 'return P.C2D / f"ax_stats_{t}.json"')
rep('cache = P.C2D / f"ad_net2_{reg}.pkl"          # v2：塘清单改成 ponds_all（10-16），旧缓存里的塘不对', 'cache = P.C2D / f"ax_net_{reg}.pkl"')
# 4 hotspots() 死代码
s, n = re.subn(r"def hotspots\(reg: str\) -> list\[str\]:\n    return \[[^\n]*\n\n\n", "", s, count=1)
assert n == 1
# 5 prep
rep('(HERE / "ad_post.py", C / "ad_post.py")):', '(HERE / "adm250_post.py", C / "adm250_post.py")):')
rep('if not d.exists() or d.name == "ad_post.py":            # ad_post.py 每次覆盖，改了脚本不用手工删', 'if not d.exists() or d.name == "adm250_post.py":        # adm250_post.py 每次覆盖，改了脚本不用手工删')
rep('(C / "ad_cases").mkdir(parents=True, exist_ok=True)', '(C / "ad250_cases").mkdir(parents=True, exist_ok=True)')
rep('shutil.copy2(f, C / "ad_cases" / f.name)', 'shutil.copy2(f, C / "ad250_cases" / f.name)')
rep("在开发机上跑 python tools/ad_regions.py 生成，随上传包带上", "在开发机上跑 tools/adm250_regions.py 生成，随上传包带上")
rep('''        raise SystemExit(f"缺 {CASES / 'hotspots_ad.json'}：在开发机上跑 python tools/ad_regions.py（不带参数）生成")''',
    '''        raise SystemExit(f"缺 {CASES / 'hotspots_ad.json'}：在开发机上跑 tools/adm250_regions.py 生成")
    if not dry and (not (C / "ax_net_CITY.pkl").exists() or not (C / f"dem_{GRID}.tif").exists()):
        print("  全市一维网截取 + 塘 + 泵站 + 250 m DEM（读 GDB 与 DTM，约十几分钟；之后缓存 ax_net_CITY.pkl / dem_AX250.tif）", flush=True)
        C.mkdir(parents=True, exist_ok=True)
        network("CITY")
        if not (C / f"dem_{GRID}.tif").exists():
            write_dem(GRID, domain_utm("CITY"), RES_M)''')
# 6 DEM：全市共用一份
rep("    zshape, dtm_res = write_dem(name, D, RES_M)\n", '''    import rasterio
    shared = C / f"dem_{GRID}.tif"
    if not shared.exists():
        write_dem(GRID, D, RES_M)
    with rasterio.open(shared) as ds:
        zshape = (ds.height, ds.width)
    d = C / f"dem_{name}.tif"                       # 耦合程序读 c2d/dem_<名>.tif：全市共用一份 250 m DEM，硬链接 / 复制给每个情景名
    if not d.exists():
        try:
            os.link(shared, d)
        except OSError:
            shutil.copy2(shared, d)
''')
rep('"dtm_res_m": dtm_res, "dem_px"', '"dtm_res_m": 0.5, "dem_px"')
rep('(C / f"ad_inputs_{t}.json").write_text', '(C / f"ax_inputs_{t}.json").write_text')
rep("（DTM {dtm_res:g} m 取平均）", "（DTM 取平均）")
# 7 步骤文字
rep("L1 / L2 的 .inp、dem_{name}.tif、pilot_{name}_<态>.json", "L2 的 .inp、dem_{name}.tif、pilot_{name}_<态>.json")
rep('f"{t}：建 L1 / L2（{rain} mm / {dur} h）"', 'f"{t}：建 L2（{rain} mm / {dur} h）"')
rep('"ADM 汇水区准备（雨型来源、耦合程序、案例数据齐不齐）"', '"ADM 全市 250 m 准备（雨型来源、耦合程序、案例数据、全市一维网与 DEM 缓存）"')
rep('dock_nas("python", "-u", "/work/c2d/ad_post.py", "--tag", t, "--res", RES_M, threads=8)', 'dock_nas("python", "-u", "/work/c2d/adm250_post.py", "--tag", t, "--res", RES_M, threads=8)')
rep("# ------------------------------------------------------------------ 建模：L1 / L2 的 .inp、DEM、pilot", "# ------------------------------------------------------------------ 建模：L2 的 .inp、DEM、pilot")
# 8 threads_of
s, n = re.subn(r"def threads_of\(reg: str\) -> int:.*?\n\n\ndef compute_step", '''def threads_of(reg: str) -> int:
    """每个态的线程数：全市一个 26 万节点的整网 SWMM，缺省 8（调度器 --threads / C2D_G5_THREADS 可改；AD_THREADS_MAP="CITY=12" 也行）。"""
    for kv in os.environ.get("AD_THREADS_MAP", "").replace(";", ",").split(","):
        if "=" in kv and kv.split("=")[0].strip() == reg:
            return max(1, int(kv.split("=")[1]))
    return int(os.environ.get("C2D_G5_THREADS", "8"))


def compute_step''', s, count=1, flags=re.S)
assert n == 1
# 9 check-patches 与零碎
rep('for f in ("ad_post.py", "ad_regions.py", "runoff.py"', 'for f in ("adm250_post.py", "runoff.py"')
s = s.replace("cases/ad/{f}", "cases/ad250/{f}").replace('"cases/ad/hotspots_ad.json"', '"cases/ad250/hotspots_ad.json"')
s = s.replace('("后处理脚本", HERE / "ad_post.py")', '("后处理脚本", HERE / "adm250_post.py")')
s = s.replace("ad_30m_server*.sh", "adm_250m_server.sh").replace("tools/ad_run_30m.py", "tools/adm250_run.py")
f.write_text(s, encoding="utf-8", newline="\n")
print("ok", len(s))
