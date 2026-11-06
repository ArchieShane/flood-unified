import re, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")
f = pathlib.Path(r"C:\freedo\zn-260930\thinking-drainage-system\tools\adm250_post.py")
s = f.read_text(encoding="utf-8")


def rep(old, new):
    global s
    assert s.count(old) >= 1, "找不到：" + old[:80]
    s = s.replace(old, new, 1)


i = s.index('"""'); j = s.index('"""', i + 3) + 3
s = s[:i] + '''"""ADM 全市 250 m · L2 后处理与交付（在 anuga-swmm 容器里跑，只用 numpy / scipy / rasterio / pyproj；2026-10-19，由 ad_post.py 改来）。

做什么（一个情景 = 全市 × 一场雨，只有 L2 一个态）：
  1. 积水点结果表：计算域内每个积水点（ADM 热点，cases/ad250/hotspots_ad.json）一行——离该点 200 m 内的最大水深、最近单元水深、
     ≥0.10 / ≥0.30 m 积水面积、最大积水量及出现时刻、水深 ≥ 0.10 m 的起始时刻与持续时间、是否积水（最大水深 ≥ 0.30 m）
  2. 积水点图层：积水点位置 + 上表指标（GeoJSON，WGS84）
  3. 汇总：全域最大水深、积水面积、最大积水量、最深点离最近积水点多远；一维溢流与水量对账（SWMM 报告）
  4. 最大水深 GeoTIFF（250 m，UTM 40N）、.sww（从 /work/c2d/anuga_out 直接搬到交付目录，本地不留）、一维 .inp / 报告、耦合运行记录、.npz、计算域 / 泵站 / 塘
  交付目录直接写在 NAS：$AD_RESULT_ROOT/$AD_HOST/ADM_City_<雨量>mm<历时>h_250m/（不用再同步）。
  标记文件 /work/c2d/ax_stats_<情景>.json 最后写（调度器据此认为本步完成）。
  不做：L2 − L1 对比（没有 L1）、逐帧图、shp + dat。

用法（容器内）：python /work/c2d/adm250_post.py --tag CITY_M61H2 --res 250
"""''' + s[j:]

rep('SNAME = {"L1": "L1", "L2": "L2"}\nDIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄"}', 'SNAME = {"L2": "L2"}\nDIRS = {"L2": "L2_已建成调蓄"}')
rep("ap.add_argument(\"--res\", type=float, default=30.0)", "ap.add_argument(\"--res\", type=float, default=250.0)")
rep('name = f"AD_{t}"', 'name = f"AX_{t}"')
rep('cases = C2D / "ad_cases"', 'cases = C2D / "ad250_cases"')
rep('inp = json.loads((C2D / f"ad_inputs_{t}.json")', 'inp = json.loads((C2D / f"ax_inputs_{t}.json")')
rep('states = [s for s in ("L1", "L2") if (C2D', 'states = [s for s in ("L2",) if (C2D')
rep("if len(states) < 2:", "if len(states) < 1:")
rep("{[s for s in ('L1', 'L2') if s not in states]} 还没算完", "L2 还没算完")
rep('    head += ["L2−L1 最大水深变化 m"]\n', "")
s, n = re.subn(r'        dd = round\(res_hs\["L2"\]\[hid\]\["max_depth_200m"\] - res_hs\["L1"\]\[hid\]\["max_depth_200m"\], 3\); r\.append\(dd\); props\["L2_minus_L1_m"\] = dd\n', "", s, count=1)
assert n == 1
rep('(C2D / f"ad_stats_{t}.json").write_text', '(C2D / f"ax_stats_{t}.json").write_text')
# 计算设置.md
a = s.index('(DD / "计算设置.md").write_text(')
b = s.index('    lines = [f"# {RG', a)
cfg = '''(DD / "计算设置.md").write_text("\\n".join([
        f"# {RG['title']} · {rain:g} mm / {dur:g} h · {a.res:g} m · 计算设置", "", "| 项 | 设置 |", "|---|---|",
        f"| 情景 | {rain:g} mm / {dur:g} h，雨型取客户 Mussafah 模型里 {dur:g} h 的设计雨型、等比放大；模拟 {inp['sim_h']:g} h（历时 + 4 h） |",
        f"| 计算域 | Abu Dhabi 全市，{inp['domain_km2']:,.1f} km²（管网服务范围 ∪ 23 座塘的地表汇水区，外扩 300 m，取最大一块、去洞）；四周自由出流 |",
        f"| 积水点 | ADM 热点中落在计算域内的 {len(HS)} 个（公共/积水点.json） |",
        f"| 一维 | EPA SWMM 5.2，全市一维（客户 GDB 重建）按计算域截取：触及计算域的连通块整块取（{inp['n_components']} 块、{inp['n_nodes']:,} 个节点，其中域内 {inp['n_nodes_in_domain']:,}），{inp['n_pipes']:,} 根管、{inp['n_outfalls']} 个出水口；动力波，地面积水关（冒出的水交给二维） |",
        f"| 产流 | SCS CN {inp['cn']:g}、Tc {inp['tc_min']:g} min（全市统一值，同 ADM 汇水区各批；没用客户子汇水区参数），净雨 {inp['rain_excess_mm']} mm，按管网节点服务面积（服务半径 {inp['R_served_m']} m）注入一维；离最近井超过服务半径的地面，净雨直接下在二维上 |",
        f"| 二维 | ANUGA，非结构三角网，单元面积上限 {a.res:g}²/3 m²（约 {inp['n_tri_est']:,} 个），曼宁 n = 0.030 |",
        f"| 地形 | 50 cm DTM 先按 {a.res:g} m 取块平均再赋给三角单元：有效分辨率 {a.res:g} m，小尺度起伏（窄沟、路缘石、小凹地）被抹平，积水深度 / 面积与 30 m、15 m、5 m 版不能直接比 |",
        "| 耦合 | couple2way_v2，同进程逐步双向，交换步 60 s；计算域内的井参与交换（井盖孔口 Cd 0.6、DN600），冒出的水分摊到井周 4 个单元 |",
        f"| L2 | 既有管网 + 计算域内现状泵站 {len(inp['pumps'])} 座" + (f"（没建的泵：{'；'.join(inp['pump_limits'])}）" if inp["pump_limits"] else "")
        + f" + 已建成塘 {len(ponds)} 座（平底蓄水节点，进水管带拍门，塘占地内的地面水直接进塘；接法：全市 L2 的 23 座取手工整理 / pond_link 的接法，其余在管网里找贴着塘的井，找不到的是只在二维收水的独立蓄水体）。**本批只算 L2，没有 L1，不出 L2 − L1 对比** |",
        f"| 积水点指标 | 离积水点坐标 200 m 内的单元：最大水深、积水面积（≥0.10 / ≥0.30 m）、最大积水量；{a.res:g} m 网格 200 m 内只有个位数单元，同时给出最近单元水深 |",
        "| 输出 | 二维每 5 分钟一帧（在 .sww / .npz 里）；.sww 与最大水深 GeoTIFF 为 UTM 40N（EPSG:32640）；本批不转 shp + dat、不进 flood-viewer / flood-aio |", ""]), encoding="utf-8")
'''
s = s[:a] + cfg + s[b:]
rep("· L1 / L2 结果（{a.res:g} m 网格）", "· L2 结果（{a.res:g} m 网格）")
s, n = re.subn(r'    lines \+= \["", \("L2 比 L1 多 ".*?\n              "", "目录：', '    lines += ["", "本批只算 L2（既有管网 + 现状泵站 + 已建成塘），没有 L1，不出对比。",\n              "", "目录：', s, count=1, flags=re.S)
assert n == 1
rep("各态 `一维/` `二维/`（含 .sww、最大水深 tif）", "`L2_已建成调蓄/一维/` `二维/`（含 .sww、.npz、最大水深 tif）")
# 积水点标题中 "30 m" 之类
s = s.replace("ad_post.py", "adm250_post.py")
f.write_text(s, encoding="utf-8", newline="\n")
print("ok")
