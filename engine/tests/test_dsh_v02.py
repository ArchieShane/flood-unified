"""dsh 0.2.x 的 web 补丁改写：改名的 id、预设声明成配置行。"""
import yaml

from swbrain import dsh_bridge as D


def test_web_patch_v02(tmp_path):
    p = tmp_path / "drainage"; p.mkdir()
    (p / "preset.yml").write_text("name: 调度研判\ndescription: 积水点\norder: 0\n", encoding="utf-8")
    (p / "agent.cordis.yml").write_text("- id: persona\n  name: '@deepseek-ai/dsh-persona'\n", encoding="utf-8")
    src = "- id: agent-presets\n  config:\n    default: drainage\n- id: workflow-worker-thread\n  disabled: false\n"
    rows = yaml.safe_load(D._web_patch_v02(src, {"drainage": p}))
    ids = [r.get("id") for r in rows]
    assert ids[:2] == ["agent-preset-registry", "workflow-ptc"]
    ins = rows[-1]["insert"][0]
    assert ins["name"] == "@deepseek-ai/dsh-agent-preset"
    assert ins["config"]["id"] == "drainage" and ins["config"]["order"] == 0
    assert ins["config"]["plugins"][0]["id"] == "persona"


def test_web_v02_detects_version(monkeypatch):
    monkeypatch.setattr(D, "launch_args", lambda *a, **k: ("dsh",))
    for v, want in (("0.1.5-rc.2", False), ("0.2.1-alpha.1", True), ("", False)):
        monkeypatch.setattr(D, "dsh_version", lambda a, v=v: v)
        assert D._web_v02({}) is want
