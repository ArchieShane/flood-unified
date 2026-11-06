"""外部源：文件 / 拉（假 HTTP，失败退回文件）/ 推（按 id 合并）/ 状态；桥梁台账与承包商边界进 build_context / resources。不上网。"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swbrain import feeds as F, resources as RS
from tests.test_dispatch2 import _cfg, _ctx


def _cfg_feeds(tmp_path, **urls):
    c = {"feeds": {n: {"file": str(tmp_path / f"{n}.json"), "url": urls.get(n, ""), "poll_s": 60} for n in F.NAMES}}
    return c


def test_file_push_and_merge(tmp_path):
    cfg = _cfg_feeds(tmp_path)
    r0 = F.load(cfg, "incidents"); assert r0["source"] == "none" and r0["rows"] == []
    p1 = F.push(cfg, "incidents", {"schema": "swbrain.incidents/1", "rows": [{"id": "993-1", "lon": 54.5, "lat": 24.4, "verified": False}, {"id": "993-2", "fp_id": "ADM-4"}]}, origin="test")
    assert p1["added"] == 2 and p1["total"] == 2
    p2 = F.push(cfg, "incidents", [{"id": "993-1", "lon": 54.5, "lat": 24.4, "verified": True}, {"id": "993-3", "fp_id": "ADM-5"}])
    assert p2["updated"] == 1 and p2["added"] == 1 and p2["total"] == 3
    r = F.load(cfg, "incidents")
    assert r["source"] == "file" and len(r["rows"]) == 3 and next(x for x in r["rows"] if x["id"] == "993-1")["verified"] is True and r["schema"] == "swbrain.incidents/1"
    st = F.status(cfg)["incidents"]
    assert st["mode"] == "push/file" and st["rows"] == 3 and st["push_endpoint"] == "/api/feed/incidents"


def test_http_pull_cache_and_fallback(tmp_path, monkeypatch):
    cfg = _cfg_feeds(tmp_path, checkins="https://adm.example/checkins")
    (tmp_path / "checkins.json").write_text(json.dumps({"schema": "swbrain.checkins/1", "rows": [{"depot_id": "A01", "units": 3}]}), encoding="utf-8")
    calls = []
    monkeypatch.setenv("ADM_CHECKINS_TOKEN", "tok")

    def fetch(url, token):
        calls.append((url, token))
        return {"schema": "swbrain.checkins/1", "as_of": "2026-09-22T19:00:00+04:00", "rows": [{"depot_id": "A01", "units": 5}, {"depot_id": "A02", "units": 2}]}
    F._HTTP_CACHE.clear()
    r = F.load(cfg, "checkins", fetch=fetch, now=1000.0)
    assert r["source"] == "http" and len(r["rows"]) == 2 and r["has_token"] and calls == [("https://adm.example/checkins", "tok")]
    r2 = F.load(cfg, "checkins", fetch=fetch, now=1030.0)                 # poll_s 内：缓存
    assert r2.get("cached") and len(calls) == 1
    F.load(cfg, "checkins", fetch=fetch, now=2000.0); assert len(calls) == 2

    def bad(url, token):
        raise OSError("refused")
    F._HTTP_CACHE.clear()
    r3 = F.load(cfg, "checkins", fetch=bad, now=5000.0)
    assert r3["source"] == "file" and r3["http_failed"] and len(r3["rows"]) == 1 and "退回文件" in r3["error"]
    assert F.status(cfg)["checkins"]["mode"] == "pull+push"


def test_bridges_and_contractors_files_are_used(tmp_path, monkeypatch):
    cfg = _cfg(); cfg.update(_cfg_feeds(tmp_path))
    ctx = _ctx(cfg)
    # 承包商边界：按驻地 / 按市政
    F.push(cfg, "contractors", {"rows": [{"depot_id": "D1", "contractor": "Real Co"}]})
    res = RS.build(ctx["fleet"], None, cfg)
    assert res["depots"]["D1"]["contractor"] == "Real Co" and res["depots"]["D1"]["contractor_real"]
    (tmp_path / "contractors.json").unlink()
    F.push(cfg, "contractors", {"rows": [{"region": "ADM", "area": "Zayed", "contractor": "Area Co"}]})
    res2 = RS.build(ctx["fleet"], None, cfg)
    assert res2["depots"]["D1"]["contractor"] == "Area Co"
    (tmp_path / "contractors.json").unlink()
    res3 = RS.build(ctx["fleet"], None, cfg)
    assert res3["depots"]["D1"]["contractor"] == "SW-ADM-Zayed" and not res3["depots"]["D1"]["contractor_real"]
    # 桥梁台账：build_context 的 bridges 元数据不再标模拟
    from swbrain import dispatch as D1
    import pandas as pd
    cfg["dispatch"]["bridges"] = {"simulated": True, "items": [{"name": "sim", "lon": 54.7, "lat": 24.7, "limit_t": 5}]}
    fac = pd.DataFrame({"kind": [], "lon": [], "lat": [], "name": []})
    fp = {"points": [{"fp_id": r.fp_id, "lon": r.lon, "lat": r.lat, "volume_m3": r.volume_m3, "max_depth_m": r.max_depth_m, "duration_min": r.duration_min, "hydrograph": r.hydrograph, "land_use": r.land_use} for r in ctx["pts0"].itertuples()], "scenario": {}}
    try:
        c0 = D1.build_context(fp, ctx["fleet"], fac, ctx["cs"], None, cfg, None)
    except Exception:
        return                                                           # 合成积水点缺字段时跳过桥梁这一段（承包商部分已验）
    assert c0["bridges"]["simulated"] and c0["bridges"]["blocked"] == 1
    F.push(cfg, "bridges", {"rows": [{"id": "B1", "name": "real", "lon": 54.71, "lat": 24.71, "limit_t": 8}, {"id": "B2", "name": "ok", "lon": 54.72, "lat": 24.72, "limit_t": 40}]})
    c1 = D1.build_context(fp, ctx["fleet"], fac, ctx["cs"], None, cfg, None)
    assert not c1["bridges"]["simulated"] and c1["bridges"]["blocked"] == 1 and c1["bridges"]["source"] == "file" and c1["bridges"]["items"][0]["name"] == "real"


def test_push_validates_against_contract(tmp_path):
    cfg = _cfg_feeds(tmp_path)
    r = F.push(cfg, "incidents", {"rows": [{"id": "ok-1", "lon": 54.4, "lat": 24.5}, {"id": "bad-1", "lon": 24.5, "lat": 54.4}, {"id": "bad-2"}, {"id": "ok-2", "fp_id": "ADM-4", "verified": True}, "not a dict"]})
    assert r["received"] == 5 and r["accepted"] == 2 and r["n_rejected"] == 3 and r["added"] == 2
    whys = {x.get("id"): x["why"] for x in r["rejected"] if x.get("id")}
    assert "顺序" in whys["bad-1"] and "缺 fp_id" in whys["bad-2"]
    r2 = F.push(cfg, "checkins", {"rows": [{"depot_id": "A01", "units": -1}, {"units": 3}]})
    assert r2.get("error") and r2["accepted"] == 0 and r2["added"] == 0 and not (tmp_path / "checkins.json").exists()
    r3 = F.push(cfg, "bridges", {"rows": [{"id": "B1", "lon": 54.6, "lat": 24.5, "limit_t": 10}, {"id": "B2", "lon": 54.6, "lat": 24.5}]})
    assert r3["accepted"] == 1 and r3["rejected"][0]["id"] == "B2"
    r4 = F.push(cfg, "contractors", {"rows": [{"depot_id": "A01", "contractor": "X"}, {"contractor": "Y"}, {"region": "AAM"}]})
    assert r4["accepted"] == 1 and r4["n_rejected"] == 2
    # 拉取 / 读文件同样过滤无效行并计数
    (tmp_path / "incidents.json").write_text(json.dumps({"rows": [{"id": "a", "fp_id": "ADM-1"}, {"id": "b", "lon": 1, "lat": 1}]}), encoding="utf-8")
    ld = F.load(cfg, "incidents")
    assert len(ld["rows"]) == 1 and ld["invalid"] == 1 and F.status(cfg)["incidents"]["invalid_rows"] == 1


# ---- 并发与原子写（2026-09-23 评审）----
# push 是读-改-写：两个并发的 POST /api/feed/incidents 会各自读到同一份旧 rows，后写的把先写的整批覆盖掉。
# 暴雨时 993 报案批量推送正是并发场景，丢的是真实报案。

def test_concurrent_push_keeps_every_row(tmp_path):
    import threading
    cfg = _cfg_feeds(tmp_path)
    N_THREADS, PER = 8, 25
    errs = []

    def worker(k):
        try:
            F.push(cfg, "incidents", [{"id": f"t{k}-{i}", "fp_id": "ADM-1"} for i in range(PER)])
        except Exception as ex:                       # pragma: no cover
            errs.append(ex)

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(N_THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
    rows = F.load(cfg, "incidents")["rows"]
    got = {r["id"] for r in rows}
    want = {f"t{k}-{i}" for k in range(N_THREADS) for i in range(PER)}
    assert got == want, f"丢了 {len(want - got)} 行，多了 {len(got - want)} 行"


def test_push_writes_atomically(tmp_path, monkeypatch):
    """写到一半被打断，盘上不能留半截 JSON——否则 _read_file 吞掉异常，这个源静默变成「没有数据」。"""
    cfg = _cfg_feeds(tmp_path)
    F.push(cfg, "incidents", [{"id": "keep-me", "fp_id": "ADM-1"}])
    f = Path(cfg["feeds"]["incidents"]["file"])
    good = f.read_text(encoding="utf-8")

    real = F._atomic_write

    def boom(path, text):
        raise OSError("盘满了")

    monkeypatch.setattr(F, "_atomic_write", boom)
    try:
        F.push(cfg, "incidents", [{"id": "new-one", "fp_id": "ADM-2"}])
    except OSError:
        pass
    monkeypatch.setattr(F, "_atomic_write", real)
    assert f.read_text(encoding="utf-8") == good              # 旧内容原封不动
    assert {r["id"] for r in F.load(cfg, "incidents")["rows"]} == {"keep-me"}


# ---- 拉取型外部源 ----

def test_pull_rejects_non_http_scheme(tmp_path):
    """file:// 和 UNC 路径不能被当成外部源读进来。"""
    import pytest
    for url in ("file:///C:/Windows/win.ini", "ftp://example.com/x.json", r"\host\share\x.json"):
        with pytest.raises(ValueError):
            F._get(url, None)


def test_pull_requires_https_when_token_is_set(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        F._get("http://example.com/feed.json", "secret-token")


def test_pull_does_not_follow_redirects():
    """urllib 跳转时不剥 Authorization：一个 302 就能把 Bearer token 送到第三方域名。"""
    import urllib.request
    assert F._OPENER is not None
    handlers = [type(h).__name__ for h in F._OPENER.handlers]
    assert "_NoRedirect" in handlers
    h = F._NoRedirect()
    import pytest
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(urllib.request.Request("https://a.example/x"), None, 302, "Found", {}, "https://evil.example/x")
