"""DeepSeek Harness（dsh）接入：把排水智能体变成一个真正的 agent 运行时里的智能体。

三件事：
1. render：把 agent/dsh/ 下的预设（人设 + swbrain MCP 工具 + 技能）、补丁与初始设置渲染到 Harness 主目录（$DSH_HOME，缺省 out/dsh_home）；
2. web：用渲染好的补丁启动 dsh Web UI（默认 http://127.0.0.1:3080），默认预设就是「排水智能体」——这是最强的交互入口（流式对话、工具调用可视、会话、审批、计划…）；
3. DshBridge：经 Python SDK（deepseek-harness-sdk，JSON-RPC stdio）驱动 sdk profile 的 agent，供控制台右侧对话抽屉与 /api/agent 使用，
   工具事件转成控制台能显示的步骤。

模型：[agent.dsh].provider / model；缺省有 DEEPSEEK_API_KEY 用 deepseek-official/deepseek-flash，否则有 OPENAI_API_KEY 用 openai/gpt-5.1。
密钥只从环境变量读，永不写进仓库。
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "agent" / "dsh"
PRESET_ID = "drainage"


# ---------------------------------------------------------------- 配置
def settings(cfg: dict) -> dict:
    AG = (cfg.get("agent") or {}); D = (AG.get("dsh") or {}); SV = (cfg.get("serve") or {})
    home = Path(str(D.get("home") or (ROOT / "out" / "dsh_home"))).expanduser()
    if not home.is_absolute():
        home = ROOT / home
    # Web UI 用单独的主目录：SDK 运行时会把会话与指向 exe 快照的包桩写进它的 home，混在一起时 Web UI 会列出它打不开的会话、选工作区也会失败
    home_web = Path(str(D.get("home_web") or (ROOT / "out" / "dsh_home_web"))).expanduser()
    if not home_web.is_absolute():
        home_web = ROOT / home_web
    from . import envkeys
    key_source = {"deepseek": envkeys.source("DEEPSEEK_API_KEY"), "openai": envkeys.source("OPENAI_API_KEY"), "anthropic": envkeys.source("ANTHROPIC_API_KEY")}   # 先记来源（present 会把注册表值写回进程环境）
    has_ds, has_oa, has_an = envkeys.present("DEEPSEEK_API_KEY"), envkeys.present("OPENAI_API_KEY"), envkeys.present("ANTHROPIC_API_KEY")   # 进程环境没有就查注册表（setx 之后没重开的终端拉起的服务）
    provider = D.get("provider") or ("deepseek-official" if has_ds else ("openai" if has_oa else ("anthropic" if has_an else "deepseek-official")))
    model = D.get("model") or {"deepseek-official": "deepseek-flash", "openai": "gpt-5.1", "anthropic": "claude-sonnet-4-5"}.get(provider, "deepseek-flash")
    port = int(SV.get("port") or 8766)
    return {"enabled": bool(D.get("enabled", True)), "home": home, "home_web": home_web, "provider": provider, "model": model, "reasoning_effort": D.get("reasoning_effort"), "max_tokens": D.get("max_tokens"),
            "web_port": int(D.get("web_port", 3080)), "web_url": D.get("web_url") or f"http://127.0.0.1:{int(D.get('web_port', 3080))}",
            "lan_port": int(D.get("lan_port", 3081) or 0),      # 局域网入口（swbrain/dsh_lanproxy.py，只放管理员）；0 = 不开

            "swbrain_url": D.get("swbrain_url") or f"http://127.0.0.1:{port}", "swbrain_key": str(SV.get("access_key") or ""), "python": sys.executable,
            "key_present": {"deepseek": has_ds, "openai": has_oa, "anthropic": has_an},
            "key_source": key_source,   # process / user / machine / None，只说来源不给值
            "initialize_timeout_s": float(D.get("initialize_timeout_s", 120)), "request_timeout_s": D.get("request_timeout_s")}


def launch_args(dsh_bin: str | None = None, kind: str = "sdk") -> tuple[str, ...]:
    """dsh 可执行。sdk：优先 Python SDK 自带的运行时（与 SDK 同版本）；web：优先 npm 全局安装的 dsh（SDK 运行时没打包 Web 前端），其次另一个。"""
    if dsh_bin:
        return (str(dsh_bin),)

    def wheel():
        try:
            from deepseek_harness_runtime import resolve_bundled_launch_args
            return tuple(resolve_bundled_launch_args())
        except Exception:
            return None

    def npm():
        w = shutil.which("dsh") or shutil.which("dsh.cmd")
        return (w,) if w and "deepseek_harness_runtime" not in w and "Python" not in w else None

    order = (npm, wheel) if kind == "web" else (wheel, npm)
    for f in order:
        r = f()
        if r:
            return r
    raise FileNotFoundError("没有 dsh：pip install deepseek-harness-sdk（自带运行时，供 SDK / 无头用）；Web UI 要 npm i -g @deepseek-ai/dsh")


def available() -> bool:
    try:
        launch_args(); import deepseek_harness  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 渲染
def _sub(text: str, vars: dict) -> str:
    for k, v in vars.items():
        text = text.replace(f"__{k}__", str(v))
    return text


def render(cfg: dict, home: Path | None = None, link_modules: bool = True) -> dict:
    """渲染预设 / 补丁 / 初始设置到一个 Harness 主目录（缺省 SDK 的 home；Web 用 home_web）。返回各路径。幂等，可反复调用。

    预设按 agent/dsh/presets/ 下的每个目录渲染一份（drainage = 调度研判，hydro / report / qa = 另外三个智能体）：
    多智能体与管理端是同一套切法（swbrain/agents/registry.py），Harness 里也就一一对应地看到四个可选预设。
    每个预设的 MCP 段带 SWBRAIN_OWNER=<目录名>，MCP 桥只把那一个智能体名下的动作做成工具。"""
    st = settings(cfg); home = Path(home) if home is not None else st["home"]
    src_root = TPL / "presets"
    presets = sorted(d.name for d in src_root.iterdir() if (d / "preset.yml").exists()) if src_root.exists() else [PRESET_ID]
    (home / "patches").mkdir(parents=True, exist_ok=True)
    out = {"home": home, "presets": {}}
    for aid in presets:
        preset_src = src_root / aid
        preset_dst = home / ".agent-presets" / aid
        skills_dst = preset_dst / "skills"
        vars = {"REPO": ROOT.as_posix(), "PYTHON": Path(st["python"]).as_posix(), "SWBRAIN_URL": st["swbrain_url"], "SWBRAIN_KEY": st["swbrain_key"],
                "PROVIDER": st["provider"], "MODEL": st["model"], "PRESET_DIR": preset_dst.as_posix(), "SKILLS_DIR": skills_dst.as_posix(),
                "DELEGATION_FILE": (home / ".delegation").as_posix()}
        preset_dst.mkdir(parents=True, exist_ok=True)
        for p in preset_src.rglob("*"):
            if p.is_file():
                q = preset_dst / p.relative_to(preset_src); q.parent.mkdir(parents=True, exist_ok=True)
                q.write_text(_sub(p.read_text(encoding="utf-8"), vars), encoding="utf-8", newline="\n")
        out["presets"][aid] = preset_dst
    _render_custom(cfg, home, out)                              # 用户自建的智能体也各出一份预设（与控制台的智能体列表一致）
    out["preset_dir"] = out["presets"].get(PRESET_ID)          # 兼容：老调用点按「那一个预设」读
    out["skills_dir"] = (out["preset_dir"] / "skills") if out["preset_dir"] else None
    for name in ("web.patch.yml", "sdk.patch.yml"):
        text = _sub((TPL / name).read_text(encoding="utf-8"), _vars_for(cfg, home))
        if name == "web.patch.yml" and _web_v02(cfg):
            text = _web_patch_v02(text, out["presets"])
        q = home / "patches" / name; q.write_text(text, encoding="utf-8", newline="\n"); out[name] = q
    s = home / "settings.yaml"
    if not s.exists():
        s.write_text((TPL / "settings.yaml").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    out["settings"] = s
    if link_modules:
        _link_node_modules(home)
    out["workspace"] = _workspace(home)
    _seed_workspace(home, out["workspace"])
    (home / "README.txt").write_text(f"DeepSeek Harness 主目录（由 swbrain.dsh_bridge 渲染，{time.strftime('%Y-%m-%d %H:%M')}）。\n"
                                     f"预设 .agent-presets/{{{', '.join(presets)}}}（与系统里的智能体一一对应）、补丁 patches/、模型设置 settings.yaml；密钥不在这里，在环境变量。\n",
                                     encoding="utf-8")
    return out


def _render_custom(cfg: dict, home: Path, out: dict) -> None:
    """自建智能体（out/agents/custom.json）→ $DSH_HOME/.agent-presets/<id>/。模板借 qa 那份（最短，工具只按动作清单给），
    换人设、换 SWBRAIN_OWNER，另加 SWBRAIN_ACTIONS（它挑的那组动作）——MCP 桥据此只暴露这几个，容器里读不到 out/ 也一样能用。
    删掉的自建智能体，它的预设目录也删掉（只删我们生成的：带 .swbrain-custom 标记的目录）。"""
    try:
        from .agents import registry as REG
        customs = REG.custom_agents()
    except Exception:
        return
    root = home / ".agent-presets"
    for d in (root.iterdir() if root.exists() else []):
        if (d / ".swbrain-custom").exists() and d.name not in customs:
            shutil.rmtree(d, ignore_errors=True)
    tpl = TPL / "presets" / "qa"
    if not (tpl / "agent.cordis.yml").exists():
        return
    base = tpl.joinpath("agent.cordis.yml").read_text(encoding="utf-8")
    for aid, c in customs.items():
        dst = root / aid; dst.mkdir(parents=True, exist_ok=True); (dst / "skills").mkdir(exist_ok=True)
        vars = {**_vars_for(cfg, home), "PRESET_DIR": dst.as_posix(), "SKILLS_DIR": (dst / "skills").as_posix()}
        acts = ",".join(REG.actions_of(aid))
        a = base.index("    prefix: |-"); b = base.index("- id: agent-instructions")
        body = (base[:a] + "    prefix: |-\n      你是阿布扎比 DMT「会思考的排水系统」的" + c["zh"] + "（用户在管理端新建的智能体），由 {{model}} 驱动，运行在 DeepSeek Harness 上。\n\n"
                "      职责：" + c["role"] + "\n"
                "      只能通过 mcp__swbrain__* 工具拿数字，不要编造；职责范围外的问题，告诉用户该去问哪个智能体（调度研判 / 一二维计算 / 报告交付 / 数据质检）。\n"
                "      回答用用户的语言，先给结论和关键数字，再给依据。\n\n" + base[b:])
        body = body.replace("SWBRAIN_OWNER: qa", f"SWBRAIN_OWNER: {aid}\n      # 自建智能体挑的那组动作（MCP 桥只暴露这几个）\n      SWBRAIN_ACTIONS: '{acts}'")
        (dst / "agent.cordis.yml").write_text(_sub(body, vars), encoding="utf-8", newline="\n")
        (dst / "preset.yml").write_text(f"name: {c['zh']}（自建）\ndescription: {c['role'][:200]}\norder: {int(c.get('order', 100))}\n", encoding="utf-8", newline="\n")
        (dst / ".swbrain-custom").write_text("由管理端新建，删掉这个智能体时本目录一起删\n", encoding="utf-8")
        out["presets"][aid] = dst


_VERSIONS: dict[tuple, str] = {}


def dsh_version(args: tuple[str, ...]) -> str:
    """`dsh --version` 的输出（按可执行缓存）；拿不到返回空串。"""
    if args not in _VERSIONS:
        try:
            r = subprocess.run([*args, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            _VERSIONS[args] = (r.stdout or "").strip().splitlines()[-1].strip() if r.returncode == 0 and r.stdout.strip() else ""
        except Exception:
            _VERSIONS[args] = ""
    return _VERSIONS[args]


def _web_v02(cfg: dict) -> bool:
    """Web 用的 dsh 是不是 0.2 及以后（预设注册表不再扫 .agent-presets/，几个条目改了 id）。"""
    D = ((cfg.get("agent") or {}).get("dsh") or {})
    try:
        v = dsh_version(launch_args(D.get("dsh_bin_web"), kind="web"))
    except Exception:
        return False
    try:
        major, minor = (int(x) for x in v.split("-")[0].split(".")[:2])
    except Exception:
        return False
    return (major, minor) >= (0, 2)


def _web_patch_v02(text: str, presets: dict) -> str:
    """把 0.1.x 写法的 web 补丁改成 0.2.x（2026-10-03 按 0.2.1-alpha.1 实测）：
    - agent-presets → agent-preset-registry；workflow-worker-thread → workflow-ptc（老 id 在新版里报 entry not found，整段被忽略）
    - 注册表不再扫描 $DSH_HOME/.agent-presets/，预设要在配置树里声明成 @deepseek-ai/dsh-agent-preset 行：
      把已渲染的每个预设（preset.yml 的名字/说明/顺序 + agent.cordis.yml 的插件行）插成一行。"""
    import yaml
    text = text.replace("- id: agent-presets\n", "- id: agent-preset-registry\n").replace("- id: workflow-worker-thread\n", "- id: workflow-ptc\n")
    rows = []
    for aid, d in presets.items():
        meta = yaml.safe_load((d / "preset.yml").read_text(encoding="utf-8")) or {}
        plugins = yaml.safe_load((d / "agent.cordis.yml").read_text(encoding="utf-8")) or []
        conf = {"id": aid, "name": meta.get("name") or aid, "plugins": plugins}
        for k in ("description", "order"):
            if meta.get(k) is not None:
                conf[k] = meta[k]
        rows.append({"id": f"preset-{aid}", "name": "@deepseek-ai/dsh-agent-preset", "config": conf})
    if rows:
        text += ("\n# ── 预设声明（dsh 0.2.x：注册表不扫目录，由 swbrain.dsh_bridge 从 .agent-presets/ 生成）──\n"
                 + yaml.safe_dump([{"insert": rows}], allow_unicode=True, sort_keys=False, width=1000))
    return text


def _vars_for(cfg: dict, home: Path) -> dict:
    """补丁文件用的占位符（预设循环之外也要一份）。"""
    st = settings(cfg)
    preset_dst = home / ".agent-presets" / PRESET_ID
    return {"REPO": ROOT.as_posix(), "PYTHON": Path(st["python"]).as_posix(), "SWBRAIN_URL": st["swbrain_url"], "SWBRAIN_KEY": st["swbrain_key"],
            "PROVIDER": st["provider"], "MODEL": st["model"], "PRESET_DIR": preset_dst.as_posix(), "SKILLS_DIR": (preset_dst / "skills").as_posix(),
            "DELEGATION_FILE": (home / ".delegation").as_posix()}


def web_url_live(cfg: dict) -> str | None:
    """Web UI 启动时会打印带一次性 token 的地址（out/dsh_web.log）；端口在监听就把它给控制台的「在 Harness 打开」。"""
    import re, socket
    st = settings(cfg)
    try:
        with socket.create_connection(("127.0.0.1", int(st["web_port"])), timeout=0.3):
            pass
    except OSError:
        return None
    log = ROOT / "out" / "dsh_web.log"
    if log.exists():
        m = re.findall(r"(http://(?:127\.0\.0\.1|0\.0\.0\.0|localhost):%d/\S*)" % int(st["web_port"]), _last_run(log))
        if m:
            return m[-1].strip()
    return st["web_url"]


def _link_node_modules(home: Path) -> None:
    """用户预设放在 $DSH_HOME/.agent-presets/<id>/，Node 从那里往上找不到 @deepseek-ai/dsh-* 包（dsh 0.1.5 会报 Cannot find package）。
    profile 初始化后 $DSH_HOME/profiles/node_modules 里有全套包：在 $DSH_HOME/node_modules 放一个目录联接指过去即可（Windows 用 mklink /J，其余 symlink）。"""
    link = home / "node_modules"
    target = None
    w = shutil.which("dsh") or shutil.which("dsh.cmd")              # npm 全局 dsh：<prefix>/node_modules/@deepseek-ai/dsh/node_modules 是真实包
    if w and "deepseek_harness_runtime" not in w and "Python" not in w:
        cand = Path(w).resolve().parent / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules"
        if cand.exists():
            target = cand
    if target is None:                                               # 退路：profile 初始化写下的包（SDK 运行时写的是指向 exe 快照的桩，npm 的 web 进程用不了）
        cand = home / "profiles" / "node_modules"
        target = cand if cand.exists() else None
    if target is None or link.exists():
        return
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
        else:
            os.symlink(target, link, target_is_directory=True)
    except Exception as ex:
        print("node_modules 联接失败（用户预设可能装不上）：", str(ex)[:160], file=sys.stderr)


def _workspace(home: Path) -> Path:
    """智能体的工作目录：一个很小的目录（README + 关键文档副本），不要用仓库根——Web UI 会索引工作区里的文件（@ 文件），
    仓库根下 out/ 里有几十 GB 与几万个 node_modules 文件，会把页面卡死。MCP 桥的 cwd 另有配置（仓库根，为了 python -m swbrain.mcp_server）。"""
    ws = home / "workspace"; (ws / "docs").mkdir(parents=True, exist_ok=True)
    for name in ("本体模型.md", "智能体运行时_DeepSeek_Harness.md", "接口约定.md", "可信度说明.md"):
        src = ROOT / "docs" / name
        if src.exists():
            (ws / "docs" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    readme = ("# 排水智能体工作区\n\n这是 DeepSeek Harness 里「排水智能体」预设的工作目录。数字都来自 swbrain MCP 工具（排水智能体 HTTP 服务），这里只放说明文档：\n\n"
              "- docs/本体模型.md：以积水点为核心的对象 / 关系 / 规则 / 动作\n- docs/智能体运行时_DeepSeek_Harness.md：运行时接入与客户五问\n- docs/接口约定.md、docs/可信度说明.md\n\n"
              f"源码仓库：{ROOT.as_posix()}（不要把它设为工作区，文件太多会卡住界面）。\n")
    (ws / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    return ws


def _seed_workspace(home: Path, ws: Path) -> None:
    """把工作目录预先登记进 Web UI 的工作区注册表（storages/workspace.json），用户就不用再点「添加工作区」走系统目录选择框。只在注册表里没有它时写；文件格式照 dsh 0.1.5 写的样子。"""
    import uuid
    st_dir = home / "storages"; st_dir.mkdir(parents=True, exist_ok=True)
    f = st_dir / "workspace.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"unit": {"name": "workspace", "version": 2}, "global": {"initialized": True, "workspaceIds": [], "archivedSessionIds": []}, "tables": {"workspaces": {}}}
        tables = d.setdefault("tables", {}).setdefault("workspaces", {})
        want = str(ws)
        if any(str(w.get("path", "")).rstrip("\\/").lower() == want.rstrip("\\/").lower() for w in tables.values()):
            return
        wid = str(uuid.uuid4()); now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        tables[wid] = {"path": want, "title": "排水智能体", "sessionIds": [], "createdAt": now, "updatedAt": now}
        g = d.setdefault("global", {}); g.setdefault("workspaceIds", []).insert(0, wid); g["initialized"] = True; g.setdefault("archivedSessionIds", [])
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    except Exception as ex:
        print("工作区注册表写入失败（可在 Web UI 里手动添加工作区）：", str(ex)[:160], file=sys.stderr)


def env_for(st: dict, home: Path | None = None) -> dict:
    env = os.environ.copy(); env["DSH_HOME"] = str(home if home is not None else st["home"]); env.setdefault("PYTHONIOENCODING", "utf-8"); env.setdefault("PYTHONUTF8", "1")
    if st.get("swbrain_key"):
        env["SWBRAIN_KEY"] = str(st["swbrain_key"])      # 只走进程环境，不写进 out/ 下的 YAML（2026-09-23 修）
    return env


LOG_MARK = "\n--- swbrain 托管启动 "


def _last_run(log: Path) -> str:
    """日志里最近一次托管启动之后的部分。dsh 先开端口、后打印新 token；这段空档里旧 token 已作废
    （浏览器报 dsh web authentication required），所以只认这次启动打印的。"""
    text = log.read_text(encoding="utf-8", errors="ignore")
    i = text.rfind(LOG_MARK)
    return text[i:] if i >= 0 else text


def web_tokens(cfg: dict) -> list[str]:
    """日志里出现过的 Web UI 一次性 token 地址（dsh 每次启动都换一张）。dsh 打印的主机可能是 127.0.0.1 也可能是 0.0.0.0。"""
    import re
    st = settings(cfg)
    log = ROOT / "out" / "dsh_web.log"
    if not log.exists():
        return []
    return re.findall(r"http://(?:127\.0\.0\.1|0\.0\.0\.0|localhost):%d/\?token=\S+" % int(st["web_port"]), _last_run(log))


def _lan_hosts(cfg: dict) -> list[str]:
    """服务登记的非回环对外地址的主机名（[identity].extra_public_bases 等）：Web UI 也要让这些主机访问。"""
    from urllib.parse import urlparse
    I = (cfg.get("identity") or {})
    extra = [str(x).strip() for x in (I.get("extra_public_bases") or [])]
    bases = [I.get("public_base") or ""] + [x for x in extra if x.lower() != "auto"]
    out = []
    if any(x.lower() == "auto" for x in extra):             # "auto"：本机当前的局域网地址（会变），见 identity.local_ipv4s
        from .identity import local_ipv4s
        bases += [f"http://{ip}" for ip in local_ipv4s()]
    for b in bases:
        h = urlparse(str(b)).hostname or ""
        if h and h not in ("127.0.0.1", "localhost", "::1") and h not in out:
            out.append(h)
    return out


def web_url_for(url: str | None, host: str | None, cfg: dict) -> str | None:
    """dsh 只听本机（它不许听全部网卡）。本机来的给 127.0.0.1:<web_port>；局域网来的给局域网入口
    <局域网主机>:<lan_port>（swbrain/dsh_lanproxy.py 转到本机，只放管理员）。只换成登记过的局域网主机，伪造 Host 换不出别处。"""
    if not url:
        return url
    url = url.replace("http://0.0.0.0:", "http://127.0.0.1:")
    lp = settings(cfg)["lan_port"]
    if not (lan_request(host, cfg) and lp):
        return url
    from urllib.parse import urlparse
    h = (str(host or "").split(":")[0]).strip().lower()
    return urlparse(url)._replace(netloc=f"{h}:{lp}").geturl()


def lan_request(host: str | None, cfg: dict) -> bool:
    """这个请求是从局域网地址（登记过的 10.0.0.47 等）进来的，不是本机浏览器。"""
    return (str(host or "").split(":")[0]).strip().lower() in _lan_hosts(cfg)


def web_running(cfg: dict) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(settings(cfg)["web_port"])), timeout=0.3):
            return True
    except OSError:
        return False


class WebBridge:
    """受控拉起 Harness 原生 Web UI（一个进程）：由 8766 摆一个入口，先过控制台的登录，再开它自己的地址。

    为什么不挂在 /harness/ 反向代理：Web UI 是单页应用，`<base href="/">` 与前端请求都写死根路径，挂子路径要改写 HTML 与前端包。
    这里改为「服务托管启动 + 登录后才把它的地址交给浏览器」；地址里那张一次性 token 只在服务端内存里过一手。
    **只读**：dsh 的 MCP 客户端 headers 是静态配置，按会话传不了身份，所以这个入口置 SWBRAIN_READONLY=1，
    会写数据的动作在 MCP 桥就被拒（要写得回控制台，平台账号登录后按职能做）。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg; self.st = settings(cfg)
        self._p = None; self._lock = threading.Lock(); self.error: str | None = None; self.started_at: str | None = None
        self.log = ROOT / "out" / "dsh_web.log"
        self.idn = None                                    # identity.Identity：serve 传进来，用来发只读凭据

    def delegation_file(self) -> Path:
        return Path(self.st["home_web"]) / ".delegation"

    def write_credential(self) -> str | None:
        """把共享只读凭据写到 Web 主目录的 .delegation（MCP 桥每次调用前读它）。
        没有 identity（共享口令模式）就删掉——那时 MCP 走 ?key= 口令，不需要凭据。
        容器方式（docker/dsh-web）下 `out/dsh_home_web` 在两边共享，这个文件写一次两边都看得到。"""
        f = self.delegation_file()
        if self.idn is None:
            f.unlink(missing_ok=True); return None
        tok = self.idn.readonly_token()
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(tok, encoding="utf-8")
        return tok

    def running(self) -> bool:
        if self._p is None:
            return False
        if self._p.poll() is not None:
            self.error = f"Web UI 已退出（退出码 {self._p.returncode}），看 {self.log.name} 末尾"
            return False
        return web_running(self.cfg)

    def foreign(self) -> bool:
        """端口在监听但不是本实例起的：多半是有人手动跑了 `python -m swbrain.dsh_bridge --web`，
        或者是本服务上一次运行留下的（服务重启后 self._p 就没了）。这时 stop() 停不掉它，
        而 ensure() 会以为「已经在跑」直接返回——它加载的可能是**改补丁之前**的旧配置，
        表现为「明明开了子智能体工具，Harness 里却没有」（2026-10-02 实际踩到）。"""
        return self._p is None and web_running(self.cfg)

    def ensure(self, wait_s: float = 90) -> dict:
        """没起就起（只读、不打开浏览器），等端口在监听；并发只起一个。
        端口被别人占着时不强行接管（可能有人在用），但在 info() 里明确标出来。"""
        with self._lock:
            if self.running():
                return self.info()
            self.log.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.write_credential()                   # 只读凭据要在 dsh 起之前落盘：MCP 桥启动时就读它
            except Exception as ex:
                self.error = f"只读凭据没写成（{type(ex).__name__}: {str(ex)[:120]}）；Web UI 里工具会调不动"
            args, env = web_command(self.cfg, open_browser=False, readonly=True)
            try:
                fh = self.log.open("a", encoding="utf-8")
                fh.write(f"{LOG_MARK}{time.strftime('%Y-%m-%d %H:%M:%S')}（只读）：{' '.join(args)}\n"); fh.flush()
                self._p = subprocess.Popen(args, env=env, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                                           creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0), start_new_session=(os.name != "nt"))
                self.error = None; self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            except Exception as ex:
                self.error = f"{type(ex).__name__}: {str(ex)[:200]}"; return self.info()
            end = time.monotonic() + wait_s
            while time.monotonic() < end:
                if self._p.poll() is not None:
                    self.error = f"启动即退出（退出码 {self._p.returncode}）：看 {self.log.name} 末尾"; break
                if web_running(self.cfg) and web_tokens(self.cfg):   # 端口先开、token 后打印：两样都有才算起好
                    break
                time.sleep(0.5)
            return self.info()

    def stop(self) -> dict:
        """按进程树停：dsh 是 node 启动器 + 真正监听的服务，只 terminate 启动器会留下占着端口的子进程。"""
        with self._lock:
            if self._p is not None and self._p.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self._p.pid), "/T", "/F"], capture_output=True)
                else:
                    try:
                        os.killpg(os.getpgid(self._p.pid), 15)
                    except Exception:
                        self._p.terminate()
                try:
                    self._p.wait(timeout=10)
                except Exception:
                    pass
            self._p = None
            return self.info()

    def info(self) -> dict:
        urls = web_tokens(self.cfg)
        foreign = self.foreign()
        return {"managed": self._p is not None, "running": self.running(), "started_at": self.started_at, "error": self.error,
                "port": int(self.st["web_port"]), "url": urls[-1] if urls else None, "readonly": True,
                # 端口在听但不是本实例起的：加载的可能是旧配置（补丁改过就要重启它），页面上的提示照这句说
                "foreign": foreign,
                "note_foreign": ("这个端口上是别的进程起的 Web UI（不是服务托管的那个，可能是手动跑的或上次服务留下的）；"
                                 "它加载的是启动那一刻的配置——改过 agent/dsh/ 下的补丁就要停掉它再重新打开。"
                                 "当前进程：可用 taskkill 或任务管理器停掉 3080 上的 node。") if foreign else None,
                "log": self.log.relative_to(ROOT).as_posix() if self.log.exists() else None}


def web_command(cfg: dict, port: int | None = None, open_browser: bool = False, dsh_bin: str | None = None, readonly: bool = True) -> tuple[list[str], dict]:
    st = settings(cfg); paths = render(cfg, home=st["home_web"], link_modules=True)
    # 启动器自己的旗标（--profile / --patch）在前，第一个不认识的 token（--port）起是 web 应用的参数
    D = ((cfg.get("agent") or {}).get("dsh") or {})
    args = [*launch_args(dsh_bin or D.get("dsh_bin_web"), kind="web"), "--profile", "web", "--patch", paths["web.patch.yml"].as_posix(), "--port", str(port or st["web_port"])]
    if not open_browser:
        args.append("--no-open")
    # 只听本机：dsh 拒绝 --host 0.0.0.0（「would expose remote code execution to the network」，直接退出），
    # 2026-10-02 加过局域网监听，结果 Web UI 一次都起不来，已撤回。局域网的人用控制台的对话（8766），不开这个入口。
    env = env_for(st, st["home_web"])
    if readonly:
        # 原生 Web UI 分不出是谁在用（dsh 的 MCP 客户端 headers 是静态配置、那边又只有一个一次性 token），
        # 所以这个入口只读：MCP 桥对会写数据的动作直接拒，并让人到控制台（2026-10-02）
        env["SWBRAIN_READONLY"] = "1"
    return args, env


# ---------------------------------------------------------------- SDK 桥（控制台对话用）
class DshBridge:
    """一个长驻的 sdk profile 进程；每个控制台对话一个 session_id，连续问答保留上下文。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg; self.st = settings(cfg); self._h = None; self._lock = threading.Lock(); self._run_lock = threading.Lock(); self.error: str | None = None; self.started_at = None
        self.start_s: float | None = None; self.starting = False
        self.auto_restart = False          # serve 预启动后置 True：出错关掉的运行时在后台立刻重拉（测试与命令行用法不受影响）

    def _start(self):
        from deepseek_harness import DeepSeekHarness
        paths = render(self.cfg, home=self.st["home"], link_modules=False)
        kw = {"dsh_home": str(self.st["home"]), "cwd": paths["workspace"].as_posix(), "provider": self.st["provider"], "model": self.st["model"], "profile": "sdk", "patches": (paths["sdk.patch.yml"].as_posix(),),
              "initialize_timeout_seconds": self.st["initialize_timeout_s"]}
        if self.st.get("max_tokens"):
            kw["max_tokens"] = int(self.st["max_tokens"])
        if self.st.get("reasoning_effort"):
            kw["reasoning_effort"] = str(self.st["reasoning_effort"])
        if self.st.get("request_timeout_s"):
            kw["request_timeout_seconds"] = float(self.st["request_timeout_s"])
        env = {"DSH_HOME": str(self.st["home"]), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        from . import envkeys
        for k_ in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            v_ = envkeys.get(k_)
            if v_:
                env[k_] = v_                                             # 子进程要拿得到密钥（注册表里的也算）
        try:
            h = DeepSeekHarness(**kw, env=env)
        except TypeError:
            h = DeepSeekHarness(**kw)
        t0 = time.perf_counter()
        h.start(); self._h = h; self.started_at = time.strftime("%Y-%m-%d %H:%M:%S"); self.error = None
        self.start_s = round(time.perf_counter() - t0, 1)       # 运行时 + MCP 桥启动要多久（实测约 60 s；控制台首问原来要付这一笔）

    def harness(self):
        with self._lock:
            if self._h is None:
                self._start()
            return self._h

    def prestart(self, log=print, reason: str = "启动时") -> threading.Thread:
        """后台把运行时拉起来，用户第一问就不用等它。失败只记下（error / 日志），不影响服务；首问时照旧按需再拉。"""
        def run():
            self.starting = True
            try:
                self.harness()
                log(f"  DeepSeek Harness 已就绪（{reason}预启动，{self.start_s} s）", flush=True)
            except Exception as ex:
                self.error = f"{type(ex).__name__}: {str(ex)[:300]}"
                log(f"  DeepSeek Harness 预启动失败（{self.error[:160]}）；首问时再试，期间调度研判退到大模型 / 规则", flush=True)
            finally:
                self.starting = False
        th = threading.Thread(target=run, name="dsh-prestart", daemon=True); th.start()
        return th

    def status(self) -> dict:
        return {"running": self._h is not None, "starting": bool(getattr(self, "starting", False)), "started_at": self.started_at,
                "start_s": getattr(self, "start_s", None), "error": self.error}

    def close(self):
        with self._lock:
            if self._h is not None:
                try:
                    self._h.close()
                finally:
                    self._h = None

    def delegation_file(self) -> Path:
        """这一轮对话的委托凭据（统一身份）。MCP 桥是长驻的独立进程，不能按对话传环境变量，所以每次调用前从这里读。
        只在 out/ 下、只在对话进行中存在，用完即删；凭据本身 15 分钟过期、只在服务内存里有效。"""
        return Path(self.st["home"]) / ".delegation"

    def chat(self, message: str, session_id: str | None = None, lang: str = "zh", delegation: str | None = None) -> dict:
        tq = time.perf_counter()
        with self._run_lock:                    # 一次只跑一轮：委托凭据文件只有一份，并发会把 A 的动作记到 B 头上
            r = self._chat(message, session_id, lang, delegation)
            r["wait_ms"] = round((time.perf_counter() - tq) * 1000 - r.get("start_ms", 0) - r.get("ms", 0), 1)   # 排在别人那一轮后面等了多久
            return r

    def _chat(self, message: str, session_id: str | None, lang: str, delegation: str | None) -> dict:
        col = StepCollector()
        df = self.delegation_file()
        try:
            ts = time.perf_counter(); cold = self._h is None
            h = self.harness()                  # 预启动还没完时，这里等它（不会再起第二个）
            start_ms = round((time.perf_counter() - ts) * 1000, 1)
            t0 = time.perf_counter()
            sid = session_id or f"console-{int(time.time())}"
            if delegation:
                df.parent.mkdir(parents=True, exist_ok=True); df.write_text(delegation, encoding="utf-8")
            else:
                df.unlink(missing_ok=True)
            r = h.run(message, session_id=sid, on_notification=col)
            reply = (r.final_response or "").strip()
            if not reply and not col.steps:
                # 模型没答、工具也没调（典型：模型账户额度用尽 PI_AI_ERROR）——当成失败抛出去，让 Agent 退回 llm / rules 规划器，而不是把空回复给用户
                raise RuntimeError(col.error or f"DeepSeek Harness 没有产出回复（finish={r.finish_reason}）")
            return {"reply": reply, "steps": col.steps, "ui": col.ui, "planner": "dsh", "session_id": sid, "finish_reason": r.finish_reason, "model": f"{self.st['provider']}/{self.st['model']}",
                    "ms": round((time.perf_counter() - t0) * 1000, 1), "start_ms": start_ms, "cold_start": cold}   # ms = 模型 + 工具；start_ms = 这一问等运行时启动（含等预启动）的时间
        except Exception as ex:
            self.error = f"{type(ex).__name__}: {str(ex)[:300]}"
            try:
                self.close()
            except Exception:
                pass
            if getattr(self, "auto_restart", False):
                self.prestart(reason="出错后")              # 立刻在后台重拉，不让下一位用户再付启动的那一分钟
            raise
        finally:
            df.unlink(missing_ok=True)


class StepCollector:
    """把 dsh 会话事件里的工具调用配成控制台能显示的步骤。

    事件形状（0.1.5 与 0.2.x 相同，2026-10-04 按两边的类型声明核对过）：
    tool/call.data = {turn, step, callId, name, arguments(JSON 串)}；
    tool/result.data = {turn, step, message: {source: {kind: tool, callId}, content: [...]}}。
    只有正文块不同：0.1.5 是 {type: tool-result, content: [text 块]}，0.2.x 直接是 text 块。
    运行失败：agent/error 的 data 有 message；turn/end 的 data.reason = {kind: 'error', error: {message, code}}。
    """

    def __init__(self):
        self.steps: list[dict] = []; self.pending: dict[str, dict] = {}; self.ui: dict = {}; self.texts: list[str] = []; self.error: str | None = None

    def __call__(self, n):
        if getattr(n, "method", None) != "session.event":
            return
        ev = (getattr(n, "payload", None) or {}).get("event") or {}
        typ = str(ev.get("type") or ""); data = ev.get("data") or {}
        if "error" in typ or (typ == "turn/end" and str(((data.get("reason") or {}) if isinstance(data.get("reason"), dict) else {}).get("kind") or "") == "error"):
            # 运行失败事件（如 agent/error、llm/error，或 turn/end reason.kind=error）：记下可读的错误文本，chat() 据此判定失败
            self.error = _error_text(data) or typ
        if typ == "tool/call":
            name = str(data.get("name") or data.get("tool") or "")
            cid = str(data.get("callId") or data.get("id") or len(self.steps))
            args = data.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args[:200]}
            self.pending[cid] = {"action": name.replace("mcp__swbrain__", ""), "params": args if isinstance(args, dict) else None, "t0": time.perf_counter()}
        elif typ == "tool/result":
            msg = data.get("message") or {}
            cid = str(((msg.get("source") or {}).get("callId")) or data.get("callId") or data.get("id") or "")
            p = self.pending.pop(cid, None) or {"action": str(data.get("name") or ""), "params": None, "t0": time.perf_counter()}
            txt = _result_text(data); self.texts.append(txt)
            summ, ok, u = _summary_from(txt)
            from .ontology.common import merge_ui
            merge_ui(self.ui, u)             # 带 pin 的主答案（as_built_next）不被后续查询盖掉
            self.steps.append({"action": p["action"], "zh": p["action"], "en": p["action"], "params": p.get("params"), "ok": ok, "summary": summ, "ui": u or {}, "ms": round((time.perf_counter() - p["t0"]) * 1000, 1), "side_effect": "none",
                               "result": _result_payload(txt)})      # 控制台结果卡片取数（分级 / 成本 / 退水时刻 / 点位表）


def _error_text(data) -> str | None:
    """从失败事件的 data 里找一段人能读的错误文字（message / error / reason.message …），找不到给 None。"""
    if not isinstance(data, dict):
        return str(data)[:300] if data else None
    for k in ("message", "error", "detail", "text"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:300]
        if isinstance(v, dict):
            t = _error_text(v)
            if t:
                return t
    r = data.get("reason")
    if isinstance(r, dict):
        t = _error_text({k: v for k, v in r.items() if k != "kind"})
        if t:
            return t
        if r.get("kind"):
            return str(r.get("kind"))[:100]
    return None


def _result_payload(txt: str, cap: int = 200_000):
    """工具结果 JSON 里的 result 字段（本体动作的结构化结果）；不是 JSON、没有 result 或太大就 None。"""
    if not txt or len(txt) > cap:
        return None
    try:
        d = json.loads(txt)
    except Exception:
        return None
    return d.get("result") if isinstance(d, dict) else None


def _result_text(data: dict) -> str:
    msg = data.get("message") if isinstance(data, dict) else None
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):          # tool/result 的正文
        # 两种块形状都要认：
        #   0.1.5 — 正文裹一层 {type: tool-result, content: [{type: text, text}]}
        #   0.2.x — 正文直接就是 text 块（ContentBlockMap 里已没有 tool-result 这个类型）
        out = []
        for blk in msg["content"]:
            if not isinstance(blk, dict):
                continue
            inner = blk.get("content")
            if isinstance(inner, list):
                out += [str(x.get("text", "")) for x in inner if isinstance(x, dict) and x.get("type") == "text"]
            elif isinstance(blk.get("text"), str):
                out.append(blk["text"])
        if out:
            return "\n".join(out)
    r = data.get("result") if isinstance(data, dict) else None
    if isinstance(r, dict):
        c = r.get("content")
        if isinstance(c, list):
            return "\n".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
        if isinstance(r.get("text"), str):
            return r["text"]
        return json.dumps(r, ensure_ascii=False)[:4000]
    if isinstance(r, str):
        return r
    for k in ("text", "output", "content"):
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, str):
            return v
    return json.dumps(data, ensure_ascii=False)[:4000]


def _summary_from(txt: str) -> tuple[str, bool, dict]:
    try:
        d = json.loads(txt)
    except Exception:
        return (txt[:300], True, {})
    if isinstance(d, dict):
        ok = bool(d.get("ok", not d.get("error")))
        summ = d.get("summary_zh") or d.get("summary") or d.get("error") or json.dumps(d, ensure_ascii=False)[:300]
        return (str(summ)[:400], ok, d.get("ui") if isinstance(d.get("ui"), dict) else {})
    return (txt[:300], True, {})


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="DeepSeek Harness 接入：渲染 / 启动 Web / 无头提问")
    ap.add_argument("--render", action="store_true", help="渲染预设、补丁、设置到 $DSH_HOME")
    ap.add_argument("--home", action="store_true", help="打印 SDK 用的 Harness 主目录")
    ap.add_argument("--home-web", action="store_true", help="打印 Web UI 用的 Harness 主目录")
    ap.add_argument("--web", action="store_true", help="启动 dsh Web UI（默认预设 = 排水智能体）")
    ap.add_argument("--open", action="store_true", help="启动 Web 时打开浏览器")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--ask", default=None, help="经 SDK 问一句（sdk profile，无头）")
    ap.add_argument("--keys", action="store_true", help="只报各模型密钥在哪一层（进程环境 / 用户级注册表 / 机器级），不打印值")
    ap.add_argument("--session", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from . import db
    cfg = db.load_config(a.config)
    st = settings(cfg)
    if a.keys:
        for k, v in st["key_source"].items():
            print(f"{k:10s} {({'process': '进程环境', 'user': '用户级环境变量（注册表 HKCU\\Environment，setx 写的）', 'machine': '机器级环境变量'}.get(v) or '没有')}")
        print(f"选用：{st['provider']}/{st['model']}"); return 0
    if a.home:
        print(st["home"]); return 0
    if a.home_web:
        print(st["home_web"]); return 0
    if a.render or not (a.web or a.ask):
        p = render(cfg, home=st["home"], link_modules=False); pw = render(cfg, home=st["home_web"], link_modules=True)
        print(f"渲染到 SDK 主目录 {p['home']} 与 Web 主目录 {pw['home']}：预设 {p['preset_dir'].name}、补丁 web/sdk、settings.yaml；模型 {st['provider']}/{st['model']}；密钥 {st['key_present']}")
        return 0
    if a.web:
        args, env = web_command(cfg, a.port, a.open)
        print("启动：", " ".join(args), flush=True)
        return subprocess.call(args, env=env)
    if a.ask:
        b = DshBridge(cfg)
        try:
            r = b.chat(a.ask, session_id=a.session)
        finally:
            b.close()
        print(f"[{r['planner']} {r['model']} {r['ms']} ms finish={r['finish_reason']}]\n{r['reply']}")
        for s in r["steps"]:
            print(f"  · {s['action']} {json.dumps(s.get('params'), ensure_ascii=False)} → {'ok' if s['ok'] else 'ERR'} {s['ms']} ms | {s['summary'][:120]}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
