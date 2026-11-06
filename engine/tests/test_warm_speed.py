"""智能体提速（2026-10-01）：预热顺序、用户在等的情景插队、同参数调度解不重算、DeepSeek Harness 后台预启动。

实测背景：首问 239–773 s，主要是冷情景现算调度上下文与调度解（单个几分钟），dsh 300 s 工具超时还会让同一份算两遍；
运行时启动约 60 s。这些测试用桩，不装载真实数据。
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swbrain import dsh_bridge
from swbrain.state import State


class _Cache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v

    def has(self, k):
        return k in self.d

    @staticmethod
    def key_for(*parts):
        return "|".join(str(p) for p in parts)


def _bare_state(cfg=None):
    s = State.__new__(State)
    s.cfg = cfg or {"serve": {}}
    s.rp_cache = {}; s.pay_cache = {}; s.cache = _Cache(); s.road = object()
    s.build_lock = threading.Lock(); s._want = []; s._want_lock = threading.Lock()
    s._pay_inflight = {}; s._pay_lock = threading.Lock()
    s.traffic = {"id": 0, "meta": None}; s.dispatch_ctx = None; s.active_rp = None
    return s


def test_warm_order_puts_warm_first_ahead(monkeypatch):
    from swbrain import scenarios
    monkeypatch.setattr(scenarios, "keys", lambda cfg: ["5", "10", "25", "50", "100"])
    s = _bare_state({"serve": {"warm_first": ["50", "10", "nope"]}})
    assert s.warm_order() == ["50", "10", "5", "25", "100"]          # 列了的先、不存在的忽略、其余按目录


def test_waiting_user_jumps_the_queue():
    s = _bare_state()
    pending = ["5", "10", "25", "50"]
    s._want.append("50")                                              # 用户正在等 50
    assert s._next_warm(pending) == "50" and pending == ["5", "10", "25"]
    assert s._next_warm(pending) == "5"


def test_ensure_ctx_registers_the_wait_and_clears_it(monkeypatch):
    s = _bare_state()
    seen = {}

    def fake_locked(key, info):
        seen["want_during"] = list(s._want)
        return {"fp": {}}, info
    monkeypatch.setattr(s, "_ensure_ctx_locked", fake_locked)
    s._ensure_ctx("50")
    assert seen["want_during"] == ["50"] and s._want == []           # 等的时候登记着，完了就撤


def test_same_dispatch_solved_once_even_if_asked_twice(monkeypatch):
    s = _bare_state()
    calls = []

    def slow_solve(ctx, key, t_min, ob, budget_aed, robust):
        calls.append(key); time.sleep(0.3)
        return {"pay": {"cached": False, "n": len(calls)}, "plan": [], "t": float(t_min)}
    monkeypatch.setattr(s, "_solve_pay", slow_solve)
    monkeypatch.setattr(s, "_tctx", lambda ctx, t: ctx)
    out = []
    ths = [threading.Thread(target=lambda: out.append(s.dispatch_at(60.0, ctx={"fp": {}}, key="50"))) for _ in range(3)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert calls == ["50"], f"同一份调度解被算了 {len(calls)} 次"            # 原来 dsh 超时重试会再算一遍
    assert len(out) == 3 and all(o["n"] == 1 for o in out)


def test_failed_solve_releases_waiters(monkeypatch):
    s = _bare_state()
    monkeypatch.setattr(s, "_tctx", lambda ctx, t: ctx)

    def boom(*a):
        raise RuntimeError("solver crashed")
    monkeypatch.setattr(s, "_solve_pay", boom)
    try:
        s.dispatch_at(60.0, ctx={"fp": {}}, key="50")
    except RuntimeError:
        pass
    assert s._pay_inflight == {}                                      # 出错也要放行，后来者不会卡 30 分钟


def test_prestart_failure_does_not_block(monkeypatch):
    br = dsh_bridge.DshBridge.__new__(dsh_bridge.DshBridge)
    br._h = None; br._lock = threading.Lock(); br.error = None; br.started_at = None; br.start_s = None; br.starting = False

    def bad_start():
        raise RuntimeError("no runtime")
    monkeypatch.setattr(br, "_start", bad_start)
    logs = []
    th = br.prestart(log=lambda *a, **k: logs.append(a[0]))
    th.join(timeout=5)
    st = br.status()
    assert not st["running"] and not st["starting"] and "no runtime" in st["error"]
    assert any("预启动失败" in x for x in logs)


def test_prestart_success_records_start_time(monkeypatch):
    br = dsh_bridge.DshBridge.__new__(dsh_bridge.DshBridge)
    br._h = None; br._lock = threading.Lock(); br.error = None; br.started_at = None; br.start_s = None; br.starting = False

    def ok_start():
        br._h = object(); br.start_s = 0.1; br.started_at = "now"
    monkeypatch.setattr(br, "_start", ok_start)
    br.prestart(log=lambda *a, **k: None).join(timeout=5)
    assert br.status()["running"] and br.status()["start_s"] == 0.1


def test_tool_timeout_longer_than_mcp_http_timeout():
    """dsh 工具超时不能短于 MCP 桥的 HTTP 超时，否则冷算到一半 dsh 先放弃并重试，服务端同一份算两遍。"""
    import re
    from swbrain import mcp_server
    yml = (Path(__file__).resolve().parents[1] / "agent" / "dsh" / "presets" / "drainage" / "agent.cordis.yml").read_text(encoding="utf-8")
    ms = int(re.search(r"toolCallTimeoutMs:\s*(\d+)", yml).group(1))
    assert ms >= mcp_server.TIMEOUT * 1000, (ms, mcp_server.TIMEOUT)
