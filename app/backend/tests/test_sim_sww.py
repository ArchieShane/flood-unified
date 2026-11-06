"""多态 .sww 交付的抽取（sim_sww.py）。

总闸是**与交付自己的摘要对账**：从 .sww 抽出来的最大积水面积（交付口径：三角形水深 ≥ 0.02 m）、
峰现时刻（.sww 时刻）、三角形最大水深、末态水量，必须与 `00_说明.md` / 耦合运行记录逐位对上；
系统口径的时间轴要比 .sww 晚 start_h（二维启动时刻）。原始交付不在本机时 skip。
"""

import json
import re
from pathlib import Path

import pytest

from stormwater import sim_ponding as sp
from stormwater import sim_residual, sim_results, sim_sww
from stormwater import simulation as sim
from stormwater.i18n import EN, tr

BACKEND = Path(__file__).resolve().parents[1]
MUSSAFAH = BACKEND / "data" / "temp260928" / "Mussafah_00_61mm2h"   # 2026-09-28 五态版

needs_mussafah = pytest.mark.skipif(not MUSSAFAH.is_dir(), reason="Mussafah_00 五态交付不在本机")


@pytest.fixture(scope="module")
def l2b(tmp_path_factory):
    # 原先锁的是 L2+B；2026-09-28 用户要求从系统删掉 L2+B，改锁 L2+suggested（Parsons 之上加 NP1–NP6）
    out = tmp_path_factory.mktemp("sww") / "L2_suggested"
    return out, sim_sww.extract_state(MUSSAFAH, "L2+suggested", out)


@needs_mussafah
class TestMussafahL2B:
    def test_matches_delivery_summary(self, l2b):
        _, s = l2b
        r = s["results"]
        assert round(r["delivery_peak_wet_area_ge_0_02m2"] / 1e4, 2) == 8.49      # 00_说明.md：8.49 ha
        assert round(r["delivery_peak_wet_time_sww_h"], 3) == 1.3                  # t = 1.30 h（.sww 时刻）
        assert round(r["maximum_depth_triangle_m"], 3) == 0.555
        assert s["coupling"]["total_swmm_to_anuga_m3"] == pytest.approx(9159.580, abs=1e-3)

    def test_page_max_area_and_depth_use_delivery_basis(self, l2b):
        """页面读的最大积水面积 / 最大水深 = 交付「五态统计值」的口径：同一时刻三角形 ≥ 0.02 m 的最大帧（8.494 ha @ 1.8 h，
        降雨起算）与三角形最大水深（0.555 m）。不能是全程格并集或 15 m 格平均水深（0.33 m）。"""
        out, s = l2b
        r = s["results"]
        assert r["peak_inundated_area_m2"] / 1e4 == pytest.approx(8.494, abs=5e-4)
        assert r["peak_inundated_area_minutes"] / 60 == pytest.approx(1.8, abs=1e-3)
        assert round(r["maximum_depth_m"], 3) == 0.555
        assert r["maximum_depth_cell_m"] < r["maximum_depth_m"]
        assert r["peak_inundated_area_m2"] < r["inundated_area_ge_0_01m2"]
        ser = json.loads((out / "surface_series.json").read_text())
        assert max(ser["wet_area_m2"]) == pytest.approx(r["peak_inundated_area_m2"], abs=0.1)
        assert max(ser["volume_m3"]) == pytest.approx(5719.098, abs=0.1)     # 地面上同一时刻最多的水
        assert ser["time_minutes"][0] == pytest.approx(31.0)                  # 首帧 .sww 60 s + 二维启动 30 min

    def test_volume_closes_without_surface_rain(self, l2b):
        _, s = l2b
        assert s["model_configuration"]["surface_rainfall_inflow_m3"] < 1.0
        c = s["coupling"]
        assert s["results"]["final_surface_volume_m3"] == pytest.approx(
            c["total_swmm_to_anuga_m3"] - c["total_anuga_to_swmm_m3"], rel=1e-3)

    def test_frames_on_rain_clock(self, l2b):
        out, s = l2b
        snaps = json.loads((out / "temporal_snapshots" / "manifest.json").read_text())["snapshots"]
        assert snaps[0]["time_minutes"] == 35.0            # .sww 首个整 5 min 帧 300 s + 二维启动 30 min
        assert snaps[-1]["time_minutes"] == s["domain"]["simulation_duration_minutes"] == 300.0
        assert {b - a for a, b in zip([f["time_minutes"] for f in snaps], [f["time_minutes"] for f in snaps][1:])} == {5.0}

    def test_inflow_hydrograph_closes_with_swmm_report(self, l2b):
        """逐井入流累加 + 二维回灌检查井 = 耦合 SWMM 报告的 External Inflow（L1：34,803 + 12,610 = 47,414 m³）。"""
        out, s = l2b
        h = json.loads((out / "inflow_hydrograph.json").read_text())
        assert s["outputs"]["inflow_hydrograph"] == "inflow_hydrograph.json"
        assert h["node_count"] == 645
        assert h["total_volume_m3"] == pytest.approx(34802.7, abs=1)
        assert h["cum_volume_m3"][-1] == pytest.approx(h["total_volume_m3"], abs=1)
        assert h["peak_m3s"] == pytest.approx(20.51, abs=0.01) and h["peak_time_min"] == 67
        assert h["equivalent_depth_mm"] == pytest.approx(42.4, abs=0.1)        # 82 ha 汇水区
        rpt = (out / "swmm_coupled.rpt").read_text(errors="replace")
        ext = float(re.search(r"External Inflow \.+\s+[\d.]+\s+([\d.]+)", rpt).group(1)) * 1000
        assert h["total_volume_m3"] + s["coupling"]["total_anuga_to_swmm_m3"] == pytest.approx(ext, rel=2e-3)

    def test_ponding_runs_and_flags_deviations(self, l2b):
        out, _ = l2b
        r = sp.compute(out)
        assert r["summary"]["cluster_count"] > 0
        assert {"terrain_from_sww", "swmm_from_rpt", "no_surface_rain"} <= set(r["deviations"])
        assert "land_mask_inferred" not in r["deviations"]

    def test_residual_ponds_explain_undrained_water(self, l2b):
        """演算末残留积水（sim_residual）：块水量 + 不足 1 cm 的薄水 = 末态水量；逐井回灌反算与耦合记录对上；
        箱涵都没满（退不掉不是容量问题）。2026-09-27 在 L2+B 上手工逐块核过算法，数字是 L2+suggested 的结果。"""
        out, s = l2b
        r = sim_residual.compute(out)
        m = r["summary"]
        assert m["surface_volume_m3"] == pytest.approx(5372.5, abs=0.5)
        assert m["pond_count"] == 67
        assert m["pond_volume_m3"] < m["surface_volume_m3"]
        assert {k: v["count"] for k, v in m["by_class"].items()} == {"no_inlet": 50, "below_rim": 9, "not_taking": 8}
        assert m["returning_node_count"] == 50
        assert m["returned_back_calc_m3"] == pytest.approx(s["coupling"]["total_anuga_to_swmm_m3"], rel=0.02)
        assert all(m["storage_fill_pct"][k] < 95 for k in ("NP1", "NP2", "NP3", "NP4", "NP5", "NP6"))
        top = r["ponds"][0]
        assert top["rank"] == 1 and top["class"] == "no_inlet" and top["volume_m3"] == pytest.approx(495.1, abs=1)


def test_state_labels_are_english_at_source():
    for st in sim_sww.STATES:
        assert sim_sww.state_label(st).replace('≡', '').isascii()


REGISTERED = "processed/sim_deliveries/Mussafah_00_61mm2h"
needs_registered = pytest.mark.skipif(
    not (sim_results.DATA_ROOT / REGISTERED / sim_results.STATES_FILE).is_file(), reason="四态还没抽取到 processed")


@needs_registered
class TestOneRunManyStates:
    """一个任务涵盖多态：output_dir 指整份交付，图层按 state 取子目录。"""

    def test_default_and_named_states(self):
        assert sim_results._resolve(REGISTERED).name == "L1"
        assert sim_results._resolve(REGISTERED, "L2+suggested").name == "L2_suggested"
        m = sim_results.run_states(REGISTERED)
        # L2+B 已按用户要求删掉（2026-09-28），不再登记
        assert [s["key"] for s in m["states"]] == ["L1", "L2", "L2+planned", "L2+suggested"]
        assert [s["defence_line_key"] for s in m["states"]] == ["line1", "line2_current"] + ["line2_planned"] * 2
        assert [s["parent"] for s in m["states"]] == [None, "L1", "L2", "L2+planned"]
        with pytest.raises(sim.SimulationError):
            sim_results._resolve(REGISTERED, "L2+B")

    def test_unknown_state_is_404(self):
        with pytest.raises(sim.SimulationError) as e:
            sim_results._resolve(REGISTERED, "L3")
        assert e.value.status == 404 and e.value.message == sim_results.NO_STATE

    def test_single_delivery_rejects_state(self):
        single = next((p for p in (sim_results.DATA_ROOT / "temp").glob("*land-masked-74h/runs/*")
                       if (p / "delivery_summary.json").is_file()), None)
        if single is None:
            pytest.skip("没有单一交付可比")
        rel = str(single.relative_to(sim_results.DATA_ROOT))
        assert sim_results._resolve(rel) == single.resolve()
        with pytest.raises(sim.SimulationError):
            sim_results._resolve(rel, "L1")


def test_state_count_label_translates():
    assert tr("4 态：L1 / L2 / L2+planned / L2+suggested", "en") == "4 states: L1 / L2 / L2+planned / L2+suggested"


@needs_registered
class TestStateEffect:
    """方案效果（sim_effect）：同一时刻对基线 L1 做减法。数字是 2026-09-27 逐帧手算的结果。"""

    @pytest.fixture(scope="class")
    def eff(self):
        from stormwater import sim_effect
        return sim_effect.load(sim_results.DATA_ROOT / REGISTERED)

    def test_same_time_cut_not_whole_run(self, eff):
        by = {s["key"]: s for s in eff["states"]}
        assert eff["base"] == "L1" and eff["default_h"] == 2.0
        # L2+ 各态只演算到 5 h：5 h 以内都有数，超出的档不出数（这里三档都在 5 h 内）
        assert by["L2+suggested"]["sim_end_minutes"] == 300.0
        assert all(cp["reached"] for cp in by["L2+suggested"]["checkpoints"])
        # L2 ≡ L1：逐档一模一样，减出来是 0
        for a, b in zip(by["L1"]["checkpoints"], by["L2"]["checkpoints"]):
            assert a["surface_volume_m3"] == b["surface_volume_m3"] and b["cleared"] == []

    def test_two_hour_numbers(self, eff):
        cp = {s["key"]: s["checkpoints"][0] for s in eff["states"]}
        assert cp["L1"]["t_h"] == 2.0
        assert cp["L1"]["surface_volume_m3"] == pytest.approx(11362, abs=1)
        assert cp["L2+suggested"]["volume_less_m3"] == pytest.approx(6283, abs=1)
        assert (cp["L2+suggested"]["base_active_count"], cp["L2+suggested"]["active_count"]) == (9, 4)
        assert cp["L2+suggested"]["cleared"] == [3, 5, 6, 9, 10] and cp["L2+suggested"]["added"] == []
        # Parsons 方案少的水不到 4%，一个点也没消掉——不是演算时长造成的假消除
        assert cp["L2+planned"]["volume_less_pct"] < 4 and cp["L2+planned"]["cleared"] == []


def test_effect_message_translates():
    assert tr(sim_results.NO_EFFECT, "en") != sim_results.NO_EFFECT


@needs_registered
class TestTimeline:
    """播放用的逐帧当前值（sim_results.timeline）：峰值帧的面积 = 卡片的「最大积水面积」，末帧累计溢水量 = 全程量，
    末帧已出现溢水点 = 片区总数；超出记录的帧不外推。"""

    @pytest.mark.parametrize("state", ["L1", "L2+planned"])
    def test_frames_close_with_whole_run(self, state, monkeypatch):
        d = sim_results._resolve(REGISTERED, state)
        monkeypatch.setattr(sim_results, "_run_dir", lambda conn, rid, st=None: d)
        tl = sim_results.timeline(None, 0)
        s = sim_results._summary(d)
        assert len(tl["time_minutes"]) == len(sim_results._manifest(d))
        assert tl["area_basis"] == "triangle" and tl["overflow_basis"] == "record"
        i = max(range(len(tl["wet_area_m2"])), key=lambda k: tl["wet_area_m2"][k] or 0)
        # 峰现时刻落在 5 min 帧上（L1 130 min），同一口径下就等于卡片的数
        if abs(tl["time_minutes"][i] - s["results"]["peak_inundated_area_minutes"]) < 1e-6:
            assert tl["wet_area_m2"][i] == pytest.approx(s["results"]["peak_inundated_area_m2"], abs=0.1)
        ov = [x for x in tl["overflow_cum_m3"] if x is not None]
        assert ov == sorted(ov)
        assert ov[-1] == pytest.approx(s["coupling"]["total_swmm_to_anuga_m3"], abs=0.1)
        assert tl["ponding_lit"][-1] == tl["ponding_total"]
