"""情景目录：重现期、气候变化系数、实测事件按片区/区域缩放。"""
import numpy as np
import pytest
from swbrain import scenarios as sc

CFG = {"idf": {"source": "T3-6", "return_periods": {"5": {"i15_mmh": 51.0, "p24_mm": 44.9}, "10": {"i15_mmh": 57.7, "p24_mm": 51.1}},
               "equation": {"5": {"alpha": 18.685, "beta": 0.725}, "10": {"alpha": 21.183, "beta": 0.723}}},
       "scenarios": {"cc": {"10cc": {"base_rp": 10, "factor": 1.2, "label": "10 年 + 20% CC", "label_en": "10-yr + 20% CC"}},
                     "events": {"2024-04": {"base_rp": 10, "p24_ref_mm": 51.12, "p24_default_mm": 94.0, "label": "2024-04 实测",
                                            "p24_by_zone": {"Al Bahyah": 159.58, "Khalifa City": 96.23}, "p24_by_area": {"Al Shahama": 159.58, "Zayed": 96.23}}}}}


def test_catalog_order_and_keys():
    ks = sc.keys(CFG)
    assert ks == ["5", "10", "10cc", "2024-04"]
    assert sc.get(CFG, 10)["kind"] == "rp" and sc.get(CFG, "10")["label_en"] == "10-yr"


def test_cc_scales_everything_by_factor():
    s = sc.get(CFG, "10cc")
    assert s["base_rp"] == 10 and abs(s["i15_mmh"] - 57.7 * 1.2) < 0.06 and abs(s["p24_mm"] - 51.1 * 1.2) < 0.01
    assert abs(sc.intensity(CFG, "10cc", 15) - sc.intensity(CFG, "10", 15) * 1.2) < 1e-9
    assert sc.factor(s, zone="anything") == 1.2


def test_event_factor_by_area_zone_default():
    s = sc.get(CFG, "2024-04")
    assert abs(sc.factor(s, area="Al Shahama") - 159.58 / 51.12) < 1e-9
    assert abs(sc.factor(s, zone="Khalifa City") - 96.23 / 51.12) < 1e-9
    assert abs(sc.factor(s, zone="Nowhere") - 94.0 / 51.12) < 1e-9          # 缺省
    assert abs(sc.p24(CFG, s, area="Al Shahama") - 159.58) < 1e-9 and abs(sc.p24(CFG, s) - 94.0) < 1e-9
    f = sc.zone_factors(CFG, s, ["Al Bahyah", "Khalifa City", "X"])
    assert np.allclose(f, [159.58 / 51.12, 96.23 / 51.12, 94.0 / 51.12])
    assert sc.intensity(CFG, s, 15, area="Al Shahama") > sc.intensity(CFG, "10", 15) * 3


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        sc.get(CFG, "999")
