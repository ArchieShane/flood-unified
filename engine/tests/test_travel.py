"""路网行程图：同一对端点的平行路段取最小代价（不是相加），单向边只按自己的方向连。

2026-09-23 评审发现：`coo_matrix` 对重复的 (row, col) 是求和，
实测 41.4 万条路网边里有 7,216 条与另一条边共用同一对端点，这些节点对之间的行程时间被高估约一倍。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from swbrain.travel import RoadNetwork


def _net(edges):
    """edges: [(source, target, cost_s, rcost_s), …]；顶点等距摆在一条纬线上。"""
    e = pd.DataFrame(edges, columns=["source", "target", "cost_s", "rcost_s"])
    ids = sorted(set(e["source"]) | set(e["target"]))
    lon = {v: 54.0 + i * 0.01 for i, v in enumerate(ids)}
    e["mid_lon"] = [(lon[a] + lon[b]) / 2 for a, b in zip(e["source"], e["target"])]
    e["mid_lat"] = 24.0
    v = pd.DataFrame({"vid": ids, "lon": [lon[i] for i in ids], "lat": [24.0] * len(ids)})
    return RoadNetwork(e, v)


def _m(net):
    return net.matrix(net.e["cost_s"].values.astype(float), net.e["rcost_s"].values.astype(float))


def test_parallel_edges_take_min_not_sum():
    """1 号点到 2 号点有两条路，300 s 和 540 s：图上的权重必须是 300，不能是 840。"""
    net = _net([(1, 2, 300.0, 300.0), (1, 2, 540.0, 540.0), (2, 3, 100.0, 100.0)])
    g = _m(net)
    i1, i2, i3 = (int(net.vid_to_ix[k]) for k in (1, 2, 3))
    assert abs(g[i1, i2] - 300.0) < 1e-6, f"平行路段被相加了：{g[i1, i2]}"
    assert abs(g[i2, i1] - 300.0) < 1e-6
    assert abs(g[i2, i3] - 100.0) < 1e-6


def test_three_parallel_edges():
    net = _net([(1, 2, 900.0, 900.0), (1, 2, 120.0, 120.0), (1, 2, 450.0, 450.0)])
    g = _m(net)
    i1, i2 = int(net.vid_to_ix[1]), int(net.vid_to_ix[2])
    assert abs(g[i1, i2] - 120.0) < 1e-6


def test_one_way_edge_keeps_direction():
    """单向：正向 200 s、反向不可通，反向权重不得凭空出现。"""
    net = _net([(1, 2, 200.0, np.inf)])
    g = _m(net)
    i1, i2 = int(net.vid_to_ix[1]), int(net.vid_to_ix[2])
    assert abs(g[i1, i2] - 200.0) < 1e-6
    assert g[i2, i1] == 0.0                                            # 稀疏矩阵里没有这条边


def test_opposite_directions_do_not_merge():
    """两条方向相反的单向边共用端点：各走各的，不能被当成平行边合并掉。"""
    net = _net([(1, 2, 300.0, np.inf), (2, 1, 700.0, np.inf)])
    g = _m(net)
    i1, i2 = int(net.vid_to_ix[1]), int(net.vid_to_ix[2])
    assert abs(g[i1, i2] - 300.0) < 1e-6 and abs(g[i2, i1] - 700.0) < 1e-6


def test_travel_minutes_uses_shorter_parallel_road():
    """端到端：行程分钟数按较快的那条路算。"""
    net = _net([(1, 2, 600.0, 600.0), (1, 2, 120.0, 120.0)])
    lon = net.v["lon"].values
    a = np.array([[float(lon[int(net.vid_to_ix[1])]), 24.0]])
    b = np.array([[float(lon[int(net.vid_to_ix[2])]), 24.0]])
    m = net.travel_minutes(a, b)
    assert np.isfinite(m[0, 0]) and m[0, 0] <= 120.0 / 60.0 + 1e-6, f"走了慢的那条：{m[0, 0]} min"


# ---- 积水挡路 ----
# 2026-09-24：pts0["geom"] 存的是 Point（零面积），而 flooded_edge_mask 用 `within` 判定，
# 点永远包不住路网边的中点。实测 374 个积水点全有几何体，却断开 0 段、减速 0 段，
# 重型与轻型的行程矩阵一模一样——「积水路段加罚/断开」这套从来没生效过。

def _cfg_flood(**over):
    c = {"dispatch": {"impassable_depth_m": {"heavy": 0.45, "light": 0.3}, "slow_factor": 3.0,
                      "flood_extent_from": "area", "flood_extent_min_r_m": 15}}
    c["dispatch"].update(over)
    return c


def _pts(rows):
    from shapely.geometry import Point
    df = pd.DataFrame(rows)
    df["geom"] = [Point(float(r["lon"]), float(r["lat"])) for _, r in df.iterrows()]
    return df


def test_flood_extent_turns_points_into_real_areas():
    from swbrain.dispatch import flood_extent
    pts = _pts([{"lon": 54.5, "lat": 24.4, "area_m2": 6762.0, "max_depth_m": 0.6},
                {"lon": 54.6, "lat": 24.5, "area_m2": 100.0, "max_depth_m": 0.2}])
    polys, depths = flood_extent(pts, _cfg_flood())
    assert len(polys) == 2 and depths == [0.6, 0.2]
    assert all(p.geom_type == "Polygon" and p.area > 0 for p in polys), "还是零面积的点"
    a0 = polys[0].area * 1.12e10                                     # 度² → m²
    assert 0.8 * 6762 < a0 < 1.2 * 6762, f"等效圆面积对不上源数据：{a0:.0f} vs 6762"
    b = polys[0].bounds                                              # 应是地面上的正圆，不是东西向拉长的椭圆
    w = (b[2] - b[0]) * 111000 * np.cos(np.radians(24.4)); h = (b[3] - b[1]) * 111000
    assert abs(w - h) / h < 0.05, f"不是正圆：{w:.0f} × {h:.0f} m"
    b1 = polys[1].bounds                                             # 面积极小的点按最小半径兜底
    assert (b1[3] - b1[1]) * 111000 / 2 >= 14.0


def test_flood_extent_keeps_polygons_from_simulated_points():
    """没有世界模型输出时 run 用 simulate.py 的模拟积水区，几何体是面不是点；原来取 g.x 在别人机器上崩（2026-10-01）。"""
    from shapely.geometry import shape
    from swbrain import simulate
    from swbrain.dispatch import flood_extent, points_frame
    fp = {"points": [{"fp_id": "FP-T001", "geometry": simulate._circle(54.37, 24.45, 40.0), "lon": 54.37, "lat": 24.45, "area_m2": 5027, "max_depth_m": 0.5},
                     {"fp_id": "FP-C002", "geometry": simulate._circle(54.40, 24.47, 25.0), "lon": 54.40, "lat": 24.47, "area_m2": 1963, "max_depth_m": 0.3}]}
    pts = points_frame(fp)
    assert all(g is None or g.geom_type == "Polygon" for g in pts["geom"]), "模拟积水区应是面（本测试要覆盖的就是这条路）"
    polys, depths = flood_extent(pts, _cfg_flood())
    assert len(polys) == len(pts) and all(p.geom_type == "Polygon" and p.area > 0 for p in polys)
    assert polys[0].equals(shape(fp["points"][0]["geometry"]))      # 面直接用，不重算


def test_flood_extent_can_be_switched_off():
    from swbrain.dispatch import flood_extent
    pts = _pts([{"lon": 54.5, "lat": 24.4, "area_m2": 6762.0, "max_depth_m": 0.6}])
    polys, _ = flood_extent(pts, _cfg_flood(flood_extent_from="point"))
    assert polys[0].geom_type == "Point" and polys[0].area == 0


def test_flooded_roads_are_actually_blocked_and_slowed():
    """端到端：积水面盖住的路段要么断开、要么代价 ×3，而且重型与轻型的判定不同。"""
    from swbrain.dispatch import flood_extent
    net = _net([(1, 2, 300.0, 300.0), (2, 3, 300.0, 300.0), (3, 4, 300.0, 300.0)])
    lon = net.v["lon"].values
    mid_lon = float((lon[int(net.vid_to_ix[2])] + lon[int(net.vid_to_ix[3])]) / 2)   # 2–3 段的中点
    # 积水中心要偏开中点 25 m：正好压在中点上时，零面积的 Point 之间 `within` 也成立，
    # 判据就会两边都过（这条夹具自己踩过一次）。面积 30,000 m² → 等效半径 98 m，面仍能盖住。
    off_lat = 24.0 + 25.0 / 111000.0
    deep = _pts([{"lon": mid_lon, "lat": off_lat, "area_m2": 30000.0, "max_depth_m": 0.6}])   # 深 0.6 m
    shallow = _pts([{"lon": mid_lon, "lat": off_lat, "area_m2": 30000.0, "max_depth_m": 0.35}])  # 0.35 m

    cost = net.e["cost_s"].values.astype(float); rcost = net.e["rcost_s"].values.astype(float)
    base = net.travel_minutes(np.array([[lon[int(net.vid_to_ix[1])], 24.0]]), np.array([[lon[int(net.vid_to_ix[4])], 24.0]]))
    p_deep, d_deep = flood_extent(deep, _cfg_flood())
    blocked = net.travel_minutes(np.array([[lon[int(net.vid_to_ix[1])], 24.0]]), np.array([[lon[int(net.vid_to_ix[4])], 24.0]]),
                                 p_deep, d_deep, impassable_depth_m=0.45, slow_factor=3.0)
    assert np.isfinite(base[0, 0]) and not np.isfinite(blocked[0, 0]), "0.6 m 深的积水没把路断开"

    p_sh, d_sh = flood_extent(shallow, _cfg_flood())
    heavy = net.travel_minutes(np.array([[lon[int(net.vid_to_ix[1])], 24.0]]), np.array([[lon[int(net.vid_to_ix[4])], 24.0]]),
                               p_sh, d_sh, impassable_depth_m=0.45, slow_factor=3.0)      # 重型能涉 0.45 m → 只减速
    light = net.travel_minutes(np.array([[lon[int(net.vid_to_ix[1])], 24.0]]), np.array([[lon[int(net.vid_to_ix[4])], 24.0]]),
                               p_sh, d_sh, impassable_depth_m=0.30, slow_factor=3.0)      # 轻型涉不过 0.35 m → 断开
    assert np.isfinite(heavy[0, 0]) and heavy[0, 0] > base[0, 0], "0.35 m 的积水没让重型变慢"
    assert not np.isfinite(light[0, 0]), "0.35 m 的积水没把轻型拦住"
