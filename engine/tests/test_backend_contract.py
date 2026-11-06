"""假后端（tests/test_ontology.py 的 Fake）与真后端（swbrain.state.State）的契约。

2026-09-23 评审里对系统信心折损最大的一条：本体层测得很好（runtime 80%、schema 90%、
actions/points 81%），但全部跑在手写的 Fake 上，而真后端 State 只有 6.8% 被执行过——
**没有任何一条测试断言两者的接口一致**。真后端少一个方法、改一个参数名，200 个测试全绿，线上炸。

这里静态比对三件事：
  1. 本体层在 `self.b.*` 上用到的每个名字，State 上都得有；
  2. Fake 声明的每个方法，State 上都得有，且必填参数对得上；
  3. Fake 声明的每个数据属性，State.__init__ 里都得赋值。
"""
import ast
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swbrain.state import State
from tests.test_ontology import Fake

ROOT = Path(__file__).resolve().parents[1]
ONT = ROOT / "swbrain" / "ontology"


def _backend_names() -> set[str]:
    """本体层源码里所有 self.b.<name> 的 name。"""
    names = set()
    for f in sorted(ONT.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            v = node.value
            if isinstance(v, ast.Attribute) and v.attr == "b" and isinstance(v.value, ast.Name) and v.value.id == "self":
                names.add(node.attr)
    return names


def _init_assigns() -> set[str]:
    """State.__init__ 里 self.<x> = … 的 x。"""
    tree = ast.parse((ROOT / "swbrain" / "state.py").read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            for sub in ast.walk(node):
                tgts = []
                if isinstance(sub, ast.Assign):
                    tgts = sub.targets
                elif isinstance(sub, ast.AnnAssign):
                    tgts = [sub.target]
                for t in tgts:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                        out.add(t.attr)
    return out


BACKEND_NAMES = _backend_names()
STATE_MEMBERS = set(dir(State)) | _init_assigns()


def test_backend_is_actually_used():
    """判据别恒真：本体层确实通过 self.b 用了不少东西。"""
    assert len(BACKEND_NAMES) >= 10, f"只扫到 {len(BACKEND_NAMES)} 个 self.b.*，提取器八成坏了"


def test_every_backend_name_exists_on_state():
    missing = sorted(n for n in BACKEND_NAMES if n not in STATE_MEMBERS)
    assert not missing, f"本体层用到但 State 上没有的：{missing}"


# 本体层用到、但假后端没有的后端方法。Fake 上没有 = 这些动作路径在单元测试里一条都没走过，
# 只有 SWBRAIN_INTEGRATION=1 的真机集成测试会碰到。这是个棘轮：只许变短，不许变长。
UNCOVERED_ON_FAKE = {
    "_ensure_ctx",      # 装某个情景的调度上下文
    "dispatch_at",      # dispatch_plan / 调度相关动作
    "focus_compare",    # 单点最优严格版 vs 全局解
    "recession",        # recession_options / recession_batch
    "replan",           # 事中重规划
    "staging_status",   # 预置到位
    "stress_test",      # 压力测试
    "traffic_status",   # 路况
}


def test_fake_gap_only_shrinks():
    """假后端撑不起来的后端方法只许变少。变多 = 又添了一条单元测试走不到的动作路径。"""
    fake_members = set(dir(Fake))
    missing = {n for n in BACKEND_NAMES if n not in fake_members}
    new_gaps = sorted(missing - UNCOVERED_ON_FAKE)
    assert not new_gaps, f"又多了单元测试覆盖不到的后端方法：{new_gaps}（要么给 Fake 补上，要么写进 UNCOVERED_ON_FAKE 并说明）"
    closed = sorted(UNCOVERED_ON_FAKE - missing)
    assert not closed, f"这些已经补上了，把它们从 UNCOVERED_ON_FAKE 里删掉：{closed}"


def test_fake_methods_match_state_signatures():
    """Fake 的方法在 State 上必须存在，且 State 的必填参数不能比 Fake 多。"""
    bad = []
    for name, fn in vars(Fake).items():
        if name.startswith("_") or not callable(fn):
            continue
        real = getattr(State, name, None)
        if real is None:
            bad.append(f"{name}: State 上没有")
            continue
        try:
            fs = inspect.signature(fn)
            rs = inspect.signature(real)
        except (TypeError, ValueError):
            continue
        def required(sig):
            return [p.name for p in list(sig.parameters.values())[1:]
                    if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        rf, rr = required(fs), required(rs)
        if len(rr) > len(rf):
            bad.append(f"{name}: State 必填 {rr}，Fake 只给了 {rf}")
    assert not bad, "假后端与真后端对不上：" + "; ".join(bad)


def test_fake_data_attributes_exist_on_state():
    """Fake 直接摆出来的数据属性（cfg / es / hotspots / by_pid …），State.__init__ 里得赋值。"""
    TEST_ONLY = {"calls"}          # Fake 自己记调用用的，不是后端接口的一部分
    data_attrs = {n for n, v in vars(Fake).items()
                  if not n.startswith("_") and not callable(v) and n not in TEST_ONLY
                  and not isinstance(v, (staticmethod, classmethod, property))}
    missing = sorted(n for n in data_attrs if n not in STATE_MEMBERS)
    assert not missing, f"Fake 摆了但 State 上没有的数据属性：{missing}"
