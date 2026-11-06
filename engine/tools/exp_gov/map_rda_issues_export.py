"""RD-A 遗留问题分布图（ArcGIS Pro）。数据：09_maps/rda_issues_map.gpkg（issues：code / type_cn；domain；pipes 治理后的管）。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/map_rda_issues_export.py <空白 aprx 模板> <输出目录>
"""
import os
import shutil
import sys

import arcpy

sys.path.insert(0, os.path.dirname(__file__))
import map_rda_export as M  # noqa: E402

M.GPKG = r"D:\swbrain_work\exp_gov\09_maps\rda_issues_map.gpkg"
# 先画多的、后画少的（少的压在上面看得见）
TYPES = [("DATE", "安装日期是占位值，需客户补（{n} 条）", (170, 170, 170), 3, "Circle 1"),
         ("CONF", "DMT 与 AECOM 数值不一致，需客户定以谁为准（{n} 处）", (0, 112, 192), 5, "Circle 1"),
         ("DATUM", "高程统一基准后仍和地形对不上，需客户核对（{n} 条）", (214, 39, 40), 11, "Circle 1"),
         ("SENT", "高程是哨兵值（−999 等），需客户补（{n} 条）", (255, 140, 0), 11, "Square 1"),
         ("REV", "倒坡管，需客户核对测量（{n} 根）", (148, 103, 189), 13, "Triangle 1")]


def main(tpl, out):
    p = os.path.join(out, "rda_issues.aprx")
    shutil.copy(tpl, p)
    aprx = arcpy.mp.ArcGISProject(p)
    d = [r for r in arcpy.da.SearchCursor(os.path.join(M.GPKG, "main.domain"), ["SHAPE@"])][0][0].extent
    pad = 0.06 * max(d.width, d.height)
    ext = arcpy.Extent(d.XMin - pad, d.YMin - pad, d.XMax + pad, d.YMax + pad, spatial_reference=arcpy.SpatialReference(32640))
    cnt = {}
    for (c,) in arcpy.da.SearchCursor(os.path.join(M.GPKG, "main.issues"), ["code"]):
        cnt[c] = cnt.get(c, 0) + 1

    def layers(m):
        l = M.add(m, "domain", "RD-A 范围")
        sym = l.symbology
        sym.renderer.symbol.color = M.rgb(0, 0, 0, 0)
        sym.renderer.symbol.outlineColor = M.rgb(80, 80, 80)
        sym.renderer.symbol.outlineWidth = 1.2
        l.symbology = sym
        M.simple(M.add(m, "pipes", "治理后的管道"), M.rgb(200, 200, 200), width=0.8)
        for code, title, col, size, shape in TYPES:
            l = M.add(m, "issues", title.format(n=f"{cnt.get(code, 0):,}"))
            l.definitionQuery = f"code = '{code}'"
            M.simple(l, M.rgb(*col), size=size, shape=shape, outline=M.rgb(255, 255, 255))

    total = sum(cnt.values())
    M.make(aprx, "RDA_issues", [layers], f"RD-A 数据治理后遗留问题分布（共 {total:,} 条，均需客户核对或补数据）",
           os.path.join(out, "RD-A_遗留问题分布图.png"), ext, extra=0.6)
    aprx.save()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:3])
