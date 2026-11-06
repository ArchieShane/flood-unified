"""RD-A 治理前后管网图（ArcGIS Pro 出图，arcpy.mp）。数据先由 map_rda_prep.py 写好。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/map_rda_export.py <空白 aprx 模板> <输出目录>
不改模板：先复制一份到输出目录再操作。输出 RD-A_治理前_管网.png、RD-A_治理后_管网.png（以及用于复查的 rda_maps.aprx）。
"""
import os
import shutil
import sys

import arcpy

GPKG = r"D:\swbrain_work\exp_gov\09_maps\rda_maps.gpkg"
FONT = "Microsoft YaHei"


def rgb(r, g, b, a=100):
    return {"RGB": [r, g, b, a]}


def add(m, name, title):
    lyr = m.addDataFromPath(os.path.join(GPKG, f"main.{name}"))
    lyr.name = title
    return lyr


def simple(lyr, color, width=None, size=None, shape=None, outline=None):
    sym = lyr.symbology
    sym.updateRenderer("SimpleRenderer")
    s = sym.renderer.symbol
    if shape:
        s.applySymbolFromGallery(shape)
    s.color = color
    if width is not None:
        s.width = width
    if size is not None:
        s.size = size
    if outline is not None:
        s.outlineColor = outline
    lyr.symbology = sym


def font_cim(layout):
    """把版面上文字（标题、图例、比例尺）的字体换成雅黑，避免中文显示成方框。"""
    cim = layout.getDefinition("V3")
    for el in cim.elements:
        for attr in ("graphic",):
            g = getattr(el, attr, None)
            if g is not None and hasattr(g, "symbol") and hasattr(g.symbol, "symbol") and hasattr(g.symbol.symbol, "fontFamilyName"):
                g.symbol.symbol.fontFamilyName = FONT
        for attr in ("labelSymbol", "headingSymbol", "titleSymbol"):
            s = getattr(el, attr, None)
            if s is not None and hasattr(s, "symbol") and hasattr(s.symbol, "fontFamilyName"):
                s.symbol.fontFamilyName = FONT
        items = getattr(el, "items", None)
        if items:
            for it in items:
                for attr in ("labelSymbol", "headingSymbol", "layerNameSymbol", "groupLayerNameSymbol"):
                    s = getattr(it, attr, None)
                    if s is not None and hasattr(s, "symbol") and hasattr(s.symbol, "fontFamilyName"):
                        s.symbol.fontFamilyName = FONT
    layout.setDefinition(cim)


def make(aprx, mapname, layers, title, png, extent, extra=0.0):
    """extra：图例条目多时，页面加高、地图整体上移的英寸数"""
    m = aprx.createMap(mapname, "Map")
    m.spatialReference = arcpy.SpatialReference(32640)
    for lyr in m.listLayers():
        m.removeLayer(lyr)
    try:
        m.addBasemap("Light Gray Canvas")
    except Exception as e:                        # 没登录门户就不加底图
        print("底图加不上：", e)
    for fn in layers:
        fn(m)
    lay = aprx.createLayout(11.0, 8.6 + extra, "INCH", mapname + "_layout")
    mf = lay.createMapFrame(arcpy.Extent(0.3, 1.45 + extra, 10.7, 7.95 + extra), m, mapname + "_frame")
    mf.camera.setExtent(extent)
    aprx.createTextElement(lay, arcpy.Point(0.35, 8.15 + extra), "POINT", title, 20, FONT)
    leg = lay.createMapSurroundElement(arcpy.Point(0.45, 1.35 + extra), "LEGEND", mf, None, mapname + "_legend")
    leg.elementPositionX, leg.elementPositionY = 0.45, 1.38 + extra
    lay.createMapSurroundElement(arcpy.Point(10.2, 7.2 + extra), "NORTH_ARROW", mf, None, mapname + "_na")
    font_cim(lay)
    lay.exportToPNG(png, resolution=200)
    print("出图", png)


def main(tpl, out):
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "rda_maps.aprx")
    shutil.copy(tpl, p)
    aprx = arcpy.mp.ArcGISProject(p)
    d = [r for r in arcpy.da.SearchCursor(os.path.join(GPKG, "main.domain"), ["SHAPE@"])][0][0]
    e = d.extent
    pad = 0.06 * max(e.width, e.height)
    ext = arcpy.Extent(e.XMin - pad, e.YMin - pad, e.XMax + pad, e.YMax + pad, spatial_reference=arcpy.SpatialReference(32640))

    def dom(m):
        l = add(m, "domain", "RD-A 范围")
        sym = l.symbology
        sym.renderer.symbol.color = rgb(0, 0, 0, 0)
        sym.renderer.symbol.outlineColor = rgb(80, 80, 80)
        sym.renderer.symbol.outlineWidth = 1.2
        l.symbology = sym

    def before(m):
        dom(m)
        l = add(m, "raw_pipes", "原始管道 2,412 根：管底比地面高约 98 m（未标注的 +100 m 基准），水进不了管")
        simple(l, rgb(214, 39, 40), width=1.3)
        l2 = add(m, "raw_pipes", "原始管道：连不到出水口（57 根）")
        l2.definitionQuery = "to_outfall = 0"
        simple(l2, rgb(90, 0, 0), width=3.0)
        l3 = add(m, "raw_dangling", "悬空管端（32 个）")
        simple(l3, rgb(255, 140, 0), size=7, shape="Circle 1", outline=rgb(90, 0, 0))
        l4 = add(m, "raw_outfalls", "出水口（DMT）")
        simple(l4, rgb(31, 119, 180), size=11, shape="Triangle 1", outline=rgb(255, 255, 255))

    def after(m):
        dom(m)
        l0 = add(m, "gov_lines", "治理后：未挂上子网（3 根）")
        l0.definitionQuery = "traced = 0 AND in_domain = 1"
        simple(l0, rgb(150, 150, 150), width=1.0)
        l = add(m, "gov_lines", "治理后：接通到出水口（2,441 根）")
        l.definitionQuery = "traced = 1 AND in_domain = 1"
        simple(l, rgb(26, 152, 80), width=1.3)
        l4 = add(m, "gov_outfalls", "出水口")
        simple(l4, rgb(31, 119, 180), size=11, shape="Triangle 1", outline=rgb(255, 255, 255))
        l5 = add(m, "gov_pumps", "泵站（AECOM 补入泵曲线）")
        simple(l5, rgb(148, 103, 189), size=11, shape="Square 1", outline=rgb(255, 255, 255))

    make(aprx, "RDA_before", [before], "治理前：DMT 原始管网（RD-A）", os.path.join(out, "RD-A_治理前_管网.png"), ext)
    make(aprx, "RDA_after", [after], "治理后：校准高程 + 接通到出水口（RD-A）", os.path.join(out, "RD-A_治理后_管网.png"), ext)
    aprx.save()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], sys.argv[2])
