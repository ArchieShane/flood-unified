"""资产台账：客户 L1/L2/L3 分级里的实体资产，以及它在本体里的对象类型。

客户 2026-09-25 给的那张 Surface Water Management Levels 图列了 20 种资产，
本体原先只覆盖 3 种（管线=Reach，移动泵与罐车=PumpUnit），L2 整层是空的——
而 L2 正是第二道防线的主体。Asset 这个类型就是补这个缺口的。

不给每种资产建一个类：种类放 kind，分级放 level，谁出钱谁建放 delivery。
这样客户以后加新种类不必改本体结构。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from swbrain import assets as FA
from swbrain.ontology import schema as S


# ---------------------------------------------------------------- 覆盖客户那张图
CLIENT_PICTURE = {
    "L1": ["pipeline", "pump_station", "outfall", "inlet", "dam", "wadi", "ditch",
           "geocell", "pond"],
    "L2": ["pond", "surface_pipeline", "spillway", "pump_station", "geocell",
           "stationary_pump", "bund", "ditch", "jersey_barrier", "sandbag", "small_barrier"],
    "L3": ["tanker", "mobile_pump", "crane"],
}


def test_every_asset_in_the_client_picture_is_representable():
    """客户图上每一种资产都要能表达——要么有 kind，要么由别的对象类型承载。"""
    want = {k for v in CLIENT_PICTURE.values() for k in v}
    missing = sorted(want - set(FA.KINDS))
    assert not missing, f"客户图上有、本体表达不了的资产：{missing}"


def test_levels_and_delivery_match_the_client_framework():
    """分级与交付方要和客户的口径一字不差，否则汇报时两边说的不是一回事。"""
    assert set(FA.LEVELS) == {"L1", "L2", "L3"}
    assert {"capex", "idd", "municipality"} <= set(FA.DELIVERY), \
        "客户把 L2 按 CAPEX / IDD / 市政三条线分开列，这三个交付方必须都在"


def test_kinds_already_modelled_elsewhere_are_marked_not_duplicated():
    """管线、移动泵、罐车已经有专门的对象类型，不能在 Asset 里重复建模。"""
    assert FA.COVERED_BY == {"pipeline": "Reach", "mobile_pump": "PumpUnit",
                             "tanker": "PumpUnit"}
    apis = {o.api for o in S.OBJECT_TYPES}
    for k, api in FA.COVERED_BY.items():
        assert api in apis, f"{k} 说由 {api} 承载，但本体里没有这个类型"


def test_missing_list_excludes_what_other_types_carry():
    """缺数清单不能把「已由别的类型承载」的算进去——那会虚报缺口。"""
    s = FA.summary([])
    assert not (set(s["kinds_missing"]) & set(FA.COVERED_BY)), \
        f"缺数清单里混进了别的类型已承载的：{set(s['kinds_missing']) & set(FA.COVERED_BY)}"


# ---------------------------------------------------------------- 装载
def _write(root: Path, rel: str, obj):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_loads_client_model_ponds_as_L2_capex(tmp_path):
    _write(tmp_path, "out/dmt_model/ponds_sites.json",
           [{"site_id": "POND-X-PO-2", "name": "PO-2", "lon": 54.7, "lat": 24.6,
             "max_stor_m3": 95074.0, "area_m2": 47537.0, "model": "X", "area": "Al Shahama",
             "stor_source": "swmm_curve", "swmm_label": "PO-2"}])
    rows = FA.load(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "pond" and r["level"] == "L2" and r["delivery"] == "capex"
    assert r["capacity_m3"] == 95074.0 and r["source"] == "client_model"
    # planned 而不是 approved。Parsons 2026-09-17 的报告是给 DMT 的**建议书**：三份报告都写明
    # 既有系统里一个塘都没有，塘「proposed within available open spaces, subject to site
    # suitability」——地块还没定、泵还没采购。DMT 批没批我们不知道，标 approved 是越界。
    assert r["status"] == "planned", "顾问建议书里的塘只能记 planned，不能记 approved"


def test_loads_system_plans_as_planned(tmp_path):
    _write(tmp_path, "out/ponds_10cc.json",
           {"chosen": [{"site": {"site_id": "NAT-ADM-1", "name": "洼地", "lon": 54.5,
                                 "lat": 24.4, "area": "Abu Dhabi"},
                        "volume_m3": 18752.0, "capacity_m3h": 6277.0, "footprint_m2": 7501.0,
                        "cost_aed": 1058840.0, "points": ["ADM-1", "ADM-2"], "label": "开挖…"}]})
    rows = FA.load(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "system_plan" and r["status"] == "planned"
    assert r["intake_m3h"] == 6277.0, "进水能力必须带上——塘起不起作用由它决定，不是库容"
    assert r["serves_fp_ids"] == ["ADM-1", "ADM-2"]


def test_client_register_wins_over_derived_sources(tmp_path):
    """客户自己的台账优先于我们从模型/规划里推出来的。"""
    _write(tmp_path, "out/dmt_model/ponds_sites.json",
           [{"site_id": "P1", "name": "从模型来的", "lon": 54.7, "lat": 24.6,
             "max_stor_m3": 100.0, "model": "X"}])
    _write(tmp_path, "exchange/assets.json",
           [{"asset_id": "P1", "kind": "pond", "name": "客户台账的", "lon": 54.7,
             "lat": 24.6, "capacity_m3": 999.0, "status": "built"}])
    rows = FA.load(tmp_path)
    assert len(rows) == 1 and rows[0]["source"] == "client_register"
    assert rows[0]["capacity_m3"] == 999.0 and rows[0]["status"] == "built"


def test_register_accepts_all_client_picture_kinds(tmp_path):
    """客户台账一来，L1/L2/L3 二十来种资产要能一次收进来。"""
    want = sorted({k for v in CLIENT_PICTURE.values() for k in v})
    _write(tmp_path, "exchange/assets.json",
           [{"asset_id": f"F{i}", "kind": k, "lon": 54.5, "lat": 24.4}
            for i, k in enumerate(want)])
    rows = FA.load(tmp_path)
    assert len(rows) == len(want), f"只收进 {len(rows)} / {len(want)} 种"
    assert {r["kind"] for r in rows} == set(want)


def test_unknown_kind_is_dropped_not_silently_relabelled(tmp_path):
    _write(tmp_path, "exchange/assets.json",
           [{"asset_id": "F1", "kind": "spaceship", "lon": 54.5, "lat": 24.4}])
    assert FA.load(tmp_path) == []


def test_facility_without_coordinates_is_skipped(tmp_path):
    _write(tmp_path, "out/dmt_model/ponds_sites.json",
           [{"site_id": "P1", "name": "没坐标", "lon": None, "lat": None, "model": "X"}])
    assert FA.load(tmp_path) == []


# ---------------------------------------------------------------- 本体
def test_asset_is_declared_in_the_ontology():
    t = next((o for o in S.OBJECT_TYPES if o.api == "Asset"), None)
    assert t is not None, "本体里没有 Asset"
    assert t.pk == "asset_id" and t.loader == "load_assets"
    names = {p.name for p in t.properties}
    for need in ("level", "kind", "delivery", "status", "capacity_m3", "intake_m3h", "source"):
        assert need in names, f"Asset 缺属性 {need}"


def test_asset_links_connect_it_to_zones_and_points():
    links = {l.api: l for l in S.LINK_TYPES}
    assert links["in_zone"].src == "Asset" and links["in_zone"].dst == "Zone"
    assert links["protects"].src == "Asset" and links["protects"].dst == "FloodPoint"
    assert links["protected_by"].src == "FloodPoint" and links["protected_by"].dst == "Asset"


def test_asset_status_matches_the_proposal_state_machine():
    """「建议 → 设施」要能接上，两边的状态机不能各说各的。"""
    t = next(o for o in S.OBJECT_TYPES if o.api == "Asset")
    desc = t.prop("status").desc
    for st in FA.STATUSES:
        assert st in desc, f"状态 {st} 没写进 Asset.status 的说明"


def test_ontology_self_checks_still_pass():
    assert S.check() == []
    assert S.check_runtime() == [], "loader 名和实现对不上"

# ---------------------------------------------------------------- 管网排放终点
def test_network_terminals_become_L1_assets(tmp_path):
    """出水口 / 泵站 / 渗井从 cs.parquet 的「列」提升成对象——这是 L1 层唯一有数据的部分。"""
    import pandas as pd
    d = pd.DataFrame([
        {"terminal": 1, "terminal_unitid": "SW_OFL_1", "terminal_kind": "出水口",
         "lon": 54.75, "lat": 23.61, "zone_name": "Al Sout", "area_m2": 3700.0,
         "n_inlets": 5.0, "max_ratio_常规": 0.156, "max_ratio_极端": 0.362,
         "tide_clearance_m": 118.1, "outfall_type": "Outfall HeadWall"},
        {"terminal": 2, "terminal_unitid": "SW_PS_1", "terminal_kind": "泵站",
         "lon": 54.5, "lat": 24.4, "zone_name": "Z", "area_m2": 900.0, "n_inlets": 2.0,
         "max_ratio_常规": 0.9, "max_ratio_极端": 1.4, "tide_clearance_m": None,
         "outfall_type": None},
        {"terminal": 3, "terminal_unitid": "SW_SOK_1", "terminal_kind": "渗井",
         "lon": 54.6, "lat": 24.5, "zone_name": "Z", "area_m2": 500.0, "n_inlets": 1.0,
         "max_ratio_常规": 0.3, "max_ratio_极端": 0.5, "tide_clearance_m": None,
         "outfall_type": None},
        {"terminal": 4, "terminal_unitid": "SW_DEAD_1", "terminal_kind": "断头",
         "lon": 54.7, "lat": 24.6, "zone_name": "Z", "area_m2": 100.0, "n_inlets": 1.0,
         "max_ratio_常规": 0.1, "max_ratio_极端": 0.2, "tide_clearance_m": None,
         "outfall_type": None},
    ])
    (tmp_path / "out" / "state").mkdir(parents=True)
    d.to_parquet(tmp_path / "out" / "state" / "cs.parquet", index=False)
    rows = {r["asset_id"]: r for r in FA.load(tmp_path)}
    assert set(rows) == {"SW_OFL_1", "SW_PS_1", "SW_SOK_1"},         "断头不是资产，是管网数据的断裂，不该收进来"
    o = rows["SW_OFL_1"]
    assert o["kind"] == "outfall" and o["level"] == "L1" and o["status"] == "built"
    assert o["catchment_m2"] == 3700.0 and o["n_inlets"] == 5.0
    assert o["load_ratio"] == {"常规": 0.156, "极端": 0.362}, "各情景的负荷比要带上"
    assert rows["SW_PS_1"]["kind"] == "pump_station", "泵站是 L1 资产，本体原先一个都没有"
    assert rows["SW_SOK_1"]["kind"] == "soakaway"


def test_client_register_still_wins_over_network(tmp_path):
    """客户台账优先级最高，管网台账推出来的垫底。"""
    import pandas as pd
    (tmp_path / "out" / "state").mkdir(parents=True)
    pd.DataFrame([{"terminal": 1, "terminal_unitid": "X1", "terminal_kind": "出水口",
                   "lon": 54.5, "lat": 24.4, "zone_name": "Z", "area_m2": 1.0,
                   "n_inlets": 1.0, "max_ratio_常规": 0.1}]).to_parquet(
        tmp_path / "out" / "state" / "cs.parquet", index=False)
    _write(tmp_path, "exchange/assets.json",
           [{"asset_id": "X1", "kind": "outfall", "name": "客户台账的", "lon": 54.5,
             "lat": 24.4, "status": "verified"}])
    rows = FA.load(tmp_path)
    assert len(rows) == 1 and rows[0]["source"] == "client_register"
    assert rows[0]["status"] == "verified"


def test_discharge_link_exists():
    """「这个点的水排到哪个出水口」必须是一条本体关系，不能只在代码里算。"""
    links = {l.api: l for l in S.LINK_TYPES}
    l = links["discharges_to"]
    assert l.src == "FloodState" and l.dst == "Asset" and l.card == "one"

# ---------------------------------------------------------------- 雨水口
def _inlet_fixture(tmp_path, n_far=1):
    """两个热点、若干雨水口：近的应被收进来，远的不该。"""
    import numpy as np
    import pandas as pd
    from pyproj import Transformer
    st = tmp_path / "out" / "state"
    st.mkdir(parents=True, exist_ok=True)
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    hx, hy = tr.transform([54.5, 54.9], [24.4, 24.8])
    rows, cat = [], []
    for i in range(3):                                   # 热点 1 附近 3 个
        rows.append({"id": 100 + i, "x": hx[0] + 30 * i, "y": hy[0], "node_table": "inlet"})
        cat.append({"vertex_id": 100 + i, "area_m2": 200.0 + i, "c": 0.5})
    rows.append({"id": 200, "x": hx[1], "y": hy[1], "node_table": "catchbasin"})
    cat.append({"vertex_id": 200, "area_m2": 500.0, "c": 0.9})
    for j in range(n_far):                               # 离所有热点都很远
        rows.append({"id": 900 + j, "x": hx[0] + 50_000, "y": hy[0], "node_table": "inlet"})
        cat.append({"vertex_id": 900 + j, "area_m2": 999.0, "c": 0.3})
    pd.DataFrame(rows).to_parquet(st / "vertices.parquet", index=False)
    pd.DataFrame(cat).to_parquet(st / "inlet_catchments.parquet", index=False)
    pd.DataFrame([{"hotspot_id": "H1", "lon": 54.5, "lat": 24.4},
                  {"hotspot_id": "H2", "lon": 54.9, "lat": 24.8}]).to_parquet(
        st / "hotspots_enriched.parquet", index=False)
    return tmp_path


def test_inlets_are_scoped_to_hotspots(tmp_path):
    """全量 19.4 万个雨水口不能都装进本体——只收热点半径内的。"""
    root = _inlet_fixture(tmp_path, n_far=5)
    rows = [r for r in FA.load(root) if r["kind"] == "inlet"]
    ids = {r["asset_id"] for r in rows}
    assert ids == {"INL-100", "INL-101", "INL-102", "INL-200"},         f"远处的雨水口不该收进来：{sorted(ids)}"
    assert all(r["level"] == "L1" and r["status"] == "built" for r in rows)


def test_inlet_carries_catchment_and_runoff(tmp_path):
    """汇水面积与径流系数必须带上——「这个点周围能收多少水」全靠这两项。"""
    root = _inlet_fixture(tmp_path)
    r = next(x for x in FA.load(root) if x["asset_id"] == "INL-200")
    assert r["catchment_m2"] == 500.0 and r["runoff_c"] == 0.9
    assert r["subkind"] == "catchbasin", "inlet 与 catchbasin 要分得开"
    assert r["serves_fp_ids"] == ["H2"]


def test_inlet_radius_is_configurable(tmp_path):
    root = _inlet_fixture(tmp_path)
    wide = FA.load(root, {"assets": {"inlet_radius_m": 200_000.0}})
    narrow = FA.load(root, {"assets": {"inlet_radius_m": 10.0}})
    assert len([r for r in wide if r["kind"] == "inlet"]) >            len([r for r in narrow if r["kind"] == "inlet"]), "半径不起作用"


def test_inlet_cap_limits_the_object_count(tmp_path):
    root = _inlet_fixture(tmp_path)
    rows = FA.load(root, {"assets": {"inlet_radius_m": 200_000.0, "inlet_max": 2}})
    assert len([r for r in rows if r["kind"] == "inlet"]) == 2, "上限没兜住"


def test_assets_config_does_not_invalidate_the_context_cache():
    """调取数半径不能把上下文缓存打掉——那东西要 75 秒才能重建。"""
    from swbrain import artifactcache as ac
    assert "assets" in ac.CONFIG_SKIP
    base = {"dispatch": {"x": 1}}
    assert ac.config_fingerprint(base) ==            ac.config_fingerprint({**base, "assets": {"inlet_radius_m": 9999.0}})
    assert ac.config_fingerprint(base) != ac.config_fingerprint({**base, "dispatch": {"x": 2}})
