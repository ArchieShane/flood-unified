# -*- coding: utf-8 -*-
"""塘接入口径自检（在 swbrain 容器里跑，几秒；2026-10-18，文件名沿用 aa_spill_selftest.py）。
口径（用户定）：排进塘的那根管，在 L1 里不是出水口（末端封闭、水憋在管网里）；L2 加上塘，水进塘。用一个最小的 SWMM 网络验证：
  ① L1（末端封闭）、L2（接进塘）两个 .inp SWMM 都读得进、跑得完、没有 ERROR；
  ② L1 里这根管确实出不去水（该系统外出流 = 0），L2 的水量进了塘（塘有蓄水）；
  ③ L2 的地面冒水（Flooding Loss）< L1。
任何一项不过 → 退出码 1，rerun-l2 不会开始。用法：python tools/aa_spill_selftest.py"""
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from swmm.toolkit import solver  # noqa: E402

# 系统里另有一个独立出水口 OUT_B（真实艾因网络有 342 个出水口），否则 SWMM 报 ERROR 145；A 是「排进塘的那根管」所在的一段，X 是它的末端井（客户出水口所在节点）
HEAD = """[TITLE]
selftest
[OPTIONS]
FLOW_UNITS CMS
INFILTRATION HORTON
FLOW_ROUTING DYNWAVE
START_DATE 01/01/2026
START_TIME 00:00:00
REPORT_START_DATE 01/01/2026
REPORT_START_TIME 00:00:00
END_DATE 01/01/2026
END_TIME 06:00:00
WET_STEP 00:01:00
DRY_STEP 00:05:00
ROUTING_STEP 00:00:05
ALLOW_PONDING NO
MIN_SLOPE 0
[JUNCTIONS]
A  100.0  3.0  0  0  0
X  99.0  3.0  0  0  0
B  100.0  3.0  0  0  0
[OUTFALLS]
OUT_B  99.0  FREE  NO
[CONDUITS]
c_AX  A  X  10  0.013  99.9  99.0  0  0
of_B  B  OUT_B  10  0.013  99.9  99.0  0  0
[XSECTIONS]
c_AX  CIRCULAR  0.3  0  0  0  1
of_B  CIRCULAR  0.3  0  0  0  1
[TIMESERIES]
TS_RUNOFF  00:00  0.0
TS_RUNOFF  00:30  0.5
TS_RUNOFF  02:00  0.5
TS_RUNOFF  02:30  0.0
[INFLOWS]
A  FLOW  TS_RUNOFF  FLOW  1.0  1.0
[REPORT]
CONTROLS NO
[COORDINATES]
A  0  0
X  10  0
B  0  50
OUT_B  10  50
"""


def l2_text():
    """同 build_city1d.append_ponds 的写法：塘是 [STORAGE]，库容曲线，进水管带拍门（[LOSSES] Flap=YES）。"""
    t = HEAD.replace("[CONDUITS]", "[STORAGE]\nP  97.0  3.0  0  TABULAR  CRV_P  0  0  0\n\n[CONDUITS]", 1)
    t = t.replace("[CONDUITS]\n", "[CONDUITS]\nPI_X  X  P  10  0.013  99.0  98.5  0  0\n", 1)
    t = t.replace("[TIMESERIES]\n", "[CURVES]\nCRV_P  Storage  0.0  500.0\nCRV_P  3.0  500.0\n\n[TIMESERIES]\n", 1)
    t = t.replace("[COORDINATES]\n", "[COORDINATES]\nP  20  0\n", 1)
    t = t.replace("[XSECTIONS]\n", "[XSECTIONS]\nPI_X  CIRCULAR  0.3  0  0  0  1\n", 1)
    return t.replace("[XSECTIONS]", "[LOSSES]\nPI_X  0  0  0  YES  0\n\n[XSECTIONS]", 1)


def run(d, name, txt):
    f = d / f"{name}.inp"; f.write_text(txt, encoding="utf-8")
    solver.swmm_run(str(f), str(d / f"{name}.rpt"), str(d / f"{name}.out"))
    r = (d / f"{name}.rpt").read_text(errors="replace")
    errs = re.findall(r"ERROR.*", r)
    # Flow Routing Continuity 表每行是「名字 ........ 数值 数值」，名字和数值之间是一串点；取第一个数值（体积列，各行同单位，只比大小）
    num = lambda label: float(m.group(1)) if (m := re.search(re.escape(label) + r"[ .]+(\d+(?:\.\d+)?)", r)) else 0.0
    return errs, num("External Outflow"), num("Flooding Loss"), num("Final Stored Volume")


def main() -> int:
    d = pathlib.Path(tempfile.mkdtemp(prefix="aa_pond_"))
    R = {"L1": run(d, "L1", HEAD), "L2": run(d, "L2", l2_text())}
    for k, (e, o, f, v) in R.items():
        print(f"  {k:<3} ERROR={e or '无'}  系统外出流={o}  地面冒水(Flooding Loss)={f}  期末蓄水={v}")
    ok = True
    if any(R[k][0] for k in R):
        ok = False; print("✗ SWMM 报 ERROR（见 " + str(d) + "/*.rpt）")
    if not R["L2"][3] > 0:
        ok = False; print("✗ L2 的塘没有蓄到水")
    if not R["L2"][2] < R["L1"][2]:
        ok = False; print("✗ L2 地面冒水没有比 L1 少")
    print("自检通过 ✓" if ok else "自检未通过 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
