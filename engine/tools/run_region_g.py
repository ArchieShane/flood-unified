# -*- coding: utf-8 -*-
"""261101 新区域的推演入口：装上本批新区域专用的设施清单（g261101_facilities）和产流子区表（g261101_runoff），再调 run_region.main()。不改 run_region.py / region_deliver.py。
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
    import run_region
    raise SystemExit(run_region.main())
