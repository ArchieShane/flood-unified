"""仿真任务接口（列表 + 向导）。

连的是真库 `syq-pipe` 的 `simulation` schema：整组测试共用一个外层事务，每次请求在
保存点里跑，最后整体回滚 —— 不留一行数据。库连不上（不在内网）就整组 skip。
"""

from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from stormwater import sim_results
from stormwater import simulation as sim
from stormwater.api.main import app
from stormwater.config import db_settings
from stormwater.i18n import tr

client = TestClient(app)


@pytest.fixture(autouse=True)
def rolled_back(monkeypatch):
    try:
        conn = psycopg.connect(db_settings().conninfo, row_factory=dict_row, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 —— 不在内网、没有 .env 都算
        pytest.skip(f"syq-pipe 连不上：{type(exc).__name__}")
    conn.execute(f"set search_path to {sim.SCHEMA}")
    conn.execute(f"set timezone to '{sim.LOCAL_TZ}'")

    @contextmanager
    def fake_connect():
        with conn.transaction():      # 外层事务里的保存点：出错只回滚这一次请求
            yield conn

    monkeypatch.setattr(sim, "connect", fake_connect)
    yield conn
    conn.rollback()
    conn.close()


DRAFT = {
    "rain_source": "design_storm",
    "storm_preset_key": "61mm_2h",
    "temporal_pattern": "Zone B official DDF alternating-block (2022)",
    "peak_position_pct": 40,
    "spatial_distribution": "Uniform citywide",
    "extent": "City-wide", "grid_m": 250, "terrain": "5 m DTM",
    "tide_label": "HAT 1.3 m NADD", "tide_level_m": 1.3, "tide_datum": "NADD", "gate_close_above_m": 1.3,
    "model_start_utc": "2026-09-28T00:00:00",
    "after_rain_mode": "until_drained", "after_rain_max_h": 36,
    "baseflow_pct": 20, "baseflow_min_dia_mm": 600,
    "defence_line_key": "line1",
    "created_by": "ZN",
}


def _line_ids(key: str) -> list[int]:
    opts = client.get("/api/simulations/options").json()
    return [r["id"] for ln in opts["defence_lines"] if ln["key"] == key for r in ln["resources"]]


def test_options_match_the_wizard():
    o = client.get("/api/simulations/options?lang=en").json()
    assert len(o["storm_presets"]) == 9
    design = [p for p in o["storm_presets"] if p["is_design_basis"]]
    assert [p["key"] for p in design] == ["61mm_2h"] and design[0]["exact_depth_mm"] == 61.3
    lines = {ln["key"]: ln for ln in o["defence_lines"]}
    assert [len(lines[k]["resources"]) for k in sim.LINE_ORDER] == [8, 5, 4]
    assert lines["line1"]["label"] == "Defence line 1"
    assert client.get("/api/simulations/options?lang=zh").json()["defence_lines"][0]["label"] == "第一道防线"
    assert o["defaults"]["resource_ids"] == _line_ids("line1")


def test_list_tabs_and_counts():
    body = client.get("/api/simulations?user=ZN").json()
    c = body["counts"]
    assert body["total"] == c["all"] >= c["running"] + c["done"] + c["queued"]
    assert all(i["status"] != "draft" for i in body["items"])
    for tab in ("running", "done", "queued"):
        items = client.get(f"/api/simulations?tab={tab}").json()["items"]
        assert all(i["status"] == tab for i in items)
    mine = client.get("/api/simulations?tab=mine&user=ZN").json()
    assert all(i["created_by"] == "ZN" for i in mine["items"]) and mine["total"] == c["mine"]
    # mine 没传人：一条都不给，不回落成全部
    assert client.get("/api/simulations?tab=mine").json()["total"] == 0
    assert client.get("/api/simulations?tab=mine&user=XX").status_code == 422


def test_draft_lifecycle():
    r = client.post("/api/simulations", json={**DRAFT, "resource_ids": _line_ids("line1")})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "draft" and d["rain_label"] == "61 mm in 2 h"
    assert d["depth_mm"] == 61.3                        # 设计基准格取实际雨深
    assert d["model_start_utc"].endswith("+04:00")      # 按本地时间返回：UTC 00:00 = +04 04:00
    assert d["model_start_utc"].startswith("2026-09-28T04:00")
    assert len(d["resource_ids"]) == 8 and d["created_by"] == "ZN"
    assert d["est_run_count"] == 1

    rid = d["id"]
    assert rid in [i["id"] for i in client.get("/api/simulations?tab=drafts").json()["items"]]
    assert rid not in [i["id"] for i in client.get("/api/simulations").json()["items"]]

    u = client.put(f"/api/simulations/{rid}", json={**DRAFT, "storm_preset_key": None,
                                                     "depth_mm": 50, "duration_h": 3})
    assert u.status_code == 200 and u.json()["rain_label"] == "50 mm in 3 h"
    assert u.json()["resource_ids"] == []

    s = client.post(f"/api/simulations/{rid}/submit").json()
    assert s["status"] == "queued" and s["queued_ids"] == [rid]
    # 提交后不能再改；排队中也不能删（要先取消）
    assert client.put(f"/api/simulations/{rid}", json=DRAFT).status_code == 409
    assert client.delete(f"/api/simulations/{rid}").status_code == 409
    assert client.post(f"/api/simulations/{rid}/cancel").json()["status"] == "cancelled"
    assert client.delete(f"/api/simulations/{rid}").status_code == 204
    assert client.get(f"/api/simulations/{rid}").status_code == 404


def test_queue_all_three_makes_a_batch(rolled_back):
    r = client.post("/api/simulations",
                    json={**DRAFT, "queue_all_lines": True, "resource_ids": _line_ids("line1")[:3],
                          "submit": True})
    assert r.status_code == 201, r.text
    ids = r.json()["queued_ids"]
    assert len(ids) == 3
    runs = [client.get(f"/api/simulations/{i}").json() for i in ids]
    assert len({x["batch_id"] for x in runs}) == 1 and runs[0]["batch_id"]
    by_line = {x["defence_line_key"]: x for x in runs}
    assert set(by_line) == set(sim.LINE_ORDER)
    l1, l2c, l2p = (set(by_line[k]["resource_ids"]) for k in sim.LINE_ORDER)
    assert len(l1) == 3                                 # 第一道只勾了三项
    assert l2c == l1 | set(_line_ids("line2_current"))  # 第二道没动过的卡按整道全选、并累加第一道
    assert l2p == l2c | set(_line_ids("line2_planned"))
    assert all(x["rain_label"] == "61 mm in 2 h" and x["created_by"] == "ZN" for x in runs)
    # 防线口径落库（用户 2026-09-28 要求），不是前端拼的
    stored = {r["id"]: r["defence_basis"] for r in rolled_back.execute(
        "select id, defence_basis from run where id = any(%s)", [ids]).fetchall()}
    assert {k: stored[by_line[k]["id"]] for k in sim.LINE_ORDER} == \
        {"line1": "Line1", "line2_current": "Line1 ｜ Line2", "line2_planned": "Line1 ｜ Line2 ｜ Line2 planned"}
    assert all(x["defence_basis"] == stored[x["id"]] for x in runs)
    # 详情页的防线切换：同批三个任务互相指向，按 LINE_ORDER 排
    for x in runs:
        assert [(lr["defence_line_key"], lr["run_id"]) for lr in x["line_runs"]] == \
            [(k, by_line[k]["id"]) for k in sim.LINE_ORDER]


def test_validation_errors():
    beyond = client.post("/api/simulations?lang=en",
                         json={**DRAFT, "resource_ids": _line_ids("line2_current")})
    assert beyond.status_code == 422 and "Line 1" in beyond.json()["detail"]
    assert client.post("/api/simulations", json={**DRAFT, "rain_source": "ncm_forecast"}).status_code == 422
    assert client.post("/api/simulations", json={**DRAFT, "storm_preset_key": None}).status_code == 422
    assert client.post("/api/simulations", json={**DRAFT, "rain_source": "observed_replay",
                                                 "storm_preset_key": None}).status_code == 422
    assert client.post("/api/simulations", json={**DRAFT, "storm_preset_key": "7mm_9h"}).status_code == 422
    assert client.get("/api/simulations/999999999").status_code == 404


def test_status_flow_and_analyses(rolled_back):
    rid = client.post("/api/simulations", json={**DRAFT, "submit": True}).json()["id"]
    assert client.patch(f"/api/simulations/{rid}/status", json={"status": "done"}).status_code == 409
    run = client.patch(f"/api/simulations/{rid}/status", json={"status": "running", "progress_pct": 10}).json()
    assert run["status"] == "running" and run["started_at"]
    assert client.post(f"/api/simulations/{rid}/analyses", json={"kind": "validation"}).status_code == 409
    done = client.patch(f"/api/simulations/{rid}/status", json={"status": "done"}).json()
    assert done["progress_pct"] == 100 and done["ended_at"] and not done["ended_is_estimate"]
    for body in ({"kind": "validation"}, {"kind": "validation"}, {"kind": "gap", "gap_hours": 4}):
        assert client.post(f"/api/simulations/{rid}/analyses", json=body).status_code == 201
    kinds = [(a["kind"], a["gap_hours"]) for a in client.get(f"/api/simulations/{rid}").json()["analyses"]]
    assert kinds == [("validation", None), ("gap", 4)]  # Validation 重复追加只记一次
    assert client.post(f"/api/simulations/{rid}/analyses", json={"kind": "gap"}).status_code == 422
    assert client.post(f"/api/simulations/{rid}/cancel").status_code == 409
    # 已完成的可以从任务历史里删掉，分析随外键级联
    assert client.delete(f"/api/simulations/{rid}").status_code == 204
    assert client.get(f"/api/simulations/{rid}").status_code == 404
    left = rolled_back.execute("select count(*) as n from run_analysis where run_id = %s", [rid]).fetchone()
    assert left["n"] == 0


def test_running_run_cannot_be_deleted():
    rid = client.post("/api/simulations", json={**DRAFT, "submit": True}).json()["id"]
    client.patch(f"/api/simulations/{rid}/status", json={"status": "running"})
    r = client.delete(f"/api/simulations/{rid}")
    assert r.status_code == 409 and r.json()["detail"] == sim.NOT_DELETABLE


def test_ar_runs_cannot_be_deleted():
    """AR 名下是交付成果：已完成也不许删，后端 403，行还在。"""
    rid = client.post("/api/simulations", json={**DRAFT, "created_by": "AR", "submit": True}).json()["id"]
    client.patch(f"/api/simulations/{rid}/status", json={"status": "done"})
    r = client.delete(f"/api/simulations/{rid}")
    assert r.status_code == 403 and r.json()["detail"] == sim.PROTECTED_RUN
    assert client.get(f"/api/simulations/{rid}").status_code == 200
    assert client.delete(f"/api/simulations/{rid}?lang=en").json()["detail"] == "Deliverables owned by AR cannot be deleted"


def test_estimate_uses_same_extent_and_grid_only():
    e = client.post("/api/simulations/estimate", json=DRAFT).json()
    assert e["basis_runs"] >= 1 and e["est_runtime_h"] is not None
    none = client.post("/api/simulations/estimate", json={**DRAFT, "grid_m": 7}).json()
    assert none["basis_runs"] == 0 and none["est_runtime_h"] is None and none["est_ready_at"] is None
    assert client.post("/api/simulations/estimate",
                       json={**DRAFT, "queue_all_lines": True}).json()["est_run_count"] == 3


def test_error_messages_have_english():
    for msg in (*sim.MESSAGES, *sim_results.MESSAGES):
        assert tr(msg, "en") != msg, msg


def _done_run(line="line1", preset="61mm_2h", still=None):
    rid = client.post("/api/simulations", json={**DRAFT, "storm_preset_key": preset,
                                                 "defence_line_key": line, "submit": True}).json()["id"]
    client.patch(f"/api/simulations/{rid}/status", json={"status": "running"})
    client.patch(f"/api/simulations/{rid}/status", json={"status": "done"})
    if still is not None:
        assert client.put(f"/api/simulations/{rid}/result", json={"still_pond_count": still}).status_code == 200
    return rid


def test_summary_same_storm_and_new_results():
    before = client.get("/api/simulations/summary").json()
    a = _done_run("line1", still=212)
    b = _done_run("line2_current", still=141)
    s = client.get("/api/simulations/summary").json()
    assert s["runs"]["done"] == before["runs"]["done"] + 2
    storm = next(x for x in s["same_storm"] if x["storm_preset_key"] == "61mm_2h")
    counts = {ln["defence_line_key"]: ln["still_pond_count"] for ln in storm["lines"]}
    assert counts["line1"] == 212 and counts["line2_current"] == 141
    new = {i["id"]: i for i in s["new_results"]["items"]}
    assert new[b]["result"]["still_pond_count"] == 141
    assert new[b]["baseline_still_pond"] == 212       # 与同雨型上一道防线比
    # 点开之后就不再算「新结果」
    assert client.post(f"/api/simulations/{b}/open").json()["opened_at"]
    after = client.get("/api/simulations/summary").json()["new_results"]
    assert after["count"] == s["new_results"]["count"] - 1
    assert a in [i["id"] for i in after["items"]] or after["count"] > len(after["items"])


def test_queue_is_serial_after_the_running_run():
    ids = [client.post("/api/simulations", json={**DRAFT, "submit": True}).json()["id"] for _ in range(2)]
    q = client.get("/api/simulations/summary").json()["queue"]
    mine = [x for x in q if x["id"] in ids]
    assert [x["status"] for x in mine] == ["queued", "queued"]
    assert mine[1]["start"] == mine[0]["end"]         # 同一对 H100 上串行
    assert q[0]["status"] == "running" or all(x["status"] == "queued" for x in q)


def test_result_needs_a_finished_run():
    rid = client.post("/api/simulations", json=DRAFT).json()["id"]
    assert client.put(f"/api/simulations/{rid}/result", json={"still_pond_count": 3}).status_code == 409


DELIVERY = sim_results.DATA_ROOT / "temp" / "abu-dhabi-citywide-coupled-33mm-2h-250m-one-way-recession-validation"


def test_result_layers(rolled_back):
    """交付目录登记成已完成任务，详情页的三个图层接口对得上：帧里的格子都在最大水深图层里。"""
    if not DELIVERY.is_dir():
        pytest.skip("交付目录不在（data/ 不进代码库）")
    rid = sim_results.import_delivery(rolled_back, DELIVERY)
    assert sim_results.import_delivery(rolled_back, DELIVERY) == rid          # 重复登记不新增
    run = client.get(f"/api/simulations/{rid}").json()
    assert run["status"] == "done" and run["has_layers"] and "output_dir" not in run
    assert run["rain_label"] == "33 mm in 2 h" and run["result"]["overflow_node_count"] > 0

    meta = client.get(f"/api/simulations/{rid}/layers").json()
    assert len(meta["frames"]) == meta["coupling"]["window_count"]
    geo = client.get(f"/api/simulations/{rid}/layers/max-depth").json()
    ids = {f["properties"]["cell_id"] for f in geo["features"]}
    assert len(ids) == meta["cell_count"]
    fr = client.get(f"/api/simulations/{rid}/layers/frames/33").json()
    assert len(fr["cells"]) == len(fr["depth_m"]) > 0 and set(fr["cells"]) <= ids

    assert client.get(f"/api/simulations/{rid}/layers/frames/99999").status_code == 404
    other = client.post("/api/simulations", json=DRAFT).json()["id"]
    assert client.get(f"/api/simulations/{other}/layers").status_code == 404


DELIVERY_V2 = sim_results.DATA_ROOT / "temp" / "abu-dhabi-citywide-coupled-61mm-2h-250m-one-way-land-masked-74h"


def test_result_layers_v2_delivery(rolled_back):
    """v2 交付（摘要没有 model_configuration、.sww 写的是算力机绝对路径）归一后照样能登记、落图，缺窗口要标出来。"""
    if not DELIVERY_V2.is_dir():
        pytest.skip("交付目录不在（data/ 不进代码库）")
    rid = sim_results.import_delivery(rolled_back, DELIVERY_V2)
    run = client.get(f"/api/simulations/{rid}").json()
    assert run["rain_label"] == "61 mm in 2 h" and run["storm_preset_key"] == "61mm_2h"
    assert run["result"]["overflow_node_count"] > 0

    meta = client.get(f"/api/simulations/{rid}/layers").json()
    assert meta["partial"] is True
    assert meta["delivery"]["completed_window_count"] == len(meta["frames"]) < meta["delivery"]["expected_window_count"]
    assert meta["model_configuration"]["rainfall_duration_minutes"] == 120
    assert meta["model_configuration"]["surface_rainfall_inflow_m3"] == 0     # 回执为准：地表不受雨
    assert meta["coupling"]["total_swmm_to_anuga_m3"] > 0
    assert meta["domain"]["simulation_duration_hours"] > 70
