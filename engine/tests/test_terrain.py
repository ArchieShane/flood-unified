"""地形模块：填洼算法（合成 DEM，不需要 DTM 文件）、无标高管段的地面坡度兜底、井盖标高补缺。"""
import numpy as np
import pandas as pd
from swbrain import terrain as tr, hydraulics as hy


def test_priority_flood_bowl_volume():
    dem = np.full((21, 21), 9.8)                   # 坑外地面比坑沿低：水漫过坑沿就流走
    rr, cc = np.ogrid[:21, :21]; d = np.hypot(rr - 10, cc - 10)
    dem[(d > 3) & (d <= 4.5)] = 10.0                # 坑沿
    dem[d <= 3] = 9.0; dem[10, 10] = 8.5            # 圆形浅坑，深 1 m，中心再低 0.5 m
    filled = tr.priority_flood(dem)
    level, mask, touches = tr.pond_from_seed(dem, filled, (10, 10))
    n = int((d <= 3).sum())
    assert abs(level - 10.0) < 1e-9 and not touches
    assert mask.sum() == n and abs(((level - dem)[mask]).sum() - ((n - 1) * 1.0 + 1.5)) < 1e-9
    # 同样的坑放在与坑沿等高的大平台上：水面沿平台连到窗边，溢出点可能在窗外 → 碰边（体积作下限）
    dem_p = np.full((21, 21), 10.0); dem_p[d <= 3] = 9.0
    f_p = tr.priority_flood(dem_p)
    level_p, mask_p, touches_p = tr.pond_from_seed(dem_p, f_p, (10, 10))
    assert abs(level_p - 10.0) < 1e-9 and mask_p.sum() == n and touches_p


def test_ridge_separates_basins():
    dem = np.full((11, 21), 10.0); dem[5, 3] = 8.0; dem[5, 17] = 7.0; dem[:, 10] = 12.0   # 中间一道岭
    filled = tr.priority_flood(dem)
    level, mask, _ = tr.pond_from_seed(dem, filled, (5, 3))
    assert mask[5, 3] and not mask[5, 17] and abs(level - 10.0) < 1e-9


def test_nodata_is_outlet_and_border_truncation():
    dem = np.full((11, 11), 10.0); dem[5, 5] = 8.0; dem[5, 6] = np.nan   # 坑旁是海（nodata）：直接排走
    filled = tr.priority_flood(dem)
    level, mask, _ = tr.pond_from_seed(dem, filled, (5, 5))
    assert abs(level - 8.0) < 1e-9 and mask.sum() == 0
    dem2 = np.full((11, 11), 10.0); dem2[4:8, 0:4] = 8.0                 # 低地一直延到窗边：窗边格就是出口，本窗内不成洼地（无积水，不碰边）
    f2 = tr.priority_flood(dem2)
    level2, mask2, touches2 = tr.pond_from_seed(dem2, f2, (5, 1))
    assert not touches2 and mask2.sum() == 0 and abs(level2 - 8.0) < 1e-9


def test_edge_slope_uses_dtm_ground_slope_only_when_steeper():
    e = pd.DataFrame({"invert_up": [None] * 3 + [10.0], "invert_dn": [None] * 3 + [9.5], "len_m": [100.0] * 4, "invert_valid": [False] * 3 + [True]})
    s, f = hy.edge_slope(e, 0.0005, 0.002, ground_slope=pd.Series([0.01, 0.0001, np.nan, 0.05]))
    assert list(f) == ["DTM地面坡度", "无标高", "无标高", "ok"]
    assert abs(s[0] - 0.01) < 1e-12 and s[1] == 0.002 and s[2] == 0.002 and abs(s[3] - 0.005) < 1e-12


def test_fill_cover_levels():
    cfg = {"model": {"datum_offset_m": 100.0}, "terrain": {"cover_tol_m": 2.0}}
    il = pd.DataFrame({"vertex_id": [1, 2, 3, 4.0], "unitid": list("abcd"), "node_table": ["inlet"] * 4,
                       "cover_level": [104.0, 0.0, None, 110.0], "invert_level": [102.0] * 4, "unittype": [None] * 4})
    vertices = pd.DataFrame({"id": [1, 2, 3, 4, 5, 6], "unitid": list("abcdef"), "node_table": ["inlet"] * 5 + ["outfall"]})
    vz = pd.Series([4.1, 2.0, 3.0, 4.0, 1.5, 0.0], index=pd.Index([1, 2, 3, 4, 5, 6], name="id"))
    out, st = tr.fill_cover_levels(il, vertices, vz, cfg)
    assert st == {"台账": 2, "DTM补缺": 2, "DTM补顶点": 1, "可疑": 1, "仍缺": 0}
    g = out.set_index("unitid")
    assert g.loc["b", "cover_level"] == 102.0 and g.loc["c", "cover_level"] == 103.0 and g.loc["e", "cover_level"] == 101.5
    assert g.loc["a", "cover_source"] == "台账" and g.loc["e", "cover_source"] == "DTM顶点" and bool(g.loc["d", "cover_suspect"])
    assert list(tr.terrain_risk_low([0.6, 0.3, 0.2, 0.01, np.nan]).astype(object).fillna(-1)) == [3, 2, 1, 0, -1]
