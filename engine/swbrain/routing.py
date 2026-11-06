"""L3 卡车路线：按 config.toml [routing] providers 的顺序取路线，缺省「本地 NAServer 优先、ArcGIS Online 备选」（2026-10-28 用户）。

每个来源都按同一套积水规则：先用全部障碍（深水禁行、浅水减速）；被深水截断（No route）就把深水也当减速重求一次，标 blocked。
本地两次都求不出（路网缺段 / 不连通）或连不上，换下一个来源；结果里 source = local / online，fallback_reason 写为什么换。
障碍用 online_route.barriers 的在线口径传入，本地模块自己换成 Attr_Minutes。
"""
from __future__ import annotations
from swbrain import local_route as LR, online_route as OR


def _guard(fn):
    """在线调用抛异常（密钥失效 498、断网）→ {error, transient}：_blocked_aware 会接着试减速障碍那次，那次常有旧缓存（缓存在取出行模式之前命中）。"""
    def g(a, b, bars, names):
        try:
            r = fn(a, b, bars, names) or {"error": "no result"}
        except Exception as ex:  # noqa: BLE001
            return {"error": f"{type(ex).__name__}: {str(ex)[:200]}", "transient": True}
        return r if "error" in r else {**r, "source": "online"}
    return g


_online_route = _guard(lambda a, b, bars, names: OR.route(a, b, bars, names=names))
_online_detail = _guard(OR.detail)


FUNCS = {"route": {"local": LR.route, "online": _online_route}, "detail": {"local": LR.detail, "online": _online_detail}}


def _blocked_aware(fn, a, b, hard, soft, names):
    r = fn(a, b, hard, names)
    # 本地服务：起点落在禁行障碍里时不报 No route，报「Unable to complete operation」（transient）；按同一规则把深水当减速重求（2026-10-28 A61D1 L2 SIM-L2-013 实测）
    if "error" in r and ("No route" in r["error"] or r.get("transient")):
        r2 = fn(a, b, soft, names)
        r = {**r2, "blocked": True} if "error" not in r2 else {**r2, "hard_error": r["error"]}
    return r


def solve(a, b, hard, soft, names=("起点", "终点"), kind: str = "route", providers=None) -> dict:
    """kind = route（整条线 + 导航文字，页面 routes.json 用）或 detail（逐段导航线，车辆回放用）。"""
    tried = []
    for p in providers or LR.cfg()["providers"]:
        try:
            r = _blocked_aware(FUNCS[kind][p], a, b, hard, soft, names)
        except Exception as ex:  # noqa: BLE001 — 取出行模式失败、密钥失效（498）、断网：记下原因，换下一个来源
            r = {"error": f"{type(ex).__name__}: {str(ex)[:200]}"}
        if "error" not in r:
            return {**r, "fallback_reason": "; ".join(tried) or None}
        tried.append(f"{p}: {r['error'][:160]}")
    return {"error": " | ".join(tried) or "no provider"}
