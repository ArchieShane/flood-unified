"""排水智能体的 MCP 服务器（stdio）：把「积水点」本体的动作与对象暴露给 DeepSeek Harness 等 MCP 客户端。

薄桥：本进程不装载管网状态，所有调用转发到正在运行的排水智能体 HTTP 服务
（/api/ontology、/api/action、/api/object、/api/objects），所以启动只要几十毫秒，数字与控制台同源、同缓存。
环境变量：SWBRAIN_URL（默认 http://127.0.0.1:8766）、SWBRAIN_KEY（控制台访问口令，有就带上）。
协议：MCP（JSON-RPC 2.0，stdio 按行分隔），实现 initialize / ping / tools/list / tools/call / resources/list / prompts/list；
服务器 instructions 由本体声明生成，会被 Harness 拼进系统提示。

自测：python -m swbrain.mcp_server --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .ontology import schema as S

SERVER_NAME = "swbrain"
SERVER_VERSION = "0.1"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
BASE_URL = os.environ.get("SWBRAIN_URL", "http://127.0.0.1:8766").rstrip("/")
def _key() -> str:
    """口令优先从环境变量拿；拿不到就自己读 config.toml。
    2026-09-23 之前是把口令渲染进 out/dsh_home*/ 下的 YAML，明文躺在盘上（dsh 的 Web UI 还会索引那个目录）。
    本进程跟服务在同一台机器、同一个仓库里，直接读配置即可，不必把口令写到任何地方。"""
    v = os.environ.get("SWBRAIN_KEY", "").strip()
    if v and not (v.startswith("${") or v.startswith("__")):
        return v
    try:
        from . import db
        return str((db.load_config(None).get("serve") or {}).get("access_key") or "")
    except Exception:
        return ""


KEY = _key()
TIMEOUT = float(os.environ.get("SWBRAIN_TIMEOUT_S", "600"))
# 只读模式（SWBRAIN_READONLY=1）：原生 Web UI 入口用。dsh 的 MCP 客户端 headers 是静态配置（z.dict(String)），
# 没法按会话带身份，分不出 Web UI 里是谁在用；那边又是共享的一次性 token。所以那个入口只放只读工具，
# 会写数据的动作一律拒，并让人到控制台（平台账号登录后按职能做）。2026-10-02
READONLY = str(os.environ.get("SWBRAIN_READONLY") or "").strip().lower() in ("1", "true", "yes")
# 这个 MCP 服务代表哪个智能体（对应 schema.ActionType.owner）：只把它的动作做成工具，别的智能体的动作不出现、也调不动。
# 控制台对话（sdk 补丁）不给这个变量 = dispatch；原生 Web UI 的四个预设各自给一个（agent/dsh/presets/*/agent.cordis.yml）。
OWNER = (os.environ.get("SWBRAIN_OWNER") or "dispatch").strip() or "dispatch"
# 自建智能体（2026-10-02）：它挑的是别的智能体名下的现成动作，按 owner 筛会一个都剩不下，所以预设里直接给出动作清单。
# 给了就只按它；没给（内置的四个）照旧按 OWNER 归属。服务端 Runtime.act 还会按 dsh:<id> 再判一次。
ACTIONS = frozenset(a.strip() for a in (os.environ.get("SWBRAIN_ACTIONS") or "").split(",") if a.strip()) or None


def _mine(api: str, owner: str | None = None) -> bool:
    """这条动作算不算「本入口」的。

    ⚠️ 传 `"*"` 表示**全放**，只给平台转发那一支用（见 routes.工具调用）。为什么
    那一支要全放：平台上「一个智能体能碰哪些工具」由**平台的智能体声明**说了算
    （它自己有一张 action_refs），而这里再按排水侧内部的四个智能体切一刀，
    会切出一个两边都不认得的交集 —— 症状是「平台上配了这条工具，一调就说它不属于
    这个智能体」。

    全放**不放宽任何一条已有的闸**：管理端停用过的动作、发起人的 `SWBRAIN.*` 职能、
    只读入口的写拦截，三条都在 `Runtime.act` 里，与 owner 无关。
    """
    if owner == "*":
        return True
    if owner is not None:
        return getattr(S.AT[api], "owner", "dispatch") == owner
    if ACTIONS is not None:
        return api in ACTIONS
    return getattr(S.AT[api], "owner", "dispatch") == OWNER


def log(*a):
    print("[swbrain-mcp]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- HTTP 桥
def _url(path: str, params: dict | None = None) -> str:
    q = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    if KEY:
        q["key"] = KEY
    return f"{BASE_URL}{path}" + ("?" + urllib.parse.urlencode(q) if q else "")


DELEGATION_FILE = os.environ.get("SWBRAIN_DELEGATION_FILE", "").strip()


def _auth_headers() -> dict:
    """统一身份：带上这一轮对话的委托凭据（服务在对话开始时写、结束时删），服务端据此还原成发起对话的人、按他的职能判。
    文件不在（共享口令模式、或不是从控制台对话来的）就不带，服务端照旧按口令认。"""
    if DELEGATION_FILE and not DELEGATION_FILE.startswith("__"):
        try:
            tok = open(DELEGATION_FILE, encoding="utf-8").read().strip()
            if tok:
                return {"Authorization": f"Bearer {tok}"}
        except OSError:
            pass
    return {}


def http_get(path: str, params: dict | None = None) -> dict:
    with urllib.request.urlopen(urllib.request.Request(_url(path, params), headers=_auth_headers()), timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post(path: str, body: dict) -> dict:
    req = urllib.request.Request(_url(path), data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", **_auth_headers()}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": f"HTTP {e.code}"}


# ---------------------------------------------------------------- 工具声明（由本体生成）
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


OBJECT_TYPES = [o.api for o in S.OBJECT_TYPES]
LINKS = [l.api for l in S.LINK_TYPES]


def tool_list(owner: str | None = None) -> list[dict]:
    tools = []
    for A in S.ACTIONS:
        if not _mine(A.api, owner):                                # 多智能体：只给这个智能体的动作
            continue
        desc = f"{A.zh} / {A.en}。{A.desc_zh} {A.desc_en}"
        if A.side_effect != "none":
            desc += f" 副作用 / side effect: {A.side_effect}。只在用户明确要求时调用。"
        if A.examples:
            desc += " 例 / e.g.: " + " | ".join(A.examples[:2])
        tools.append({"name": A.api, "description": desc,
                      "inputSchema": {"type": "object", "properties": {p.name: _schema(p) for p in A.params}, "required": [p.name for p in A.params if p.required], "additionalProperties": False}})
    tools.append({"name": "get_object", "description": "按类型与主键取一个本体对象及其规则判定与关系 / Get one ontology object with rule verdicts and links. 类型: " + ", ".join(OBJECT_TYPES) + "。FloodState 的主键写 fp_id@情景，如 ADM-62@50。",
                  "inputSchema": {"type": "object", "properties": {"type": {"type": "string", "enum": OBJECT_TYPES}, "id": {"type": "string"}, "scenario": {"type": "string", "description": "情景键 5/10/25/50/100/10cc/2024-04/2026-03"}, "links": {"type": "boolean", "description": "是否带关系对象（默认 true）"}}, "required": ["type", "id"], "additionalProperties": False}})
    tools.append({"name": "search_objects", "description": "查对象集：等值过滤、排序、条数 / Search objects with equality filters, ordering and limit. FloodState 结果自动带积水点静态属性；如 {type: FloodState, scenario: '50', filters: {level: 'III', served: false}, order_by: 'priority', limit: 10}。",
                  "inputSchema": {"type": "object", "properties": {"type": {"type": "string", "enum": OBJECT_TYPES}, "filters": {"type": "object", "additionalProperties": True}, "order_by": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "scenario": {"type": "string"}}, "required": ["type"], "additionalProperties": False}})
    tools.append({"name": "follow_link", "description": "沿关系走 / Follow a link: " + "; ".join(f"{l.src}.{l.api}→{l.dst}" for l in S.LINK_TYPES),
                  "inputSchema": {"type": "object", "properties": {"type": {"type": "string", "enum": OBJECT_TYPES}, "id": {"type": "string"}, "link": {"type": "string", "enum": LINKS}, "scenario": {"type": "string"}}, "required": ["type", "id", "link"], "additionalProperties": False}})
    tools.append({"name": "ontology_overview", "description": "本体全貌与当前状态：对象类型、关系、规则、动作、可用情景、控制台地址 / The ontology and current runtime overview.",
                  "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}})
    return tools


def instructions() -> str:
    ot = "\n".join(f"- {o.api}（{o.zh}，主键 {o.pk}{'，随情景' if o.scoped else ''}）：{o.desc}" for o in S.OBJECT_TYPES)
    rl = "\n".join(f"- {r.api}（{r.zh}，{r.kind}，{r.applies_to}）：{r.logic_zh}" for r in S.RULES)
    return (
        "你是阿布扎比 DMT「会思考的排水系统」的排水智能体。系统围绕「积水点」的全生命周期工作；客户最终要的两个答案是：某个积水点**什么时候退水**、**用什么手段最快退水**，"
        "并且分清**单点最优**（只顾这一个点）与**全局最优**（第三道防线的调度求解器，全网加权积水时长最小）。\n\n"
        "工作方式：\n"
        "1. 只用 swbrain 工具拿数字，只引用工具返回的数字，不编造。问到某个点「什么时候退 / 怎样最快 / 派几台泵」先调 recession_options，它一次给出自然 / 全局最优 / 单点最优 / 预置 / 罐车 / 换管六种手段的退干时刻与单点最优对全局的代价；要全貌用 inspect_point；筛点用 list_points；全网用 dispatch_plan / preposition_plan。\n"
        "2. 情景键：5/10/25/50/100（年一遇）、10cc（10 年 + 20% 气候变化）、2024-04 与 2026-03（实测事件）；没说情景就用当前情景（ontology_overview 里有），回答里点明情景。\n"
        "3. 积水点编号 ADM-n / AAM-n / DRM-n；Parsons 报告里的 HS-n 等于 ADM-n。\n"
        "4. 会写文件的动作只有 issue_alert（写预警单）与 record_observation（登记现场观测），只在用户明确要求时调用，其余动作只读。永不写源数据库。\n"
        "5. 世界模型（GWM）是研究级仿真器：水深只涨不退、体积偏大，只作空间对照，调度结论不用它的体积。标出「模拟 / 占位」的参数。\n"
        "6. 回答用用户的语言（中文或英文），先给结论和关键数字（带单位、带情景），再给依据；动作结果里的 ui 字段是控制台联动提示（切情景 / 打开详情 / 高亮点位），控制台的对话框会自动照做；结果里的 console_link 是能在地图上看到这些点的控制台地址，用户在别处（如 Harness Web UI）问时把它给出来。\n"
        "7. 客户常问的一串问题及对应工具：预报 X mm 相当于几年一遇、该用哪个情景 → rain_to_scenario（缺省顺手把情景切过去，后面的问题都用它）；现在有多少个积水点 → count_points；世界模型推演后新增了多少 → world_model_new_points；其中最严重的有哪些 → world_model_new_points（按体积）或 list_points（III 级按优先级）；怎么加快这些点的退水 → recession_batch（把上一步的编号传进 fp_ids；世界模型新增点用它最近的本系统积水点）；需要投入的成本和代价 → cost_summary（带 fp_ids 给这批点的账，不带给情景全局账）。\n\n"
        f"本体对象类型：\n{ot}\n\n规则（可用 explain_rule 解释）：\n{rl}\n"
    )


# ---------------------------------------------------------------- 工具执行
def _clip(v, depth=0):
    if isinstance(v, dict):
        return {k: _clip(x, depth + 1) for k, x in v.items() if not str(k).startswith("_")}
    if isinstance(v, list):
        return [_clip(x, depth + 1) for x in v[: (60 if depth < 2 else 15)]]
    return v


def call_tool(name: str, args: dict, get=None, post=None, owner: str | None = None,
              readonly: bool | None = None) -> tuple[str, bool]:
    """返回 (文本, isError)。

    `get` / `post` 是取数口，缺省走 HTTP（stdio 那一条路：本进程不装载状态，
    所有调用转发给控制台服务）。**平台转发进来的那一支传直连的取数器** ——
    那时服务端就在本进程里，再走一遍 HTTP 是白绕一圈，而多出来的那一跳
    出错时报的是「服务不可达」，离真因很远。两个取数口的形状一样：
    `get(path, params) -> dict`、`post(path, body) -> dict`。

    `owner` 决定归属判定（见 {@link _mine}）：缺省按本进程的 OWNER，
    平台转发那一支传 `"*"` —— 那边「谁能碰哪些工具」由平台的智能体声明定。

    `readonly` 缺省用本进程的 {@link READONLY}（Harness 原生 Web UI 那个入口
    装的是只读的进程）。显式传 False 的是平台转发那一支：那个入口按**发起人的
    `SWBRAIN.*` 职能**判写不写，而不是一律拒写。
    """
    args = dict(args or {})
    get = get or http_get
    post = post or http_post
    if readonly is None:
        readonly = READONLY
    if name == "ontology_overview":
        d = get("/api/ontology"); h = get("/api/health")
        out = {"core": d.get("core"), "object_types": [{"api": o["api"], "zh": o["zh"], "pk": o["pk"], "n_props": len(o["properties"])} for o in d.get("object_types", [])],
               "links": [f"{l['src']}.{l['api']}→{l['dst']}" for l in d.get("link_types", [])], "rules": [{"api": r["api"], "zh": r["zh"], "kind": r["kind"]} for r in d.get("rules", [])],
               "actions": [{"api": a["api"], "zh": a["zh"], "side_effect": a["side_effect"]} for a in d.get("actions", [])], "scenarios": d.get("scenarios"), "active_scenario": h.get("active_rp"),
               "warm_done": (h.get("warm") or {}).get("done"), "console": BASE_URL, "examples": d.get("examples")}
        return json.dumps(out, ensure_ascii=False), False
    if name == "get_object":
        d = get("/api/object", {"type": args.get("type"), "id": args.get("id"), "rp": args.get("scenario"), "links": "0" if args.get("links") is False else "1"})
        if d.get("object") is None and not d.get("error"):
            return json.dumps({"error": f"没有 {args.get('type')} {args.get('id')}"}, ensure_ascii=False), True
        return json.dumps(_clip(d), ensure_ascii=False), bool(d.get("error"))
    if name == "search_objects":
        params = {"type": args.get("type"), "rp": args.get("scenario"), "order": args.get("order_by"), "limit": args.get("limit")}
        for k, v in (args.get("filters") or {}).items():
            params[k] = str(v).lower() if isinstance(v, bool) else v
        d = get("/api/objects", params)
        return json.dumps(_clip(d), ensure_ascii=False), bool(d.get("error"))
    if name == "follow_link":
        d = get("/api/object", {"type": args.get("type"), "id": args.get("id"), "rp": args.get("scenario"), "links": "1"})
        rows = (d.get("links") or {}).get(args.get("link"))
        if rows is None:
            return json.dumps({"error": f"{args.get('type')} 没有关系 {args.get('link')} 或对象不存在", "available": list((d.get('links') or {}).keys())}, ensure_ascii=False), True
        return json.dumps({"type": args.get("type"), "id": args.get("id"), "link": args.get("link"), "rows": _clip(rows)}, ensure_ascii=False), False
    if name in S.AT:
        if not _mine(name, owner):                                 # 不是这个智能体的：桥这层就拒（照 tool_list 的口径，别只靠客户端不列出来）
            return json.dumps({"error": f"「{S.AT[name].zh}」不属于这个智能体（{OWNER}）；它归 {getattr(S.AT[name], 'owner', 'dispatch')}，"
                                        "请到那个智能体（或控制台对话）里做"}, ensure_ascii=False), True
        if readonly and S.AT[name].side_effect != "none":
            return json.dumps({"error": f"「{S.AT[name].zh}」会写数据，这个入口（Harness 原生 Web UI）只读。"
                                        "要到控制台登录（平台账号，按职能授权）后再做，控制台地址见 console_link。",
                               "console_link": BASE_URL}, ensure_ascii=False), True
        d = post("/api/action", {"action": name, "params": args, "actor": f"dsh:{OWNER}", "lang": "zh"})   # 带上智能体 id：服务端按它判自建智能体的动作
        if d.get("error") and "ok" not in d:
            return json.dumps({"error": d["error"]}, ensure_ascii=False), True
        out = {"ok": d.get("ok"), "summary_zh": d.get("summary_zh") or d.get("summary"), "summary_en": d.get("summary_en"), "ui": d.get("ui"), "console_link": (BASE_URL + d["console_link"]) if d.get("console_link") else None, "side_effect": d.get("side_effect"), "result": _clip(d.get("result"))}
        if not d.get("ok"):
            out["error"] = d.get("error")
        return json.dumps(out, ensure_ascii=False), not bool(d.get("ok"))
    return json.dumps({"error": f"没有工具 {name}"}, ensure_ascii=False), True


# ---------------------------------------------------------------- JSON-RPC over stdio
def handle(msg: dict) -> dict | None:
    mid = msg.get("id"); method = msg.get("method"); params = msg.get("params") or {}
    if method is None:                                   # 响应消息（我们不发请求，忽略）
        return None
    if mid is None:                                      # 通知
        if method == "notifications/initialized":
            log("client initialized")
        return None
    try:
        if method == "initialize":
            v = params.get("protocolVersion"); v = v if v in SUPPORTED else SUPPORTED[0]
            return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": v, "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
                                                              "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "instructions": instructions()}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}}
        if method == "tools/call":
            t0 = time.perf_counter()
            text, is_err = call_tool(str(params.get("name")), params.get("arguments") or {})
            log(f"tools/call {params.get('name')} {'ERR' if is_err else 'ok'} {round((time.perf_counter() - t0) * 1000)} ms")
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": is_err}}
        if method in ("resources/list", "resources/templates/list"):
            return {"jsonrpc": "2.0", "id": mid, "result": {"resources": [], "resourceTemplates": []} if method.endswith("templates/list") else {"resources": []}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"prompts": []}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except urllib.error.URLError as ex:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": f"排水智能体服务不可达 {BASE_URL}：{ex}"}}
    except Exception as ex:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(ex).__name__}: {str(ex)[:300]}"}}


def serve_stdio():
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except Exception:
        pass
    log(f"serving; backend {BASE_URL}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n"); sys.stdout.flush()
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        for m in msgs:
            resp = handle(m) if isinstance(m, dict) else None
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n"); sys.stdout.flush()
    log("stdin closed, exiting")


def selftest() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tools = tool_list()
    print(f"tools: {len(tools)} → {[t['name'] for t in tools]}")
    print(f"instructions: {len(instructions().encode('utf-8'))} bytes")
    text, err = call_tool("ontology_overview", {})
    print("ontology_overview:", "ERR" if err else "ok", text[:300])
    text, err = call_tool("recession_options", {"fp_id": "ADM-62"})
    print("recession_options ADM-62:", "ERR" if err else "ok", text[:400])
    return 1 if err else 0


def main(argv=None):
    global BASE_URL, KEY, READONLY
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None); ap.add_argument("--key", default=None); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--readonly", action="store_true", help="只读：会写数据的动作一律拒（原生 Web UI 入口用）")
    a = ap.parse_args(argv)
    if a.url:
        BASE_URL = a.url.rstrip("/")
    if a.key is not None:
        KEY = a.key
    if a.readonly:
        READONLY = True
    if a.selftest:
        return selftest()
    serve_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())
