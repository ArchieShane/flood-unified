"""多智能体（swbrain/agents，2026-09-30）：注册表、主智能体分派、专业智能体规划、计算申请队列、管理端口令、工具隔离。

锁住的行为：
  · 分派要命中「领域词」：「检查井」「调度结果」「C1 模型版由哪些汇水区组成」「Mussafah L2+ 还要补什么」都留在调度研判；
  · 计算只能申请：submit 只写 pending，批准（approve）才起执行进程；状态机不允许越级；
  · 管理员口令与访问口令分开：没设 admin_key 管理端关闭（403），输错 5 次锁；
  · DeepSeek Harness（MCP）与调度研判的规划器看不到别的智能体的动作；管理端停用的动作被拒。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pytest

from swbrain.agents import admin_routes as ADM, computejobs as CJ, orchestrator as ORC, registry as REG
from swbrain.ontology import schema as S


@pytest.fixture()
def tmp_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(REG, "OVR", tmp_path / "overrides.json")
    monkeypatch.setattr(CJ, "DIR", tmp_path / "jobs")
    monkeypatch.setattr(CJ, "external_runs", lambda: [])                            # 不去问本机 docker
    monkeypatch.setattr(ORC, "LOG", tmp_path / "sessions.jsonl")
    monkeypatch.setattr(ADM, "AUDIT", tmp_path / "audit.jsonl")
    return tmp_path


# ---------------------------------------------------------------- 注册表
def test_every_action_has_one_known_owner():
    owners = {getattr(A, "owner", "dispatch") for A in S.ACTIONS}
    assert owners <= set(REG.AGENTS) - {"orchestrator"}
    for aid in ("hydro", "report", "qa"):
        assert REG.actions_of(aid), aid
    assert "hydro_submit" in REG.actions_of("hydro") and "qa_results" in REG.actions_of("qa") and "delivery_status" in REG.actions_of("report")


def test_override_roundtrip(tmp_agents):
    e = REG.set_override("qa", {"enabled": False, "disabled_actions": ["qa_data", "不存在的动作"], "keywords": ["质检", " ", "对账"]})
    assert e["enabled"] is False and e["keywords"] == ["质检", "对账"]
    assert REG.allowed_actions("qa") == {"qa_results"}
    with pytest.raises(ValueError):
        REG.set_override("orchestrator", {"enabled": False})
    assert REG.reset("qa")["enabled"] is True and REG.allowed_actions("qa") == set(REG.actions_of("qa"))


# ---------------------------------------------------------------- 分派与规划
CASES = [
    ("ADM-62 在 50 年一遇下派几台泵", ["dispatch"]),
    ("ADM-62 附近的检查井", ["dispatch"]),
    ("C1 模型版由哪些汇水区组成？", ["dispatch"]),
    ("ADM-94 在哪个汇水区？", ["dispatch"]),
    ("Mussafah L2+ 还要补什么？", ["dispatch"]),
    ("箱涵是什么？", ["dispatch"]),
    ("模型版四个态算完了吗？", ["hydro"]),
    ("现在一二维算到哪了，还要多久", ["hydro"]),
    ("模型版的交付目录在哪？", ["report"]),
    ("换台机器能复现 C1 吗？缺什么？", ["qa"]),
    ("模型版算完了吗？质检过了没有？", ["hydro", "qa"]),
    ("模型版四个态算完了吗？质检过了没有？", ["hydro", "qa"]),
    ("R61 v3 结果质检过了吗？有没有副作用", ["qa"]),
]


@pytest.mark.parametrize("q,want", CASES)
def test_route(tmp_agents, q, want):
    got = [r["agent"] for r in ORC.MultiAgent(None, None).route(q)]
    assert got == want, (q, got)


def test_disabled_agent_not_routed(tmp_agents):
    REG.set_override("hydro", {"enabled": False})
    assert [r["agent"] for r in ORC.MultiAgent(None, None).route("模型版四个态算完了吗？")] == ["dispatch"]


def test_specialist_plans(tmp_agents):
    M = ORC.MultiAgent(None, None)
    assert M.plan("hydro", "申请重算模型版 L1 L2 L2+planned") == [("hydro_submit", {"tag": "M61", "states": "L1,L2,L2P", "then_v3": False, "reason": "申请重算模型版 L1 L2 L2+planned"})]
    assert M.plan("hydro", "模型版 L2+planned 比 L1 积水减了多少？")[0] == ("hydro_result", {"tag": "M61"})         # 问对比：全部态都给
    assert M.plan("hydro", "模型版 L2+planned 的积水面积")[0] == ("hydro_result", {"tag": "M61", "state": "L2P"})
    assert M.plan("hydro", "现在一二维算到哪了")[0][0] == "hydro_progress"
    assert M.plan("hydro", "申请重算一下")[0][0] == "_ask"                        # 没说情景不提交
    assert M.plan("hydro", "申请跑一次快速验证") == [("hydro_submit", {"kind": "selftest", "reason": "申请跑一次快速验证"})]   # 快速验证不要情景
    assert M.plan("hydro", "快速验证过了吗")[0][0] == "hydro_progress"
    assert [r["agent"] for r in M.route("申请跑一次快速验证")] == ["hydro"]
    assert M.plan("qa", "R61 v3 质检过了吗")[0] == ("qa_results", {"tag": "R61", "state": "L2S3"})
    assert M.plan("qa", "能不能复现")[0] == ("qa_data", {})
    REG.set_override("hydro", {"disabled_actions": ["hydro_submit"]})
    assert M.plan("hydro", "申请重算模型版 L1") == []                              # 停用的动作不会被规划


# ---------------------------------------------------------------- 计算申请队列
def test_steps_for():
    assert CJ.steps_for("compute", "M61", ["L1", "L2", "L2P"]) == ["build:M61", "compute:M61:L1,L2,L2P"]
    assert CJ.steps_for("compute", "R96", ["L1"]) == ["build:R96:L1", "compute:R96:L1"]
    assert CJ.steps_for("compute", "M61", ["L2S3"]) == ["design:M61", "compute:M61:L2S3"]
    assert CJ.steps_for("compute", "D24", None, then_v3=True)[-2:] == ["design:D24", "compute:D24:L2S3"]
    assert CJ.steps_for("post", "M61") == ["post:M61"]
    with pytest.raises(ValueError):
        CJ.steps_for("compute", "X99")


def test_submit_approve_lifecycle(tmp_agents, monkeypatch):
    started = []
    monkeypatch.setattr(CJ, "start", lambda j: (started.append(j["id"]), CJ.save({**j, "status": "running", "pid": None}))[1])
    monkeypatch.setattr(CJ, "_pid_alive", lambda pid: True)
    j = CJ.submit("compute", "M61", ["L1"], reason="测试", requested_by={"agent": "hydro"})
    assert j["status"] == "pending" and not started                               # 提交不起计算
    assert CJ.submit("compute", "M61", ["L1"])["duplicate"] is True                 # 同样的申请不重复
    with pytest.raises(ValueError):
        CJ.pause(j["id"])                                                           # 没批准不能暂停
    j2 = CJ.approve(j["id"])
    assert started == [j["id"]] and j2["status"] == "running"
    with pytest.raises(ValueError):
        CJ.approve(j["id"])                                                         # 不能批准两次
    k = CJ.submit("post", "M61")
    assert CJ.approve(k["id"])["status"] == "approved" and started == [j["id"]]    # 同时只跑一条：第二条排队
    assert CJ.reject(CJ.submit("prep", "M61")["id"])["status"] == "rejected"


def test_selftest_job(tmp_agents, monkeypatch, tmp_path):
    """快速验证走同一条队列：不看情景，执行时跑 c1_pipeline selftest，结束把 verify.json 的判定摘要挂到任务上。"""
    import json, subprocess
    assert CJ.steps_for("selftest", "") == ["selftest"]
    with pytest.raises(ValueError):
        CJ.steps_for("compute", "")                                                 # 别的类型仍然要情景
    j = CJ.submit("selftest", "", reason="换机器验环境")
    assert j["status"] == "pending" and j["tag"] == "DEMO" and j["est_h"] == 0.05
    vf = tmp_path / "verify.json"
    vf.write_text(json.dumps({"ok": False, "when": "2026-10-02 11:11", "t_run_s": 49.1,
                              "checks": [{"check": "L1 算完（帧数）", "ok": True}, {"check": "L2 连续性", "ok": False}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(CJ, "VERIFY", vf)
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (seen.append(cmd[2:]), type("R", (), {"returncode": 0})())[1])
    CJ.save({**j, "status": "running"})
    CJ._exec(j["id"])
    d = CJ.load(j["id"])
    assert seen == [["selftest"]] and d["status"] == "done"
    assert d["verify"] == {"n": 2, "passed": 1, "failed": ["L2 连续性"], "all_ok": False, "t_run_s": 49.1, "when": "2026-10-02 11:11", "file": "out/demo/verify.json"}


def test_waits_for_external_run(tmp_agents, monkeypatch):
    """队列外（命令行后台链）有计算在跑：批准后先等，机器空了才开算；暂停着的队列外计算不挡；等的时候取消就不开算。"""
    import subprocess
    ext = {"c1_D24_L2S": {"container": "c1_D24_L2S", "name": "C1_D24", "state": "L2S", "via_queue": False, "paused": False}}
    monkeypatch.setattr(CJ, "external_runs", lambda: list(ext.values()))
    monkeypatch.setattr(CJ, "start", lambda j: CJ.save({**j, "status": "running", "pid": None}))
    j = CJ.approve(CJ.submit("compute", "M61", ["L1"])["id"])
    assert j["status"] == "running" and "c1_D24_L2S" in j["note"]
    ticks, ran = [], []
    def sleep(_):
        ticks.append(1)
        if len(ticks) == 2:
            ext.clear()                                                         # 第二轮：队列外的算完了
    monkeypatch.setattr(CJ.time, "sleep", sleep)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (ran.append(cmd[2:]), type("R", (), {"returncode": 0})())[1])
    assert CJ._exec(j["id"]) == 0
    d = CJ.load(j["id"])
    assert len(ticks) == 2 and ran == [["run", "build:M61:L1", "compute:M61:L1", "--tag", "M61", "--res", "15"]] and d["status"] == "done" and not d.get("note")
    ext["x"] = {"container": "c1m_x", "name": "C1_M61", "state": "L2", "via_queue": False, "paused": True}
    assert CJ.busy_external() == []                                            # 暂停着的不挡
    ext["x"]["paused"] = False
    k = CJ.save({**CJ.submit("post", "M61"), "status": "running"})
    monkeypatch.setattr(CJ.time, "sleep", lambda _: CJ.save({**CJ.load(k["id"]), "status": "cancelled"}))
    ran.clear()
    assert CJ._exec(k["id"]) == 1 and ran == []                                 # 等的时候被取消：不开算


def test_parse_log(tmp_path):
    lg = tmp_path / "x.log"
    lg.write_text("    t= 3.00h 域内    600,000 m³ 淹没   550.3 ha 最深 1.93 m ↓1D→2D   1 ↑2D→1D  2 雨  3 进塘  4 [5400s | 1D 1]\n", encoding="utf-8")
    d = CJ.parse_log(lg, 6.0)
    assert d["t_h"] == 3.0 and d["pct"] == 50.0 and d["wet_ha"] == 550.3 and d["eta_h"] == 1.5


# ---------------------------------------------------------------- 管理端口令
class _State:
    def __init__(self, key):
        self.cfg = {"serve": {"access_key": "console-key", "admin_key": key}}


class _Req:
    def __init__(self, cookie=""):
        self.h = {"Cookie": cookie}

    def header(self, n, d=None):
        return self.h.get(n, d)


def test_admin_disabled_without_key(tmp_agents):
    out = ADM.admin_agents(_State(""), {}, {}, _Req())
    assert isinstance(out, tuple) and out[1] == 403


def test_admin_login_and_lockout(tmp_agents, monkeypatch):
    monkeypatch.setattr(ADM, "_fail", {"n": 0, "until": 0.0, "first": 0.0})
    st = _State("s3cret-admin")
    assert ADM.admin_jobs(st, {}, {}, _Req("swb_key=console-key"))[1] == 401        # 控制台口令进不了管理端
    ok = ADM.login(st, {}, {"key": "s3cret-admin"}, _Req())
    assert ok.code == 200 and "HttpOnly" in ok.headers["Set-Cookie"]
    assert ADM.is_admin(st, _Req("swb_admin=s3cret-admin"))
    for _ in range(5):
        assert ADM.login(st, {}, {"key": "wrong"}, _Req())[1] == 401
    assert ADM.login(st, {}, {"key": "s3cret-admin"}, _Req())[1] == 429             # 锁住后对的也不行


# ---------------------------------------------------------------- 工具隔离
def test_dsh_tools_only_dispatch_actions():
    from swbrain import mcp_server
    names = {t["name"] for t in mcp_server.tool_list()}
    assert not names & set(REG.actions_of("hydro") + REG.actions_of("report") + REG.actions_of("qa"))
    assert "recession_options" in names


# ---------------------------------------------------------------- 大模型分派与专业智能体（假 _call，不联网）
class _Rt:
    def __init__(self):
        self.calls = []

    def act(self, api, params, actor=None, lang="zh"):
        self.calls.append((api, params, actor))
        return {"action": api, "ok": True, "params": params, "result": {"n": 1}, "summary": f"{api} 完成", "ui": {}, "side_effect": S.AT[api].side_effect}


def _llm_multi(monkeypatch, replies):
    """replies：按调用顺序给的假响应；返回 (MultiAgent, 记录下的请求)。"""
    from swbrain.ontology import agent as AG
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = []

    def fake(self, system, messages, tools, tool_choice=None):
        seen.append({"owner": self.owner, "tools": [t["name"] for t in tools], "tool_choice": tool_choice})
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(AG.LlmPlanner, "_call", fake)
    return ORC.MultiAgent(None, _Rt(), {"agent": {"api_key_env": "ANTHROPIC_API_KEY"}, "agents": {"llm": True}}), seen


def _tool(name, inp, i="t1"):
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}


def _text(t):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": t}]}


def test_llm_route_and_specialist_tools(tmp_agents, monkeypatch):
    M, seen = _llm_multi(monkeypatch, [_tool("delegate", {"agents": ["hydro", "nobody"], "reason": "问计算进度"}),
                                       _tool("hydro_progress", {}), _text("正在算 M61 L2，约 40%。")])
    out = M.chat("模型版现在怎么样了")
    assert [r["agent"] for r in out["route"]] == ["hydro"] and out["route"][0]["by"] == "llm"     # 不存在的智能体被丢掉
    assert seen[0]["tool_choice"] == {"type": "tool", "name": "delegate"}
    assert set(seen[1]["tools"]) == set(REG.actions_of("hydro"))                                  # 专业智能体只看得到自己名下的动作
    assert out["agents"][0]["planner"] == "llm" and out["reply"].startswith("正在算")
    assert M.rt.calls == [("hydro_progress", {}, "agent:hydro")]


def test_llm_submit_guard(tmp_agents, monkeypatch):
    M, _ = _llm_multi(monkeypatch, [_tool("delegate", {"agents": ["hydro"], "reason": "x"}),
                                    _tool("hydro_submit", {"tag": "M61"}), _text("没有提交。")])
    out = M.chat("模型版 L1 的结果怎么样")                                                        # 没说要提交
    assert M.rt.calls == [] and out["steps"][0]["ok"] is False                                    # 申请动作被闸拦下
    M2, _ = _llm_multi(monkeypatch, [_tool("delegate", {"agents": ["hydro"], "reason": "x"}),
                                     _tool("hydro_submit", {"tag": "M61"}), _text("已提交，待批准。")])
    M2.chat("申请重算模型版 L1")
    assert [c[0] for c in M2.rt.calls] == ["hydro_submit"]


def test_llm_disabled_action_not_offered(tmp_agents, monkeypatch):
    REG.set_override("hydro", {"disabled_actions": ["hydro_submit"]})
    M, seen = _llm_multi(monkeypatch, [_tool("delegate", {"agents": ["hydro"], "reason": "x"}), _tool("hydro_submit", {"tag": "M61"}), _text("不能提交。")])
    M.chat("申请重算模型版 L1")
    assert "hydro_submit" not in seen[1]["tools"] and M.rt.calls == []                            # 不在清单，模型硬调也拒


def test_llm_failure_falls_back_to_rules(tmp_agents, monkeypatch):
    M, _ = _llm_multi(monkeypatch, [OSError("network down"), OSError("network down")])
    out = M.chat("现在一二维算到哪了，还要多久")
    assert [r["agent"] for r in out["route"]] == ["hydro"] and "by" not in out["route"][0]          # 规则分派
    assert out["agents"][0]["planner"] == "rules" and M.rt.calls[0][0] == "hydro_progress"
    assert "规则分派" in out["note"] and "规则规划器" in out["note"]


def test_no_key_means_rules(tmp_agents, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    M = ORC.MultiAgent(None, _Rt(), {"agent": {"api_key_env": "ANTHROPIC_API_KEY"}, "agents": {"llm": True}})
    assert M.llm("orchestrator") is None and M.llm("hydro") is None
    assert ORC.MultiAgent(None, _Rt(), {"agents": {"llm": False}}).llm("qa") is None


def test_router_retries_without_forced_tool_choice(tmp_agents, monkeypatch):
    import io, urllib.error
    bad = urllib.error.HTTPError("u", 400, "Thinking mode does not support this tool_choice", {}, io.BytesIO(b""))
    M, seen = _llm_multi(monkeypatch, [bad, _tool("delegate", {"agents": ["qa"], "reason": "x"})])
    assert [r["agent"] for r in M.route_llm("能复现吗")] == ["qa"]
    assert seen[0]["tool_choice"] and seen[1]["tool_choice"] is None


def test_agents_section_overrides_agent_model(tmp_agents, monkeypatch):
    monkeypatch.setenv("DS_TEST_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    M = ORC.MultiAgent(None, _Rt(), {"agent": {"api_key_env": "ANTHROPIC_API_KEY", "model": "m-a"},
                                     "agents": {"llm": True, "api_key_env": "DS_TEST_KEY", "base_url": "https://x.example/anthropic/", "model": "m-b"}})
    p = M.llm("hydro")
    assert p is not None and p.model == "m-b" and p.base == "https://x.example/anthropic" and M.llm("orchestrator").max_tokens == 2000


def test_api_action_cannot_claim_console(tmp_agents):
    """/api/action 的 actor 是调用方自报的标签：自称 console 也绕不过管理端停用。"""
    from swbrain import routes as RTS
    assert RTS.api_actor("console") == "api:console" and RTS.api_actor(None) == "api:anon"
    assert RTS.api_actor("dsh") == "api:dsh" and RTS.api_actor("a b\n<x>") == "api:abx"
    REG.set_override("qa", {"disabled_actions": ["qa_data"]})
    from swbrain.ontology import runtime as RTm
    rt = RTm.Runtime.__new__(RTm.Runtime); rt.log_path = tmp_agents / "acts.jsonl"
    out = RTS.action(type("St", (), {"ontology": rt})(), {}, {"action": "qa_data", "actor": "console"}, None)
    assert out["ok"] is False and "停用" in out["error"]


# ---------------------------------------------------------------- 自建智能体（配置化新建，2026-10-02）
@pytest.fixture()
def custom_store(tmp_agents, monkeypatch):
    monkeypatch.setattr(REG, "CUSTOM", tmp_agents / "custom.json")
    return tmp_agents


SPEC = {"id": "point_watch", "zh": "积水点值守", "role": "只看积水点现状与历史，不派泵不签预警。",
        "actions": ["inspect_point", "point_history", "count_points"], "keywords": ["值守", "积水点现状"]}


def test_create_custom_agent_and_it_is_listed(custom_store):
    e = REG.create_custom(dict(SPEC))
    assert e["custom"] is True and e["enabled"] is True and REG.is_custom("point_watch")
    assert [a["api"] for a in e["actions"]] == SPEC["actions"]                   # 挑的是 dispatch 名下的现成动作
    assert "point_watch" in REG.all_agents() and "point_watch" in ORC.specialists()
    assert REG.actions_of("dispatch")                                            # 内置的归属不受影响（可以重叠）


def test_create_custom_rejects_bad_input(custom_store):
    for bad, why in [({"id": "Bad-ID"}, "id"), ({"id": "dispatch"}, "内置"), ({"actions": ["no_such_action"]}, "不存在"),
                     ({"actions": []}, "至少挑一个动作"), ({"keywords": []}, "关键词"), ({"zh": ""}, "名称")]:
        with pytest.raises(ValueError, match=why):
            REG.create_custom({**SPEC, **bad})
    REG.create_custom(dict(SPEC))
    with pytest.raises(ValueError, match="已经有"):
        REG.create_custom(dict(SPEC))


def test_reset_does_not_delete_custom_agent(custom_store):
    """overrides 与 custom 分开存：「恢复缺省」只清覆盖，不会把用户建的智能体删掉。"""
    REG.create_custom(dict(SPEC))
    REG.set_override("point_watch", {"enabled": False})
    assert REG.effective("point_watch")["enabled"] is False
    REG.reset("point_watch")
    assert REG.is_custom("point_watch") and REG.effective("point_watch")["enabled"] is True
    REG.delete_custom("point_watch")
    assert not REG.is_custom("point_watch") and "point_watch" not in REG.all_agents()
    with pytest.raises(ValueError, match="内置"):
        REG.delete_custom("hydro")


def test_routing_reaches_custom_agent(custom_store):
    REG.create_custom(dict(SPEC))
    assert [r["agent"] for r in ORC.MultiAgent(None, None).route("ADM-62 值守情况怎么样")] == ["point_watch"]


def test_custom_agent_rule_plan_only_uses_its_actions(custom_store):
    REG.create_custom(dict(SPEC))
    M = ORC.MultiAgent(None, None)
    plan = M.plan("point_watch", "ADM-62 的历史积水")
    assert plan and plan[0][0] in SPEC["actions"]
    assert all(api in SPEC["actions"] for api, _ in plan)


def test_runtime_enforces_custom_agent_action_set(custom_store, tmp_path):
    """自建智能体只能调它挑的那组；被停用后什么都不能调。判定在 Runtime.act，智能体自报 agent:<id> / dsh:<id> 都认。"""
    REG.create_custom(dict(SPEC))
    from swbrain.ontology import runtime as RTm
    rt = RTm.Runtime.__new__(RTm.Runtime); rt.log_path = tmp_path / "a.jsonl"
    rt.validate = lambda A, p: dict(p); rt.console_link = lambda ui: None
    for api in ("count_points", "issue_alert"):
        setattr(rt, f"_act_{api}", lambda P: {"ok": 1}); setattr(rt, f"_sum_{api}", lambda r, P, lang: "ok")
    assert rt.act("count_points", {}, actor="agent:point_watch")["ok"] is True
    out = rt.act("issue_alert", {}, actor="agent:point_watch")
    assert out["ok"] is False and "不在智能体 point_watch" in out["error"]
    out = rt.act("issue_alert", {}, actor="api:dsh:point_watch")                 # 经 MCP 回来的标签形如 api:dsh:<id>
    assert out["ok"] is False and "point_watch" in out["error"]
    REG.set_override("point_watch", {"enabled": False})
    assert "停用" in rt.act("count_points", {}, actor="agent:point_watch")["error"]


def test_render_emits_custom_preset_and_removes_it(custom_store, tmp_path):
    from swbrain import dsh_bridge as D
    REG.create_custom(dict(SPEC))
    out = D.render({"agent": {"dsh": {}}}, home=tmp_path / "h", link_modules=False)
    f = out["presets"]["point_watch"] / "agent.cordis.yml"
    text = f.read_text(encoding="utf-8")
    assert "SWBRAIN_OWNER: point_watch" in text and "inspect_point,point_history,count_points" in text and "积水点值守" in text
    REG.delete_custom("point_watch")
    D.render({"agent": {"dsh": {}}}, home=tmp_path / "h", link_modules=False)
    assert not (tmp_path / "h" / ".agent-presets" / "point_watch").exists()      # 删了智能体，预设也跟着删
    assert (tmp_path / "h" / ".agent-presets" / "drainage").exists()             # 内置的不动


def test_mcp_bridge_honours_custom_action_list(monkeypatch):
    from swbrain import mcp_server as Mm
    monkeypatch.setattr(Mm, "OWNER", "point_watch"); monkeypatch.setattr(Mm, "ACTIONS", frozenset(SPEC["actions"]))
    names = {t["name"] for t in Mm.tool_list()}
    assert set(SPEC["actions"]) <= names and "issue_alert" not in names and "recession_options" not in names
    text, err = Mm.call_tool("issue_alert", {})
    assert err and "不属于" in text
