"""路况：模拟时段曲线、出发时刻解析、Routes 矩阵分块与解析（假 HTTP）、驻地系数、套矩阵、调度单精算、缓存 TTL。不上网。"""
import sys
import datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from swbrain import traffic as T
from tests.test_dispatch2 import _cfg, _ctx


def _P(**kw):
    cfg = {"traffic": {"provider": "google", **kw}}
    return T.params(cfg)


def test_simulated_factor_and_departure(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    P = T.params({"traffic": {"provider": "google"}})
    assert T.provider(P) == "simulated"                                   # 配了 google 没密钥 → 模拟
    tz = dt.timezone(dt.timedelta(hours=4))
    rush = dt.datetime(2026, 9, 23, 8, 0, tzinfo=tz).astimezone(dt.timezone.utc)       # 周三早高峰
    night = dt.datetime(2026, 9, 23, 3, 0, tzinfo=tz).astimezone(dt.timezone.utc)
    fri = dt.datetime(2026, 9, 25, 8, 0, tzinfo=tz).astimezone(dt.timezone.utc)        # 周五 = 周末曲线
    assert T.simulated_factor(rush, P) > T.simulated_factor(night, P) and T.simulated_factor(fri, P) < T.simulated_factor(rush, P)
    assert abs(T.simulated_factor(rush, P, raining=False) * 1.15 - T.simulated_factor(rush, P)) < 1e-9
    d = T.parse_departure("2099-09-25T14:00", 30.0, P)                    # 当地 14:00 + 30 min → UTC 10:30
    assert d.tzinfo is not None and d.hour == 10 and d.minute == 30
    now = T.parse_departure("now", 0.0, P)
    assert (now - dt.datetime.now(dt.timezone.utc)).total_seconds() >= 0   # 不早于现在（Google 不接受过去时刻）
    past = T.parse_departure("2000-01-01T00:00", 0.0, P)
    assert past > dt.datetime.now(dt.timezone.utc)


def _fake_post_factory(calls, ratio=1.4, missing=()):
    def post(body, key, timeout_s):
        calls.append(body)
        out = []
        for oi, o in enumerate(body["origins"]):
            for di, d in enumerate(body["destinations"]):
                if (oi, di) in missing:
                    out.append({"originIndex": oi, "destinationIndex": di, "condition": "ROUTE_NOT_FOUND"}); continue
                la1 = o["waypoint"]["location"]["latLng"]; la2 = d["waypoint"]["location"]["latLng"]
                km = np.hypot((la2["longitude"] - la1["longitude"]) * 111 * 0.91, (la2["latitude"] - la1["latitude"]) * 111)
                static = max(60.0, km / 40.0 * 3600.0)
                out.append({"originIndex": oi, "destinationIndex": di, "duration": f"{static * ratio:.0f}s", "staticDuration": f"{static:.0f}s", "distanceMeters": int(km * 1000), "condition": "ROUTE_EXISTS"})
        return out
    return post


def test_route_matrix_chunks_and_parses():
    P = _P(routing_preference="TRAFFIC_AWARE_OPTIMAL")                  # 上限 100 元素
    calls = []
    origins = [(54.5 + i * 0.01, 24.4) for i in range(3)]
    dests = [(54.5, 24.4 + j * 0.005) for j in range(70)]
    els = T.route_matrix(origins, dests, dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc), P, "k", post=_fake_post_factory(calls))
    assert len(els) == 3 * 70 and all(len(c["origins"]) * len(c["destinations"]) <= 100 for c in calls)
    assert {(e["oi"], e["di"]) for e in els} == {(i, j) for i in range(3) for j in range(70)}
    e = els[0]; assert e["duration_s"] and e["static_s"] and abs(e["duration_s"] / e["static_s"] - 1.4) < 0.02 and e["condition"] == "ROUTE_EXISTS"
    assert calls[0]["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL" and calls[0]["trafficModel"] == "BEST_GUESS" and calls[0]["departureTime"].endswith("Z")


def test_depot_factors_apply_and_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    P = _P(sample_per_depot=3, cache_ttl_min=15)
    cfg = _cfg(); ctx = _ctx(cfg)
    ctx["pts0"]["priority"] = [300.0, 60.0, 40.0]
    dep = dt.datetime(2099, 1, 1, 8, 0, tzinfo=dt.timezone.utc)
    cache = T.Cache(tmp_path / "c.json", 15)
    calls = []
    meta = T.depot_factors(ctx, P, dep, "test-key", cache, post=_fake_post_factory(calls, ratio=1.5))
    assert meta["provider"] == "google" and not meta["simulated"] and meta["elements"] == 3
    f = meta["factors"]["D1"]; assert abs(f["factor"] - 1.5) < 0.02 and f["n"] == 3 and f["source"] == "google"
    # 套矩阵：驻地行 ×1.5，点→点按均值
    c2 = T.apply(ctx, meta, P)
    assert np.allclose(c2["T_heavy_d"], np.asarray(ctx["T_heavy_d"]) * f["factor"]) and np.allclose(c2["T_pp"], np.asarray(ctx["T_pp"]) * f["factor"])
    assert c2["traffic"]["applied"] and c2["traffic"]["by_depot"]["D1"]["factor"] == f["factor"] and ctx["T_heavy_d"][0, 0] == 10.0   # 原矩阵不动
    # 第二次同一出发时段：全走缓存，不再请求
    meta2 = T.depot_factors(ctx, P, dep, "test-key", cache, post=_fake_post_factory(calls, ratio=9.9))
    assert meta2["elements"] == 0 and abs(meta2["factors"]["D1"]["factor"] - 1.5) < 0.02
    # 系数夹在 [factor_min, factor_max]
    P3 = _P(sample_per_depot=3, factor_max=1.2)
    meta3 = T.depot_factors(ctx, P3, dep + dt.timedelta(hours=1), "test-key", None, post=_fake_post_factory([], ratio=2.5))
    assert meta3["factors"]["D1"]["factor"] == 1.2


def test_depot_factors_fallback_when_http_fails(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    P = _P(sample_per_depot=2)
    cfg = _cfg(); ctx = _ctx(cfg)

    def boom(body, key, timeout_s):
        raise OSError("network down")
    meta = T.depot_factors(ctx, P, dt.datetime(2099, 1, 1, 8, 0, tzinfo=dt.timezone.utc), "test-key", None, post=boom)
    assert meta["provider"] == "simulated" and meta["simulated"] and "退回模拟" in meta["note"] and meta["factors"]["D1"]["source"] == "simulated"


def test_refine_plan_reports_deltas(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    P = _P()
    cfg = _cfg(); ctx = _ctx(cfg)
    plan = pd.DataFrame([{"unit_id": "P12-0", "from": "D1", "fp_id": "A", "lon": 54.50, "lat": 24.40, "travel_min": 10.0},
                         {"unit_id": "P12-1", "from": "D1", "fp_id": "A", "lon": 54.50, "lat": 24.40, "travel_min": 10.0},
                         {"unit_id": "P8-0", "from": "D1", "fp_id": "C", "lon": 54.60, "lat": 24.50, "travel_min": 40.0},
                         {"unit_id": "P8-1", "from": "A", "fp_id": "B", "lon": 54.51, "lat": 24.41, "travel_min": 8.0}])       # 从点出发的不算
    calls = []
    r = T.refine_plan(plan, ctx, P, dt.datetime(2099, 1, 1, 8, 0, tzinfo=dt.timezone.utc), "test-key", None, post=_fake_post_factory(calls, ratio=1.3))
    assert r["refined"] and r["n_pairs"] == 2 and r["elements"] == 2 and {w["fp_id"] for w in r["worst"]} == {"A", "C"}
    a = next(w for w in r["worst"] if w["fp_id"] == "A"); assert a["units"] == 2 and a["model_min"] == 10.0 and a["google_min"] > 0 and a["delta_min"] == round(a["google_min"] - 10.0, 1)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY")
    r2 = T.refine_plan(plan, ctx, P, dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc), None, None, post=_fake_post_factory([], 1.3))
    assert not r2["refined"]


def test_status_and_none_provider(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    st = T.status({"traffic": {"provider": "google", "cache_file": "out/_no_such_cache.json"}})
    assert st["configured"] == "google" and st["effective"] == "simulated" and not st["has_key"] and st["cached_pairs"] == 0
    cfg = _cfg(); ctx = _ctx(cfg)
    meta = T.depot_factors(ctx, T.params({"traffic": {"provider": "none"}}), dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc), None)
    assert meta["provider"] == "none" and meta["factors"]["D1"]["factor"] == 1.0


def test_factor_series_and_window_scaling(monkeypatch):
    """时变：模拟曲线按窗口出发时刻各取一次系数；build_classes 在后续窗口按比值缩放行程行。"""
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    P = T.params({"traffic": {"provider": "simulated", "windows_ahead": 4, "window_min": 60}})
    cfg = _cfg(); ctx = _ctx(cfg)
    tz = dt.timezone(dt.timedelta(hours=4))
    dep0 = dt.datetime(2099, 9, 23, 5, 30, tzinfo=tz).astimezone(dt.timezone.utc)      # 05:30 → 后面四个窗口跨进早高峰
    base = T.depot_factors(ctx, P, dep0, None)
    ser = T.factor_series(ctx, P, dep0, 210.0, None, base=base)
    assert set(ser["series"]) == {"210", "270", "330", "390", "450"} and ser["series_min"] < ser["series_max"]
    f0 = ser["series"]["210"]["D1"]; f_peak = ser["series"]["390"]["D1"]                # 08:30 早高峰
    assert abs(f0 - round(0.90 * 1.15, 3)) < 1e-6 and abs(f_peak - round(1.45 * 1.15, 3)) < 1e-6
    c2 = T.apply(ctx, {**base, **ser}, P)
    sc, sc_pp = T.scale_at(c2, 210.0); assert np.allclose(sc, 1.0) and abs(sc_pp - 1.0) < 1e-9
    sc, sc_pp = T.scale_at(c2, 395.0)                                                 # 落在 390 这一档
    assert abs(sc[0] - f_peak / f0) < 1e-6 and abs(sc_pp - f_peak / f0) < 1e-6
    from swbrain import dispatch2 as D2, resources as RS
    res = RS.build(ctx["fleet"], None, cfg)
    units = ctx["units"].copy(); units["at_pt"] = -1; units["busy_until"] = np.nan
    cl0 = D2.build_classes(units, ctx["depots"], res, {**c2, "cfg": cfg}, 210.0)
    cl1 = D2.build_classes(units, ctx["depots"], res, {**c2, "cfg": cfg}, 395.0)
    assert np.allclose(cl1[0]["travel"], cl0[0]["travel"] * f_peak / f0) and np.allclose(cl0[0]["travel"], np.asarray(c2["T_heavy_d"][0]))
    assert T.scale_at(ctx, 395.0) == (None, 1.0)                                       # 没套路况：不缩放
