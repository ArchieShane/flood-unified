"""RD-A 局部放大图（ArcGIS Pro，arcpy.mp）。数据由 map_rda_zoom_prep.py 写好；出图函数沿用 map_rda_export.py。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/map_rda_zoom_export.py <空白 aprx 模板> <输出目录> <标题后缀>
输出 RD-A_局部_治理前.png、RD-A_局部_治理后.png（同一范围）。
"""
import os
import shutil
import sys

import arcpy

sys.path.insert(0, os.path.dirname(__file__))
import map_rda_export as M  # noqa: E402

M.GPKG = r"D:\swbrain_work\exp_gov\09_maps\rda_zoom.gpkg"


def n(name):
    return int(arcpy.management.GetCount(os.path.join(M.GPKG, f"main.{name}"))[0])


def opt(m, name, title, **kw):
    """图层有要素才加（不同局部位置要的图层不一样）"""
    if n(name):
        M.simple(M.add(m, name, title), **kw)


def main(tpl, out, where, prefix="RD-A_局部"):
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "rda_zoom.aprx")
    shutil.copy(tpl, p)
    aprx = arcpy.mp.ArcGISProject(p)
    b = [r for r in arcpy.da.SearchCursor(os.path.join(M.GPKG, "main.box"), ["SHAPE@"])][0][0].extent
    ext = arcpy.Extent(b.XMin, b.YMin, b.XMax, b.YMax, spatial_reference=arcpy.SpatialReference(32640))
    k_ab, k_aec = n("raw_bad_abandoned"), n("gov_aecom")

    def pts(m, tag):
        opt(m, f"{tag}_inlet", "雨水口", color=M.rgb(120, 120, 120), size=4, shape="Square 1", outline=M.rgb(255, 255, 255))
        opt(m, f"{tag}_mh", "检查井", color=M.rgb(60, 60, 60), size=9, shape="Circle 1", outline=M.rgb(255, 255, 255))

    def before(m):
        opt(m, "raw_ok", "DMT 原始管道（连得到出水口，但管底比地面高约 98 m）", color=M.rgb(214, 39, 40), width=2.0)
        opt(m, "raw_bad_abandoned", f"DMT 子类型记为「废弃」的管：几何上连不到出水口（{k_ab} 根）", color=M.rgb(90, 0, 0), width=4.5)
        opt(m, "raw_dangling", "悬空管端", color=M.rgb(255, 140, 0), size=10, shape="Circle 1", outline=M.rgb(90, 0, 0))
        pts(m, "raw")
        opt(m, "raw_outfall", "DMT 出水口", color=M.rgb(31, 119, 180), size=14, shape="Triangle 1", outline=M.rgb(255, 255, 255))
        opt(m, "raw_pump", "DMT 泵站：只有一个点，流量 0、泵数 0，没有湿井和压力管", color=M.rgb(148, 103, 189), size=16, shape="Square 1", outline=M.rgb(90, 0, 0))

    def after(m):
        opt(m, "gov_excluded", f"DMT 记为废弃的记录：按状态规则不装（{k_ab} 根，被下面的蓝线覆盖）", color=M.rgb(190, 190, 190), width=4.5)
        opt(m, "gov_dmt", "治理后 DMT 管道：高程校准、接通到出水口", color=M.rgb(26, 152, 80), width=2.0)
        opt(m, "gov_aecom", f"同一位置 AECOM 建为在用管：补入并接通到出水口（{k_aec} 根；与 DMT 不一致，待客户确认）" if k_ab else f"从 AECOM 补入的重力管（{k_aec} 根）", color=M.rgb(0, 112, 192), width=4.0)
        opt(m, "gov_force", f"从 AECOM 补入的压力管（{n('gov_force')} 段）", color=M.rgb(148, 103, 189), width=4.0)
        pts(m, "gov")
        opt(m, "gov_outfall", "出水口（含 AECOM 补入）", color=M.rgb(31, 119, 180), size=14, shape="Triangle 1", outline=M.rgb(255, 255, 255))
        opt(m, "gov_wetwell", "湿井（DMT 泵站点 + AECOM 湿井参数）", color=M.rgb(255, 190, 60), size=20, shape="Circle 1", outline=M.rgb(90, 50, 0))
        opt(m, "gov_pump", f"泵（AECOM 补入泵曲线，{n('gov_pump')} 台）", color=M.rgb(148, 103, 189), size=12, shape="Square 1", outline=M.rgb(255, 255, 255))

    M.make(aprx, "Zoom_before", [before], f"治理前（局部放大）：{where}", os.path.join(out, f"{prefix}_治理前.png"), ext)
    M.make(aprx, "Zoom_after", [after], f"治理后（同一范围）：{where}", os.path.join(out, f"{prefix}_治理后.png"), ext)
    aprx.save()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:5])
