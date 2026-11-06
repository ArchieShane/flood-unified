"""平台转发那条进程内取数口（routes._本地取数）要把请求体原样交给处理函数。

2026-10-04 实测：post() 丢了 body，于是平台经 /api/tool/<名> 转进来的每一个动作
都落成「没有动作 」（空名字）；走 HTTP 的 /api/action 没这个问题，所以只靠浏览器点
永远发现不了。这里不起服务、不装本体，只换一个假的 state 看处理函数收到了什么。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swbrain import routes as R


class _本体:
    def __init__(self):
        self.收到 = None

    def act(self, api, params, actor=None, lang="zh"):
        self.收到 = (api, params, lang)
        return {"ok": True, "api": api}


class _State:
    def __init__(self):
        self.ontology = _本体()


def test_post_把请求体交给处理函数():
    s = _State()
    _, post = R._本地取数(s)
    out = post("/api/action", {"action": "asset_kind", "params": {"kind": "pump"}, "lang": "en"})
    assert out == {"ok": True, "api": "asset_kind"}
    assert s.ontology.收到 == ("asset_kind", {"kind": "pump"}, "en")


def test_post_不改调用方的字典():
    s = _State()
    _, post = R._本地取数(s)
    体 = {"action": "asset_kind", "params": {}}
    post("/api/action", 体)
    assert 体 == {"action": "asset_kind", "params": {}}


def test_post_未知接口照实说():
    _, post = R._本地取数(_State())
    assert "未知接口" in post("/api/没有这条", {}).get("error", "")
