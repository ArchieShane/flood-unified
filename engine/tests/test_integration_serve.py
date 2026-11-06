"""真机集成测试（可选）：装载真实 out/state，起一个临时端口的服务，把轻接口各打一遍。
默认跳过；`SWBRAIN_INTEGRATION=1 python -m pytest tests/test_integration_serve.py -q` 才跑（装载约 20–60 s）。重接口（调度求解）只验异步任务的接口形状，不等它算完。"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = os.environ.get("SWBRAIN_INTEGRATION") == "1" and (ROOT / "out" / "state" / "es.parquet").exists() and (ROOT / "config.toml").exists()
pytestmark = pytest.mark.skipif(not RUN, reason="需要 SWBRAIN_INTEGRATION=1 与真实 out/state")


@pytest.fixture(scope="module")
def server():
    from http.server import ThreadingHTTPServer
    from swbrain import db, serve
    cfg = db.load_config(None)
    cfg.setdefault("serve", {})["no_warm"] = True; cfg["serve"]["solver_workers"] = 0          # 集成测试不预热、不开子进程
    state = serve.State(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(state))
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True); th.start()
    key = str((cfg.get("serve") or {}).get("access_key") or "")
    yield {"port": port, "key": key, "state": state}
    httpd.shutdown()


def _get(srv, path):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"http://127.0.0.1:{srv['port']}{path}{sep}key={srv['key']}", timeout=600) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_light_endpoints(server):
    for path in ("/api/health", "/api/summary", "/api/scenario?i_mmh=30", "/api/ontology", "/api/feeds", "/api/traffic", "/api/assumptions", "/api/jobs",
                 "/api/objects?type=RainEvent&limit=20", "/api/object?type=FloodPoint&id=ADM-62&links=0", "/api/zone?name=Mussafah"):
        code, d = _get(server, path)
        assert code == 200 and isinstance(d, dict) and not d.get("error"), (path, d if not isinstance(d, dict) else d.get("error"))
    code, h = _get(server, "/api/health")
    assert h["ok"] and h["disk_cache"]["enabled"] is not None and "solver_pool" in h


def test_dispatch_async_shape_and_key_redaction(server):
    code, d = _get(server, "/api/dispatch?t=210&async=1")
    assert code == 200 and (("job_id" in d and d["status"] in ("queued", "running", "done")) or "kpis" in d)
    if "job_id" in d:
        code, j = _get(server, f"/api/jobs/{d['job_id']}")
        assert code == 200 and j["job_id"] == d["job_id"] and j["status"] in ("queued", "running", "done", "error")
    code, a = _get(server, "/api/assumptions")
    assert a["summary"]["n"] >= 5 and all("level" in x and "what" in x for x in a["items"])


def test_unknown_and_unauthorised(server):
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/api/nope?key={server['key']}", timeout=30)
    assert ei.value.code == 404
    if server["key"]:
        with pytest.raises(urllib.error.HTTPError) as e2:
            urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/api/health", timeout=30)
        assert e2.value.code == 401


def test_every_scenario_uses_the_configured_flood_source(server):
    """八个情景的积水点口径必须一致，都按 [dispatch].flood_source 来。

    2026-09-24 踩过两次：启动时按「盘上哪个预测文件最新」装初始上下文，并把那个文件的
    情景键当成 active_rp；而 warm_up 会跳过 active_rp，于是那个情景永远停在预测文件的
    口径上。config 写着 flood_source = system，50 年一遇却一直是世界模型的点，
    调度单全空——演示时点过去就是一片空白。
    """
    from swbrain import scenarios
    cfg = server["state"].cfg
    want = str((cfg.get("dispatch") or {}).get("flood_source") or "auto")
    if want != "system":
        pytest.skip(f"flood_source = {want}，这条只管 system")
    bad = []
    for k in scenarios.keys(cfg):
        code, r = _get(server, f"/api/dispatch?rp={urllib.parse.quote(str(k))}")
        src = str(((r.get("flood_source") or {}) or {}).get("type"))
        n_plan = len(r.get("plan") or [])
        if src != "system" or n_plan == 0:
            bad.append(f"{k}: 来源 {src}、调度单 {n_plan} 行")
    assert not bad, "这些情景的口径和配置对不上：" + "; ".join(bad)


def test_startup_scenario_is_deterministic(server):
    """初始情景由配置定（留空 = 情景目录第一个），不能取决于盘上哪个预测文件最新。"""
    from swbrain import scenarios
    st = server["state"]; cfg = st.cfg
    cat = scenarios.keys(cfg)
    want = str((cfg.get("serve") or {}).get("startup_scenario") or "").strip()
    expect = want if want in cat else (cat[0] if cat else None)
    assert str(st.active_rp) == str(expect), f"初始情景是 {st.active_rp}，按配置该是 {expect}"
