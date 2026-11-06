# -*- coding: utf-8 -*-
"""多智能体注册表：有哪些智能体、各管什么、能用哪些本体动作、用什么规划器（2026-09-30 用户定：主智能体 + 4 个专业智能体）。

  orchestrator  主智能体   听懂问题、分派给专业智能体、汇总回答；自己不调业务动作
  dispatch      调度研判   三道防线：积水点退水、派泵、全局调度、预警、汇水区（= 原来的排水智能体，DeepSeek Harness 驱动）
  hydro         一二维计算 一二维结果、计算进度、提交计算申请（只能提交，批准在管理端）
  report        报告交付   交付目录、报告、申请重出后处理与交付
  qa            数据质检   客户数据齐不齐、每个态的计算结果过没过质检

分派：keywords 是「领域词」，至少命中一个才会交给这个智能体；名下动作的意图词只加分（避免「检查井」「调度结果」这类词被抢走）。
动作归属：ActionType.owner（schema.py）；没标的都归 dispatch。管理端改的配置（启用、停用某些动作、分派关键词）存 out/agents/overrides.json，
模型与人设提示词不在这里改（调度研判的模型在 config.toml [agent.dsh]，人设在 agent/dsh/presets/；主智能体与其余三个用 [agent] 的大模型、出错退回规则，人设是下面的 prompt），
不改代码和 config.toml；删掉那个文件就回到缺省。
"""
from __future__ import annotations

import json
import pathlib
import re
import time

from ..ontology import schema as S

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVR = ROOT / "out" / "agents" / "overrides.json"

AGENTS: dict[str, dict] = {
    "orchestrator": {"zh": "主智能体", "en": "Orchestrator", "order": 0,
                     "role": "听懂用户的问题，判断该交给哪个专业智能体（可以交给几个），把它们的结果汇总成一个回答；自己不调业务动作。",
                     "keywords": [], "planner": "llm|rules",
                     "prompt": "你是排水系统的主智能体。先判断问题属于调度研判、一二维计算、报告交付还是数据质检，交给对应的专业智能体；一个问题涉及几块就分几次交。不要自己编数字。"},
    "dispatch": {"zh": "调度研判", "en": "Dispatch & diagnosis", "order": 1,
                 "role": "三道防线：积水点什么时候退水、用什么手段最快、派几台泵、全局调度与单点最优的差、预警、汇水区与分析范围。",
                 "keywords": [], "planner": "dsh",
                 "prompt": "（沿用排水智能体的人设：agent/dsh/presets/drainage/agent.cordis.yml）"},
    "hydro": {"zh": "一二维计算", "en": "1D/2D modelling", "order": 2,
              "role": "热点群一二维水动力计算（SWMM + ANUGA）：各情景 / 各态的结果、计算进度与预计完成时间、提交计算申请（要管理端批准才会执行）。",
              "keywords": ["一二维", "二维", "水动力", "耦合", "anuga", "swmm", "l2+planned", "l2+suggested", "l2p", "l2s3", "planned", "suggested", "多态", "四个态",
                           "模型版", "m61", "r61", "r96", "d24", "u61", "设计暴雨", "计算进度", "算完", "算到", "重算", "计算申请", "提交计算", "1d/2d", "hydrodynamic",
                           "快速验证", "合成算例", "selftest", "模拟积水点", "二维积水点", "积水点清单", "sim-", "rb-01", "rb01"],
              "planner": "llm|rules",
              "prompt": "你是一二维计算智能体。只回答一二维计算的结果、进度与申请；要起计算只能提交申请，告诉用户去管理端批准。"},
    "report": {"zh": "报告交付", "en": "Reports & delivery", "order": 3,
               "role": "交付目录与报告：各情景交付了哪些文件、在哪里、多大；申请重出后处理与交付目录（要管理端批准）。",
               "keywords": ["交付", "交付物", "交付目录", "资料清单", "在线报告", "c1 报告", "deliverable", "delivery"],
               "planner": "llm|rules",
               "prompt": "你是报告交付智能体。回答交付物在哪里、有什么；重出交付要提交申请，由管理端批准。"},
    "qa": {"zh": "数据质检", "en": "Data & QA", "order": 4,
           "role": "客户数据与环境齐不齐（能不能复现）；每个态的计算结果过没过质检：一维连续性误差、一二维交换对账、二维水量对账、L2+suggested 的副作用。",
           "keywords": ["质检", "数据齐", "缺数据", "复现", "连续性误差", "对账", "水量平衡", "副作用", "qa", "continuity"],
           "planner": "llm|rules",
           "prompt": "你是数据质检智能体。给出每项检查的结果和标准，不合格的说明原因。"},
}


def overrides() -> dict:
    try:
        return json.loads(OVR.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- 用户自建的智能体（2026-10-02）
# 存 out/agents/custom.json，和 overrides.json 分开：overrides 是「覆盖内置的缺省」，「恢复缺省」会清掉它；
# 自建智能体是新增的实体，不该被「恢复缺省」顺手删掉。
# 范围：**重新打包现有动作**——名字、职责、分派关键词、从现成动作里挑一组。不能造出新的动作能力（那要写代码）。
CUSTOM = ROOT / "out" / "agents" / "custom.json"
BUILTIN = frozenset(AGENTS)
_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def custom_agents() -> dict:
    try:
        d = json.loads(CUSTOM.read_text(encoding="utf-8"))
        return {k: v for k, v in d.items() if isinstance(v, dict) and k not in BUILTIN}
    except (OSError, ValueError):
        return {}


def all_agents() -> dict:
    """内置 + 自建，按 order 排。所有「列出智能体」的地方都应走这里，而不是直接读 AGENTS。"""
    out = dict(AGENTS)
    for aid, c in custom_agents().items():
        out[aid] = {"zh": c["zh"], "en": c.get("en") or c["zh"], "order": int(c.get("order", 100)), "role": c["role"],
                    "keywords": list(c.get("keywords") or []), "planner": "llm|rules", "custom": True,
                    "prompt": f"你是{c['zh']}。职责：{c['role']}只回答职责范围内的事，范围外的告诉用户该问哪个智能体。"}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["order"]))


def get(aid: str) -> dict:
    a = all_agents().get(aid)
    if a is None:
        raise KeyError(aid)
    return a


def is_custom(aid: str) -> bool:
    return aid in custom_agents()


def actions_of(aid: str) -> list[str]:
    """内置智能体：按动作声明的 owner 归属（互斥）。自建智能体：它自己挑的那组现有动作（可以和内置的重叠）。"""
    c = custom_agents().get(aid)
    if c is not None:
        return [a for a in dict.fromkeys(c.get("actions") or []) if a in S.AT]
    return [A.api for A in S.ACTIONS if getattr(A, "owner", "dispatch") == aid]


def create_custom(spec: dict, by: str = "admin") -> dict:
    aid = str(spec.get("id") or "").strip().lower()
    if not _ID.match(aid):
        raise ValueError("id 要小写字母开头，只用小写字母、数字、下划线，2–31 位（例：permit_review）")
    if aid in BUILTIN or aid in ("drainage", "orchestrator"):
        raise ValueError(f"{aid} 是内置智能体或保留名，换一个")
    zh = str(spec.get("zh") or "").strip()[:30]; role = str(spec.get("role") or "").strip()[:400]
    if not zh or not role:
        raise ValueError("名称（zh）和职责（role）都要填")
    acts = [a for a in dict.fromkeys(spec.get("actions") or []) if a in S.AT]
    bad = [a for a in (spec.get("actions") or []) if a not in S.AT]
    if bad:
        raise ValueError(f"这些动作不存在：{bad}（只能从现有动作里挑；要新动作得写代码，见 docs/智能体架构.md 第 7 节）")
    if not acts:
        raise ValueError("至少挑一个动作")
    kws = [str(x).strip()[:40] for x in (spec.get("keywords") or []) if str(x).strip()][:80]
    if not kws:
        raise ValueError("至少给一个分派关键词（主智能体靠它决定什么话交给这个智能体）")
    d = custom_agents()
    if aid in d and not spec.get("_update"):
        raise ValueError(f"已经有 {aid}；要改就先删再建，或用更新")
    d[aid] = {"zh": zh, "en": str(spec.get("en") or "").strip()[:60] or zh, "role": role, "actions": acts, "keywords": kws,
              "order": int(spec.get("order") or (100 + len(d))), "_created": d.get(aid, {}).get("_created") or time.strftime("%Y-%m-%d %H:%M:%S"),
              "_updated": time.strftime("%Y-%m-%d %H:%M:%S"), "_by": by}
    CUSTOM.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return effective(aid)


def delete_custom(aid: str) -> None:
    d = custom_agents()
    if aid not in d:
        raise ValueError(f"{aid} 不是自建智能体（内置的只能停用，不能删）")
    d.pop(aid)
    CUSTOM.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    ov = overrides()
    if ov.pop(aid, None) is not None:
        OVR.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")


def effective(aid: str) -> dict:
    base = get(aid); ov = overrides().get(aid, {})
    acts = actions_of(aid)
    off = set(ov.get("disabled_actions") or [])
    return {"id": aid, **{k: v for k, v in base.items()}, "enabled": bool(ov.get("enabled", True)) or aid == "orchestrator",
            "keywords": list(ov["keywords"]) if isinstance(ov.get("keywords"), list) else list(base["keywords"]), "keywords_default": list(base["keywords"]),
            "actions": [{"api": a, "zh": S.AT[a].zh, "en": S.AT[a].en, "side_effect": S.AT[a].side_effect, "enabled": a not in off} for a in acts],
            "overridden": sorted(ov), "custom": bool(base.get("custom"))}


def allowed_actions(aid: str) -> set[str]:
    e = effective(aid)
    return {a["api"] for a in e["actions"] if a["enabled"]}


def set_override(aid: str, patch: dict, by: str = "admin") -> dict:
    if aid not in all_agents():
        raise ValueError(f"没有智能体 {aid}")
    ov = overrides(); cur = ov.get(aid, {})
    for k in ("enabled", "disabled_actions", "keywords"):
        if k in patch:
            v = patch[k]
            if k == "enabled":
                if aid == "orchestrator" and not v:
                    raise ValueError("主智能体不能停用（停用多智能体请在 config.toml 设 [agents].enabled = false）")
                v = bool(v)
            elif k == "disabled_actions":
                v = [a for a in (v or []) if a in actions_of(aid)]
            else:
                v = [str(x).strip()[:40] for x in (v or []) if str(x).strip()][:80]
            cur[k] = v
    cur["_updated"] = time.strftime("%Y-%m-%d %H:%M:%S"); cur["_by"] = by
    ov[aid] = cur
    OVR.parent.mkdir(parents=True, exist_ok=True)
    OVR.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    return effective(aid)


def reset(aid: str) -> dict:
    ov = overrides(); ov.pop(aid, None)
    OVR.parent.mkdir(parents=True, exist_ok=True)
    OVR.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    return effective(aid)
