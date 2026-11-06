"""智能体：排水智能体的交互入口。用本体驱动——它只能做本体声明的动作，看到的只有本体对象。

两个规划器，同一套动作：
- LlmPlanner：有大模型密钥时用 Anthropic Messages API（纯 urllib，不依赖 SDK），工具 = 本体动作 + 取对象 / 查对象集 / 列规则，系统提示由本体自动生成；
- RulePlanner：没有密钥或调用失败时的确定性规划器——按动作声明的 intents 关键词识别意图、抽取实体（积水点编号 / 情景 / 雨量 / 水深 / 片区 …），
  回答由各动作自带的中英小结拼成。演示现场断网也能用，且每一步都是可审计的本体动作。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from . import schema as S
from .common import merge_ui
from .runtime import Runtime, ActionError, norm_fp_id, _FP_RE

_HELP_ZH = "我是排水智能体，围绕「积水点」本体工作。可以：{acts}。例如：{ex}"
_HELP_EN = "I am the drainage agent, working on the flood-point ontology. I can: {acts}. For example: {ex}"


def detect_lang(s: str) -> str:
    return "zh" if re.search(r"[一-鿿]", s or "") else "en"


# ---------------------------------------------------------------- 规则规划器
class RulePlanner:
    def __init__(self, rt: Runtime):
        self.rt = rt

    # ---- 实体抽取
    def entities(self, msg: str) -> dict:
        t = msg; low = msg.lower()
        e: dict = {}
        e["fp_ids"] = []
        for m in _FP_RE.finditer(msg):
            fp = norm_fp_id(m.group(0))
            if fp and fp not in e["fp_ids"]:
                e["fp_ids"].append(fp)
        stripped = _FP_RE.sub(" ", msg)
        m = re.search(r"20\d\d-\d\d", low)
        if m:
            e["scenario"] = m.group(0)
        elif re.search(r"\d+\s*(?:年|-?\s*y(?:ea)?rs?\b|-?\s*yr\b)", low) or "cc" in low or "气候" in low or "climate" in low:
            mm = re.search(r"(\d+)\s*(?:年|-?\s*y(?:ea)?rs?\b|-?\s*yr\b|\s*\+|\s*cc)", low)
            if mm:
                try:
                    e["scenario"] = self.rt.scenario_key(("%scc" % mm.group(1)) if ("cc" in low or "气候" in low or "climate" in low or "+20" in low) else mm.group(1))
                except ActionError:
                    pass
        mm = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", low)
        if mm:
            e["p24_mm"] = float(mm.group(1))
        ml = re.search(r"([一二三四五六七八九十两\d]+)\s*(?:天|日)\s*(?:后|之后|以后|内)|(?:in|within|after)\s+(\d+)\s+days?|(\d+)\s*days?\s*(?:later|ahead|out|from now|time)", low)
        if ml:
            g = ml.group(1) or ml.group(2) or ml.group(3); cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            try:
                e["lead_days"] = float(g) if g.isdigit() else float(cn[g])
            except (KeyError, ValueError):
                pass
        md = re.search(r"(\d+(?:\.\d+)?)\s*(?:m\b|米|公分|cm)", low.replace("mm", "  "))
        if md:
            v = float(md.group(1)); e["depth_m"] = v / 100 if ("cm" in md.group(0) or "公分" in md.group(0)) else v
        mt = re.search(r"(?:第|t\s*=|minute|at\s+)\s*(\d{1,4})\s*(?:分钟|分|min)?", low)
        if mt:
            e["decision_time_min"] = float(mt.group(1))
        for zh, en in (("蓝", "blue"), ("黄", "yellow"), ("橙", "orange"), ("红", "red")):
            if zh in t or re.search(r"\b" + en + r"\b", low):
                e["alert_level"] = zh
        ml = re.search(r"\b(III|II|I)\s*级|([一二三])\s*级", t)
        if ml:
            e["level"] = ml.group(1) or {"一": "I", "二": "II", "三": "III"}[ml.group(2)]
        if "极高" in t or "very high" in low:
            e["criticality"] = "Very High"
        elif re.search(r"重要度\s*高|高重要度|\bhigh\b", t if "重要度" in t else low):
            e["criticality"] = "High"
        elif "重要度中" in t or re.search(r"\bmedium\b", low):
            e["criticality"] = "Medium"
        elif "重要度低" in t or re.search(r"\blow\b", low):
            e["criticality"] = "Low"
        mr = re.search(r"\b(ADM|AAM|DRM)\b", stripped, re.I)
        if mr:
            e["region"] = mr.group(1).upper()
        names = self.rt.zone_names(); hit = [n for n in names if n and n.lower() in low]
        if hit:
            e["zone"] = sorted(hit, key=len)[-1]
        else:                                                   # 拼法不一致（Mussafah / Musaffah）：按词模糊配
            import difflib
            words = [w for w in re.findall(r"[a-z][a-z' ]{3,}", low)]
            cands = []
            for w in words:
                for piece in (w.strip(), *[p for p in w.split() if len(p) >= 5]):
                    cands += difflib.get_close_matches(piece, [n.lower() for n in names], n=1, cutoff=0.8)
            if len(set(cands)) == 1:
                e["zone"] = next(n for n in names if n.lower() == cands[0])
        mp = re.search(r"(?:建议|proposal|pid)\s*#?\s*(\d+)", low)
        if mp:
            e["pid"] = int(mp.group(1))
        if re.search(r"无积水|没积水|已退|退干|退了|\bdry\b|no water|receded", low):
            e["status"] = "dry" if re.search(r"无积水|没积水|退干|\bdry\b|no water", low) else "receding"
        elif re.search(r"退水中|正在退|receding", low):
            e["status"] = "receding"
        elif re.search(r"积水中|还在积|在积水|flooding|flooded|still", low) or "depth_m" in e:
            e["status"] = "flooding"
        if re.search(r"未派|没派|没有派|unserved|not served", low):
            e["served"] = False
        elif re.search(r"已派|served", low):
            e["served"] = True
        if re.search(r"可派", t) or re.search(r"\bdispatchable\b", low):
            e["dispatchable"] = True
        mn = re.search(r"前\s*(\d+)|top\s*(\d+)|(\d+)\s*个|(\d+)\s*条|first\s+(\d+)", low)
        if mn:
            e["limit"] = int(next(g for g in mn.groups() if g))
        CN = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
        mpu = re.findall(r"([\d一两二三四五六]+)\s*台\s*(\d+)\s*寸|(\d+)\s*x\s*(\d+)\s*(?:in|inch)\b|(\d+)\s*(?:in|inch|寸)\s*(?:泵|pumps?)?\s*[×x*]\s*(\d+)|(\w+)\s+(\d+)\s*(?:in|inch)\s+pumps?", low)
        if mpu:
            parts = []
            for g in mpu:
                if g[0]:
                    parts.append(f"{g[1]}in:{CN.get(g[0], g[0] if g[0].isdigit() else 1)}")
                elif g[2]:
                    parts.append(f"{g[3]}in:{g[2]}")
                elif g[4]:
                    parts.append(f"{g[4]}in:{g[5]}")
                elif g[6] and (g[6].isdigit() or g[6] in ("two", "three", "four", "one")):
                    parts.append(f"{g[7]}in:{ {'one': 1, 'two': 2, 'three': 3, 'four': 4}.get(g[6], g[6]) }")
            arrive = int(e.get("decision_time_min") or 240)
            e["pumps"] = ",".join(f"{p}@{arrive}" for p in parts)
        ms = re.search(r"(?:放大|换大|加大|扩大|扩|大)\s*(\d|一|两|二|三)\s*级|(\d)\s*(?:steps?|sizes?)|dn\s*\+\s*(\d)", low)
        if ms:
            g = next(x for x in ms.groups() if x); e["dn_steps"] = {"一": 1, "两": 2, "二": 2, "三": 3}.get(g, None) or int(g)
        if re.search(r"\b(en|english)\b", low) and "报告" in t:
            e["lang"] = "en"
        return e

    def _score(self, low: str, A: S.ActionType) -> int:
        return sum(1 for k in A.intents if k.lower() in low)

    # ---- 规划
    def plan(self, msg: str, default_scenario: str | None = None) -> list[tuple[str, dict]]:
        e = self.entities(msg); low = msg.lower()
        fp = e["fp_ids"][0] if e["fp_ids"] else None
        sc = e.get("scenario") or default_scenario
        scores = {A.api: self._score(low, A) for A in S.ACTIONS if getattr(A, "owner", "dispatch") == "dispatch"}   # 多智能体：别的智能体的动作不参与
        plan: list[tuple[str, dict]] = []

        def add(api, **kw):
            plan.append((api, {k: v for k, v in kw.items() if v is not None}))

        fps_all = e["fp_ids"]
        wm = bool(re.search(r"世界模型|GWM|推演|world model|gwm", msg, re.I))
        # L1 / L2 / L2+ 规划结果（2026-09-28）：片区名 + 态 / 设施 / 既成事实的问法，先于积水点路由
        reg = ("Mussafah_00" if re.search(r"mussafah|musaffah|穆萨法", low) else "29.RB2" if re.search(r"29\.?\s*rb\s*_?0?2|rb\s*_?0?2", low)
               else "Madinat Al Riyad" if re.search(r"madinat al riyad|riyad|利雅得", low) else None)
        # 汇水区 / 分析范围（2026-09-29）：「ADM-94 在哪个汇水区」「RB-3 里有哪些积水点」「C1 客户单元版漏了哪些热点」
        cname = None
        if re.search(r"汇水区|集水区|流域|catchment|watershed|drainage area|分析范围|计算域|客户单元|u61|模型版", low) or re.search(r"(?<![A-Za-z0-9])(rb-\d|mbz|adgc-\d+|rd-\d+)(?![A-Za-z0-9])", low):
            try:
                names = {str(c.get(k)) for c in self.rt.load_catchments().values() for k in ("name", "new_number", "old_number") if c.get(k)}
                hits = sorted((n for n in names if len(n) >= 2 and re.search(r"(?<![A-Za-z0-9])" + re.escape(n.lower()) + r"(?![A-Za-z0-9])", low)), key=len, reverse=True)
                cname = hits[0] if hits else None
            except Exception:
                cname = None
        catch_q = bool(re.search(r"汇水区|集水区|流域|catchment|watershed|drainage area|分析范围|计算域|客户单元|u61|模型版", low)) or bool(cname and re.search(r"积水点|热点|设施|哪些|多少|怎么样|重叠|flood point|hotspot|asset|overlap", low))
        if catch_q:
            dom = "C1M" if re.search(r"模型版|model version|c1m", low) else "C1U" if re.search(r"客户单元|u61|client unit", low) else ("C1" if re.search(r"(?<![a-z0-9])c1(?![0-9])", low) and not cname and not fp else None)
            if fp or cname or dom:
                add("catchment_info", fp_id=fp if not cname else None, catchment=cname, domain=dom if not (fp or cname) else None)
                return plan
        if not fp and re.search(r"是什么|什么是|什么叫|有什么区别|what is|what's a|difference between", low):
            from .. import asset_kb as _K
            k = next((e["zh"] for e in _K.KB.values() if e["zh"] in msg or e["en"].lower() in low), None) or next((w for w in ("箱涵", "湿井", "集水井", "临时塘", "塘", "护栏", "地面管", "移动泵", "泵站") if w in msg), None)
            if k:
                add("asset_kind", kind=k)
                return plan
        if not fp:
            if re.search(r"既成事实|还要补|还需要补|还要做什么|还需要做什么|做完以后|做完之后|之上.*(补|加)|on top of|as[- ]built|what else|still need", low) and reg != "Madinat Al Riyad":
                add("as_built_next", region=reg or "Mussafah_00")
                return plan
            if re.search(r"全市|全城|citywide|whole city", low) and re.search(r"l1|l2|差值|差多少|缺口|gap|塘|pond", low):
                add("city_gap")
                return plan
            if reg and re.search(r"设施|箱涵|临时塘|集水井|建哪些|建什么|放在哪|facilit|box|pond", low):
                st = re.search(r"l2\s*\+\s*a\s*\+\s*b|l2\s*\+\s*b|l2\s*\+\s*a|l2|l1", low)
                add("facility_plan", region=reg, state=(st.group(0).replace(" ", "").upper() + ("（放宽）" if re.search(r"放宽|relax", low) else "")) if st else None)
                return plan
            if re.search(r"五态|四态|七态|几个态|各态|缺口对比|态对比|l2\s*\+|l1\s*(和|与|vs|、|/)\s*l2|mitigation states|states compared", low) or (reg and re.search(r"缺口|gap|对比|compare", low)):
                add("state_gap", region=reg)
                return plan
        if scores["rain_to_scenario"] > 0 and "p24_mm" in e and not re.search(r"签发|发布|发出|发预警|issue|publish|send out", low):
            add("rain_to_scenario", p24_mm=e["p24_mm"], lead_days=e.get("lead_days"))     # 值班第一句：预报 X mm 相当于几年一遇、用哪个情景
            return plan
        if fp and scores["point_history"] > 0 and not re.search(r"派几台|几点能|退水|退干|dispatch|drain", low):
            add("point_history", fp_id=fp)
            return plan
        if scores["event_review"] > 0 and not fp:
            m_ev = re.search(r"(20\d\d)[-年/\s]*(\d{1,2})", low)
            add("event_review", event_id=(f"{m_ev.group(1)}-{int(m_ev.group(2)):02d}" if m_ev else None))
            return plan
        if scores["lifecycle_overview"] > 0 and not fp:
            add("lifecycle_overview")
            return plan
        if scores["assumptions"] > 0 and not fp and not re.search(r"派几台|退水|dispatch|drain", low):
            add("assumptions", scenario=sc)
            return plan
        if scores["traffic_status"] > 0 and not fp:
            add("traffic_status", scenario=sc, refresh=(True if re.search(r"刷新|重新取|重取|refresh|re-fetch|update", low) else None))
            return plan
        if scores["feeds_status"] > 0 and not fp and not re.search(r"重规划|重排|replan|到位率|readiness", low):
            add("feeds_status")
            return plan
        if scores["dispatch_plan"] > 0 and re.search(r"鲁棒|同时应对|几种雨|三种雨|robust", low) and not fp:
            add("dispatch_plan", scenario=sc, decision_time_min=e.get("decision_time_min"), robust=True)
            return plan
        if scores["cost_summary"] > 0 and not (fp and scores["recession_options"] > 0):
            add("cost_summary", fp_ids=(",".join(fps_all) if fps_all else None), scenario=sc, limit=e.get("limit"))
            return plan
        if (scores["recession_batch"] > 0 and (len(fps_all) != 1 or re.search(r"这些|这几个|these|加快|speed", low))) or len(fps_all) > 1:
            add("recession_batch", fp_ids=(",".join(fps_all) if fps_all else None), scenario=sc, level=e.get("level"), criticality=e.get("criticality"), zone=e.get("zone"), limit=e.get("limit"))
            return plan
        if wm and re.search(r"新增|多了|新的|新出|added|new|additional|model-only|独有", low):
            add("world_model_new_points", scenario=sc, limit=e.get("limit"))
            return plan
        if scores["count_points"] > 0 and not fp and re.search(r"多少|几个|数量|盘点|how many|count|inventory", low) and not re.search(r"新增|added|new ", low):
            add("count_points", scenario=sc)
            return plan
        if re.search(r"最严重|最重|worst|most severe|most serious", low) and not fp:
            if wm:
                add("world_model_new_points", scenario=sc, limit=e.get("limit"))
            else:
                add("list_points", scenario=sc, level=e.get("level") or "III", criticality=e.get("criticality"), region=e.get("region"), zone=e.get("zone"), limit=e.get("limit"))
            return plan
        if e.get("status") and fp and (scores["record_observation"] > 0 or "depth_m" in e):
            add("record_observation", fp_id=fp, status=e["status"], depth_m=e.get("depth_m", 0), note=None)
            return plan
        if scores["explain_rule"] > 0 and (re.search(r"为什么|怎么判|怎么算|依据|why|how is|criteria|rule", low)):
            rule = None
            for k, v in (("等级", "emergency_level"), ("级", "emergency_level"), ("level", "emergency_level"), ("时限", "target_drain_time"), ("target", "target_drain_time"), ("可派", "dispatchable"), ("接力", "relay_factor"), ("relay", "relay_factor"), ("达标", "within_target"),
                         ("预警", "alert_level"), ("alert", "alert_level"), ("法定", "reach_compliance"), ("标准", "reach_compliance"), ("compliance", "reach_compliance"), ("坐标", "coordinate_suspect"), ("世界模型", "world_model_confirmed"), ("罐车", "tanker_viable"), ("tanker", "tanker_viable"), ("升级", "needs_escalation")):
                if k in low:
                    rule = v; break
            if fp:
                add("inspect_point", fp_id=fp, scenario=sc)
            add("explain_rule", rule=rule or "emergency_level")
            return plan
        if scores["issue_alert"] > 0 and "p24_mm" in e:
            add("issue_alert", p24_mm=e["p24_mm"], area=e.get("zone"))
            return plan
        if e.get("pid") is not None and (scores["proposal_whatif"] > 0 or "what" in low or "如果" in low):
            add("proposal_whatif", pid=e["pid"])
            return plan
        if scores["compare_world_model"] > 0:
            add("compare_world_model", scenario=sc)
            return plan
        if scores["generate_report"] > 0:
            add("generate_report", scenario=sc, lang=e.get("lang"))
            return plan
        if scores["preposition_plan"] > 0:
            add("preposition_plan", scenario=sc, alert_level=e.get("alert_level"))
            return plan
        if fp and scores["upsize_whatif"] > 0:
            add("upsize_whatif", fp_id=fp, dn_steps=e.get("dn_steps"), scenario=sc)
            return plan
        if fp and scores["trace_downstream"] > 0:
            add("trace_downstream", fp_id=fp)
            return plan
        if fp and e.get("pumps"):
            add("replay_point", fp_id=fp, pumps=e.get("pumps"), scenario=sc)
            return plan
        if fp and scores["recession_options"] > 0:
            add("recession_options", fp_id=fp, scenario=sc, decision_time_min=e.get("decision_time_min"))
            return plan
        if fp and scores["dispatch_plan"] > 0:
            add("dispatch_plan", fp_id=fp, scenario=sc, decision_time_min=e.get("decision_time_min"))
            return plan
        if fp and scores["replay_point"] > 0:
            add("replay_point", fp_id=fp, scenario=sc)
            return plan
        if scores["list_points"] > 0 or (not fp and any(k in e for k in ("level", "criticality", "region", "served", "dispatchable"))):
            add("list_points", scenario=sc, level=e.get("level"), criticality=e.get("criticality"), region=e.get("region"), zone=e.get("zone"), dispatchable=e.get("dispatchable"), served=e.get("served"), limit=e.get("limit"))
            return plan
        if scores["dispatch_plan"] > 0:
            add("dispatch_plan", scenario=sc, decision_time_min=e.get("decision_time_min"))
            return plan
        if e.get("zone") and not fp:
            add("zone_overview", zone=e["zone"], scenario=sc)
            return plan
        if fp:
            add("inspect_point", fp_id=fp, scenario=sc)
            return plan
        if e.get("scenario") and (scores["set_scenario"] > 0 or len(msg.strip()) <= 12):
            add("set_scenario", scenario=e["scenario"])
            return plan
        return plan

    def compose(self, steps: list[dict], lang: str) -> str:
        if not steps:
            own = [a for a in S.ACTIONS if getattr(a, "owner", "dispatch") == "dispatch"]
            acts = "、".join(a.zh for a in own) if lang == "zh" else ", ".join(a.en for a in own)
            ex = " / ".join(x for a in S.ACTIONS[:6] for x in a.examples[:1]) if lang == "zh" else " / ".join((a.examples[1] if len(a.examples) > 1 else a.examples[0]) for a in S.ACTIONS[:6] if a.examples)
            return (_HELP_ZH if lang == "zh" else _HELP_EN).format(acts=acts, ex=ex)
        return "\n\n".join(s.get("summary") or "" for s in steps)


# ---------------------------------------------------------------- 大模型规划器（Anthropic Messages API，无 SDK）
class LlmPlanner:
    """owner：只把这个智能体名下的动作做成工具（多智能体：dispatch 另带取对象 / 查对象集 / 沿关系走三个通用工具，专业智能体不带）；
    system：替换系统提示（专业智能体的人设）；guard(name, inp) 返回非空字符串就拒绝这次工具调用（例：没明确要求就不许提交申请）。"""

    def __init__(self, cfg: dict, owner: str = "dispatch", system: str | None = None, guard=None, actor: str = "llm"):
        AG = cfg.get("agent") or {}
        self.key = os.environ.get(str(AG.get("api_key_env", "ANTHROPIC_API_KEY"))) or AG.get("api_key")
        self.model = str(AG.get("model", "claude-sonnet-5"))
        self.base = str(AG.get("base_url", "https://api.anthropic.com")).rstrip("/")
        self.max_steps = int(AG.get("max_steps", 6))
        self.timeout = float(AG.get("timeout_s", 60))
        self.max_tokens = int(AG.get("max_tokens", 1500))
        self.owner, self.system, self.guard, self.actor = owner, system, guard, actor

    def available(self) -> bool:
        return bool(self.key)

    @staticmethod
    def _schema(p: S.Parameter) -> dict:
        t = {"int": "integer", "float": "number", "bool": "boolean"}.get(p.type, "string")
        d: dict = {"type": t, "description": f"{p.zh} / {p.en}"}
        if p.choices:
            d["enum"] = list(p.choices)
        if p.lo is not None:
            d["minimum"] = p.lo
        if p.hi is not None:
            d["maximum"] = p.hi
        return d

    def tools(self, allowed: set[str] | None = None) -> list[dict]:
        out = []
        for A in S.ACTIONS:
            # 给了 allowed（多智能体的专业智能体都给）就只按它：自建智能体挑的是别人名下的现成动作，按 owner 筛会一个都剩不下
            if (A.api not in allowed) if allowed is not None else (getattr(A, "owner", "dispatch") != self.owner):
                continue
            out.append({"name": A.api, "description": f"{A.zh} / {A.en}：{A.desc_zh} {A.desc_en}" + (f" 副作用：{A.side_effect}" if A.side_effect != "none" else ""),
                        "input_schema": {"type": "object", "properties": {p.name: self._schema(p) for p in A.params}, "required": [p.name for p in A.params if p.required]}})
        if self.owner != "dispatch":
            return out
        out.append({"name": "get_object", "description": "按类型与主键取一个本体对象及其规则判定。类型：" + ", ".join(o.api for o in S.OBJECT_TYPES),
                    "input_schema": {"type": "object", "properties": {"type": {"type": "string", "enum": [o.api for o in S.OBJECT_TYPES]}, "id": {"type": "string"}, "scenario": {"type": "string"}}, "required": ["type", "id"]}})
        out.append({"name": "search_objects", "description": "查对象集：等值过滤（JSON 对象）、排序字段、条数。FloodState 结果自动带积水点静态属性。",
                    "input_schema": {"type": "object", "properties": {"type": {"type": "string", "enum": [o.api for o in S.OBJECT_TYPES]}, "filters": {"type": "object"}, "order_by": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "scenario": {"type": "string"}}, "required": ["type"]}})
        out.append({"name": "follow_link", "description": "沿关系走：" + "; ".join(f"{l.src}.{l.api}→{l.dst}" for l in S.LINK_TYPES),
                    "input_schema": {"type": "object", "properties": {"type": {"type": "string"}, "id": {"type": "string"}, "link": {"type": "string", "enum": [l.api for l in S.LINK_TYPES]}, "scenario": {"type": "string"}}, "required": ["type", "id", "link"]}})
        return out

    def system_prompt(self, rt: Runtime, lang: str, scenario: str | None) -> str:
        ot = "\n".join(f"- {o.api}（{o.zh}，主键 {o.pk}）：{o.desc} 属性：{', '.join(p.name for p in o.properties[:14])}{'…' if len(o.properties) > 14 else ''}" for o in S.OBJECT_TYPES)
        lk = "; ".join(f"{l.src}.{l.api}→{l.dst}({l.card})" for l in S.LINK_TYPES)
        rl = "\n".join(f"- {r.api}（{r.zh}，{r.kind}，{r.applies_to}）：{r.logic_zh}" for r in S.RULES)
        keys = ", ".join(rt.scenario_keys())
        return (f"你是阿布扎比 DMT「会思考的排水系统」的智能体，交互入口。你只能通过工具（本体动作）行动，只能引用工具返回的数字，不许编造。\n"
                f"核心对象是积水点 FloodPoint（客户编号 ADM-n / AAM-n / DRM-n，Parsons 的 HS-n 等于 ADM-n）。当前情景：{scenario or rt.scenario_key(None)}；可用情景键：{keys}。\n"
                f"回答用{'中文' if lang == 'zh' else 'English'}，简短、给结论和关键数字，说明是哪个情景，标出假设与「模拟 / 占位」。真正会写文件的动作（issue_alert、record_observation）只有在用户明确要求时才做。\n"
                f"本体对象类型：\n{ot}\n关系：{lk}\n规则：\n{rl}\n"
                f"世界模型（GWM）水深只涨不退、体积偏大，只作空间对照；调度结论要带上这条。")

    def _call(self, system: str, messages: list, tools: list, tool_choice: dict | None = None) -> dict:
        b = {"model": self.model, "max_tokens": self.max_tokens, "system": system, "messages": messages, "tools": tools}
        if tool_choice:
            b["tool_choice"] = tool_choice
        body = json.dumps(b).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/v1/messages", data=body, method="POST",
                                     headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def run(self, message: str, history: list[dict], rt: Runtime, lang: str, scenario: str | None, allowed: set[str] | None = None) -> tuple[str, list[dict], dict]:
        msgs = [{"role": m["role"], "content": m["content"]} for m in (history or []) if m.get("role") in ("user", "assistant") and m.get("content")] + [{"role": "user", "content": message}]
        tools = self.tools(allowed); steps: list[dict] = []; ui: dict = {}
        system = self.system if self.system is not None else self.system_prompt(rt, lang, scenario)
        for _ in range(self.max_steps):
            resp = self._call(system, msgs, tools)
            content = resp.get("content") or []
            if resp.get("stop_reason") != "tool_use":
                text = "".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
                return text or ("（无回答）" if lang == "zh" else "(no answer)"), steps, ui
            msgs.append({"role": "assistant", "content": content})
            results = []
            for c in content:
                if c.get("type") != "tool_use":
                    continue
                name, inp = c.get("name"), c.get("input") or {}
                why = self.guard(name, inp) if self.guard else None
                if why or name not in {t["name"] for t in tools}:                # 工具清单外的名字（模型臆造 / 别的智能体的动作）一律拒
                    why = why or f"没有工具 {name}"
                    out = {"action": name, "zh": name, "en": name, "params": inp, "ok": False, "error": why, "summary": why, "ui": {}, "ms": 0.0}
                else:
                    out = self._exec(rt, name, inp, lang, self.actor)
                steps.append({k: v for k, v in out.items() if k != "result"} | {"result": _brief(out.get("result"))})
                merge_ui(ui, out.get("ui"))
                results.append({"type": "tool_result", "tool_use_id": c.get("id"), "content": json.dumps(_clip(out.get("result") if out.get("ok") else {"error": out.get("error")}), ensure_ascii=False)[:12000]})
            msgs.append({"role": "user", "content": results})
        return ("步数用完，以上是已做的动作。" if lang == "zh" else "Step budget exhausted; actions above."), steps, ui

    @staticmethod
    def _exec(rt: Runtime, name: str, inp: dict, lang: str, actor: str = "llm") -> dict:
        t0 = time.perf_counter()
        try:
            if name in S.AT:
                return rt.act(name, inp, actor=actor, lang=lang)
            if name == "get_object":
                o = rt.get(inp["type"], inp["id"], inp.get("scenario")); res = {"object": o, "rules": rt.rules(inp["type"], o, inp.get("scenario")) if o else []}
            elif name == "search_objects":
                res = {"rows": rt.search(inp["type"], inp.get("filters") or {}, inp.get("order_by"), True, int(inp.get("limit") or 20), inp.get("scenario"))}
            elif name == "follow_link":
                res = {"rows": rt.links(inp["type"], inp["id"], inp["link"], inp.get("scenario"))}
            else:
                raise ActionError(f"没有工具 {name}")
            return {"action": name, "zh": name, "en": name, "params": inp, "ok": True, "result": res, "summary": f"{name} → {len(res.get('rows', [res.get('object')]) or [])} 个对象", "ui": {}, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except ActionError as ex:
            return {"action": name, "zh": name, "en": name, "params": inp, "ok": False, "error": str(ex), "summary": str(ex), "ui": {}, "ms": round((time.perf_counter() - t0) * 1000, 1)}


def _clip(v, depth=0):
    """工具结果喂给大模型前收口：长列表截断、深层字典保留。"""
    if isinstance(v, dict):
        return {k: _clip(x, depth + 1) for k, x in v.items() if not str(k).startswith("_")}
    if isinstance(v, list):
        return [_clip(x, depth + 1) for x in v[: (40 if depth < 2 else 12)]]
    return v


def _brief(v, depth: int = 0):
    """给控制台看的结果摘要（避免整包回传）：列表最多 40 项、字典最多 40 键、嵌套 4 层；控制台的结果卡片（分级 / 成本 / 退水时刻 / 点位表）靠它取数。"""
    if depth > 4:
        return "…"
    if isinstance(v, dict):
        return {k: (f"[{len(x)} 项]" if isinstance(x, list) and len(x) > 40 else _brief(x, depth + 1)) for k, x in list(v.items())[:40] if not str(k).startswith("_")}
    if isinstance(v, list):
        return [_brief(x, depth + 1) for x in v[:40]]
    return v


# ---------------------------------------------------------------- 智能体
class Agent:
    """交互入口。规划器按可用性依次：dsh（DeepSeek Harness 真 agent，经 MCP 用本体动作）→ llm（Anthropic 直连）→ rules（关键词）。"""

    def __init__(self, rt: Runtime, cfg: dict | None = None):
        self.rt = rt
        self.cfg = cfg if cfg is not None else rt.cfg
        self.llm = LlmPlanner(self.cfg)
        self.rules = RulePlanner(rt)
        self.dsh = None; self.dsh_error: str | None = None
        D = ((self.cfg.get("agent") or {}).get("dsh") or {})
        if D.get("enabled", True):
            try:
                from .. import dsh_bridge
                if dsh_bridge.available() and (dsh_bridge.settings(self.cfg)["key_present"].get({"deepseek-official": "deepseek", "openai": "openai", "anthropic": "anthropic"}.get(dsh_bridge.settings(self.cfg)["provider"], ""), False) or D.get("provider")):
                    self.dsh = dsh_bridge.DshBridge(self.cfg)
            except Exception as ex:
                self.dsh_error = str(ex)[:160]

    def planner_name(self) -> str:
        return "dsh" if self.dsh is not None else ("llm" if self.llm.available() else "rules")

    def dsh_info(self) -> dict:
        try:
            from .. import dsh_bridge
            st = dsh_bridge.settings(self.cfg)
            live = dsh_bridge.web_url_live(self.cfg)
            return {"enabled": bool(((self.cfg.get("agent") or {}).get("dsh") or {}).get("enabled", True)), "available": self.dsh is not None, "provider": st["provider"], "model": st["model"], "key_source": (st.get("key_source") or {}).get({"deepseek-official": "deepseek", "openai": "openai", "anthropic": "anthropic"}.get(st["provider"], "deepseek")), "web_url": live or st["web_url"], "web_up": live is not None, "home": str(st["home"]), "home_web": str(st["home_web"]), "error": self.dsh_error or (self.dsh.error if self.dsh else None),
                    "runtime": self.dsh.status() if self.dsh is not None else None}
        except Exception as ex:
            return {"enabled": False, "available": False, "error": str(ex)[:160]}

    def web_ui(self) -> "dsh_bridge.WebBridge":
        """Harness 原生 Web UI（服务托管、只读）；第一次要用时再建，不启动任何东西。"""
        if getattr(self, "_web", None) is None:
            from .. import dsh_bridge
            self._web = dsh_bridge.WebBridge(self.cfg)
        return self._web

    def _delegation(self) -> str | None:
        """发起这次对话的人 → 一张委托凭据，交给 DeepSeek Harness 经 MCP 回调时带上（统一身份；没有当前主体就不发）。"""
        from .. import identity
        p = identity.current(); idn = getattr(getattr(self.rt, "b", None), "identity", None)
        if p is None or idn is None:
            return None
        sid = getattr(p, "session_ref", None)
        return idn.delegate(p, sid)

    def chat(self, message: str, history: list[dict] | None = None, lang: str | None = None, scenario: str | None = None, session_id: str | None = None) -> dict:
        t0 = time.perf_counter()
        lang = lang or detect_lang(message)
        key = None
        try:
            key = self.rt.scenario_key(scenario) if scenario else None
        except ActionError:
            key = None
        self.rt.default_scenario = key
        note = None; planner = "rules"
        steps: list[dict] = []; ui: dict = {}; reply = ""
        if self.dsh is not None:
            try:
                msg = message if not key else f"{message}\n\n（控制台当前情景：{key}；用户未指定情景时用它。）"
                r = self.dsh.chat(msg, session_id=session_id, lang=lang, delegation=self._delegation())
                r.update({"note": None, "lang": lang, "scenario": key})
                r["run_ms"] = r.get("ms"); r["ms"] = round((time.perf_counter() - t0) * 1000, 1)     # ms = 用户等的总时间；run_ms / start_ms / wait_ms 是拆分
                return r
            except Exception as ex:
                note = (f"DeepSeek Harness 不可用（{str(ex)[:120]}），改用备用规划器。" if lang == "zh" else f"DeepSeek Harness unavailable ({str(ex)[:120]}); using the fallback planner.")
        if self.llm.available():
            try:
                reply, steps, ui = self.llm.run(message, history or [], self.rt, lang, key); planner = "llm"
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, KeyError) as ex:
                note = ("大模型不可达（%s），改用规则规划器。" % str(ex)[:80]) if lang == "zh" else ("LLM unreachable (%s); using the rule planner." % str(ex)[:80])
        if planner == "rules":
            plan = self.rules.plan(message, key)
            for api, params in plan[:3]:
                if key and "scenario" in {p.name for p in S.AT[api].params} and not params.get("scenario"):
                    params["scenario"] = key
                out = self.rt.act(api, params, actor="agent", lang=lang)
                steps.append({k: v for k, v in out.items() if k != "result"} | {"result": _brief(out.get("result"))})
                merge_ui(ui, out.get("ui"))
            reply = self.rules.compose(steps, lang)
        return {"reply": reply, "steps": steps, "ui": ui, "planner": planner, "note": note, "lang": lang, "scenario": key, "session_id": session_id, "ms": round((time.perf_counter() - t0) * 1000, 1)}


def main(argv=None):
    """python -m swbrain.ontology.agent "ADM-62 在 50 年一遇下派几台泵"  （装载完整状态，首问要等情景上下文）"""
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="+")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from .. import db, serve
    cfg = db.load_config(a.config)
    st = serve.State(cfg)
    ag = Agent(Runtime(st), cfg)
    r = ag.chat(" ".join(a.message), lang=a.lang, scenario=a.scenario)
    print(f"[{r['planner']}] " + r["reply"])
    for s in r["steps"]:
        print(f"  · {s['action']} {json.dumps(s.get('params'), ensure_ascii=False)} → {'ok' if s.get('ok') else s.get('error')} ({s.get('ms')} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
