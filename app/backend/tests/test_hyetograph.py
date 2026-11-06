"""派生雨型（`hyetograph.py` 与 `/api/hyetograph*`）的护栏。

雨型图只看不算，但它挂着「官方 DDF」的名头，数字必须和交付包
（docs/Zone_B官方DDF与雨型图_20260926/）对得上。这里锁死四件事：

1. 6 个重现期 × 180 min × 5 min × 峰值 40% 与交付包 CSV 逐值相等；
2. DDF 累计雨量表与 idf.py 的强度表是同一张 Table 3-6（只差强度表的舍入）；
3. 接口的序列与模块一致，参数不合法回 422 且报错跟着语言走；
4. 页签上出现的后端文案都有英文。
"""

import csv
import math
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stormwater import hyetograph as hy
from stormwater.api.main import app
from stormwater.i18n import tr
from stormwater.idf import (
    ADQCC_TABLE_DURATIONS_MIN,
    ADQCC_ZONE_B_DDF_DEPTH_MM,
    ADQCC_ZONE_B_DEPTH_24H_MM,
    ADQCC_ZONE_B_TABLE,
)

CSV_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/Zone_B官方DDF与雨型图_20260926/Zone_B_180分钟_5分钟派生雨型_全部重现期.csv"
)
CJK = re.compile(r"[一-鿿]")

client = TestClient(app)


@pytest.fixture(scope="module")
def delivered():
    if not CSV_PATH.exists():
        pytest.skip("交付包 CSV 不在本机")
    rows: dict[int, list[tuple[float, float]]] = {}
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.setdefault(int(r["return_period_years"]), []).append(
                (float(r["elapsed_minutes"]), float(r["depth_mm_per_5min"]))
            )
    return rows


class TestAgainstDeliveredPackage:
    @pytest.mark.parametrize("rp", hy.RETURN_PERIODS)
    def test_matches_csv_value_by_value(self, delivered, rp):
        p = ADQCC_ZONE_B_DDF_DEPTH_MM[rp][ADQCC_TABLE_DURATIONS_MIN.index(180)]
        h = hy.build(p, 180, 5, 40)
        ref = delivered[rp]
        assert len(ref) == len(h.step_depth_mm) == 36
        for (t, d), t2, d2 in zip(ref, h.t_min, h.step_depth_mm):
            assert t == t2
            assert d == pytest.approx(d2, abs=1e-9)
        assert h.equivalent.kind == "within"
        assert h.equivalent.years == pytest.approx(rp)


class TestDdfTable:
    def test_same_table_as_published_intensity(self):
        """雨量表与强度表出自同一张 Table 3-6：强度 × 历时 ÷ 60 与雨量差不过 0.5%（强度表舍入）。"""
        for rp, depths in ADQCC_ZONE_B_DDF_DEPTH_MM.items():
            for d, depth, i in zip(ADQCC_TABLE_DURATIONS_MIN, depths, ADQCC_ZONE_B_TABLE[rp]):
                assert i * d / 60 == pytest.approx(depth, rel=5e-3), (rp, d)

    def test_24h_column_is_the_ledger_column(self):
        for rp, depths in ADQCC_ZONE_B_DDF_DEPTH_MM.items():
            assert depths[-1] == ADQCC_ZONE_B_DEPTH_24H_MM[rp]


class TestBuild:
    @pytest.mark.parametrize("p, d, step, peak", [(96, 1440, 15, 40), (33, 60, 5, 0), (61, 120, 10, 100)])
    def test_total_and_peak(self, p, d, step, peak):
        h = hy.build(p, d, step, peak)
        n = d // step
        assert len(h.step_depth_mm) == n
        assert sum(h.step_depth_mm) == pytest.approx(p)
        assert h.peak_index == min(n - 1, math.floor(peak / 100 * n))
        assert h.step_depth_mm[h.peak_index] == max(h.step_depth_mm)
        assert h.intensity_mm_hr[h.peak_index] == pytest.approx(h.step_depth_mm[h.peak_index] * 60 / step)

    @pytest.mark.parametrize("p, d", [(96, 1440), (33, 60), (61, 120)])
    def test_peak_5min_intensity_does_not_depend_on_step(self, p, d):
        """步长只是画图选择：同一场雨换步长，报出的峰值 5 分钟雨强必须不变。"""
        vals = [hy.build(p, d, s, 40).peak_intensity_5min_mm_hr for s in hy.STEP_CHOICES_MIN]
        assert vals == pytest.approx([vals[0]] * len(vals))
        h5 = hy.build(p, d, 5, 40)
        assert vals[0] == pytest.approx(h5.intensity_mm_hr[h5.peak_index])  # 5 min 步长时即最高那根柱

    def test_peak_5min_on_official_curve_matches_ddf(self):
        """落在官方档位上：峰值 5 分钟雨强 = DDF 5 分钟雨量 × 12（96 mm / 24 h → 26.99 × 12）。"""
        assert hy.build(96, 1440, 15, 40).peak_intensity_5min_mm_hr == pytest.approx(26.99 * 12)

    def test_equivalent_return_periods_of_presets(self):
        """96 mm / 24 h 恰是 100 年 24 h 雨量 —— 不能因浮点误差被判成「超百年」。"""
        eq = {(x["depth_mm"], x["duration_min"]): x["equivalent"] for x in hy.presets()}
        assert len(eq) == 9
        assert eq[(96, 1440)]["kind"] == "within"
        assert eq[(96, 1440)]["years"] == pytest.approx(100)
        assert eq[(61, 120)]["kind"] == "above"
        assert eq[(61, 120)]["ratio"] == pytest.approx(61 / 55.12, rel=1e-3)
        assert eq[(33, 1440)]["years"] == pytest.approx(3.48, abs=0.01)

    def test_outside_table_scales_end_curve(self):
        h = hy.build(10, 1440, 15, 40)  # 远小于 2 年 24 h（20.64 mm）
        assert h.equivalent.kind == "below"
        assert sum(h.step_depth_mm) == pytest.approx(10)

    @pytest.mark.parametrize(
        "args, msg",
        [
            ((0, 60, 5, 40), hy.ERR_DEPTH),
            ((30, 2000, 5, 40), hy.ERR_DURATION),
            ((30, 60, 7, 40), hy.ERR_STEP_CHOICE),
            ((30, 62, 5, 40), hy.ERR_STEP_MULTIPLE),
            ((30, 10, 5, 40), hy.ERR_STEP_MULTIPLE),
            ((30, 60, 5, 120), hy.ERR_PEAK),
        ],
    )
    def test_rejects_bad_input(self, args, msg):
        with pytest.raises(ValueError, match=re.escape(msg)):
            hy.build(*args)


class TestApi:
    def test_presets(self):
        d = client.get("/api/hyetograph/presets").json()
        assert d["default"] == {"depth_mm": 96, "duration_min": 1440}
        assert len(d["presets"]) == 9
        assert d["durations_min"] == list(ADQCC_TABLE_DURATIONS_MIN)
        assert d["ddf_depth_mm"]["100"][-1] == 96

    def test_series_equals_module(self):
        d = client.get("/api/hyetograph", params={"depth_mm": 45, "duration_min": 180, "peak_pct": 30}).json()
        h = hy.build(45, 180, 5, 30)
        assert d["step_min"] == 5  # 省略步长 → 3 h 以内取 5 min
        assert d["step_depth_mm"] == h.step_depth_mm
        assert d["peak_index"] == h.peak_index
        assert d["equivalent"] == h.equivalent.to_dict()

    def test_default_step_for_long_storm(self):
        d = client.get("/api/hyetograph", params={"depth_mm": 96, "duration_min": 1440}).json()
        assert d["step_min"] == 15 and len(d["t_min"]) == 96

    def test_bad_input_is_422_in_request_language(self):
        zh = client.get("/api/hyetograph", params={"depth_mm": 30, "duration_min": 62, "step_min": 5})
        en = client.get("/api/hyetograph", params={"depth_mm": 30, "duration_min": 62, "step_min": 5, "lang": "en"})
        assert zh.status_code == en.status_code == 422
        assert zh.json()["detail"] == hy.ERR_STEP_MULTIPLE
        assert not CJK.search(en.json()["detail"])

    def test_english_texts(self):
        d = client.get("/api/hyetograph/presets", params={"lang": "en"}).json()
        for k in ("source", "method", "boundary"):
            assert not CJK.search(d[k]), k


@pytest.mark.parametrize(
    "text",
    [hy.SOURCE, hy.METHOD, hy.BOUNDARY, hy.ERR_DEPTH, hy.ERR_DURATION, hy.ERR_STEP_CHOICE,
     hy.ERR_STEP_MULTIPLE, hy.ERR_PEAK],
)
def test_every_text_has_english(text):
    assert not CJK.search(tr(text, "en"))
