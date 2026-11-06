"""DeepSeek Harness 接入：MCP 服务器的工具声明 / 协议处理 / 转发；dsh_bridge 的设置、渲染、启动参数、事件→步骤。不启动真实运行时。"""
import json
from types import SimpleNamespace

import pytest

from swbrain import mcp_server as M, dsh_bridge as B
from swbrain.ontology import schema as S


def test_mcp_tool_list_and_instructions():
    tools = M.tool_list()
    names = [t["name"] for t in tools]
    own = [a.api for a in S.ACTIONS if getattr(a, "owner", "dispatch") == "dispatch"]      # 多智能体：MCP 只给调度研判自己的动作
    assert names[: len(own)] == own and {"get_object", "search_objects", "follow_link", "ontology_overview"} <= set(names)
    rec = next(t for t in tools if t["name"] == "recession_options")
    assert rec["inputSchema"]["required"] == ["fp_id"] and rec["inputSchema"]["properties"]["decision_time_min"]["maximum"] == 2880
    ia = next(t for t in tools if t["name"] == "issue_alert")
    assert "副作用" in ia["description"] and ia["inputSchema"]["properties"]["p24_mm"]["type"] == "number"
    ins = M.instructions()
    assert "什么时候退水" in ins and "recession_options" in ins and "FloodPoint" in ins and len(ins.encode("utf-8")) < 32768


def test_mcp_handle_protocol(monkeypatch):
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
    assert r["result"]["protocolVersion"] == "2025-03-26" and r["result"]["serverInfo"]["name"] == "swbrain" and "instructions" in r["result"]
    r = M.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == M.SUPPORTED[0]
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert M.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}
    assert len(M.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})["result"]["tools"]) == len([a for a in S.ACTIONS if getattr(a, "owner", "dispatch") == "dispatch"]) + 4
    assert M.handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601
    assert M.handle({"jsonrpc": "2.0", "id": 6, "method": "resources/list"})["result"] == {"resources": []}
    # 转发：动作 → POST /api/action；对象 → GET
    calls = []

    def fake_post(path, body):
        calls.append(("POST", path, body)); return {"ok": True, "summary_zh": "好", "summary_en": "ok", "ui": {"hs": body["params"]["fp_id"]}, "result": {"x": list(range(100))}, "side_effect": "none"}

    def fake_get(path, params=None):
        calls.append(("GET", path, params))
        if path == "/api/object":
            return {"object": {"fp_id": "ADM-1"}, "rules": [], "links": {"drains_via": [{"gid": 1}], "zone": []}}
        if path == "/api/objects":
            return {"rows": [{"fp_id": "ADM-1"}]}
        if path == "/api/ontology":
            return {"core": "FloodPoint", "object_types": [], "link_types": [], "rules": [], "actions": [], "scenarios": ["10"], "examples": []}
        return {"active_rp": "10", "warm": {"done": True}}
    monkeypatch.setattr(M, "http_post", fake_post); monkeypatch.setattr(M, "http_get", fake_get)
    r = M.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "inspect_point", "arguments": {"fp_id": "ADM-1"}}})
    txt = json.loads(r["result"]["content"][0]["text"])
    assert r["result"]["isError"] is False and txt["ui"] == {"hs": "ADM-1"} and len(txt["result"]["x"]) == 60 and calls[-1][2]["actor"] == "dsh:dispatch"   # 带上智能体 id，服务端据此判自建智能体
    r = M.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "follow_link", "arguments": {"type": "FloodPoint", "id": "ADM-1", "link": "drains_via"}}})
    assert json.loads(r["result"]["content"][0]["text"])["rows"] == [{"gid": 1}]
    r = M.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "follow_link", "arguments": {"type": "FloodPoint", "id": "ADM-1", "link": "nope"}}})
    assert r["result"]["isError"] is True
    r = M.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "search_objects", "arguments": {"type": "FloodState", "filters": {"served": False, "level": "III"}, "limit": 3}}})
    assert calls[-1][2]["served"] == "false" and calls[-1][2]["level"] == "III"
    r = M.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "ontology_overview", "arguments": {}}})
    assert json.loads(r["result"]["content"][0]["text"])["active_scenario"] == "10"
    r = M.handle({"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "no_such", "arguments": {}}})
    assert r["result"]["isError"] is True


def test_bridge_settings_and_render(tmp_path, monkeypatch):
    from swbrain import envkeys
    monkeypatch.setattr(envkeys, "present", lambda n: bool(__import__("os").environ.get(n)))      # 这条测试只看进程环境，不让注册表里的真密钥参与
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False); monkeypatch.setenv("OPENAI_API_KEY", "sk-test"); monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {"agent": {"dsh": {"home": str(tmp_path / "home"), "home_web": str(tmp_path / "home_web"), "web_port": 3099}}, "serve": {"port": 8766, "access_key": "k1"}}
    st = B.settings(cfg)
    assert st["provider"] == "openai" and st["model"] == "gpt-5.1" and st["web_url"] == "http://127.0.0.1:3099" and st["swbrain_url"] == "http://127.0.0.1:8766" and st["swbrain_key"] == "k1"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    assert B.settings(cfg)["provider"] == "deepseek-official" and B.settings({**cfg, "agent": {"dsh": {"provider": "openai", "model": "gpt-5-mini"}}})["model"] == "gpt-5-mini"
    paths = B.render(cfg)
    a = (paths["preset_dir"] / "agent.cordis.yml").read_text(encoding="utf-8")
    assert "__REPO__" not in a and "__PYTHON__" not in a and "serverName: swbrain" in a and paths["skills_dir"].as_posix() in a
    # 访问口令不许渲染进 out/ 下的任何文件（2026-09-23 修：原来明文躺在 5 个 YAML 里，dsh 的 Web UI 还会索引那个目录）。
    # MCP 服务自己从 config.toml 读，另外 env_for 也会把它传给子进程。
    for f in (paths["preset_dir"] / "agent.cordis.yml", paths["sdk.patch.yml"], paths["web.patch.yml"], paths["settings"]):
        assert "k1" not in f.read_text(encoding="utf-8"), f"口令明文出现在 {f}"
    assert B.env_for(st)["SWBRAIN_KEY"] == "k1"
    assert (paths["skills_dir"] / "drainage-flood-point" / "SKILL.md").exists() and (paths["preset_dir"] / "preset.yml").exists()
    w = paths["web.patch.yml"].read_text(encoding="utf-8"); sd = paths["sdk.patch.yml"].read_text(encoding="utf-8")
    import re
    assert "provider: deepseek-official" in w and "default: drainage" in w and "session-title-llm" in w and "mcp-swbrain" in sd
    assert "id: client-hmr" in w and "id: ui-sidebar-files" in w and w.count("disabled: true") >= 3      # 浏览器每主机 6 条连接：关掉 HMR 事件流与文件树变更流
    assert not re.search(r"__[A-Z][A-Z_]*__", sd) and not re.search(r"__[A-Z][A-Z_]*__", w) and not re.search(r"__[A-Z][A-Z_]*__", a)      # 占位符都换掉了（mcp__swbrain__ 是工具名前缀，不算）
    assert paths["settings"].exists() and "llm-pi-ai" in paths["settings"].read_text(encoding="utf-8")
    monkeypatch.setattr(B, "launch_args", lambda dsh_bin=None, kind="sdk": ("dsh.exe",))
    args, env = B.web_command(cfg, port=3099)
    assert args[:3] == ["dsh.exe", "--profile", "web"] and args[3] == "--patch" and args[-3:] == ["--port", "3099", "--no-open"] and env["DSH_HOME"] == str(st["home_web"])      # Web 与 SDK 各用一个主目录
    assert (tmp_path / "home_web" / ".agent-presets" / "drainage" / "agent.cordis.yml").exists() and st["home_web"] != st["home"]
    # PyYAML 能读渲染出的预设（没有 !!js 标签）
    import yaml
    rows = yaml.safe_load(a)
    assert [r["id"] for r in rows][:3] == ["persona", "agent-instructions", "mcp-swbrain"] and rows[2]["config"]["args"] == ["-m", "swbrain.mcp_server"]
    assert isinstance(yaml.safe_load(sd), list) and isinstance(yaml.safe_load(w), list)


def test_step_collector_maps_dsh_events():
    col = B.StepCollector()
    N = lambda method, payload: SimpleNamespace(method=method, payload=payload)
    col(N("session.status", {"status": "running"}))
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/call", "data": {"turn": 1, "step": 1, "callId": "c1", "name": "mcp__swbrain__recession_options", "arguments": "{\"fp_id\":\"ADM-3\",\"scenario\":\"50\"}"}}}))
    res = {"ok": True, "summary_zh": "ADM-3：最快退水的手段：预置最优", "ui": {"hs": "ADM-3", "rp": "50"}, "result": {}}
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/result", "data": {"turn": 1, "step": 1, "message": {"source": {"kind": "tool", "callId": "c1"}, "content": [{"type": "tool-result", "toolCallId": "c1", "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]}]}}}}))
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/call", "data": {"callId": "c2", "name": "skill", "arguments": "{\"name\":\"drainage-flood-point\"}"}}}))
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/result", "data": {"message": {"source": {"kind": "tool", "callId": "c2"}, "content": [{"type": "tool-result", "toolCallId": "c2", "content": [{"type": "text", "text": "<skill_content>…</skill_content>"}]}]}}}}))
    assert [s["action"] for s in col.steps] == ["recession_options", "skill"]
    assert col.steps[0]["params"] == {"fp_id": "ADM-3", "scenario": "50"} and col.steps[0]["ok"] and "预置最优" in col.steps[0]["summary"] and col.ui == {"hs": "ADM-3", "rp": "50"}
    assert col.steps[1]["ok"] and col.steps[1]["summary"].startswith("<skill_content>")
    # 出错的工具结果
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/call", "data": {"callId": "c3", "name": "mcp__swbrain__inspect_point", "arguments": "{\"fp_id\":\"ADM-9999\"}"}}}))
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/result", "data": {"message": {"source": {"kind": "tool", "callId": "c3"}, "content": [{"type": "tool-result", "toolCallId": "c3", "content": [{"type": "text", "text": json.dumps({"ok": False, "error": "没有积水点 ADM-9999", "summary_zh": "查看积水点：没有积水点"})}]}]}}}}))
    assert col.steps[2]["ok"] is False and "没有积水点" in col.steps[2]["summary"]
    assert B._summary_from("plain text")[0] == "plain text" and B._result_text({"result": "x"}) == "x"
    # 运行失败事件：记下错误文字（模型额度用尽这类）
    col2 = B.StepCollector()
    col2(N("session.event", {"sessionId": "s", "event": {"type": "agent/error", "data": {"message": "You have no credits remaining.", "code": "PI_AI_ERROR"}}}))
    assert col2.error == "You have no credits remaining." and col2.steps == []
    col3 = B.StepCollector()
    col3(N("session.event", {"sessionId": "s", "event": {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "error", "message": "boom"}}}}))
    assert col3.error == "boom"
    assert B._error_text({"reason": {"kind": "error"}}) == "error" and B._error_text({}) is None


def test_step_collector_reads_dsh_02_shape():
    """0.2.x 的 tool/result：正文不再裹 tool-result 块，直接就是 text（ContentBlockMap 里已无该类型）。
    形状按 dsh-session 与 dsh-sdk-protocol 的类型声明核对（2026-10-04，dsh 0.2.0-rc.2）。"""
    col = B.StepCollector()
    N = lambda method, payload: SimpleNamespace(method=method, payload=payload)
    res = {"ok": True, "summary_zh": "ADM-7：派 3 台泵", "ui": {"hs": "ADM-7"}, "result": {"n": 3}}
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/call", "data": {
        "turn": 1, "step": 1, "callId": "c1", "name": "mcp__swbrain__dispatch_plan", "arguments": "{\"fp_id\":\"ADM-7\"}"}}}))
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/result", "data": {
        "turn": 1, "step": 1, "message": {"id": "m1", "role": "tool", "source": {"kind": "tool", "callId": "c1"},
                                         "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]}}}}))
    assert [s["action"] for s in col.steps] == ["dispatch_plan"] and col.steps[0]["ok"]
    assert "派 3 台泵" in col.steps[0]["summary"] and col.ui == {"hs": "ADM-7"} and col.steps[0]["result"] == {"n": 3}
    # 失败：0.2 的 tool/result 带 isError 与独立的 error{name, code, reason}。
    # 没有 pending 的 callId 时退化成一条无名步骤，但正文照样要取到（不能因为多了 error 字段就读不出文字）。
    col(N("session.event", {"sessionId": "s", "event": {"type": "tool/result", "data": {
        "message": {"role": "tool", "isError": True, "source": {"kind": "tool", "callId": "c9"},
                    "content": [{"type": "text", "text": "工具执行失败"}]},
        "error": {"name": "MCPError", "code": "TOOL_FAILED", "reason": "连接超时"}}}}))
    assert "工具执行失败" in col.texts[-1]
    # turn/end 的失败原因：reason.error 是 LlmFailure{message, code}，不是 0.1.5 的 reason.message
    col2 = B.StepCollector()
    col2(N("session.event", {"sessionId": "s", "event": {"type": "turn/end", "data": {
        "turn": 1, "reason": {"kind": "error", "error": {"message": "no credits remaining", "code": "PI_AI_ERROR"}}}}}))
    assert col2.error == "no credits remaining"


def test_bridge_chat_empty_reply_is_failure(monkeypatch):
    """模型没答、工具也没调 → chat() 抛异常，Agent 才会退回备用规划器。"""
    cfg = {"agent": {"dsh": {"provider": "openai", "model": "gpt-5.1"}}, "serve": {"port": 8766, "access_key": "k"}}
    br = B.DshBridge(cfg)

    class R:
        final_response = ""; finish_reason = "error"

    class H:
        def run(self, message, session_id=None, on_notification=None):
            on_notification(SimpleNamespace(method="session.event", payload={"event": {"type": "agent/error", "data": {"message": "no credits"}}}))
            return R()
    monkeypatch.setattr(br, "harness", lambda: H()); monkeypatch.setattr(br, "close", lambda: None)
    with pytest.raises(RuntimeError) as ei:
        br.chat("hi", session_id="t1")
    assert "no credits" in str(ei.value) and "no credits" in (br.error or "")


def test_render_emits_one_preset_per_agent(tmp_path):
    """Harness 里的预设与系统里的工作智能体一一对应（2026-10-02）：drainage/hydro/report/qa 四份，
    每份带自己的 SWBRAIN_OWNER，MCP 桥只暴露那个智能体名下的动作。"""
    D = B
    cfg = {"agent": {"dsh": {"home": str(tmp_path / "h")}}}
    from swbrain.agents import registry as REG
    out = D.render(cfg, home=tmp_path / "h", link_modules=False)
    assert sorted(out["presets"]) == ["drainage", "hydro", "qa", "report"], sorted(out["presets"])
    # 预设目录名 → 系统里的智能体 id（drainage 是调度研判，目录名沿用历史）
    OWNER = {"drainage": "dispatch", "hydro": "hydro", "report": "report", "qa": "qa"}
    for pid, aid in OWNER.items():
        text = (out["presets"][pid] / "agent.cordis.yml").read_text(encoding="utf-8")
        assert "SWBRAIN_DELEGATION_FILE" in text, pid                  # 委托凭据（统一身份）每份都要有
        assert f"SWBRAIN_OWNER: {aid}" in text, (pid, aid)             # 这份预设代表哪个智能体
        assert REG.actions_of(aid), aid
    assert sorted(OWNER.values()) == sorted(set(REG.AGENTS) - {"orchestrator"})   # 与注册表同一套切法：四个工作智能体各一份
    assert sorted(out["presets"]) == sorted(OWNER)                        # 目录名（drainage 是 dispatch 的历史目录名）
    assert "SWBRAIN_OWNER" not in (out["sdk.patch.yml"].read_text(encoding="utf-8"))   # 控制台走 sdk：不给 = dispatch


def test_mcp_bridge_only_serves_its_own_agent(tmp_path, monkeypatch):
    """桥那一层就按 OWNER 过滤：既不出现在工具清单里，硬调也被拒。"""
    from swbrain import mcp_server as M
    monkeypatch.setattr(M, "OWNER", "hydro")
    names = [t["name"] for t in M.tool_list()]
    assert "hydro_result" in names and "recession_options" not in names
    text, err = M.call_tool("recession_options", {"fp_id": "ADM-62"})
    assert err and "不属于这个智能体" in text, text


def test_webbridge_flags_a_foreign_process_on_its_port(tmp_path, monkeypatch):
    """端口在听但不是本实例起的：不谎报 running（加载的可能是改补丁之前的旧配置，2026-10-02 实际踩到）。"""
    cfg = {"agent": {"dsh": {"home_web": str(tmp_path / "hw"), "web_port": 3099}}}
    br = B.WebBridge(cfg)
    monkeypatch.setattr(B, "web_running", lambda cfg: True)          # 端口有人占着
    assert br.running() is False and br.foreign() is True            # 不是我们起的
    info = br.info()
    assert info["running"] is False and info["foreign"] is True
    assert "启动那一刻的配置" in info["note_foreign"]                       # 提示里说清为什么要注意它
    br._p = SimpleNamespace(poll=lambda: None)                        # 我们自己起的、还在跑
    assert br.running() is True and br.foreign() is False


def test_web_ui_local_only_even_with_lan(tmp_path):
    """登记了局域网地址也只听本机：dsh 拒绝 --host 0.0.0.0（带上它 Web UI 起不来，2026-10-02 踩过）。局域网来的请求认得出来。"""
    cfg = {"agent": {"dsh": {"home": str(tmp_path / "h"), "home_web": str(tmp_path / "hw"), "web_port": 3080}},
           "identity": {"public_base": "http://127.0.0.1:8766", "extra_public_bases": ["http://10.0.0.47:8766"]}}
    args, _ = B.web_command(cfg)
    assert "--host" not in args and "--trusted-host" not in args
    url = "http://0.0.0.0:3080/?token=abc"
    assert B.web_url_for(url, "10.0.0.47:8766", cfg) == "http://10.0.0.47:3081/?token=abc"     # 局域网来的：给局域网入口
    assert B.web_url_for(url, "127.0.0.1:8766", cfg) == "http://127.0.0.1:3080/?token=abc"
    assert B.web_url_for(url, "evil.test", cfg) == "http://127.0.0.1:3080/?token=abc"          # 只换成登记过的主机
    cfg0 = {**cfg, "agent": {"dsh": {**cfg["agent"]["dsh"], "lan_port": 0}}}
    assert B.web_url_for(url, "10.0.0.47:8766", cfg0) == "http://127.0.0.1:3080/?token=abc"    # 局域网入口关了
    assert B.lan_request("10.0.0.47:8766", cfg) is True
    assert B.lan_request("127.0.0.1:8766", cfg) is False and B.lan_request("evil.test", cfg) is False


def test_web_ui_lan_hosts_auto(tmp_path, monkeypatch):
    """extra_public_bases = ["auto"]：局域网入口认本机当前的局域网地址。"""
    from swbrain import identity as I
    monkeypatch.setattr(I, "local_ipv4s", lambda: ("192.168.50.224",))
    cfg = {"agent": {"dsh": {"home": str(tmp_path / "h"), "home_web": str(tmp_path / "hw"), "web_port": 3080}},
           "identity": {"public_base": "http://127.0.0.1:8766", "extra_public_bases": ["auto"]}}
    assert B._lan_hosts(cfg) == ["192.168.50.224"]
    assert B.web_url_for("http://127.0.0.1:3080/?token=abc", "192.168.50.224:8766", cfg) == "http://192.168.50.224:3081/?token=abc"
