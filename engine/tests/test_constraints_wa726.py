"""WA-726 约束登记表 → 本体校验规则（2026-10-23）"""
import re

from swbrain.ontology import constraints as K
from swbrain.ontology import schema as S


def test_register_items_have_source():
    R = K.register()
    assert len(R) >= 800
    for cid, c in R.items():
        assert c["quote"] and c["pdf_page"] and c["vol"] in (1, 2, 3), cid
        assert c["status"] in ("核对原文", "部分核对", "待人工核对"), cid


def test_every_referenced_id_exists():
    src = open(K.__file__, encoding="utf-8").read()
    ids = set(re.findall(r'"(WA726-\d-\d{3}|DEC-\d\d)"', src))
    assert ids
    missing = [i for i in ids if i not in K.register() and i not in K.decisions()]
    assert not missing, missing


def test_figure_4_1_lookup():
    assert K.clear_width_b(2.5, 400)[0] == 3.5
    assert K.clear_width_b(3.5, 600)[0] == 4.0
    assert K.clear_width_b(4.5, 1200)[0] == 6.0
    assert K.clear_width_b(7.0, 900)[0] == 6.5


def test_open_pond_in_developed_area_fails_tank_passes():
    base = {"required_volume_m3": 1000, "effective_volume_m3": 1050, "drain_mode": "pump", "in_developed_area": True}
    r = K.check("Pond", {**base, "pond_type": "open", "access_width_m": 5, "side_slope_h_per_v": 3, "fenced": True,
                         "freeboard_m": 1.0, "has_emergency_spillway": True})
    assert not r["compliant"] and any("开敞塘" in f for f in r["fails"])
    t = K.check("Pond", {**base, "pond_type": "underground"})
    assert t["compliant"]
    short = K.check("Pond", {**base, "pond_type": "underground", "effective_volume_m3": 1000})
    assert not short["compliant"]                          # 没留 5 % 积砂


def test_pump_station_rules():
    ok = {"gravity_infeasible": True, "pump_count": 2, "pump_capacity_m3s": 0.4, "design_flow_m3s": 0.5,
          "wetwell_volume_m3": 60, "surface_site_m2": 400}
    r = K.check("PumpStation", ok)
    assert r["compliant"], r["fails"]
    assert not K.check("PumpStation", {**ok, "pump_count": 1})["compliant"]
    assert not K.check("PumpStation", {**ok, "wetwell_volume_m3": 10})["compliant"]       # 0.25 × 0.4 × 600 = 60
    assert not K.check("PumpStation", {**ok, "gravity_infeasible": False})["compliant"]


def test_pressure_pipe_velocity_and_clearance():
    o = {"velocity_peak_ms": 2.2, "material": "HDPE", "clearance_to_utilities_m": 0.6, "clearance_to_building_m": 4.0,
         "depth_m": 2.0, "diameter_mm": 600}
    r = K.check("PressurePipe", o)
    assert any("2.0" in f for f in r["fails"])
    assert K.check("PressurePipe", {**o, "velocity_peak_ms": 1.8})["compliant"]


def test_schema_rules_wired():
    for api, fac in (("pond_compliance", "Pond"), ("pump_station_compliance", "PumpStation"), ("pressure_pipe_compliance", "PressurePipe"),
                     ("gravity_pipe_compliance", "GravityPipe"), ("outfall_compliance", "Outfall")):
        rule = S.RL[api]
        assert rule.applies_to == fac and "wa726" in rule.source
    v = S.RL["pond_compliance"].fn({"pond_type": "open", "in_developed_area": True}, {})
    assert v and v["compliant"] is False
