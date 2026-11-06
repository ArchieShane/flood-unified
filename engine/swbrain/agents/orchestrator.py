# -*- coding: utf-8 -*-
"""主智能体：把一句话分派给专业智能体，汇总回答，记调用轨迹（多智能体，2026-09-30）。

分派（可审计）：按各专业智能体的领域词（至少命中一个）+ 它名下动作的意图词打分；一二维计算 / 报告交付 / 数据质检有得分就交给它们（一句话可以交给两个），
都没有就交给调度研判（= 原来的排水智能体，DeepSeek Harness → 大模型 → 规则）。每次分派的理由写进轨迹。
有大模型（[agents].llm = true 且 [agent] 的密钥在）时，分派先让大模型调 delegate(agents, reason)，出错退回上面的规则分派。
专业智能体（一二维计算 / 报告交付 / 数据质检）同理：大模型规划（工具只有自己名下、没停用的动作），出错退回规则规划器——从话里抽情景 / 态 / 热点，
按意图挑自己名下的动作，回答由动作小结拼成。
只能调自己名下、且在管理端没被停用的动作；会写文件的两个申请动作只在话里明确要求提交 / 重算时才调，且只进待批准队列。

轨迹：out/agents/sessions.jsonl（一问一行：问题、分派与理由、各智能体的步骤、回答、耗时）。
"""
from __future__ import annotations

import json
import pathlib
import re
import time
import urllib.error
import uuid

from ..ontology import schema as S
from ..ontology.agent import _brief, detect_lang
from ..ontology.common import merge_ui
from . import registry as R

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG = ROOT / "out" / "agents" / "sessions.jsonl"
SPECIALISTS = ("hydro", "report", "qa")          # 内置的三个；分派时用 specialists()，自建的也带上


def specialists() -> tuple:
    """参与分派的专业智能体：内置三个 + 用户自建的（2026-10-02）。调度研判是兜底，不在这里。"""
    return tuple(a for a in R.all_agents() if a not in ("orchestrator", "dispatch"))

_TAG = [("M61", r"模型版|m61|model version"), ("D24", r"设计暴雨|d24|24\s*h|24\s*小时|61\.34"), ("U61", r"客户单元|u61"), ("R96", r"r96|96\s*mm|96\s*毫米"),
        ("R61", r"r61|原计算域|原域|61\s*mm|61\s*毫米")]
_ST = [("L2S3", r"v3|l2\+?\s*suggested|l2s3|suggested"), ("L2S", r"l2s(?!3)|v2"), ("L2P", r"l2\+?\s*planned|l2p|planned|parsons\s*方案"), ("L2", r"(?<![a-z0-9])l2(?![a-z0-9+])|移动泵"), ("L1", r"(?<![a-z0-9])l1(?![a-z0-9])|现状")]
TAGWORDS = {"模型版", "m61", "r61", "r96", "d24", "u61", "设计暴雨"}
REGION = re.compile(r"mussafah|musaffah|穆萨法|29\.?\s*rb|rb\s*_?0?2|madinat|riyad|利雅得")
CATCH = re.compile(r"汇水区|集水区|分析范围|漏了哪些|catchment")
COMPUTE = re.compile(r"一二维|二维|水动力|计算|算完|算到|积水面积|积水量|水深|l1|l2|planned|suggested|进度")
C1 = re.compile(r"(?<![a-z0-9])c1(?![0-9])|模型版|m61|r61|r96|d24|u61|一二维|二维")
_SUBMIT = r"提交|申请|重算|重新算|跑一下|跑一次|开跑|再算|再跑|submit|rerun|re-run"


def _log(rec: dict):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def sessions(limit: int = 100, session_id: str | None = None) -> list[dict]:
    if not LOG.exists():
        return []
    rows = []
    with LOG.open(encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if session_id and r.get("session_id") != session_id and r.get("id") != session_id:
                continue
            rows.append(r)
    return rows[-limit:][::-1]


def usage() -> dict:
    """每个智能体被分派了几次、最近一次、平均耗时（管理端的状态栏用）。"""
    out = {a: {"calls": 0, "last": None, "ms_sum": 0.0, "errors": 0} for a in R.all_agents()}
    for r in sessions(5000):
        out["orchestrator"]["calls"] += 1; out["orchestrator"]["last"] = out["orchestrator"]["last"] or r.get("ts")
        for x in r.get("results", []):
            u = out.get(x.get("agent"))
            if u is None:
                continue
            u["calls"] += 1; u["last"] = u["last"] or r.get("ts"); u["ms_sum"] += x.get("ms") or 0
            u["errors"] += sum(1 for s in x.get("steps", []) if not s.get("ok", True))
    for u in out.values():
        u["avg_ms"] = round(u.pop("ms_sum") / u["calls"], 1) if u["calls"] else None
    return out


def _specialist_system(aid: str, lang: str) -> str:
    a = R.get(aid)
    return (f"{a['prompt']}\n职责：{a['role']}\n你只能通过工具行动，只能引用工具返回的数字，不许编造；工具答不了的就直说答不了，并说明该问哪个智能体"
            f"（调度研判 / 一二维计算 / 报告交付 / 数据质检）。情景键：M61 = 模型版（以此为准）、R61 = 原计算域 61 mm、R96 = 96 mm、D24 = 设计暴雨 24 h、U61 = 客户单元版；"
            f"态：L1 现状、L2 移动泵、L2P = L2+planned、L2S3 = L2+suggested v3。申请类工具只进待批准队列，要告诉用户去管理端批准。"
            f"回答用{'中文' if lang == 'zh' else 'English'}，简短，先给结论和关键数字。")


def _router_system() -> str:
    ags = R.all_agents()
    rows = "\n".join(f"- {aid}（{ags[aid]['zh']}）：{ags[aid]['role']}" for aid in ("dispatch",) + specialists())
    return (ags["orchestrator"]["prompt"] + "\n必须调一次 delegate 工具交出去，不要直接回答。缺省只给一个智能体；只有一句话里明确问了两件不同的事（例：「算完了吗？质检过了没有？」）才给两个。各智能体：\n" + rows +
            "\n规则：Mussafah / 29.RB2 / Madinat 的 L1 / L2 方案、汇水区组成、积水点 / 派泵 / 预警 / 检查井都归 dispatch；"
            "C1 热点群（模型版 / M61 / R61 / R96 / D24 / U61）与 RB-01（RB01_H60）等一二维运行各态（L1 / L2 / L2+planned / L2+suggested）的一二维结果、积水面积对比与进度归 hydro；"
            "「模拟积水点 / 二维积水点 / 积水点清单」和 SIM- 开头的编号是一二维结果判出来的，归 hydro（单说「积水点」、问派泵退水的才归 dispatch）；交付目录与报告归 report；数据齐不齐、能否复现、结果质检归 qa。拿不准给 dispatch。")


class MultiAgent:
    """[agents].llm = true 且有大模型密钥（[agent] 的 api_key_env）时：主智能体用大模型分派（delegate 工具），专业智能体用大模型规划（工具 = 自己名下、
    没被停用的动作）；任何一步出错、没密钥或 llm = false，都退回规则分派 / 规则规划，并把原因写进 note 与轨迹。"""

    def __init__(self, dispatch_agent, rt, cfg: dict | None = None):
        self.dispatch = dispatch_agent        # swbrain.ontology.agent.Agent：调度研判智能体
        self.rt = rt
        self.cfg = cfg or {}
        A = self.cfg.get("agents") or {}
        self.use_llm = bool(A.get("llm", True)) and bool(self.cfg)                   # 测试里 MultiAgent(None, None) 只走规则
        self._llm = {}

    def llm(self, aid: str):
        """aid 的大模型规划器（没密钥 / 没开返回 None）。专业智能体的申请动作加一道闸：话里没明确要求提交 / 重算就拒。"""
        if not self.use_llm:
            return None
        if aid not in self._llm:
            from ..ontology.agent import LlmPlanner
            A = self.cfg.get("agents") or {}                                   # [agents] 里写了模型 / 端点 / 密钥变量就用它，否则沿用 [agent]
            cfg = {**self.cfg, "agent": {**(self.cfg.get("agent") or {}), **{k: A[k] for k in ("api_key_env", "base_url", "model", "timeout_s", "max_steps") if k in A}}}
            if aid == "orchestrator":
                p = LlmPlanner(cfg, owner="orchestrator", system=_router_system(), actor="agent:orchestrator")
                p.max_tokens = int(A.get("router_max_tokens", 2000))           # 思考型模型（deepseek-flash）先想再调工具，300 不够
            else:
                p = LlmPlanner(cfg, owner=aid, actor=f"agent:{aid}")
            self._llm[aid] = p if p.available() else None
        return self._llm[aid]

    # ---------------------------------------------------------------- 分派
    def route_llm(self, message: str) -> list[dict]:
        """大模型分派：强制调 delegate(agents, reason)；只认启用了的智能体，最多两个。出错抛给调用方退回规则。"""
        p = self.llm("orchestrator")
        live = [a for a in ("dispatch",) + specialists() if R.effective(a)["enabled"]]
        tool = {"name": "delegate", "description": "把问题交给一个或两个专业智能体",
                "input_schema": {"type": "object", "properties": {"agents": {"type": "array", "items": {"type": "string", "enum": live}, "minItems": 1, "maxItems": 2},
                                                                  "reason": {"type": "string", "description": "一句话理由（写进轨迹）"}}, "required": ["agents", "reason"]}}
        msgs = [{"role": "user", "content": message}]
        try:
            resp = p._call(p.system, msgs, [tool], {"type": "tool", "name": "delegate"})
        except urllib.error.HTTPError as ex:                                    # 有的兼容端点（思考模式）不收强制 tool_choice：不强制再试一次
            if ex.code != 400:
                raise
            resp = p._call(p.system, msgs, [tool])
        use = next((c for c in resp.get("content") or [] if c.get("type") == "tool_use" and c.get("name") == "delegate"), None)
        if use is None:
            raise ValueError("大模型没有调 delegate（stop_reason=%s）" % resp.get("stop_reason"))
        got = [a for a in dict.fromkeys((use.get("input") or {}).get("agents") or []) if a in live][:2]
        if not got:
            raise ValueError(f"delegate 给了不可用的智能体：{(use.get('input') or {}).get('agents')}")
        why = str((use.get("input") or {}).get("reason") or "")[:200]
        return [{"agent": a, "score": None, "reason": "大模型分派：" + why, "by": "llm"} for a in got]

    def route(self, message: str) -> list[dict]:
        low = message.lower(); cand = []
        for aid in specialists():
            e = R.effective(aid)
            if not e["enabled"]:
                continue
            kw = [k for k in e["keywords"] if k.lower() in low]
            core = [k for k in kw if k.lower() not in TAGWORDS]
            it = sorted({k for a in R.allowed_actions(aid) for k in S.AT[a].intents if k.lower() in low})
            score = (2 * len(kw) + len(it)) if kw else 0                   # 没有领域词就不分给它
            if aid == "hydro" and REGION.search(low) and not C1.search(low):
                score = 0                                                   # Mussafah / 29.RB2 / Madinat 的 L1 / L2 方案问答属于调度研判
            if aid == "hydro" and CATCH.search(low) and not COMPUTE.search(low):
                score = 0                                                   # 「C1 模型版由哪些汇水区组成」是汇水区问答（调度研判的 catchment_info）
            if score:
                cand.append({"agent": aid, "score": score, "core": bool(core), "reason": "命中：" + "、".join((kw + [k for k in it if k not in kw])[:6])})
        if not cand:
            why = "没有命中一二维计算 / 报告交付 / 数据质检的关键词，按调度研判处理"
            if not R.effective("dispatch")["enabled"]:
                return [{"agent": None, "score": 0, "reason": why + "；但调度研判智能体已在管理端停用"}]
            return [{"agent": "dispatch", "score": 0, "reason": why}]
        cand.sort(key=lambda c: -c["score"])
        # 一句话里问了几件事（「算完了吗？质检过了没有？」）→ 各自命中了**核心**领域词的都分；情景名（模型版 / M61 …）只是上下文，
        # 单靠它不构成第二个去处（「模型版的交付目录在哪」只给报告交付）
        keep = [cand[0]] + [c for c in cand[1:] if c["core"]]
        return keep[:2]

    # ---------------------------------------------------------------- 专业智能体的规则规划
    @staticmethod
    def entities(message: str) -> dict:
        low = message.lower(); e = {}
        for t, pat in _TAG:
            if re.search(pat, low):
                e["tag"] = t; break
        for st, pat in _ST:
            if re.search(pat, low):
                e["state"] = st; break
        m = re.search(r"\b(ADM|AAM|DRM)\s*-?\s*(\d+)\b", message, re.I)
        if m:
            e["hotspot"] = f"{m.group(1).upper()}-{m.group(2)}"
        norm = {"l2+planned": "L2P", "l2 planned": "L2P", "planned": "L2P", "l2p": "L2P", "l2+suggested": "L2S3", "suggested": "L2S3", "l2s3": "L2S3", "v3": "L2S3", "l2": "L2", "l1": "L1"}
        sts = [norm[m.group(1)] for m in re.finditer(r"(?<![a-z0-9])(l2\+planned|l2 planned|l2\+suggested|l2s3|l2p|planned|suggested|v3|l2|l1)(?![a-z0-9+])", low)]
        if len(set(sts)) > 1:
            e["states"] = ",".join(dict.fromkeys(sts))
        return e

    def plan(self, aid: str, message: str) -> list[tuple[str, dict]]:
        low = message.lower(); e = self.entities(message); allowed = R.allowed_actions(aid)
        want_submit = bool(re.search(_SUBMIT, low))
        tag = e.get("tag")
        plan: list[tuple[str, dict]] = []

        def add(api, **kw):
            if api in allowed:
                plan.append((api, {k: v for k, v in kw.items() if v is not None}))

        selftest = bool(re.search(r"快速验证|合成算例|selftest|self-test", low))   # 快速验证不看情景
        if aid == "hydro":
            if want_submit and selftest:
                add("hydro_submit", kind="selftest", reason=message[:200])
            elif selftest:
                add("hydro_progress")                                                     # 问快速验证结果：进度里带最近一次的判定
            elif want_submit and tag:
                add("hydro_submit", tag=tag, states=e.get("states") or (e.get("state") if e.get("state") else None), then_v3=bool(re.search(r"v3|suggested|接着", low)), reason=message[:200])
            elif re.search(r"进度|算到|还要多久|什么时候算完|在算|队列|progress|eta|running", low):
                add("hydro_progress")
            elif re.search(r"模拟积水点|二维积水点|积水点清单|sim-|ponding", low):          # 二维模拟积水点（SIM-…）；单说「积水点」归调度研判
                run = "RB01_H60" if re.search(r"rb\s*-?\s*0?1(?![0-9])", low) else (f"C1_{tag}" if tag else None)
                st = e.get("state")
                if st == "L2S3" and run and not run.startswith("C1_"):
                    st = "L2S"                                                               # 动作里按这个运行最新的 L2S 版本取
                add("hydro_ponding", run=run, state=st, hotspot=e.get("hotspot"))
            elif tag and re.search(r"积水|面积|减了|效果|结果|水深|深|积水量|result|area|depth", low):
                one = e.get("state") if not (e.get("states") or re.search(r"各态|每个态|几个态|四个态|对比|比\s*l|减了|compare", low)) else None
                add("hydro_result", tag=tag, state=one, hotspot=e.get("hotspot"))              # 问对比 / 各态 → 全部态都给
            else:
                add("hydro_cases", tag=tag)
        elif aid == "report":
            if want_submit and tag:
                add("report_submit", tag=tag, reason=message[:200])
            else:
                add("delivery_status", tag=tag)
        elif aid == "qa":
            if tag and re.search(r"质检|结果|连续性|对账|平衡|副作用|过没过|通过|可信|qa|continuity|balance", low) and not re.search(r"数据齐|缺数据|缺什么|复现|环境", low):
                add("qa_results", tag=tag, state=e.get("state"))
            else:
                add("qa_data", tag=tag)
        elif R.is_custom(aid):
            plan.extend(self._plan_custom(aid, message, e, allowed, want_submit))
        if want_submit and not tag and aid in ("hydro", "report") and not (aid == "hydro" and selftest):
            return [("_ask", {"q": "要算 / 重出哪个情景？（M61 模型版 / R61 / R96 / D24 设计暴雨）"})]
        return plan

    @staticmethod
    def _plan_custom(aid: str, message: str, e: dict, allowed: set, want_submit: bool) -> list:
        """自建智能体没有专属规则，没有大模型时的兜底：在它挑的那组动作里按意图词打分取最高的一个；
        必填参数从话里抽（情景 / 态 / 热点编号）凑不齐就不调；写动作照旧要话里明说「提交 / 申请」。"""
        low = message.lower(); best = None
        fill = {"tag": e.get("tag"), "state": e.get("state"), "states": e.get("states"), "hotspot": e.get("hotspot"), "fp_id": e.get("hotspot")}
        for api in allowed:
            A = S.AT[api]
            if A.side_effect != "none" and not want_submit:
                continue
            score = sum(1 for k in A.intents if k.lower() in low)
            params = {p.name: fill[p.name] for p in A.params if fill.get(p.name) is not None}
            if any(p.required and p.name not in params for p in A.params):
                continue
            if best is None or score > best[0]:
                best = (score, api, params)
        return [(best[1], best[2])] if best and best[0] > 0 else []

    def run_specialist(self, aid: str, message: str, lang: str, history=None) -> dict:
        t0 = time.perf_counter(); note = None
        p = self.llm(aid)
        if p is not None:
            want_submit = bool(re.search(_SUBMIT, message.lower()))
            p.system = _specialist_system(aid, lang)
            p.guard = lambda name, inp: (None if want_submit or S.AT.get(name) is None or S.AT[name].side_effect == "none"
                                         else "用户没有明确要求提交 / 重算，不能调申请动作" if lang == "zh" else "The user did not ask to submit; request actions are not allowed")
            try:
                reply, steps, ui = p.run(message, history or [], self.rt, lang, None, allowed=R.allowed_actions(aid))
                return {"agent": aid, "planner": "llm", "reply": reply, "steps": steps, "ui": ui, "ms": round((time.perf_counter() - t0) * 1000, 1)}
            except Exception as ex:                                          # 网络 / 鉴权 / 格式出错都退回规则规划
                note = (f"{R.get(aid)['zh']}的大模型不可用（{str(ex)[:100]}），改用规则规划器。" if lang == "zh" else f"LLM unavailable for {aid} ({str(ex)[:100]}); using rules.")
        out = self._run_rules(aid, message, lang, t0)
        if note:
            out["note"] = note
        return out

    def _run_rules(self, aid: str, message: str, lang: str, t0: float) -> dict:
        steps = []; ui: dict = {}
        plan = self.plan(aid, message)
        if plan and plan[0][0] == "_ask":
            reply = plan[0][1]["q"] if lang == "zh" else "Which case? (M61 / R61 / R96 / D24)"
        else:
            for api, params in plan[:2]:
                out = self.rt.act(api, params, actor=f"agent:{aid}", lang=lang)
                steps.append({k: v for k, v in out.items() if k != "result"} | {"result": _brief(out.get("result"))})
                merge_ui(ui, out.get("ui"))
            reply = "\n\n".join(s.get("summary") or "" for s in steps) or ("这个问题我这里没有对应的动作。" if lang == "zh" else "No matching action here.")
        return {"agent": aid, "planner": "rules", "reply": reply, "steps": steps, "ui": ui, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    # ---------------------------------------------------------------- 入口（/api/agent）
    def chat(self, message: str, history=None, lang=None, scenario=None, session_id=None) -> dict:
        t0 = time.perf_counter(); lang = lang or detect_lang(message)
        routes, route_note = None, None
        if self.llm("orchestrator") is not None:
            try:
                routes = self.route_llm(message)
            except Exception as ex:
                route_note = (f"主智能体的大模型分派不可用（{str(ex)[:100]}），改用规则分派。" if lang == "zh" else f"LLM routing unavailable ({str(ex)[:100]}); using rule routing.")
        routes = routes or self.route(message)
        results = []
        for r in routes:
            aid = r["agent"]
            if aid is None:
                results.append({"agent": None, "planner": None, "reply": "调度研判智能体已停用，这个问题没有智能体可以回答。" if lang == "zh" else "The dispatch agent is disabled.", "steps": [], "ui": {}, "ms": 0})
            elif aid == "dispatch":
                x = self.dispatch.chat(message, history=history, lang=lang, scenario=scenario, session_id=session_id)
                results.append({"agent": "dispatch", **x})
            else:
                results.append(self.run_specialist(aid, message, lang, history))
        if len(results) == 1:
            reply = results[0]["reply"]
        else:
            ags = R.all_agents()
            reply = "\n\n".join(f"【{ags.get(x['agent'], {}).get('zh' if lang == 'zh' else 'en', x['agent'])}】{x['reply']}" for x in results if x.get("agent"))
        ui: dict = {}
        steps = []
        for x in results:
            merge_ui(ui, x.get("ui"))
            steps += [{**s, "agent": x.get("agent")} for s in x.get("steps", [])]
        rid = uuid.uuid4().hex[:12]
        out = {"reply": reply, "steps": steps, "ui": ui, "planner": "multi:" + "+".join(f"{x.get('agent')}/{x.get('planner')}" for x in results),
               "note": " ".join(n for n in [route_note] + [x.get("note") for x in results] if n) or None, "lang": lang, "scenario": next((x.get("scenario") for x in results if x.get("scenario")), None),
               "session_id": session_id, "agents": [{"agent": x.get("agent"), "zh": (R.all_agents().get(x.get("agent")) or {}).get("zh"), "planner": x.get("planner"), "ms": x.get("ms")} for x in results],
               "route": routes, "trace_id": rid, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        _log({"id": rid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "session_id": session_id, "message": message, "lang": lang, "routes": routes, "note": out["note"],
              "results": [{"agent": x.get("agent"), "planner": x.get("planner"), "ms": x.get("ms"), "reply": x.get("reply"), "note": x.get("note"),
                           **{k: x[k] for k in ("run_ms", "start_ms", "wait_ms", "cold_start") if k in x},         # 慢在哪：模型+工具 / 等运行时启动 / 排队
                           "steps": [{k: s.get(k) for k in ("action", "params", "ok", "ms", "summary", "error", "side_effect")} for s in x.get("steps", [])]} for x in results],
              "reply": reply, "ms": out["ms"]})
        return out

    def planner_name(self) -> str:
        return "multi"
