# -*- coding: utf-8 -*-
"""1105 批的推演入口（由 run_region_g1104.py 复制改出，调 run_region_1105：加内存防护）（同 run_region_g.py，但调 run_region_1105：L2 / L2planned 输入与 L1 相同就复制 L1 结果）；原说明：261101 新区域的推演入口：装上本批新区域专用的设施清单（g261101_facilities）和产流子区表（g261101_runoff），再调 run_region.main()。不改 run_region.py / region_deliver.py。
参数同 run_region.py。多进程用 fork，补丁会带进每个场景的子进程。
"""
import multiprocessing
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    multiprocessing.set_start_method("fork")
    import g261101_facilities
    g261101_facilities.install()
    import g261101_net
    g261101_net.install()
    import g261101_nanfix
    g261101_nanfix.install()
    import g261101_ids
    g261101_ids.install()
    import g261101_runoff
    g261101_runoff.install()
    import g1105_patch
    g1105_patch.install()
    import run_region_1105 as run_region
    raise SystemExit(run_region.main())
