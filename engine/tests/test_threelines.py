"""三道防线的三个现状对比：客户领导要看的那张表。

    现状①  防线1   只有既有管网
    现状②  防线2   加上已规划的塘（Parsons 方案，尚未建成）
    现状③  防线2+  加上我们在电脑里新挖的塘

守两个真踩过的坑：

1. 节点坐标只抽了基准那一份，防线2 多出来的集水井 W-*、泵出口 *_Junction 查不到坐标，
   溢流被**静默丢掉** —— Shaliela 因此漏掉 35,692 m³。
2. 排除「构筑物溢流」时把集水井也排掉了。集水井是路边一个小竖井，它冒水就是路上淹了；
   一排，Parsons 方案的成绩从 −31.0% 虚增到 −49.6%，凭空多 19 个百分点。
   只有塘（露天大水面）的溢流才不算街道积水。
"""
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from swbrain import threelines as TL

RPT = """
  Flow Routing Continuity        hectare-m      10^6 ltr
  External Inflow ..........        37.582       375.823
  External Outflow .........        25.623       256.233
  Flooding Loss ............         3.569        35.695
  Final Stored Volume ......        13.262       132.622
  Continuity Error (%) .....       {err}


  Node Flooding Summary
  *********************

  --------------------------------------------------------------------------
                                 Maximum   Time of Max       Flood    Ponded
                        Hours       Rate    Occurrence      Volume     Depth
  Node                 Flooded       LPS   days hr:min    10^6 ltr    Meters
  --------------------------------------------------------------------------
{nodes}


  Storage Volume Summary
  **********************

  ------------------------------------------------------------------------------------------------
                         Average    Avg   Evap  Exfil     Maximum    Max    Time of Max    Maximum
                          Volume   Pcnt   Pcnt   Pcnt      Volume   Pcnt     Occurrence    Outflow
  Storage Unit           1000 m3   Full   Loss   Loss     1000 m3   Full    days hr:min        LPS
  ------------------------------------------------------------------------------------------------
{store}


  *************
"""


def _rpt(tmp, name, nodes, store="", err="0.42"):
    f = tmp / name
    f.write_text(RPT.format(nodes="\n".join(nodes), store="\n".join(store), err=err),
                 encoding="utf-8")
    return f


def _nodes(tmp, name, rows):
    import pandas as pd
    pd.DataFrame(rows, columns=["node", "lon", "lat"]).to_parquet(tmp / name, index=False)


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "tl"
    d.mkdir()
    # 防线1：两个街道节点
    _rpt(d, "X.L1.rpt",
         ["  MH_1                    9.35    126.07      0  12:14       0.100     0.20",
          "  MH_2                    4.00     18.73      0  12:12       0.050     0.10"])
    _nodes(d, "X.L1.nodes.parquet",
           [("MH_1", 54.50, 24.40), ("MH_2", 54.51, 24.41)])
    # 防线2：街道节点少了，但多出集水井 W-3 与塘 PO-2 在冒
    _rpt(d, "X.L2.rpt",
         ["  MH_1                    2.00    126.07      0  12:14       0.040     0.10",
          "  W-3                     6.00     90.00      0  12:20       0.030     0.30",
          "  PO-2                    1.00     10.00      0  13:00       0.010     0.05"],
         ["  PO-2                    42.101   44.3    0.0    0.0      65.536   68.9       1  08:23"],
         err="-12.86")
    _nodes(d, "X.L2.nodes.parquet",
           [("MH_1", 54.50, 24.40), ("W-3", 54.52, 24.42), ("PO-2", 54.53, 24.43)])
    return {"three_lines": {"dir": str(d), "scenario": "10cc", "top_points": 400}}


def test_catchments_lists_what_has_been_run(cfg):
    cs = TL.catchments(cfg)
    assert len(cs) == 1
    assert cs[0]["catchment"] == "X"
    assert cs[0]["states"] == ["L1", "L2"], "防线2+ 的两种形式还没跑，不该报成有"
    assert cs[0]["complete"] is False


def test_wetwell_spill_counts_as_street_flooding(cfg):
    """集水井是路边小竖井，冒水就是路上淹了——排掉它会给方案虚增成绩。"""
    s = TL.state(cfg, "X", "L2")
    k = s["kpi"]
    assert k["street_flood_m3"] == pytest.approx(70.0), \
        f"MH_1 40 + W-3 30 = 70 m³ 才对，得到 {k['street_flood_m3']}"
    assert k["pond_spill_m3"] == pytest.approx(10.0), "只有塘 PO-2 的 10 m³ 该被排除"


def test_per_state_coordinates_so_nothing_is_dropped(cfg):
    """防线2 多出来的节点要用它自己那份坐标，不能拿基准的去查。"""
    s = TL.state(cfg, "X", "L2")
    got = {p["node"] for p in s["points"]}
    assert "W-3" in got, "W-3 只在防线2 的模型里，用基准坐标就查不到、被静默丢掉"
    assert all(p["lon"] is not None for p in s["points"]), "有点没拿到坐标"
    assert s["kpi"]["no_coord_m3"] == 0.0


def test_pond_is_not_listed_as_a_flood_point(cfg):
    s = TL.state(cfg, "X", "L2")
    assert "PO-2" not in {p["node"] for p in s["points"]}
    assert s["kpi"]["n_ponds"] == 1
    assert s["ponds"][0]["max_m3"] == pytest.approx(65536.0)
    assert s["ponds"][0]["origin"] == "client_model"


def test_compare_gives_deltas_against_line1(cfg):
    r = TL.compare(cfg, "X")
    st = {x["key"]: x for x in r["states"]}
    assert st["L1"]["available"] and st["L2"]["available"]
    for k in ("L2B", "L2L"):
        assert st[k]["available"] is False, "没跑的那一态要标出来，不能假装有"
    d = st["L2"]["delta_vs_L1"]
    assert d["street_flood_m3"] == pytest.approx(70.0 - 150.0)
    assert d["street_flood_pct"] == pytest.approx(-53.3, abs=0.1)


def test_continuity_error_is_surfaced(cfg):
    """连续性误差要带出来：防线2 那一态 −12.86%，超过 10% 的通行可接受线。"""
    assert TL.state(cfg, "X", "L1")["kpi"]["continuity_pct"] == pytest.approx(0.42)
    assert TL.state(cfg, "X", "L2")["kpi"]["continuity_pct"] == pytest.approx(-12.86)


def test_caveat_names_the_reconstructed_baseline(cfg):
    """现状① 是我们剥出来的，不是客户的既有管网模型——这句不能丢。"""
    c = TL.compare(cfg, "X")["caveat"]
    assert "剥离" in c and "existing" in c


def test_missing_state_does_not_crash(cfg):
    s = TL.state(cfg, "X", "L2B")
    assert s["available"] is False and "还没跑" in s["note"]


def test_route_is_registered_and_params_match():
    from swbrain import routes
    r = next((x for x in routes.ROUTES if x.path == "/api/three_lines"), None)
    assert r is not None, "接口没登记"
    assert "catchment" in r.params

# ---------------------------------------------------------------- 三区一起看
def test_compare_all_only_totals_complete_catchments(cfg, tmp_path):
    """合计只能算三态都跑齐的区——拿不完整的数凑总量，等于虚报整体效果。"""
    d = pathlib.Path(cfg["three_lines"]["dir"])
    # 再造一个只跑了防线1 的区
    _rpt(d, "Y.L1.rpt",
         ["  MH_9                    3.00     50.00      0  12:00       0.500     0.20"])
    _nodes(d, "Y.L1.nodes.parquet", [("MH_9", 54.6, 24.5)])
    A = TL.compare_all(cfg)
    assert len(A["catchments"]) == 2, "两个区都要列出来"
    w = A["total"]
    assert w["catchments"] == 0, "X 缺防线2+ 的两种形式、Y 只有防线1，都不该进合计"
    assert w["partial"] == ["X", "Y"]
    assert w["L1"] == 0.0


def _add_l2plus(d):
    """补上防线2+ 的两种构筑物形式。"""
    _rpt(d, "X.L2B.rpt",
         ["  MH_1                    1.00     20.00      0  12:14       0.020     0.05"],
         ["  NP1                     10.000   50.0    0.0    0.0      20.000   80.0       0  13:00"])
    _nodes(d, "X.L2B.nodes.parquet",
           [("MH_1", 54.50, 24.40), ("NP1", 54.505, 24.405)])
    _rpt(d, "X.L2L.rpt",
         ["  MH_1                    2.00     40.00      0  12:14       0.060     0.12"],
         ["  NP1                      4.000   50.0    0.0    0.0       8.000   90.0       0  13:00"])
    _nodes(d, "X.L2L.nodes.parquet",
           [("MH_1", 54.50, 24.40), ("NP1", 54.60, 24.50)])


def test_compare_all_totals_when_complete(cfg):
    """四态齐了才进合计，且合计 = 各区之和。"""
    d = pathlib.Path(cfg["three_lines"]["dir"])
    _add_l2plus(d)
    A = TL.compare_all(cfg)
    w = A["total"]
    assert w["catchments"] == 1 and w["partial"] == []
    assert w["L1"] == pytest.approx(150.0)
    assert w["L2B"] == pytest.approx(20.0)
    assert w["L2B_pct"] == pytest.approx(-86.7, abs=0.1)
    assert w["L2L"] == pytest.approx(60.0), "地面开敞塘那一态也要单独汇总"
    assert w["L2L_pct"] == pytest.approx(-60.0, abs=0.1)
    assert w["pond_L2P"] == pytest.approx(20000.0), "塘的用量按箱涵那一态汇总"


def test_two_forms_are_separate_states(cfg):
    """地下箱涵与地面开敞塘是**两种构筑物**，不能混成一个「防线2+」。

    它们的地面处理、进水方式、造价量级都不同：箱涵埋在管底以下、重力进水、地面不变；
    开敞塘塘顶齐地面、多数要泵提升、二维地形必须按轮廓挖。混在一起报，
    等于把「能不能建」和「建了多好」搅成一个数。
    """
    d = pathlib.Path(cfg["three_lines"]["dir"])
    _add_l2plus(d)
    r = TL.compare(cfg, "X")
    ks = [x["key"] for x in r["states"]]
    assert ks == ["L1", "L2", "L2B", "L2L"], f"四态要齐且有序，得到 {ks}"
    st = {x["key"]: x for x in r["states"]}
    assert st["L2B"]["kpi"]["street_flood_m3"] != st["L2L"]["kpi"]["street_flood_m3"]
    for k in ("L2B", "L2L"):
        assert "箱涵" in st[k]["desc"] or "开敞塘" in st[k]["desc"]


def test_all_route_mode_is_declared():
    from swbrain import routes
    r = next(x for x in routes.ROUTES if x.path == "/api/three_lines")
    assert "all" in r.desc, "catchment=all 这个用法要写进接口说明，否则没人知道"
