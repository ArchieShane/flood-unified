"""DTM 子汇水区：D8 流向、流域标记、面积与径流系数加权（合成地形，不需要 DTM 与数据库）。"""
import numpy as np
import pandas as pd
from swbrain import catchments as cm, terrain as tr


def test_d8_on_tilted_plane_flows_downhill():
    rr, cc = np.mgrid[:9, :9]
    dem = 10.0 - 0.1 * cc            # 向东（列增）倾斜
    down = cm.d8_downstream(dem)
    d = down.reshape(9, 9)
    assert d[4, 4] == 4 * 9 + 5      # 正东邻格
    assert d[4, 8] == -1             # 东边界是出口
    assert (d[1:-1, 1:-1] >= 0).all()


def test_label_basins_splits_between_two_inlets():
    # 中间一道脊（列 10），两边各向自己一侧的进水口倾斜
    H, W = 11, 21
    rr, cc = np.mgrid[:H, :W]
    dem = 10.0 + 0.05 * np.abs(cc - 10) * -1 + 0.3   # 脊在中间：离脊越远越低
    dem = 12.0 - 0.1 * np.abs(cc - 10)
    # 每一侧再让各行往中排（行 5）汇：加一个南北向的浅槽
    dem = dem + 0.01 * np.abs(rr - 5)
    filled = tr.priority_flood(dem, eps=1e-4)
    down = cm.d8_downstream(filled)
    seeds = np.array([5 * W + 2, 5 * W + 18])     # 左右两侧各一个进水口
    lab = cm.label_basins(down, seeds, np.array([0, 1]), H * W).reshape(H, W)
    assert lab[5, 2] == 0 and lab[5, 18] == 1
    # 进水口上游（靠脊一侧）的格归它；进水口下游（更靠外侧）的格流向边界出口，不归任何进水口
    assert lab[5, 6] == 0 and lab[5, 14] == 1
    assert lab[5, 0] == -1 and lab[5, 20] == -1


def test_tile_catchments_area_and_runoff_c():
    H, W, res = 20, 20, 10.0
    rr, cc = np.mgrid[:H, :W]
    dem = 20.0 - 0.1 * cc + 0.05 * np.abs(rr - 10)   # 向东倾斜、明显向中排汇（斜向下降比正东更陡）
    x0, y1 = 1000.0, 2000.0                           # 左上角
    # 一个进水口放在行 10 列 15 的格心
    inl = pd.DataFrame({"id": [7], "x": [x0 + 15 * res + 5], "y": [y1 - 10 * res - 5]})
    codes = np.zeros((H, W), dtype=np.uint8); codes[:, :10] = 2   # 西半边是路面 0.90，东半边是「空地」0.30
    P = cm.params({})
    out = cm.tile_catchments(dem, res, inl, x0, y1, (x0, y1 - H * res, x0 + W * res, y1), codes, P)
    r = out.iloc[0]
    assert r["vertex_id"] == 7 and r["n_cells"] > 30 and abs(r["area_m2"] - r["n_cells"] * res * res) < 1e-6
    assert 0.30 < r["c"] < 0.90 and not r["shared_cell"]
    # 同格再放一个进水口：第二个不重复计面积
    inl2 = pd.concat([inl, pd.DataFrame({"id": [8], "x": [inl.x[0] + 1], "y": [inl.y[0] - 1]})], ignore_index=True)
    out2 = cm.tile_catchments(dem, res, inl2, x0, y1, (x0, y1 - H * res, x0 + W * res, y1), codes, P)
    a7, a8 = out2.loc[out2.vertex_id == 7, "area_m2"].iat[0], out2.loc[out2.vertex_id == 8, "area_m2"].iat[0]
    assert out2.loc[out2.vertex_id == 8, "shared_cell"].iat[0] and abs(a7 - a8) < 1e-6 and abs(a7 + a8 - r["area_m2"]) < 1e-6   # 同格均分
    # 面积上限：超过 max_area_m2 的截到上限并打标
    P2 = dict(P); P2["max_area_m2"] = 1000.0
    out3 = cm.tile_catchments(dem, res, inl, x0, y1, (x0, y1 - H * res, x0 + W * res, y1), codes, P2)
    assert out3.iloc[0]["area_m2"] == 1000.0 and bool(out3.iloc[0]["capped"])


def test_plot_self_drain_excludes_plot_and_building_cells():
    H, W, res = 20, 20, 10.0
    rr, cc = np.mgrid[:H, :W]
    dem = 20.0 - 0.1 * cc + 0.05 * np.abs(rr - 10)
    x0, y1 = 0.0, H * res
    inl = pd.DataFrame({"id": [1], "x": [15 * res + 5], "y": [y1 - 10 * res - 5]})
    codes = np.zeros((H, W), dtype=np.uint8); codes[:, :8] = 5; codes[:, 8:10] = 1   # 西边是地块与建筑，东边是空地
    P = cm.params({})
    on = cm.tile_catchments(dem, res, inl, x0, y1, (0.0, 0.0, W * res, y1), codes, P).iloc[0]
    P2 = dict(P); P2["plot_self_drain"] = False
    off = cm.tile_catchments(dem, res, inl, x0, y1, (0.0, 0.0, W * res, y1), codes, P2).iloc[0]
    assert off["area_m2"] > on["area_m2"] > 0 and abs(on["area_all_m2"] - off["area_m2"]) < 1e-6
    assert abs(on["c"] - 0.30) < 1e-9                        # 只剩空地格


def test_flow_length_limit_and_step_lengths():
    H, W, res = 5, 41, 10.0
    rr, cc = np.mgrid[:H, :W]
    dem = 20.0 - 0.1 * cc + 0.05 * np.abs(rr - 2)          # 长条向东倾斜，汇到中排
    x0, y1 = 0.0, H * res
    inl = pd.DataFrame({"id": [1], "x": [38 * res + 5], "y": [y1 - 2 * res - 5]})   # 进水口在东端
    P = cm.params({}); P["max_flow_len_m"] = 1e9
    full = cm.tile_catchments(dem, res, inl, x0, y1, (0.0, 0.0, W * res, y1), None, P).iloc[0]
    P2 = dict(P); P2["max_flow_len_m"] = 100.0            # 只计 100 m 以内
    near = cm.tile_catchments(dem, res, inl, x0, y1, (0.0, 0.0, W * res, y1), None, P2).iloc[0]
    assert full["n_cells"] > near["n_cells"] >= 10
    down = cm.d8_downstream(dem); sl = cm.step_lengths(down, (H, W), res)
    assert abs(sl[2 * W + 20] - res) < 1e-9 and sl[down < 0].sum() == 0


def test_params_merge_runoff_c():
    P = cm.params({"terrain": {"catchment": {"res_m": 20, "runoff_c": {"open": 0.4}}}})
    assert P["res_m"] == 20 and P["runoff_c"]["open"] == 0.4 and P["runoff_c"]["building"] == 0.85
