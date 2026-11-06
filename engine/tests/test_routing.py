"""swbrain/routing.py：本地优先、在线备选的取路线顺序（不连网，替换两个来源的函数）。"""
from swbrain import local_route as LR, routing as RT


def _fake(results):
    """results: {障碍标记: 返回值}；障碍列表第一个元素当标记（hard / soft）。"""
    calls = []

    def fn(a, b, bars, names):
        tag = bars[0] if bars else "none"
        calls.append(tag)
        r = results.get(tag, {"error": "no route"})
        if isinstance(r, Exception):
            raise r
        return dict(r)
    return fn, calls


def _with(monkeypatch, local, online):
    monkeypatch.setitem(RT.FUNCS["route"], "local", local)
    monkeypatch.setitem(RT.FUNCS["route"], "online", online)
    monkeypatch.setitem(LR._CFG, "providers", ["local", "online"])


def test_local_first(monkeypatch):
    lo, lc = _fake({"hard": {"minutes": 10, "source": "local"}})
    on, oc = _fake({"hard": {"minutes": 12, "source": "online"}})
    _with(monkeypatch, lo, on)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert r["source"] == "local" and r["minutes"] == 10 and r["fallback_reason"] is None
    assert oc == []


def test_deep_water_cut_retries_soft_on_same_provider(monkeypatch):
    lo, lc = _fake({"hard": {"error": 'No route from location "A" to location "B".'}, "soft": {"minutes": 15, "source": "local"}})
    on, oc = _fake({})
    _with(monkeypatch, lo, on)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert r["source"] == "local" and r["blocked"] is True
    assert lc == ["hard", "soft"] and oc == []


def test_local_transient_with_hard_barriers_retries_soft(monkeypatch):
    lo, lc = _fake({"hard": {"error": "Unable to complete operation.", "transient": True}, "soft": {"minutes": 4, "source": "local"}})
    on, oc = _fake({})
    _with(monkeypatch, lo, on)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert r["source"] == "local" and r["blocked"] is True and lc == ["hard", "soft"] and oc == []


def test_local_disconnected_falls_back_to_online(monkeypatch):
    nr = {"error": 'No route from location "A" to location "B".  No solution found.'}
    lo, lc = _fake({"hard": nr, "soft": nr})
    on, oc = _fake({"hard": {"minutes": 12, "source": "online"}})
    _with(monkeypatch, lo, on)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert r["source"] == "online" and "blocked" not in r
    assert r["fallback_reason"].startswith("local: No route")


def test_local_exception_falls_back(monkeypatch):
    lo, _ = _fake({"hard": RuntimeError("service down")})
    on, _ = _fake({"hard": {"minutes": 12, "source": "online"}})
    _with(monkeypatch, lo, on)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert r["source"] == "online" and "RuntimeError" in r["fallback_reason"]


def test_both_fail_reports_both(monkeypatch):
    lo, _ = _fake({})
    on, _ = _fake({"hard": RuntimeError("Invalid Token")})
    _with(monkeypatch, lo, on)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert "error" in r and "local:" in r["error"] and "online:" in r["error"] and "Invalid Token" in r["error"]


def test_local_bars_move_slow_factor_to_minutes():
    bars = [{"geometry": {}, "attributes": {"BarrierType": 1, "Attr_TruckTravelTime": 3.0}}, {"geometry": {}, "attributes": {"BarrierType": 0}}]
    out = LR._local_bars(bars)
    assert out[0]["attributes"] == {"BarrierType": 1, "Attr_Minutes": 3.0}
    assert out[1]["attributes"] == {"BarrierType": 0}
    assert bars[0]["attributes"]["Attr_TruckTravelTime"] == 3.0          # 不改原障碍


def test_error_text_drops_off_network_barrier_notes():
    err = {"code": 400, "message": "Unable to complete operation.",
           "details": ['Barrier "Locations 32" in "PolygonBarriers" has no associated network location information.',
                       'No route from location "A13" to location "SIM-L1-032".']}
    assert LR.error_text(err) == 'No route from location "A13" to location "SIM-L1-032".'


def test_detail_applies_time_factor(monkeypatch):
    monkeypatch.setitem(LR._CFG, "local_time_factor", 1.5)
    monkeypatch.setattr(LR, "raw", lambda *a, **k: {"minutes": 10.0, "km": 5.0, "steps_en": ["Go"],
                                                     "segs": [{"text": "走", "road": "R", "meters": 5000, "minutes": 10.0, "path": [[0, 0], [1, 1]]}]})
    r = LR.detail([0, 0], [1, 1])
    assert r["minutes"] == 15.0 and r["minutes_raw"] == 10.0 and r["segs"][0]["minutes"] == 15.0 and r["source"] == "local"


def test_online_exception_on_hard_still_tries_soft_cache(monkeypatch):
    nr = {"error": 'No route from location "A" to location "B".'}
    lo, _ = _fake({"hard": nr, "soft": nr})
    calls = []

    def on(a, b, bars, names):
        calls.append(bars[0])
        if bars[0] == "hard":
            raise RuntimeError("取出行模式失败：Invalid Token")
        return {"minutes": 6.5}
    monkeypatch.setattr(RT, "_online_route", RT._guard(on))
    _with(monkeypatch, lo, RT._online_route)
    r = RT.solve([0, 0], [1, 1], ["hard"], ["soft"])
    assert r["source"] == "online" and r["blocked"] is True and calls == ["hard", "soft"]
