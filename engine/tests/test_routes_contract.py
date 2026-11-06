"""接口表的声明与实现对不对得上。

2026-09-23 评审实测：`/api/dispatch` 的 params 里没有 rp，函数体也不读它，
但调用方普遍会传 `?rp=10cc`——传了被静默忽略，算的是当前激活情景。
同一条 URL 在不同进程里给出完全不同的数（点数 434 vs 448、加权积水时长差三倍），
而返回里没有任何地方说明它用的是哪个情景。
"""
import ast
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swbrain import routes as R

SRC = (Path(__file__).resolve().parents[1] / "swbrain" / "routes.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)

# qs 的取值助手；第二个位置参数就是参数名
HELPERS = {"s1", "f1", "i1", "b1"}
# 这些键不在 params 里声明也合理：内部/通用开关
IMPLICIT = {"key", "lang"}
# params 里的文档占位（/api/objects 支持按任意属性过滤），不是真参数名
PLACEHOLDER = re.compile(r"^<.*>$")


def _functions():
    """{函数名: (声明的 params 集合, 函数体里实际读的 qs 键集合)}"""
    out = {}
    for node in TREE.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        declared = set()
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "route"):
                continue
            for kw in dec.keywords:
                if kw.arg == "params" and isinstance(kw.value, ast.Constant):
                    declared = {p.strip() for p in str(kw.value.value).split(",") if p.strip()}
        if not node.decorator_list:
            continue
        read = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") in HELPERS and len(sub.args) >= 2:
                a = sub.args[1]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    read.add(a.value)
            # qs["x"] / qs.get("x") / body["x"] / body.get("x")（POST 的参数在请求体里）
            if isinstance(sub, ast.Subscript) and getattr(sub.value, "id", "") in ("qs", "body") and isinstance(sub.slice, ast.Constant):
                read.add(str(sub.slice.value))
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "get" \
                    and getattr(sub.func.value, "id", "") in ("qs", "body") and sub.args and isinstance(sub.args[0], ast.Constant):
                read.add(str(sub.args[0].value))
        out[node.name] = (declared, read)
    return out


FUNCS = _functions()


def test_every_declared_param_is_actually_read():
    """声明了就得读。声明了不读 = 调用方以为生效、实际被忽略。"""
    bad = {}
    for name, (declared, read) in FUNCS.items():
        miss = {p for p in declared - read - IMPLICIT if not PLACEHOLDER.match(p)}
        if miss:
            bad[name] = sorted(miss)
    assert not bad, "这些接口声明了参数却不读它：" + "; ".join(f"{k}: {v}" for k, v in bad.items())


def test_every_read_param_is_declared():
    """读了就得声明，否则接口清单（/api）骗人。"""
    bad = {name: sorted(read - declared - IMPLICIT) for name, (declared, read) in FUNCS.items()
           if read - declared - IMPLICIT}
    assert not bad, "这些接口读了没声明的参数：" + "; ".join(f"{k}: {v}" for k, v in bad.items())


def test_dispatch_declares_scenario():
    declared, read = FUNCS["api_dispatch"]
    assert "rp" in declared and "rp" in read, "调度接口必须认 rp，否则算的是当前激活情景"


def test_route_table_has_no_duplicate_paths():
    seen = {}
    for r in R.ROUTES if hasattr(R, "ROUTES") else []:
        k = (r.method, r.path)
        assert k not in seen, f"路由重复：{k}"
        seen[k] = True


def test_every_route_has_a_description():
    missing = [r.path for r in (R.ROUTES if hasattr(R, "ROUTES") else []) if not str(getattr(r, "desc", "")).strip()]
    assert not missing, f"这些路由没有说明：{missing}"
