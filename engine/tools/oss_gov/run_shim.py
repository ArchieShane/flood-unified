"""用开源替身运行治理链里依赖 arcpy 的脚本（开源路线；没有 ArcGIS 时用）。

  python tools/oss_gov/run_shim.py <脚本> [参数…]
  例：python tools/oss_gov/run_shim.py tools/exp_gov/s2_prep.py D:/swbrain_work/oss_full

把 tools/oss_gov/shim 放到 sys.path 最前面，脚本里的 `import arcpy` 拿到的是替身（shim/arcpy）；
脚本跑完，把替身内存里登记的数据集一次写成文件地理数据库。
"""
import pathlib
import runpy
import sys

SHIM = pathlib.Path(__file__).resolve().parent / "shim"


def main():
    script = pathlib.Path(sys.argv[1]).resolve()
    sys.argv = [str(script)] + sys.argv[2:]
    sys.path.insert(0, str(SHIM))
    sys.path.insert(1, str(script.parent))
    import arcpy                                   # 替身
    assert getattr(arcpy, "ENGINE", None) == "oss", "没拿到开源替身"
    runpy.run_path(str(script), run_name="__main__")
    arcpy.flush()                                  # 只在脚本成功跑完时写盘；中途失败不留半成品


if __name__ == "__main__":
    main()
