# -*- coding: utf-8 -*-
"""管理端接口（/api/admin/*）：多智能体清单与状态、配置、会话与调用轨迹、一二维计算任务队列（2026-09-30）。

口令：[serve].admin_key（只在 config.toml，不入库），与控制台的访问口令 access_key 分开——能进控制台的人进不了管理端。
  POST /api/admin/login {key} 对了种 HttpOnly cookie swb_admin（12 小时）；连续输错 5 次锁 10 分钟。
  admin_key 为空 = 管理端关闭（接口一律 403），不会因为没设口令就对所有人敞开。
所有写操作（改配置、批准 / 驳回 / 暂停 / 恢复 / 取消计算）记 out/agents/admin_audit.jsonl。
这些接口挂在 routes.ROUTES 上（routes.py 末尾 import 本模块），先过 serve.py 的访问口令，再过这里的管理员口令。
"""
from __future__ import annotations

import hmac
import json
import pathlib
import threading
import time
from urllib.parse import quote, unquote

from .. import routes as RT
from . import computejobs as CJ, orchestrator as ORC, registry as REG

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUDIT = ROOT / "out" / "agents" / "admin_audit.jsonl"
COOKIE = "swb_admin"
_fail = {"n": 0, "until": 0.0, "first": 0.0}
_lock = threading.Lock()


def _key(state) -> str:
    return str((state.cfg.get("serve") or {}).get("admin_key") or "").strip()


def _cookie(req) -> str:
    for part in (req.header("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return unquote(v)
    return ""


def _sdip(state) -> bool:
    idn = getattr(state, "identity", None)
    return bool(idn is not None and idn.sdip)


def _who() -> str:
    """审计与任务历史里记的「谁」：统一身份下是 sdip:<主体号>，共享口令下是 admin。"""
    from .. import identity
    p = identity.current()
    return p.actor if (p is not None and p.kind != "local") else "admin"


def is_admin(state, req) -> bool:
    if _sdip(state):                                          # 统一身份：持有 SWBRAIN.ADMIN 才是管理员，admin_key 不认
        from .. import identity
        p = identity.current()
        return bool(p is not None and p.has(identity.ADMIN))
    want = _key(state)
    return bool(want) and hmac.compare_digest(_cookie(req).encode(), want.encode())


def _need(state, req):
    if _sdip(state):
        return None if is_admin(state, req) else ({"error": "要职能 SWBRAIN.ADMIN（系统管理）", "admin": "forbidden"}, 403)
    if not _key(state):
        return {"error": "管理端没有开：在 config.toml 的 [serve] 里设 admin_key", "admin": "disabled"}, 403
    if not is_admin(state, req):
        return {"error": "需要管理员口令", "admin": "required"}, 401
    return None


def _audit(op: str, detail: dict):
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "op": op, "who": _who(), **detail}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _json_raw(obj, code=200, cookie: str | None = None):
    return RT.Raw(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", {"Set-Cookie": cookie} if cookie else {}, code)


# ---------------------------------------------------------------- 登录
@RT.route("/api/admin/whoami", desc="管理端：是否已登录、管理端开没开")
def whoami(state, qs, body, req):
    if _sdip(state):
        from .. import identity
        p = identity.current()
        return {"admin": is_admin(state, req), "configured": True, "mode": "sdip", "who": p.public() if p else None}
    return {"admin": is_admin(state, req), "configured": bool(_key(state)), "mode": "shared_key"}


@RT.route("/api/admin/login", method="POST", desc="管理端登录（管理员口令，与访问口令分开）", params="key")
def login(state, qs, body, req):
    if _sdip(state):
        return {"error": "已启用统一身份：用 SDIP 账号登录，持有 SWBRAIN.ADMIN 才能进管理端；管理员口令不再使用", "admin": "sdip"}, 400
    want = _key(state)
    if not want:
        return {"error": "管理端没有开：在 config.toml 的 [serve] 里设 admin_key", "admin": "disabled"}, 403
    with _lock:
        now = time.time()
        if now < _fail["until"]:
            return {"error": f"输错太多次，{int(_fail['until'] - now) // 60 + 1} 分钟后再试"}, 429
        if hmac.compare_digest(str(body.get("key") or "").encode(), want.encode()):
            _fail.update(n=0, until=0.0)
            _audit("login", {"ok": True})
            return _json_raw({"admin": True}, 200, f"{COOKIE}={quote(want)}; Path=/; Max-Age=43200; SameSite=Strict; HttpOnly")
        if now - _fail["first"] > 600:
            _fail.update(n=0, first=now)
        _fail["n"] += 1
        if _fail["n"] >= 5:
            _fail["until"] = now + 600
        _audit("login", {"ok": False})
    return {"error": "管理员口令不对"}, 401


@RT.route("/api/admin/logout", method="POST", desc="管理端退出")
def logout(state, qs, body, req):
    return _json_raw({"admin": False}, 200, f"{COOKIE}=; Path=/; Max-Age=0; SameSite=Strict; HttpOnly")


# ---------------------------------------------------------------- 智能体与配置
def _agents_view(state) -> dict:
    use = ORC.usage()
    try:
        dsh = state.agent.dsh_info(); dplanner = state.agent.planner_name()
    except Exception:
        dsh, dplanner = {}, "rules"
    rows = []
    for aid in REG.all_agents():
        e = REG.effective(aid)
        multi = getattr(state, "multi", None)
        lp = multi.llm(aid) if (multi is not None and aid != "dispatch") else None
        e["planner_now"] = dplanner if aid == "dispatch" else ("llm" if lp is not None else "rules")
        e["planner_zh"] = {"dsh": f"DeepSeek Harness（{dsh.get('provider')}/{dsh.get('model')}）", "llm": f"大模型（{lp.model if lp else 'Anthropic'}），出错退回规则",
                           "rules": "规则规划器"}.get(e["planner_now"], e["planner_now"])
        e["usage"] = use.get(aid)
        e["n_actions"] = len(e["actions"])
        rows.append(e)
    return {"agents": rows, "dsh": dsh}


@RT.route("/api/agents", desc="多智能体清单（只读，公开给控制台）：名字、职责、启用与否、名下动作数")
def agents_public(state, qs, body, req):
    return {"agents": [{k: e[k] for k in ("id", "zh", "en", "role", "enabled")} | {"n_actions": len(e["actions"])} for e in (REG.effective(a) for a in REG.all_agents())]}


@RT.route("/api/admin/agents", desc="管理端：智能体清单、状态（规划器、调用次数、最近一次、平均耗时）、有效配置")
def admin_agents(state, qs, body, req):
    return _need(state, req) or _agents_view(state)


@RT.route("/api/admin/agent", method="POST", desc="管理端：改一个智能体的配置（启用、模型、提示词、停用哪些动作）", params="id, patch")
def admin_agent_set(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    aid = str(body.get("id") or ""); patch = body.get("patch") or {}
    try:
        e = REG.set_override(aid, patch)
    except ValueError as ex:
        return {"error": str(ex)}, 400
    _audit("agent_config", {"agent": aid, "patch": {k: (v if k != "prompt" else f"<{len(str(v))} 字>") for k, v in patch.items()}})
    return {"agent": e}


@RT.route("/api/admin/agent_reset", method="POST", desc="管理端：一个智能体的配置恢复缺省", params="id")
def admin_agent_reset(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    aid = str(body.get("id") or "")
    if aid not in REG.all_agents():
        return {"error": f"没有智能体 {aid}"}, 400
    _audit("agent_reset", {"agent": aid})
    return {"agent": REG.reset(aid)}


@RT.route("/api/admin/actions", desc="管理端：全部现有动作（新建智能体时从这里挑）：名称、归属、会不会写数据")
def admin_actions(state, qs, body, req):
    from ..ontology import schema as S
    return _need(state, req) or {"actions": [{"api": A.api, "zh": A.zh, "en": A.en, "owner": getattr(A, "owner", "dispatch"),
                                               "side_effect": A.side_effect, "desc": A.desc_zh} for A in S.ACTIONS]}


@RT.route("/api/admin/agent_create", method="POST", desc="管理端：新建一个智能体（从现有动作里挑一组，起名字、写职责、给分派关键词）", params="id, zh, en, role, actions, keywords")
def admin_agent_create(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    spec = {"id": body.get("id"), "zh": body.get("zh"), "en": body.get("en"), "role": body.get("role"),
            "actions": body.get("actions"), "keywords": body.get("keywords")}
    try:
        e = REG.create_custom(spec, by=_who())
    except ValueError as ex:
        return {"error": str(ex)}, 400
    _sync_presets(state)
    _audit("agent_create", {"agent": e["id"], "actions": [a["api"] for a in e["actions"]], "keywords": e["keywords"]})
    return {"agent": e}


@RT.route("/api/admin/agent_delete", method="POST", desc="管理端：删除一个自建智能体（内置的只能停用）", params="id")
def admin_agent_delete(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    aid = str(body.get("id") or "")
    try:
        REG.delete_custom(aid)
    except ValueError as ex:
        return {"error": str(ex)}, 400
    _sync_presets(state)
    _audit("agent_delete", {"agent": aid})
    return {"deleted": aid}


def _sync_presets(state):
    """自建智能体增删后，Harness 那边的预设也跟着重渲染（否则两条入口的智能体列表又对不上）。
    已经开着的 Harness Web UI 加载的是启动那一刻的配置：重渲染后要重启它才看得到。"""
    try:
        from .. import dsh_bridge as D
        st = D.settings(state.cfg)
        for home in (st["home"], st["home_web"]):
            D.render(state.cfg, home=home, link_modules=False)
    except Exception:
        pass


# ---------------------------------------------------------------- 会话与轨迹
@RT.route("/api/admin/sessions", desc="管理端：最近的对话与调用轨迹（主智能体分派给谁、为什么、每步动作）", params="limit, agent, q")
def admin_sessions(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    rows = ORC.sessions(RT.i1(qs, "limit", 100) or 100)
    ag = RT.s1(qs, "agent"); q = (RT.s1(qs, "q") or "").lower()
    if ag:
        rows = [r for r in rows if any(x.get("agent") == ag for x in r.get("results", []))]
    if q:
        rows = [r for r in rows if q in (r.get("message") or "").lower()]
    return {"sessions": rows}


# ---------------------------------------------------------------- 计算任务队列
@RT.route("/api/admin/jobs", desc="管理端：一二维计算申请队列 + 机器上正在算的全部一二维容器")
def admin_jobs(state, qs, body, req):
    return _need(state, req) or {"jobs": CJ.list_jobs(), "runs": CJ.external_runs()}


@RT.route("/api/admin/job", method="POST", desc="管理端：批准 / 驳回 / 暂停 / 恢复 / 取消一条计算申请", params="id, op, note")
def admin_job(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    jid = str(body.get("id") or ""); op = str(body.get("op") or ""); note = str(body.get("note") or "")[:500]
    fn = {"approve": CJ.approve, "reject": CJ.reject, "pause": CJ.pause, "resume": CJ.resume, "cancel": CJ.cancel}.get(op)
    if fn is None:
        return {"error": f"不认识的操作 {op}"}, 400
    try:
        j = fn(jid, _who(), note) if op in ("approve", "reject", "cancel") else fn(jid, _who())
    except ValueError as ex:
        return {"error": str(ex)}, 400
    _audit("job_" + op, {"job": jid, "note": note})
    return {"job": j}


# ---------------------------------------------------------------- 新建计算（2026-10）：参数表 + 计算域（行政区 / 汇水区 / 自己画 / 上传）
def _job_params(body: dict) -> dict:
    keep = ("kind", "tag", "domain", "states", "then_v3", "res", "tif_res", "sww_res", "rain", "dur", "force", "run")
    return {k: body.get(k) for k in keep if k in body}


@RT.route("/api/admin/job_form", desc="管理端：新建计算表单的选项与缺省值（预置情景、态、参数范围、计算域来源）")
def admin_job_form(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    from .. import domains as DM
    return {"presets": [{"tag": t, "km2": CJ.PRESET_KM2[t], "rain": CJ.PRESET_RAIN[t][0], "dur": CJ.PRESET_RAIN[t][1]} for t in CJ.TAGS],
            "states": list(CJ.STATES), "defaults": CJ.PARAM_DEFAULTS, "limits": CJ.LIMITS, "sources": DM.SOURCES, "kinds": CJ.KINDS,
            "max_upload_mb": DM.MAX_UPLOAD / 1e6}


@RT.route("/api/admin/domain_options", desc="管理端：计算域备选（行政区 / 汇水区）清单；geom=1 另给几何", params="kind, geom, ids")
def admin_domain_options(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    from .. import domains as DM
    kind = RT.s1(qs, "kind") or ""
    try:
        out = DM.options(kind, state.cfg)
        if RT.b1(qs, "geom"):
            ids = [x for x in (RT.s1(qs, "ids") or "").split(",") if x]
            out["geojson"] = DM.shapes(kind, ids or None, state.cfg)
        return out
    except DM.DomainError as ex:
        return {"error": str(ex), "kind": kind}, 400


@RT.route("/api/admin/domain_upload", method="POST", desc="管理端：上传计算域面图层（shp 压缩包 / geojson / gpkg / kml，base64）", params="name, data")
def admin_domain_upload(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    import base64, binascii
    from .. import domains as DM
    try:
        data = base64.b64decode(str(body.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        return {"error": "文件内容不是 base64"}, 400
    try:
        r = DM.save_upload(str(body.get("name") or ""), data)
    except DM.DomainError as ex:
        return {"error": str(ex)}, 400
    except Exception as ex:
        return {"error": f"读不了这个文件：{type(ex).__name__}: {str(ex)[:200]}"}, 400
    _audit("domain_upload", {"note": f"{body.get('name')} → {r['upload_id']}（{r['n']} 个要素）"})
    return r


@RT.route("/api/admin/job_plan", method="POST", desc="管理端：预检一条计算申请（解析计算域、运行名、步骤、单元数、预估耗时），不提交")
def admin_job_plan(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    from .. import domains as DM
    try:
        return {"plan": CJ.plan(_job_params(body), state.cfg)}
    except (ValueError, DM.DomainError) as ex:
        return {"error": str(ex)}, 400


@RT.route("/api/admin/job_create", method="POST", desc="管理端：新建一条计算申请（待批准；参数同 job_plan，另加 reason）")
def admin_job_create(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    from .. import domains as DM
    P = _job_params(body)
    try:
        j = CJ.submit(P.get("kind") or "compute", P.get("tag") or "", P.get("states"), bool(P.get("then_v3")), str(body.get("reason") or "")[:500],
                      {"agent": "admin", "who": _who()}, params=P, cfg=state.cfg)
    except (ValueError, DM.DomainError) as ex:
        return {"error": str(ex)}, 400
    _audit("job_create", {"job": j["id"], "note": f"{j.get('run')} · {' '.join(j.get('steps', []))}" + (" （已有同样的申请）" if j.get("duplicate") else "")})
    return {"job": j}


@RT.route("/api/admin/job_log", desc="管理端：一条计算申请的执行日志（末尾若干行）", params="id, n")
def admin_job_log(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    try:
        return {"id": RT.s1(qs, "id"), "log": CJ.log_tail(RT.s1(qs, "id") or "", RT.i1(qs, "n", 200) or 200)}
    except ValueError as ex:
        return {"error": str(ex)}, 400


@RT.route("/api/admin/audit", desc="管理端：管理员操作记录", params="limit")
def admin_audit(state, qs, body, req):
    bad = _need(state, req)
    if bad:
        return bad
    if not AUDIT.exists():
        return {"audit": []}
    lines = AUDIT.read_text(encoding="utf-8").splitlines()[-(RT.i1(qs, "limit", 100) or 100):]
    return {"audit": [json.loads(x) for x in lines[::-1] if x.strip()]}
