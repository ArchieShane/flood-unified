"""声明与实现对不对得上：loader 名、动作实现、副作用标注、参数校验的边界值。

2026-09-23 评审发现自检有盲区——Scenario / Reach / Zone 的 loader 名根本不存在，
而它们会经 /api/ontology 和 docs/本体模型.md 给客户看到，自检却一直是绿的。
"""
import dataclasses
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from swbrain.ontology import schema as S
from swbrain.ontology.runtime import Runtime, ActionError


def test_self_check_passes():
    assert S.check() == []


def test_every_loader_exists_on_runtime():
    missing = [(o.api, o.loader) for o in S.OBJECT_TYPES
               if o.loader and o.loader != S.INLINE and not hasattr(Runtime, o.loader)]
    assert not missing, f"loader 名在 Runtime 上不存在：{missing}"


def test_inline_types_are_marked_as_such():
    """没有单独装载方法的类型要如实写「内联」，不能编一个不存在的方法名。"""
    inline = {o.api for o in S.OBJECT_TYPES if o.loader == S.INLINE}
    assert inline == {"Scenario", "Reach", "Zone"}


def test_check_runtime_catches_a_bad_loader():
    """让自检先报出一条我已知的，确认它不是恒真。"""
    orig = S.OBJECT_TYPES
    try:
        S.OBJECT_TYPES = (dataclasses.replace(orig[0], loader="load_nothing_here"),) + orig[1:]
        errs = S.check_runtime()
        assert any("load_nothing_here" in e for e in errs), errs
    finally:
        S.OBJECT_TYPES = orig
    assert S.check_runtime() == []


def test_every_action_has_implementation_and_summary():
    miss_act = [a.api for a in S.ACTIONS if not hasattr(Runtime, "_act_" + a.api)]
    miss_sum = [a.api for a in S.ACTIONS if not hasattr(Runtime, "_sum_" + a.api)]
    assert not miss_act and not miss_sum, f"缺实现 {miss_act}；缺小结 {miss_sum}"


def test_actions_that_write_or_go_online_declare_it():
    """会写盘或出网的动作不许标 none。"""
    must = {
        "issue_alert": "out/alerts",
        "record_observation": "out/observations",
        "open_event": "out/events",
        "update_episode": "out/episodes",
        "close_point": "out/episodes",
        "set_proposal_status": "out/proposal_status",
        "traffic_status": "Google",              # refresh=true 会调 Google Routes 并写 out/traffic_cache.json
    }
    for api, hint in must.items():
        a = S.AT[api]
        se = str(a.side_effect or "none")
        assert se != "none" and hint.lower() in se.lower(), f"{api} 的 side_effect 是 {se!r}，没说清它会做什么"


def test_param_bounds_are_sane():
    bad = [(a.api, p.name) for a in S.ACTIONS for p in a.params
           if p.lo is not None and p.hi is not None and p.lo > p.hi]
    assert not bad


# ---- 参数校验的边界 ----

class _Stub:
    pass


def _validate(api, params):
    return Runtime.validate(_Stub(), S.AT[api], params)


@pytest.mark.parametrize("bad", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_non_finite_numbers_are_rejected(bad):
    """NaN 的比较恒为 False，会直接穿过 lo/hi。穿过去之后 open_event 还会先落盘再算，
    写出一行不是合法 JSON 的记录，此后整个生命周期层都读不了（2026-09-23 修）。"""
    with pytest.raises(ActionError):
        _validate("issue_alert", {"p24_mm": bad})


def test_normal_value_still_passes():
    P = _validate("issue_alert", {"p24_mm": "80"})
    assert P["p24_mm"] == 80.0 and math.isfinite(P["p24_mm"])


def test_out_of_range_still_rejected():
    with pytest.raises(ActionError):
        _validate("issue_alert", {"p24_mm": 0.0001})
    with pytest.raises(ActionError):
        _validate("issue_alert", {"p24_mm": 99999})


def test_undeclared_param_is_rejected():
    with pytest.raises(ActionError):
        _validate("issue_alert", {"p24_mm": 80, "definitely_not_a_param": 1})
