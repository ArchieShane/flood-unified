"""快速验证合成算例（tools/demo_case.py）：不起 docker 的那一半——生成的模型能被 SWMM 读入并跑完，L2P 的泵真的少冒水。

完整的一二维（docker）验证太慢不进 pytest，用 `python tools/demo_case.py`（约 2 分钟）。
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("swmm") is None or importlib.util.find_spec("rasterio") is None, reason="没有 swmm-toolkit / rasterio")


def test_demo_builds_and_runs_1d(tmp_path, monkeypatch):
    work = tmp_path / "demo"
    if any(ord(c) > 127 for c in str(work)):
        pytest.skip("临时目录有中文，SWMM 不认")
    monkeypatch.setenv("SWBRAIN_DEMO_WORK", str(work))
    import demo_case as D
    D._proj_env()
    b = D.build(D.work_dir())
    assert abs(b["rain_excess_mm"] - 23.5) < 0.3                       # 61 mm / 2 h、CN 82：与 C1 全市一维同一口径
    for s in ("L1", "L2P"):
        meta = json.loads((work / "c2d" / f"pilot_DEMO_{s}.json").read_text(encoding="utf-8"))
        assert set(meta) >= {"nodes", "domain_polygon", "R_served_m", "rain_t_h", "rain_excess_mm", "ponds"}   # couple2way_v2 要的键
    assert (work / "c2d" / "dem_DEMO.tif").exists() and (work / "c2d" / "couple2way_v2.py").exists()
    from swmm.toolkit import solver
    flood = {}
    for s in ("L1", "L2P"):
        rpt = work / f"DEMO_{s}_1d.rpt"
        solver.swmm_run(str(work / f"DEMO_{s}.inp"), str(rpt), str(work / f"DEMO_{s}_1d.out"))
        t = rpt.read_text(encoding="utf-8", errors="replace")
        assert "ERROR" not in t
        ce = float(re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", t)[-1])
        assert abs(ce) < 2.0, (s, ce)
        flood[s] = float(re.search(r"Flooding Loss \.+\s+[\d.]+\s+([\d.]+)", t).group(1)) * 1000
    assert flood["L1"] > 1000 and flood["L2P"] < flood["L1"]            # 合成管网故意偏小要冒水；泵进塘后少冒
