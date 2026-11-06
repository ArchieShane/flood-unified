# -*- coding: utf-8 -*-
"""tools/inlet_hyd.py：WA-726 附录 A3 洼地格栅进水口公式"""
import sys
import pathlib

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import inlet_hyd as IH  # noqa: E402

P, A = 3 * 0.635, 0.635 * 0.635 * 0.5          # 635 × 635 格栅，三边周长（不计路缘侧），开孔率 50 %


def test_zero_and_negative_depth():
    assert IH.grate_sump_q(0.0, P, A) == 0.0
    assert IH.grate_sump_q(-0.2, P, A) == 0.0


def test_matches_hec22_si_coefficients():
    # 式 A3-22 / A3-23 换算后就是 HEC-22 公制：堰 1.66 P d^1.5、孔口 0.67 A √(2 g d)
    d = 0.05
    assert np.isclose(IH.grate_sump_q(d, P, A, clog=1.0), 1.656 * P * d ** 1.5, rtol=0.01)       # 浅水：堰流控制
    d = 0.40
    assert np.isclose(IH.grate_sump_q(d, P, A, clog=1.0), 0.67 * A * np.sqrt(2 * 9.806 * d), rtol=0.01)   # 深水：孔口控制


def test_clog_factor_and_vector():
    d = np.array([0.0, 0.1, 0.3])
    q = IH.grate_sump_q(d, P, A)
    assert np.allclose(q, 0.5 * IH.grate_sump_q(d, P, A, clog=1.0))
    assert np.all(np.diff(q) > 0)
