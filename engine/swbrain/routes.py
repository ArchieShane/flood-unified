"""接口表：路径 → 处理函数，一处声明、一处解析参数、一处写说明。

处理函数签名 `fn(state, qs, body, req) -> dict | (dict, code) | Raw`：
  state 服务端状态（swbrain/state.py）；qs 查询串（parse_qs 的结果）；body POST 的 JSON（GET 为 {}）；req 只给 `header(name)` 与 `path`。
返回 dict 就是 200 的 JSON，(dict, code) 指定状态码，Raw 是原样响应（报告 HTML）。错误处理与口令在 serve.py。
`GET /api` 列出全部接口（自描述），控制台与文档都可以取它。"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from . import assets as assetreg, assumptions as asm, dispatch, feeds, scenarios, threelines
from .ontology import schema as ontschema


# ---------------------------------------------------------------- 参数助手
def s1(qs: dict, k: str, d=None):
    """取第一个值；空串按缺省。"""
    v = qs.get(k)
    return (v[0] if v and v[0] != "" else d)


def f1(qs: dict, k: str, d=None):
    v = s1(qs, k)
    return float(v) if v is not None else d


def i1(qs: dict, k: str, d=None):
    v = s1(qs, k)
    return int(v) if v is not None else d


def b1(qs: dict, k: str, d: bool = False) -> bool:
    v = s1(qs, k)
    return d if v is None else str(v).lower() in ("1", "true", "yes", "on")


@dataclass
class Raw:
    """原样响应：报告 HTML 这类不是 JSON 的。"""
    body: bytes
    content_type: str = "text/html; charset=utf-8"
    headers: dict = field(default_factory=dict)
    code: int = 200


@dataclass
class Req:
    """给处理函数的请求视图：只有路径（不带查询串）、方法与请求头。让 routes.py 不依赖 http.server。"""
    path: str
    method: str = "GET"
    headers: object = None

    def header(self, name: str, default=None):
        return self.headers.get(name, default) if self.headers is not None else default


@dataclass
class Route:
    path: str
    method: str
    fn: Callable
    desc: str
    params: str = ""
    prefix: bool = False


ROUTES: list[Route] = []


def route(path: str, method: str = "GET", desc: str = "", params: str = "", prefix: bool = False):
    def deco(fn):
        ROUTES.append(Route(path, method, fn, desc, params, prefix))
        return fn
    return deco


def resolve(method: str, path: str) -> Route | None:
    for r in ROUTES:                                                   # 先精确，后前缀（精确的都登记在前）
        if r.method == method and not r.prefix and r.path == path:
            return r
    for r in ROUTES:
        if r.method == method and r.prefix and path.startswith(r.path):
            return r
    return None


# ---------------------------------------------------------------- 总览与状态
@route("/api", desc="接口清单（本表）")
def api_index(state, qs, body, req):
    return {"endpoints": [{"method": r.method, "path": r.path + ("*" if r.prefix else ""), "desc": r.desc, "params": r.params} for r in ROUTES]}


@route("/api/health", desc="服务状态：装载、情景目录、预热、盘上缓存、求解子进程、任务数")
def health(state, qs, body, req):
    return {"ok": True, "loaded_at": state.loaded_at, "load_s": state.load_s, "n_edges": int(len(state.es)), "n_proposals": len(state.props), "idf": state.cfg.get("idf"), "active_rp": state.active_rp,
            "warm": state.warm, "cached_keys": sorted(state.rp_cache.keys()), "disk_cache": state.cache.stats(), "solver_pool": state.pool.stats(), "jobs": len(state.jobs.list()),
            "state_manifest": getattr(state, "state_manifest", None),
            "scenarios": [{k: v for k, v in s.items() if k in ("key", "kind", "label", "label_en", "i15_mmh", "p24_mm", "source", "source_en", "factor")} for s in scenarios.catalog(state.cfg)], "run": state.summary.get("生成时间")}


@route("/api/summary", desc="上一轮批处理的总览（out/summary.json）")
def summary(state, qs, body, req):
    return state.summary


@route("/api/assumptions", desc="假设总账：当前依赖的模拟 / 假设 / 占位 / 未核实 / 缺失输入", params="rp")
def assumptions(state, qs, body, req):
    key = s1(qs, "rp") or state.active_rp
    items = asm.register(state.cfg, state, key)
    return {"items": items, "summary": asm.summarize(items), "scenario": key}


@route("/api/feeds", desc="六个外部源的接入状态")
def feeds_status(state, qs, body, req):
    return {"feeds": feeds.status(state.cfg)}


@route("/api/jobs", desc="异步任务：/api/jobs 列表，/api/jobs/<id> 查一个", prefix=True)
def jobs(state, qs, body, req):
    jid = req.path.rsplit("/", 1)[-1] if req.path != "/api/jobs" else ""
    if jid:
        j = state.jobs.get(jid)
        return (j, 200) if j else ({"error": f"没有任务 {jid}"}, 404)
    return {"jobs": state.jobs.list(), "pool": state.pool.stats()}


# ---------------------------------------------------------------- 第一道：筛查、片区、建议
@route("/api/scenario", desc="任意 15 min 雨强下的管网承载筛查（向量化重算）", params="i_mmh")
def scenario(state, qs, body, req):
    return state.scenario(f1(qs, "i_mmh", 30.0))


@route("/api/return_period", desc="切情景：筛查 + 积水点 + 调度一起给", params="yr")
def return_period(state, qs, body, req):
    return state.return_period(s1(qs, "yr", "100"))


@route("/api/zone", desc="片区详情", params="name")
def zone(state, qs, body, req):
    return state.zone(s1(qs, "name", ""))


@route("/api/three_lines", desc="三道防线的三个现状对比（catchment=all 三区一起看，不给就列有哪些汇水区）",
       params="catchment")
def three_lines(state, qs, body, req):
    """客户领导要看的那张对比：只有既有管网 / 加已规划的塘 / 加新挖的塘，内涝各是什么样。"""
    c = s1(qs, "catchment", "")
    if c == "all":                                            # 三个区一起看
        return threelines.compare_all(state.cfg)
    if not c:
        return {"catchments": threelines.catchments(state.cfg),
                "states": [{"key": k, "zh": zh, "en": en, "desc": d}
                           for k, zh, en, d in threelines.STATES]}
    return threelines.compare(state.cfg, c)


@route("/api/state_gap", desc="一二维耦合的四态缺口对照（L1 / L2 / L2+A / L2+B），含二维峰值面积与水深",
       params="region")
def state_gap(state, qs, body, req):
    """**和 /api/three_lines 不是一套东西，别混。**

    /api/three_lines 读 out/three_lines/，是纯一维的流水线（R61D1 / R61D24 标签）。
    这一个读 out/state_table_<区>_<雨情>.json —— 一二维**耦合**跑出来的，
    带二维侧的峰值淹没面积与水深。两套的雨情、演算窗口、是否耦合都不同，
    数对不上是正常的，所以页面上必须分开标，不能并排当成同一批。
    """
    import json
    from pathlib import Path

    reg = s1(qs, "region", "Mussafah_00")
    root = Path(__file__).resolve().parents[1] / "out"
    hits = sorted(root.glob(f"state_table_{reg}*.json"))
    if not hits:
        avail = sorted(f.name for f in root.glob("state_table_*.json"))
        return {"available": False, "region": reg,
                "note": f"没有 {reg} 的耦合四态结果", "files": avail}
    d = json.loads(hits[-1].read_text(encoding="utf-8"))
    d["available"] = True
    d["source"] = hits[-1].name
    # 逐座设施的理由，有就一起带上
    w = root / "l2plus" / f"why_Mussafah_{d.get('scen','').replace('/', '')}.json"
    if not w.exists():
        cand = sorted((root / "l2plus").glob("why_*.json")) if (root / "l2plus").exists() else []
        w = cand[-1] if cand else None
    if w and w.exists():
        d["facilities"] = json.loads(w.read_text(encoding="utf-8"))
    return d


@route("/api/ponding", desc="二维模拟积水点（SIM-态-序号，不是客户热点编号）：按水深 / 面积 / 持续阈值从一二维结果判出每片积水的位置、水深、面积、峰值积水量、退水；不给 run 就列有哪些运行；给 ref（对照基准态）就按共有网格对应基准态的积水点，列出已消除 / 新出现",
       params="run, state, depth, min_area, min_dur, hotspot, geom, pond_scope, ref")
def ponding_points(state, qs, body, req):
    from . import ponding
    run, st = s1(qs, "run"), s1(qs, "state")
    if not run:
        return {"runs": ponding.available(), "defaults": ponding.DEFAULTS}
    if not st:
        return {"run": run, "states": ponding.available(run).get(run, []), "defaults": ponding.DEFAULTS}
    args = (f1(qs, "depth", ponding.DEFAULTS["depth"]), f1(qs, "min_area", ponding.DEFAULTS["min_area"]), f1(qs, "min_dur", ponding.DEFAULTS["min_dur"]))
    scope = s1(qs, "pond_scope") or "state"
    try:
        r = ponding.compute(run, st, *args, geometry=b1(qs, "geom", True), pond_scope=scope)
        ref = s1(qs, "ref")
        if ref and ref != st:
            c = ponding.compare(run, st, ref, *args, pond_scope=scope, geometry=b1(qs, "geom", True))
            r = {**r, "feats": [{**f, "ref": c["match"].get(f["id"], [])} for f in r["feats"]],
                 "compare": {k: c[k] for k in ("ref", "gone", "summary", "ref_brief")}}
    except (ValueError, FileNotFoundError, RuntimeError) as ex:
        return {"error": str(ex)}, 400
    hs = s1(qs, "hotspot")
    if hs:
        r = {**r, "feats": ponding.near_hotspot(r, hs), "filter_hotspot": hs}
    return r


@route("/api/three_lines_net", desc="三态页的基础设施底图（管线、检查井、雨水口、出水口、泵、湿井、防线2 的塘）",
       params="catchment, rebuild")
def three_lines_net(state, qs, body, req):
    """三个现状共用同一套既有管网，底图单独发一次，页面三态复用。"""
    c = s1(qs, "catchment", "")
    if not c:
        return {"catchments": [x["catchment"] for x in threelines.catchments(state.cfg)],
                "layers": [{"key": k, "zh": zh, "en": en} for k, zh, en in threelines.NET_LAYERS]}
    return threelines.network(state.cfg, c, rebuild=b1(qs, "rebuild"))


@route("/api/assets", desc="排水资产台账（客户 L1/L2/L3 分级里的实体资产）",
       params="kind, level, delivery, source, status, limit")
def assets(state, qs, body, req):
    """筛选写成逐个 s1(qs, "字面名") —— 合约自检是静态扫 AST 的，用循环变量它看不见。"""
    rows = assetreg.load(cfg=state.cfg)
    for f, v in (("kind", s1(qs, "kind", "")), ("level", s1(qs, "level", "")),
                 ("delivery", s1(qs, "delivery", "")), ("source", s1(qs, "source", "")),
                 ("status", s1(qs, "status", ""))):
        if v:
            rows = [r for r in rows if str(r.get(f)) == v]
    n = len(rows)
    lim = i1(qs, "limit", 2000)
    return {"n": n, "shown": min(n, lim), "summary": assetreg.summary(rows),
            "assets": rows[:lim]}


@route("/api/proposal", desc="一条工程建议", params="pid")
def proposal(state, qs, body, req):
    return state.proposal(i1(qs, "pid", 0))


@route("/api/whatif", desc="一条建议的 what-if（全网代理评估）", params="pid")
def whatif(state, qs, body, req):
    return state.whatif_one(i1(qs, "pid", 0))


@route("/api/trace", desc="管段下游追溯", params="gid")
def trace(state, qs, body, req):
    return state.trace(i1(qs, "gid", 0))


@route("/api/hotspot", desc="单个客户热点详情（匹配管段、上游树、洼地、退水曲线）", params="id, dn, rp")
def hotspot(state, qs, body, req):
    return state.hotspot_detail(s1(qs, "id", ""), i1(qs, "dn", 2), key=s1(qs, "rp"))


# ---------------------------------------------------------------- 第二、三道：调度与运行
@route("/api/dispatch", desc="某决策时刻的调度（async=1 走任务）", params="rp, t, objective, budget, robust, async")
def api_dispatch(state, qs, body, req):
    # rp 以前不是参数，传了会被静默忽略，算的一直是当前激活情景——同一个 ?rp=10cc 在不同进程里
    # 会给出完全不同的数（2026-09-23 修）。现在指定了就装那个情景自己的上下文。
    key = s1(qs, "rp") or None
    ctx = None
    if key and str(key) != str(state.active_rp):
        ctx, _ = state._ensure_ctx(str(key))
        if ctx is None:
            return {"error": f"情景 {key} 的调度上下文装不起来"}, 400
    base = ctx if ctx is not None else state.dispatch_ctx
    t_def = dispatch.default_decision_time(base["fp"], state.cfg) if base else state.cfg["dispatch"]["decision_time_min"]
    args = dict(t_min=f1(qs, "t", float(t_def)), ctx=ctx, key=str(key) if key else state.active_rp,
                objective=s1(qs, "objective"), budget_aed=f1(qs, "budget"), robust=b1(qs, "robust"))
    if b1(qs, "async"):
        hit = state.dispatch_cached(t_min=args["t_min"], key=args["key"], objective=args["objective"], budget_aed=args["budget_aed"], robust=args["robust"])
        return hit if hit is not None else state.jobs.submit("dispatch", {k: v for k, v in args.items() if k != "ctx"}, lambda: state.dispatch_at(**args))
    return state.dispatch_at(**args)


@route("/api/stress", desc="压力测试：当前调度单放到更重情景（async=1 走任务）", params="rp, worse, t, objective, async")
def stress(state, qs, body, req):
    args = (s1(qs, "rp", str(state.active_rp or "10")), s1(qs, "worse"), f1(qs, "t"), s1(qs, "objective"))
    if b1(qs, "async"):
        return state.jobs.submit("stress", {"rp": args[0], "worse": args[1], "t": args[2], "objective": args[3]}, lambda: state.stress_test(*args))
    return state.stress_test(*args)


@route("/api/focus", desc="单点最优严格版与全局解对比（async=1 走任务）", params="rp, fp, t, objective, async")
def focus(state, qs, body, req):
    args = (s1(qs, "rp", str(state.active_rp or "10")), s1(qs, "fp", ""), f1(qs, "t"), s1(qs, "objective"))
    if b1(qs, "async"):
        return state.jobs.submit("focus", {"rp": args[0], "fp": args[1], "t": args[2], "objective": args[3]}, lambda: state.focus_compare(*args))
    return state.focus_compare(*args)


@route("/api/staging_status", desc="预置到位：需要 vs 预案 A vs check-in", params="rp, alert")
def staging_status(state, qs, body, req):
    return state.staging_status(s1(qs, "rp"), s1(qs, "alert", "橙"))


@route("/api/traffic", desc="路况：来源、驻地系数、时变序列、逐对精算", params="refresh, depart, t, rp")
def traffic(state, qs, body, req):
    return state.traffic_status(refresh=b1(qs, "refresh"), depart=s1(qs, "depart"), t_min=f1(qs, "t"), key=s1(qs, "rp"))


@route("/api/replay", desc="单点退水重演（按调度单或手动派泵）", params="fp, pumps, t")
def replay(state, qs, body, req):
    return state.replay_point(s1(qs, "fp", ""), s1(qs, "pumps"), f1(qs, "t"))


@route("/api/recession", desc="一个点什么时候退水、怎样最快", params="fp, rp, t, upsize")
def recession(state, qs, body, req):
    return state.recession(s1(qs, "fp", ""), s1(qs, "rp"), f1(qs, "t"), b1(qs, "upsize", True))


@route("/api/alert", desc="预报雨量 → 预警等级与分角色提示", params="p24, scenario, lead_h, area, source")
def alert(state, qs, body, req):
    return state.alert(p24_mm=f1(qs, "p24"), key=s1(qs, "scenario"), lead_h=f1(qs, "lead_h", 24.0), area=s1(qs, "area"), source=s1(qs, "source", "manual"))


@route("/api/gwm_points", desc="世界模型 × 客户热点 × 本系统三方对照", params="rp, run")
def gwm_points(state, qs, body, req):
    return state.gwm_points(s1(qs, "rp"), b1(qs, "run"))


@route("/api/report", desc="一键报告（自包含 HTML）", params="rp, lang")
def report(state, qs, body, req):
    key = s1(qs, "rp"); lang = s1(qs, "lang", "zh")
    html = state.report_html(key, lang).encode("utf-8")
    return Raw(html, headers={"Content-Disposition": f'inline; filename="swbrain_report_{key or "x"}.html"'})


# ---------------------------------------------------------------- 本体与智能体
@route("/api/ontology", desc="本体全貌：对象 / 关系 / 规则 / 动作 + 规划器")
def ontology(state, qs, body, req):
    d = ontschema.to_dict(); d["planner"] = state.agent.planner_name(); d["scenarios"] = scenarios.keys(state.cfg); d["dsh"] = state.agent.dsh_info()
    d["examples"] = [{"zh": (a.examples[0] if a.examples else ""), "en": (a.examples[1] if len(a.examples) > 1 else (a.examples[0] if a.examples else ""))} for a in ontschema.ACTIONS]
    from . import asset_kb                                  # 资产图鉴：每种资产的定义 / 功能 / 分级 / 建模口径 / 图（多模态）
    d["asset_kinds"] = asset_kb.catalog(s1(qs, "lang", "zh"))
    d["asset_functions"] = {k: {"zh": v[0], "en": v[1]} for k, v in asset_kb.FUNCTIONS.items()}
    return d


@route("/api/object", desc="一个对象 + 规则判定 + 全部关系", params="type, id, rp, links")
def obj(state, qs, body, req):
    ot = s1(qs, "type", "FloodPoint"); oid = s1(qs, "id", ""); rp_ = s1(qs, "rp")
    o = state.ontology.get(ot, oid, rp_)
    links = {l.api: state.ontology.links(ot, oid, l.api, rp_) for l in ontschema.LINK_TYPES if l.src == ot} if (o and b1(qs, "links", True)) else {}
    return {"type": ot, "id": oid, "object": o, "rules": (state.ontology.rules(ot, o, rp_) if o else []), "links": links}


@route("/api/objects", desc="对象集查询（任意属性等值过滤 + 排序 + 截断）", params="type, rp, order, desc, limit, <属性>=<值>")
def objs(state, qs, body, req):
    ot = s1(qs, "type", "FloodState"); rp_ = s1(qs, "rp")
    filt = {k: ({"true": True, "false": False}.get(v[0].lower(), v[0])) for k, v in qs.items() if k not in ("type", "rp", "order", "limit", "key", "desc")}
    return {"type": ot, "filters": filt, "rows": state.ontology.search(ot, filt, s1(qs, "order"), b1(qs, "desc", True), i1(qs, "limit", 20), rp_)}


def _chat(state):
    """多智能体（主智能体分派）开着就走它，否则直接找调度研判智能体（原来的排水智能体）。"""
    ag = getattr(state, "multi", None) or getattr(state, "agent", None)
    if ag is None:
        from .ontology.common import ActionError
        raise ActionError("精简模式：还没有运行产物，智能体不可用（放好 out/state/ 等运行产物或跑 python -m swbrain.run，再重启服务）")
    return ag


@route("/api/agent", desc="问一句（GET 用 q=；POST 带 history / session_id）", params="q, lang, rp")
def agent_get(state, qs, body, req):
    return _chat(state).chat(s1(qs, "q", ""), lang=s1(qs, "lang"), scenario=s1(qs, "rp"))


@route("/api/agent", method="POST", desc="对话（参数在请求体里）", params="message, history, lang, scenario, session_id")
def agent_post(state, qs, body, req):
    return _chat(state).chat(str(body.get("message") or ""), history=body.get("history") or [], lang=(body.get("lang") or None), scenario=(body.get("scenario") or None), session_id=(body.get("session_id") or None))


@route("/api/whoami", desc="当前是谁、有哪些职能、每个会写数据的动作能不能做（只读；统一身份模式下按平台现查的职能算）")
def whoami(state, qs, body, req):
    from . import identity as I
    from .ontology import schema as S
    p = I.current()
    if p is None:
        return {"who": None}
    if p.refresh is not None:
        p = p.refresh() or p                                  # 现查：撤销立即反映
    acts = {A.api: p.has(I.action_function(A.api)) for A in S.ACTIONS if A.side_effect != "none"}
    acts["traffic_status(refresh)"] = p.has(I.ADMIN)
    idn = getattr(state, "identity", None)
    return {"who": p.public(), "mode": idn.cfg.mode if idn else "shared_key", "can": acts, "admin": p.has(I.ADMIN),
            "functions": {c: I.FUNCTIONS[c] for c in I.FUNCTIONS if p.has(c)}}


@route("/api/action_check", method="POST", desc="试一下：按当前登录人的职能判这个动作能不能做，不执行、不写数据（联调核对用）", params="action, params")
def action_check(state, qs, body, req):
    from . import identity as I
    from .ontology import schema as S
    api = str(body.get("action") or ""); A = S.AT.get(api)
    if A is None:
        return {"error": f"没有动作 {api}"}, 400
    p = I.current()
    if p is not None and p.refresh is not None:
        p = p.refresh() or p
    need = I.action_function(api, body.get("params") or {})
    ok = p is None or p.has(need)
    return {"action": api, "need": need, "need_zh": I.FUNCTIONS.get(need), "allowed": ok, "who": p.actor if p else None}


@route("/api/identity_uat", method="POST", desc="统一身份联调：把 whoami.html 的核对结果记一行（out/identity_uat.jsonl，带服务端认定的主体号）", params="checked_at, who, mode, actions, admin, self_approval")
def identity_uat(state, qs, body, req):
    import json as _j, pathlib as _p, time as _t
    from . import identity as I
    p = I.current()
    rec = {"ts": _t.strftime("%Y-%m-%d %H:%M:%S"), "server_actor": p.actor if p else None,          # 以服务端认定的身份为准，不信页面自报
           "checked_at": body.get("checked_at"), "who": body.get("who"), "mode": body.get("mode"), "actions": body.get("actions"),
           "admin": body.get("admin"), "self_approval": body.get("self_approval")}
    f = _p.Path(__file__).resolve().parents[1] / "out" / "identity_uat.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(_j.dumps(rec, ensure_ascii=False) + "\n")
    return {"file": "out/identity_uat.jsonl"}


@route("/api/harness", desc="Harness 原生 Web UI：状态；launch=1 时按需启动（只读），登录后才给带一次性 token 的地址", params="launch")
def harness(state, qs, body, req):
    from . import identity as I
    a = getattr(state, "agent", None)
    if a is None:
        return {"error": "精简模式：智能体未装载（要先放运行产物或跑 python -m swbrain.run）"}, 400
    from . import dsh_bridge as _D
    if _D.lan_request(req.header("Host"), state.cfg):                # dsh 只听本机；局域网走局域网入口（dsh_lanproxy，只放管理员）
        if not _D.settings(state.cfg)["lan_port"]:
            return {"error": "Harness 原生 Web UI 的局域网入口没开（[agent.dsh].lan_port = 0）；请在服务所在的电脑上打开，或用控制台里的对话。",
                    "running": False, "url": None}, 400
        from .agents import admin_routes as _A
        if not _A.is_admin(state, req):
            return {"error": "从局域网打开 Harness 原生 Web UI 要管理员（它能在服务所在的电脑上执行代码）。普通用户请用控制台里的对话，功能相同。",
                    "running": False, "url": None}, 403
    br = a.web_ui()
    br.idn = getattr(state, "identity", None)                       # 只读凭据由这个实例发（服务端内存）
    if br.idn is not None:
        br.write_credential()                                       # 写 <home_web>/.delegation：容器方式下那边读同一个文件
    launch = b1(qs, "launch")
    info = br.ensure() if launch else br.info()
    info["url"] = _D.web_url_for(info.get("url"), req.header("Host"), state.cfg)
    p = I.current()
    info.update({"who": p.public() if p is not None else None,
                 "note": "这个入口只读：会写数据的动作（签发预警、登记观测、提交申请等）会被拒，请到控制台用平台账号登录后按职能做。"})
    return info


def needs(r, method: str, path: str, qs: dict) -> str | None:
    """这个接口要的职能（统一身份；serve.py 在调处理函数前判）。只读接口 = VIEW（登录时已判过，这里返回 None）。
    本体动作（/api/action、/api/agent 里智能体调的）由 Runtime.act 按动作判，不在这里。"""
    from . import identity as I
    if path.startswith("/api/feed/") and method == "POST":
        return I.FEED_FUNCTION.get(path.rsplit("/", 1)[-1], I.FEED)
    if path == "/api/traffic" and b1(qs, "refresh"):
        return I.ADMIN                                       # 刷新路况按次计费
    if path.startswith("/api/admin/") and path not in ("/api/admin/whoami", "/api/admin/login", "/api/admin/logout"):
        return I.ADMIN
    return None


def api_actor(claimed) -> str:
    """/api/action 的 actor 由服务端定：一律 api:<调用方自报的标签>。调用方自报的只是标签、不是身份，
    不能自称 console 绕过管理端停用（Runtime.act 只对 actor == "console" 豁免）。接入统一身份后改为会话里的主体号。"""
    tag = re.sub(r"[^A-Za-z0-9_.:-]", "", str(claimed or ""))[:40] or "anon"
    return f"api:{tag}"


@route("/api/action", method="POST", desc="直接执行一个本体动作（参数在请求体里）；actor 只作标签，记为 api:<标签>，管理端停用的动作照样被拒", params="action, params, actor, lang")
def action(state, qs, body, req):
    return state.ontology.act(str(body.get("action") or ""), body.get("params") or {}, actor=api_actor(body.get("actor")), lang=str(body.get("lang") or "zh"))


# ---------------------------------------------------------------- 写入：重规划与外部源推送
@route("/api/replan", method="POST", desc="现场重规划", params="rp, t_now, dry_points, failed_units, incidents, objective, async")
def replan(state, qs, body, req):
    args = (str(body.get("rp") or state.active_rp or "10"), float(body.get("t_now") or 0), body.get("dry_points") or [], body.get("failed_units") or [], body.get("incidents") or [], body.get("objective"))
    if body.get("async") or b1(qs, "async"):
        return state.jobs.submit("replan", {"rp": args[0], "t_now": args[1], "dry": args[2], "failed": args[3], "n_inc": len(args[4]), "objective": args[5]}, lambda: state.replan(*args))
    return state.replan(*args)


@route("/api/feed/", method="POST", desc="推送外部源：/api/feed/<incidents|checkins|bridges|contractors|events|episodes>", prefix=True)
def feed_push(state, qs, body, req):
    name = req.path.rsplit("/", 1)[-1]
    if name not in feeds.NAMES:
        return {"error": f"未知的源：{name}"}, 404
    r = feeds.push(state.cfg, name, body, origin=str(req.header("X-Source") or "push"))
    if r.get("accepted"):
        try:
            state.ontology._invalidate_lifecycle()             # 事件 / 经历 / 报案变了，本体的生命周期缓存作废
        except Exception:
            pass
    return (r, 400) if (r.get("received") and not r.get("accepted")) else r


# ---------------------------------------------------------------- 平台转发：按工具名直接调
def _本地取数(state):
    """给 mcp_server.call_tool 的两个取数口，走**本进程内的处理函数**，不再 HTTP 调自己。

    ⚠️ 这一处不改任何判据：调的是同一张接口表里的同一个函数，参数照 parse_qs 的形状给，
    所以 s1/f1/i1 那些助手照常工作。区别只有「不经过 socket 与身份重建」这一条。
    """
    def 一次(method, path, qs, body):
        r = resolve(method, path)
        if r is None:
            return {"error": f"未知接口 {path}"}
        out = r.fn(state, qs, body, Req(path=path, method=method))
        return (out[0] if isinstance(out, tuple) else out)

    def get(path, params=None):
        return 一次("GET", path, {k: [str(v)] for k, v in (params or {}).items() if v is not None and v != ""}, {})

    # ⚠️ 请求体要原样传给处理函数：POST 的处理函数读的是 body.get(...)，不是查询串。
    #    此前这里丢了 body，于是平台转发进来的每一个动作都落成「没有动作 」（空名字）——
    #    走 HTTP 的 /api/action 没这个问题，只有这条进程内的路有（2026-10-04 实测）。
    def post(path, body=None):
        return 一次("POST", path, {}, dict(body or {})) or {}

    return get, post


@route("/api/tool/", method="POST", desc="按工具名直接调一个本体工具（请求体就是入参）；平台把一次问数转进来时走这一条，判据与 /api/action 同源", prefix=True)
def tool_call(state, qs, body, req):
    from . import mcp_server as M
    name = req.path.rstrip("/").rsplit("/", 1)[-1]
    get, post = _本地取数(state)
    # ⚠️ owner="*"：平台上「一个智能体能碰哪些工具」由平台的智能体声明定，
    #    这里再按排水侧内部的四个智能体切一刀会切出两边都不认得的交集。
    #    readonly=False：这一支不是 Harness 那个只读入口，写不写按发起人的职能判。
    #    三条已有的闸（管理端停用、发起人职能、只读入口拦截）都在 Runtime.act 里，与这两项无关。
    text, is_err = M.call_tool(name, body or {}, get=get, post=post, owner="*", readonly=False)
    try:
        out = json.loads(text)
    except Exception:
        out = {"结果": text}
    return {"tool": name, "isError": bool(is_err), **({"结果": out} if not isinstance(out, dict) else out)}


from .agents import admin_routes as _admin_routes  # noqa: E402,F401  管理端接口（/api/admin/*、/api/agents）登记到本表