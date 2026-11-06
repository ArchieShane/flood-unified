# -*- coding: utf-8 -*-
"""ANUGA .sww -> 飞渡 DTS（DTS Cloud h2d：shp + RESULTSxxx.dat）转换器的命令行入口。

转换代码已并入系统：swbrain/dts_h2d.py（交付导出与后处理自动调用，每个态写 <态>/飞渡DTS/）。
这里保留原来的用法，选项不变，例如：
  python hydro2d_convert.py <结果目录或 .sww 文件> -o <输出目录> --name abudhabi_z40
详细选项：python hydro2d_convert.py -h
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from swbrain.dts_h2d import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
