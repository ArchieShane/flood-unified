"""积水点生命周期层：事件目录（配置 + 客户文件 + 开事件日志）、经历合成（客户记录 / 观测 / 993 / 模型推演）、阶段与对账、复发率、规则、七个动作。假后端，不读库。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from swbrain import lifecycle as LC, feeds as F
from swbrain.ontology import schema as S, runtime as R
from tests.test_ontology import Fake


@pytest.fixture
def rt(tmp_path, monkeypatch):
    import swbrain.scenarios as sc
    cat = [{"key": "10", "kind": "rp", "label": "10 年", "label_en": "10-yr", "i15_mmh": 57.7, "p24_mm": 51.1}, {"key": "50", "kind": "rp", "label": "50 年", "label_en": "50-yr", "i15_mmh": 118.2, "p24_mm": 81.8},
           {"key": "2024-04", "kind": "event", "label": "2024-04 实测", "label_en": "Apr 2024 observed", "i15_mmh": 100.0, "p24_mm": 94.0}, {"key": "2026-03", "kind": "event", "label": "2026-03 实测", "label_en": "Mar 2026 observed", "i15_mmh": 80.0, "p24_mm": 72.0}]
    monkeypatch.setattr(sc, "keys", lambda cfg: [c["key"] for c in cat])
    monkeypatch.setattr(sc, "catalog", lambda cfg: cat)
    monkeypatch.setattr(sc, "get", lambda cfg, k: next(c for c in cat if c["key"] == str(k)))
    monkeypatch.setattr(R, "ROOT", tmp_path); monkeypatch.setattr(LC, "ROOT", tmp_path); monkeypatch.setattr(F, "ROOT", tmp_path)
    cfg = json.loads(json.dumps(Fake.cfg))
    cfg["scenarios"] = {"events": {"2024-04": {"label": "2024-04 实测", "label_en": "Apr 2024 observed", "date": "2024-04-16", "p24_default_mm": 94.0, "base_rp": 10},
                                   "2026-03": {"label": "2026-03 实测", "label_en": "Mar 2026 observed", "date": "2026-03", "p24_default_mm": 72.0, "base_rp": 10}}}
    cfg["feeds"] = {n: {"file": str(tmp_path / f"{n}.json"), "url": ""} for n in F.NAMES}
    cfg["lifecycle"] = {"events_file": str(tmp_path / "events.jsonl"), "episodes_file": str(tmp_path / "episodes.jsonl"), "proposal_status_file": str(tmp_path / "pstatus.jsonl"), "min_observed_for_verification": 2}
    cfg["verification"] = {"pass_pod_min": 0.8, "pass_far_max": 0.3, "pass_depth_rmse_m": 0.1, "recalibrate_after_failures": 2}
    cfg["agent"] = {"observations_file": str(tmp_path / "obs.jsonl"), "action_log": str(tmp_path / "actions.jsonl")}
    F._HTTP_CACHE.clear()
    b = Fake(); b.calls = []
    return R.Runtime(b, cfg)


def test_events_catalogue_and_windows(rt):
    evs = rt.load_events()
    assert set(evs) == {"2024-04", "2026-03"} and evs["2024-04"]["start"] == "2024-04-13" and evs["2024-04"]["end"] == "2024-04-19" and evs["2026-03"]["start"] == "2026-03-01" and evs["2026-03"]["end"] == "2026-03-31"
    assert evs["2024-04"]["kind"] == "historical" and evs["2024-04"]["scenario_key"] == "2024-04" and evs["2024-04"]["rp_equiv_yr"] > 50   # 94 mm 超过表里 50 年的 81.8
    assert rt.event_key("2024年4月") == "2024-04" and rt.event_key("April 2024") == "2024-04" and rt.event_key(None) == "2026-03"
    with pytest.raises(R.ActionError):
        rt.event_key("2031-01")
    # 客户补一场事件
    F.push(rt.cfg, "events", {"rows": [{"event_id": "2019-11", "date": "2019-11-10", "p24_mm": 58.0, "scenario_key": "10", "label": "2019-11"}]})
    rt._invalidate_lifecycle()
    assert "2019-11" in rt.load_events() and rt.load_events()["2019-11"]["origin"] == "client"


def test_episodes_merge_sources_and_agreement(rt):
    # 模型：Fake 的每个情景都给 ADM-1 一个有水状态 → 两场历史雨都「推演积水」；ADM-2 没状态且没管网 → 模型没意见
    eps = rt.load_episodes()
    e1 = eps["ADM-1@2024-04"]
    assert e1["model_flooded"] is True and e1["observed_flooded"] is None and e1["status"] == "estimated" and e1["agreement"] == "unknown" and e1["source"] == "system_estimate"
    assert "ADM-2@2024-04" not in eps
    h = rt.point_lifecycle("ADM-1")
    assert h["n_events"] == 2 and h["n_observed"] == 0 and h["recurrence"] is None and h["recurrence_model"] == "2/2" and h["lifecycle_status"] == "idle"
    fp = rt.get("FloodPoint", "ADM-1")
    assert fp["n_events"] == 2 and fp["recurrence_model"] == "2/2" and fp["lifecycle_status"] == "idle" and fp["treatment_status"] == "proposed"      # Fake 有一条「建议」状态的建议挂在 E1
    # 客户记录：2024-04 积水 0.5 m、派 3 台；2026-03 无积水 → 一命中一误报
    F.push(rt.cfg, "episodes", {"rows": [{"id": "a", "fp_id": "ADM-1", "event_id": "2024-04", "flooded": True, "depth_m": 0.5, "units": 3, "source": "inspection"},
                                          {"id": "b", "fp_id": "ADM-1", "event_id": "2024-04", "stage": "reported", "at": "2024-04-16 09:00:00", "source": "993"},
                                          {"id": "c", "fp_id": "ADM-1", "event_id": "2026-03", "flooded": False, "source": "inspection"}]})
    rt._invalidate_lifecycle()
    eps = rt.load_episodes(); e1 = eps["ADM-1@2024-04"]; e2 = eps["ADM-1@2026-03"]
    assert e1["observed_flooded"] is True and e1["obs_depth_max_m"] == 0.5 and e1["units_sent"] == 3 and e1["agreement"] == "hit" and e1["status"] == "verified" and e1["stages"]["reported"] == "2024-04-16 09:00:00" and e1["source"] == "client_record"
    assert e2["observed_flooded"] is False and e2["agreement"] == "false_alarm" and e2["status"] == "none"
    h = rt.point_lifecycle("ADM-1")
    assert h["recurrence"] == "1/2" and h["last_flooded"] == "2024-04-16"
    rules = {x["rule"]: x for x in rt.rules("FloodPoint", rt.get("FloodPoint", "ADM-1"), "10")}
    assert rules["recurrence"]["value"] == "1/2" and rules["recurrence"]["consistent"] is True
    assert rules["history_vs_model"]["fired"] and "2026-03" in rules["history_vs_model"]["value"]["events"]
    assert not rules["repeat_offender"]["fired"]                                    # 2026-03 记录无积水 → 不是每场都积
    # 现场观测落进窗口：2026-03 里一条 flooding 观测 → 记录改为积水（观测胜过「无记录」）
    (Path(rt.cfg["agent"]["observations_file"])).write_text(json.dumps({"obs_id": "o1", "fp_id": "ADM-1", "observed_at": "2026-03-12 10:00:00", "depth_m": 0.3, "status": "flooding"}) + "\n", encoding="utf-8")
    rt.invalidate("obs"); rt._invalidate_lifecycle()
    e2 = rt.load_episodes()["ADM-1@2026-03"]
    assert e2["observed_flooded"] is True and e2["agreement"] == "hit" and "observation" in e2["sources"] and e2["stages"]["verified"] == "2026-03-12 10:00:00"
    rules = {x["rule"]: x for x in rt.rules("FloodPoint", rt.get("FloodPoint", "ADM-1"), "10")}
    assert rules["repeat_offender"]["fired"] and rules["repeat_offender"]["value"]["events"] == ["2024-04", "2026-03"]


def test_event_review_and_verification(rt):
    F.push(rt.cfg, "episodes", {"rows": [{"id": "a", "fp_id": "ADM-1", "event_id": "2024-04", "flooded": True, "depth_m": 0.6}]})
    rt._invalidate_lifecycle()
    ev = rt.get("RainEvent", "2024年4月")
    assert ev["event_id"] == "2024-04" and ev["n_observed_flooded"] == 1 and ev["n_model_flooded"] == 1 and ev["verification"] == "insufficient"   # 只有 1 个有记录，阈值 2
    r = rt.act("event_review", {"event_id": "2024-04"}, lang="zh")
    assert r["ok"] and r["result"]["verification"]["hits"] == 1 and r["result"]["by_status"] == {"verified": 1} and "insufficient" in r["summary"]
    # 再给一条记录 → 两个有记录（都命中）→ pass
    F.push(rt.cfg, "episodes", {"rows": [{"id": "b", "fp_id": "ADM-1", "event_id": "2024-04", "flooded": True, "depth_m": 0.66}]})
    rt._invalidate_lifecycle()
    ev = rt.get("RainEvent", "2024-04")
    assert ev["verification"] == "insufficient"                                    # 同一个点的两条记录合成一个经历，仍是 1 个
    links = rt.links("RainEvent", "2024-04", "event_episodes")
    assert len(links) == 1 and links[0]["fp_id"] == "ADM-1"
    assert rt.links("FloodEpisode", "ADM-1@2024-04", "ep_state")[0]["fp_id"] == "ADM-1" and rt.links("FloodEpisode", "ADM-1@2024-04", "ep_event")[0]["event_id"] == "2024-04"
    assert rt.links("Scenario", "2024-04", "scenario_event")[0]["event_id"] == "2024-04"
    rows = rt.search("FloodEpisode", {"event_id": "2024-04"})
    assert len(rows) == 1 and rows[0]["_type"] == "FloodEpisode"


def test_open_event_log_close_and_proposal_status(rt):
    r = rt.act("open_event", {"label": "9 月 25 日预报强降雨", "date": "2026-09-25", "p24_mm": 60}, lang="zh")
    assert r["ok"]; ev = r["result"]["event"]
    assert ev["event_id"] == "EV-20260925" and ev["kind"] == "forecast" and ev["scenario_key"] == "10" and ev["alert_level"] in ("黄", "蓝", "橙") and r["result"]["n_alerted"] == 1   # 60 mm 最贴近 10 年 51.1 而不是 50 年 81.8
    fp = rt.get("FloodPoint", "ADM-1")
    assert fp["lifecycle_status"] == "alerted"
    assert rt.event_key(None) == "EV-20260925"                                      # 缺省 = 开着的事件
    # 登记：上报 → 到位（2 台）→ 退干；关闭前置条件
    assert not rt.act("close_point", {"fp_id": "ADM-1"}, lang="zh")["ok"]
    r = rt.act("update_episode", {"fp_id": "ADM-1", "stage": "reported", "at": "2026-09-25 08:10:00"}, lang="zh"); assert r["ok"] and r["result"]["episode"]["status"] == "reported"
    r = rt.act("update_episode", {"fp_id": "ADM-1", "stage": "arrived", "at": "2026-09-25 09:40:00", "units": 2, "depth_m": 0.35}, lang="zh"); assert r["result"]["episode"]["status"] == "arrived" and r["result"]["episode"]["units_sent"] == 2
    assert not rt.act("close_point", {"fp_id": "ADM-1"}, lang="zh")["ok"]
    r = rt.act("update_episode", {"fp_id": "ADM-1", "stage": "dry", "at": "2026-09-25 15:00:00"}, lang="zh")
    assert r["result"]["episode"]["status"] == "dry" and any(x["rule"] == "ready_to_close" for x in r["result"]["rules_fired"])
    r = rt.act("close_point", {"fp_id": "ADM-1", "note": "现场复核"}, lang="zh")
    assert r["ok"] and r["result"]["episode"]["status"] == "closed" and r["result"]["was"] == "dry"
    assert not rt.act("close_point", {"fp_id": "ADM-1"}, lang="zh")["ok"]
    ov = rt.act("lifecycle_overview", {}, lang="zh")["result"]
    assert ov["event"]["event_id"] == "EV-20260925" and ov["n_closed"] == 1 and ov["n_active"] == 0 and [x["status"] for x in ov["funnel"]] == ["closed"]
    ph = rt.act("point_history", {"fp_id": "ADM-1"}, lang="zh")
    assert ph["ok"] and ph["result"]["history"]["current"]["status"] == "closed" and "ADM-1" in ph["summary"] and "推演估计" in ph["summary"]
    # 工程建议推进：建议 12 → 已建成 → 治理状态 built；治理是否见效 = 待验证
    r = rt.act("set_proposal_status", {"pid": 12, "status": "已建成", "note": "2026-09 完工"}, lang="zh")
    assert r["ok"] and r["result"]["proposal"]["status"] == "已建成" and r["result"]["was"] == "建议"
    fp = rt.get("FloodPoint", "ADM-1"); assert fp["treatment_status"] == "built"
    rules = {x["rule"]: x for x in rt.rules("FloodPoint", fp, "10")}
    assert rules["treatment_status"]["value"] == "built" and rules["treatment_effective"]["fired"] and "等下一场雨" in rules["treatment_effective"]["value"]["why"]
    # 历史事件上不允许无 event_id 地登记
    assert not rt.act("update_episode", {"fp_id": "ADM-1", "stage": "dry", "event_id": "2031-01"}, lang="zh")["ok"]


def test_feeds_validate_events_and_episodes(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "ROOT", tmp_path)
    cfg = {"feeds": {n: {"file": str(tmp_path / f"{n}.json"), "url": ""} for n in F.NAMES}}
    r = F.push(cfg, "episodes", {"rows": [{"id": "1", "fp_id": "ADM-62", "event_id": "2024-04", "flooded": True}, {"id": "2", "fp_id": "nope", "event_id": "2024-04"}, {"id": "3", "fp_id": "ADM-62", "event_id": "2024-04", "stage": "flying"}, {"id": "4", "fp_id": "ADM-62"}]})
    assert r["accepted"] == 1 and r["n_rejected"] == 3
    r2 = F.push(cfg, "events", {"rows": [{"event_id": "2019-11", "date": "2019-11-10"}, {"event_id": "x", "date": "Nov 2019"}, {"date": "2019-11-10"}]})
    assert r2["accepted"] == 1 and r2["n_rejected"] == 2


def test_schema_has_lifecycle_layer():
    assert S.check() == []
    assert {"RainEvent", "FloodEpisode"} <= set(S.OT) and {"episodes", "ep_point", "ep_event", "ep_state", "event_episodes", "event_scenario", "scenario_event"} <= set(S.LT)
    assert {"episode_status", "episode_agreement", "recurrence", "lifecycle_status", "treatment_status", "event_verification", "history_vs_model", "repeat_offender", "ready_to_close", "treatment_effective"} <= set(S.RL)
    acts = {a.api for a in S.ACTIONS}
    assert {"point_history", "event_review", "lifecycle_overview", "update_episode", "close_point", "set_proposal_status", "open_event"} <= acts
    assert S.OT["FloodPoint"].prop("recurrence").derived_by == "recurrence" and S.OT["FloodPoint"].prop("lifecycle_status").derived_by == "lifecycle_status"


def test_area_reports_mark_points_without_claiming_records(rt):
    """公开报道点名 Al Shahama → ADM-1（Al Shahama Tunnel）在该事件里 area_reported，但 observed_flooded 仍是 None、对账仍是 unknown。"""
    F.push(rt.cfg, "events", {"rows": [{"event_id": "2024-04", "date": "2024-04-16", "impacted_areas": ["Al Shahama", "Khalifa City"]}]})
    rt._invalidate_lifecycle()
    e = rt.load_episodes()["ADM-1@2024-04"]
    assert e["area_reported"] and e["area_report_names"] == ["Al Shahama"] and "area_report" in e["sources"] and e["observed_flooded"] is None and e["agreement"] == "unknown" and e["source"] == "system_estimate"
    h = rt.point_lifecycle("ADM-1")
    assert h["n_area_reported"] == 1 and next(x for x in h["history"] if x["event_id"] == "2024-04")["area_reported"]
    r = rt.act("point_history", {"fp_id": "ADM-1"}, lang="zh")
    assert "片区（Al Shahama）有公开报道积水" in r["summary"]
    # 没映射情景又只有片区报道的事件：状态 unknown、不算 none
    F.push(rt.cfg, "events", {"rows": [{"event_id": "2019-11", "date": "2019-11-20", "impacted_areas": ["Al Shahama"]}]})
    rt._invalidate_lifecycle()
    e2 = rt.load_episodes().get("ADM-1@2019-11")
    assert e2 is not None and e2["status"] == "unknown" and e2["model_flooded"] is None and e2["source"] == "area_report"
    bad = F.push(rt.cfg, "events", {"rows": [{"event_id": "2030-01", "date": "2030-01-01", "impacted_areas": "Al Shahama"}]})
    assert bad["accepted"] == 0


def test_reflooding_after_dry_blocks_close(rt):
    """一场雨里退了又涨：状态不能停在「已退干」，也不许关闭。

    2026-09-23 评审实测：derive_status 取的是阶段序的最大值、完全不看时间戳，
    10:00 记一条已退干、18:00 记一条正在积水 0.6 m，系统仍判「已退干」，
    ready_to_close 触发，close_point 返回 ok=True——把一个现场正在积水的点关掉了。
    """
    assert rt.act("open_event", {"label": "二次积水演练", "date": "2026-09-25", "p24_mm": 60}, lang="zh")["ok"]
    EP = "ADM-1@EV-20260925"
    rt.act("update_episode", {"fp_id": "ADM-1", "stage": "reported", "at": "2026-09-25 08:10:00"}, lang="zh")
    r = rt.act("update_episode", {"fp_id": "ADM-1", "stage": "dry", "at": "2026-09-25 10:00:00"}, lang="zh")
    assert r["ok"], r.get("error")
    assert r["result"]["episode"]["status"] == "dry"
    assert any(x["rule"] == "ready_to_close" for x in r["result"]["rules_fired"])      # 这时候可以关

    # 18:00 现场又涨起来了（record_observation 的时间戳只能是「现在」，这里按现场登记的口径直接写观测文件）
    import json as _json
    rt.obs_path.parent.mkdir(parents=True, exist_ok=True)
    with rt.obs_path.open("a", encoding="utf-8") as f:
        f.write(_json.dumps({"obs_id": "OBS-X", "fp_id": "ADM-1", "observed_at": "2026-09-25 18:00:00",
                             "depth_m": 0.6, "status": "flooding", "reporter": "现场"}, ensure_ascii=False) + "\n")
    rt.invalidate("obs"); rt._invalidate_lifecycle()
    ep = rt.load_episodes()[EP]
    assert ep["reflooded"] is True
    assert ep["status"] != "dry", f"退干后又涨回来了，状态却还是 {ep['status']}"
    assert ep["status"] in ("reported", "verified")
    fired = [x["rule"] for x in rt.rules("FloodEpisode", ep, None) if x.get("fired")]
    assert "ready_to_close" not in fired, "还在积水就建议关闭"
    assert not rt.act("close_point", {"fp_id": "ADM-1"}, lang="zh")["ok"], "把正在积水的点关掉了"

    # 再退干一次，才又能关
    rt.act("update_episode", {"fp_id": "ADM-1", "stage": "dry", "at": "2026-09-25 21:30:00"}, lang="zh")
    ep = rt.load_episodes()[EP]
    assert ep["reflooded"] is False and ep["status"] == "dry"
    assert rt.act("close_point", {"fp_id": "ADM-1", "note": "二次退干后复核"}, lang="zh")["ok"]


def test_normal_forward_progression_unchanged(rt):
    """没有二次积水时，行为和以前一样：阶段一路往前，dry 之后可以关。"""
    assert rt.act("open_event", {"label": "正常推进", "date": "2026-09-25", "p24_mm": 60}, lang="zh")["ok"]
    for stage, at in (("reported", "2026-09-25 08:00:00"), ("dispatched", "2026-09-25 08:30:00"),
                      ("arrived", "2026-09-25 09:20:00"), ("pumping", "2026-09-25 09:40:00"), ("dry", "2026-09-25 14:00:00")):
        r = rt.act("update_episode", {"fp_id": "ADM-1", "stage": stage, "at": at}, lang="zh")
        assert r["ok"], r.get("error")
        assert r["result"]["episode"]["status"] == stage, (stage, r["result"]["episode"]["status"])
    ep = rt.load_episodes()["ADM-1@EV-20260925"]
    assert ep["reflooded"] is False
    assert rt.act("close_point", {"fp_id": "ADM-1"}, lang="zh")["ok"]


def test_dry_then_993_report_also_reopens(rt):
    """退干之后来一条 993 报案（有水的证据），同样不能再判已退干。"""
    assert rt.act("open_event", {"label": "993 复涨", "date": "2026-09-25", "p24_mm": 60}, lang="zh")["ok"]
    rt.act("update_episode", {"fp_id": "ADM-1", "stage": "dry", "at": "2026-09-25 11:00:00"}, lang="zh")
    F.push(rt.cfg, "incidents", {"rows": [{"fp_id": "ADM-1", "at": "2026-09-25 16:20:00", "verified": True, "lon": 54.5, "lat": 24.4}]})
    rt._invalidate_lifecycle()
    ep = rt.load_episodes()["ADM-1@EV-20260925"]
    assert ep["reflooded"] is True and ep["status"] != "dry"
    assert not rt.act("close_point", {"fp_id": "ADM-1"}, lang="zh")["ok"]
