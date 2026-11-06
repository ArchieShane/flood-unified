# -*- coding: utf-8 -*-
"""一二维计算任务队列（多智能体 · 一二维计算智能体提交，管理端批准后才执行）。

为什么要排队 + 人工批准（用户 2026-09-30 选定）：一次计算要跑几小时、占满 CPU，而且只能跑用户批准过的计算。
所以智能体只能 submit（状态 pending），批准（approve）只能从管理端做（管理员口令），批准后才起进程。

一条任务 = tools/c1_pipeline.py 的若干步骤（如 build:M61 compute:M61:L1,L2,L2P）。执行：
  approve → 起一个**独立的**执行进程（python -m swbrain.agents.computejobs _exec <id>），它再跑 c1_pipeline，
  跑完把退出码写回任务文件。独立进程 = 控制台服务重启不会打断计算。
  pause / resume = docker pause / unpause 这条任务起的容器（c1pipe_<tag>_<态>）；cancel = 停容器 + 结束执行进程。
任务文件：out/agents/jobs/<id>.json；日志：out/agents/jobs/<id>.log。

另外把机器上正在跑的、不是经队列起的一二维容器（命令行后台链起的 c1m_* / c1_D24_* 等）也列出来（只读），管理端能看到全部计算。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import hashlib
import subprocess
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = ROOT / "out" / "agents" / "jobs"
PIPE = ROOT / "tools" / "c1_pipeline.py"
TAGS = ("R61", "R96", "M61", "D24", "U61")
STATES = ("L1", "L2", "L2P", "L2S3")
# 计算参数（2026-10，用户要求：创建任务时能定计算域、网格、雨等）。缺省值 = 现在 C1 的做法，不填就和以前一样。
# 计算域：任意范围面（行政区 / 汇水区 / 自己画 / 上传；C1 是工程项目编码，不是汇水区），见 swbrain/domains.py
PARAM_DEFAULTS = {"res": 15.0, "tif_res": None, "sww_res": None, "rain": 61.0, "dur": 2.0, "force": False}
LIMITS = {"res": (3.0, 100.0), "tif_res": (1.0, 200.0), "sww_res": (3.0, 200.0), "rain": (1.0, 600.0), "dur": (0.25, 72.0), "buffer_m": (0.0, 5000.0)}
RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,39}")
WALL_REF = {"km2": 38.1, "res": 15.0, "sim_h": 6.0, "h": 4.5}    # 实测：M61 38.1 km² · 15 m · 6 h 模拟 · 单态 ≈ 3–4.5 h
KINDS = {  # kind → (说明, 步骤生成, 单态墙钟小时的粗估)
    "compute": "一二维计算（建模 → 算 → 可选接着设计 v3 再算）",
    "post": "后处理与交付目录",
    "prep": "准备中间数据（客户数据 → 中间数据）",
    # 快速验证（2026-10-02）：合成算例（800 × 600 m、两态、14 项判定，约 2 分钟），不要客户数据、不用情景。
    # 用来整条走一遍「智能体提交 → 管理员批准 → 独立进程执行 → 结果回到智能体」，换机器时也先用它验环境。
    "selftest": "快速验证（合成算例跑通一二维全链路，14 项判定，约 2 分钟）",
}
SELFTEST = ["selftest"]                    # 快速验证的步骤：不是 c1_pipeline 的 run 步骤，_exec 单独处理
VERIFY = ROOT / "out" / "demo" / "verify.json"
FINAL = {"done", "failed", "cancelled", "rejected"}


def work_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _path(jid: str) -> pathlib.Path:
    if not re.fullmatch(r"[a-z0-9]{8,32}", jid or ""):
        raise ValueError(f"任务编号不对：{jid}")
    return DIR / f"{jid}.json"


def load(jid: str) -> dict | None:
    p = _path(jid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(j: dict) -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(j["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_path(j["id"]))
    return j


def steps_for(kind: str, tag: str, states: list[str] | None = None, then_v3: bool = False, check_tag: bool = True) -> list[str]:
    """把申请翻译成 c1_pipeline 的步骤。L2S3 必须等 L2P 算完才能设计，所以单独成一步。tag = 运行名（预置情景名或自定义运行名）。"""
    if kind == "selftest":
        return list(SELFTEST)                                  # 不看情景：合成算例自带地形、管网、雨
    if check_tag and tag not in TAGS:
        raise ValueError(f"情景只能是 {TAGS}")
    if kind == "prep":
        return ["prep"]
    if kind == "post":
        return [f"post:{tag}"]
    if kind != "compute":
        raise ValueError(f"任务类型只能是 {list(KINDS)}")
    st = [s for s in (states or ["L1", "L2", "L2P"]) if s in STATES]
    if not st:
        raise ValueError(f"态只能是 {STATES}")
    base = [s for s in st if s != "L2S3"]
    out = []
    if base:
        out += [f"build:{tag}" if set(base) - {"L1"} else f"build:{tag}:L1", f"compute:{tag}:{','.join(base)}"]
    if "L2S3" in st or then_v3:
        out += [f"design:{tag}", f"compute:{tag}:L2S3"]
    return out


PRESET_KM2 = {"R61": 42.8, "R96": 42.8, "D24": 42.8, "M61": 38.1, "U61": 20.3}
PRESET_RAIN = {"R61": (61.0, 2.0), "R96": (96.0, 2.0), "D24": (61.34, 24.0), "M61": (61.0, 2.0), "U61": (61.0, 2.0)}


def sim_h(dur: float) -> float:
    return float(dur) + 4.0                                      # 模拟到降雨历时 + 4 h（参数对齐页 E17）


def estimate_h(kind: str, tag: str, steps: list[str], km2: float | None = None, res: float = 15.0, dur: float | None = None) -> float:
    """墙钟粗估（20 线程机器）。按 M61 实测（38.1 km²、15 m、模拟 6 h、单态 3–4.5 h）外推：
    ∝ 面积 ×（15 / 网格）³（单元数 × CFL 时间步）× 模拟时长。D24 用这个式子得 23.6 h，实测约 21 h。给批准的人看量级，不是承诺。"""
    if kind == "selftest":
        return 0.05                                            # 实测 1–2 分钟
    if kind in ("prep", "post"):
        return {"prep": 1.0, "post": 0.5}[kind]
    km2 = km2 or PRESET_KM2.get(tag, WALL_REF["km2"])
    dur = dur if dur is not None else PRESET_RAIN.get(tag, (61.0, 2.0))[1]
    per = WALL_REF["h"] * (km2 / WALL_REF["km2"]) * (WALL_REF["res"] / float(res)) ** 3 * (sim_h(dur) / WALL_REF["sim_h"])
    n_rounds = sum(1 for s in steps if s.startswith("compute:"))
    return round(n_rounds * per, 1)


def n_tri(km2: float, res: float) -> int:
    """三角单元数粗估（同 build_c1：面积 / (0.65 × 网格² / 3)；M61 15 m 估 78 万，实际 78.3 万）。"""
    return int(km2 * 1e6 / (0.65 * res * res / 3.0))


def _num(P: dict, k: str):
    v = P.get(k)
    if v is None or v == "":
        return PARAM_DEFAULTS.get(k)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{k} 要是数字：{P.get(k)!r}")
    lo, hi = LIMITS[k]
    if not lo <= v <= hi:
        raise ValueError(f"{k} = {v:g} 超出范围 {lo:g}–{hi:g}")
    return v


def plan(P: dict, cfg: dict | None = None, write: bool = False) -> dict:
    """计算申请参数 → 完整的执行计划（预检与提交共用）。不起计算。

    P: kind, tag（预置情景，可空）, domain（计算域规格，见 swbrain.domains.resolve；给了就是自定义范围）,
       states, then_v3, res, tif_res, sww_res, rain, dur, force, run（运行名；空 = 自动生成）, reason
    规则：
      · 不改任何参数的预置情景 → 运行名 = 情景名（与以前一样，产物接着用）；
      · 改了网格 / 雨 / 计算域 → 新的运行名（自动生成带参数指纹，或手填），产物不覆盖已有情景；
      · 自定义范围只能算 L1：L2 / L2+planned / v3 的设施是 C1 工程方案（Parsons OP-02 移动泵、27 座泵站、12 座塘），只在 C1 的计算域里有；
      · 后处理：预置情景出全套 C1 统计与交付目录；其余运行出通用产物（逐帧图、GeoTIFF、.sww）。"""
    from .. import domains as DM
    kind = P.get("kind") or "compute"
    if kind == "selftest":
        steps = steps_for("selftest", "")
        return {"kind": kind, "run": "DEMO", "tag": "DEMO", "steps": steps, "est_h": estimate_h(kind, "", steps), "params": {}, "warnings": [], "notes": []}
    tag = (P.get("tag") or "").strip() or None
    if tag and tag not in TAGS:
        raise ValueError(f"情景只能是 {TAGS}（或不选情景、给计算域）")
    spec = P.get("domain") or None
    if not tag and not spec:
        raise ValueError("要么选一个预置情景，要么给计算域（行政区 / 汇水区 / 自己画 / 上传）")
    res, tif, sww = _num(P, "res"), _num(P, "tif_res"), _num(P, "sww_res")
    base_rain, base_dur = PRESET_RAIN.get(tag, (PARAM_DEFAULTS["rain"], PARAM_DEFAULTS["dur"]))
    rain = _num(P, "rain") if P.get("rain") not in (None, "") else base_rain
    dur = _num(P, "dur") if P.get("dur") not in (None, "") else base_dur
    force = bool(P.get("force"))
    warn, notes, dom = [], [], None
    if spec:
        if isinstance(spec, dict) and spec.get("buffer_m") not in (None, ""):
            _num(spec, "buffer_m")
        D, what, dnotes = DM.resolve(spec, cfg)
        km2 = D.area / 1e6
        dom = {"spec": {k: v for k, v in spec.items() if k != "geojson"}, "what": what, "km2": round(km2, 2), "notes": dnotes,
               "geojson": DM.as_geojson(D), "_D": D}
    else:
        km2 = PRESET_KM2[tag]
    custom = bool(spec) or res != PARAM_DEFAULTS["res"] or rain != base_rain or dur != base_dur
    states = [s for s in (P.get("states") or ["L1"] if spec else P.get("states") or ["L1", "L2", "L2P"]) if s]
    if isinstance(states, str):
        states = [s.strip().upper() for s in re.split(r"[,，/\s]+", states) if s.strip()]
    then_v3 = bool(P.get("then_v3"))
    if spec and (set(states) - {"L1"} or then_v3):
        raise ValueError("自定义范围只能算 L1（现状管网 + 现状泵站）。L2 / L2+planned / v3 是 C1 工程方案的设施，只在 C1 的计算域里有")
    run = (P.get("run") or "").strip()
    auto = auto_run_name(tag, dom, res, rain, dur, tif, sww) if custom else tag
    if not run:
        run = auto
    elif not RUN_RE.fullmatch(run):
        raise ValueError("运行名：字母开头，2–40 个字母 / 数字 / _ / -")
    if run in TAGS and run != tag:
        raise ValueError(f"运行名 {run} 是预置情景名，换一个")
    if custom and run in TAGS:
        raise ValueError(f"改了参数就不能用预置情景名 {run}（会覆盖它的结果），换一个运行名或留空自动生成")
    steps = steps_for(kind, run if kind != "prep" else (tag or "M61"), states, then_v3, check_tag=False)
    if kind == "post" and not custom and tag:
        steps = [f"post:{tag}"]
    params = {"res": res, "tif_res": tif, "sww_res": sww, "rain": rain, "dur": dur, "sim_h": sim_h(dur), "force": force}
    nt = n_tri(km2, res)
    if nt > 4_000_000:
        warn.append(f"三角单元约 {nt / 1e6:.1f} 百万：内存可能不够（M61 15 m 是 78 万），先用粗一点的网格或小一点的范围")
    if tif and tif < res:
        notes.append(f"GeoTIFF {tif:g} m 比计算网格 {res:g} m 细：插值出图，不多出信息")
    if sww and sww < res:
        notes.append(f".sww {sww:g} m 比计算网格 {res:g} m 细：插值写出，不多出信息")
    if spec:
        notes.append("现状泵站只取到 C1 计算域里的那些（out/c1_existing_pumps.json），范围外的泵站暂时不进模型")
    clash = [j for j in list_jobs() if (j.get("run") or j.get("tag")) == run and j.get("kind") == kind and j["status"] not in ("rejected", "cancelled")]
    fp = fingerprint(kind, tag, dom, params, states, then_v3)
    same = [j for j in clash if j.get("fingerprint") == fp]
    other = [j for j in clash if j.get("fingerprint") != fp and j.get("fingerprint")]
    if other and not force:
        raise ValueError(f"运行名 {run} 已被任务 {other[0]['id']}（参数不同）用过，再算会覆盖它的结果：换个运行名、留空自动生成，或勾「强制重做」")
    out = {"kind": kind, "run": run, "auto_run": auto, "tag": tag, "base": tag if custom else None, "custom": custom, "states": states, "then_v3": then_v3,
           "steps": steps, "params": params, "fingerprint": fp, "km2": round(km2, 2), "n_tri": nt,
           "est_h": estimate_h(kind, tag or "", steps, km2, res, dur), "warnings": warn, "notes": notes,
           "duplicate_of": next((j["id"] for j in same if j["status"] in ("pending", "approved", "running", "paused")), None),
           "post": "full" if (tag and not custom) else "generic"}
    if dom:
        out["domain"] = {k: v for k, v in dom.items() if k != "_D"}
        if write:
            f = DM.write(dom["_D"], run, dom["what"])
            out["domain"]["file"] = f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) else f.as_posix()   # 仓库外（测试 / 改过目录）记绝对路径
    return out


def auto_run_name(tag, dom, res, rain, dur, tif, sww) -> str:
    """缺省运行名：范围 + 雨 + 网格 + 4 位指纹，如 Mussafah_61mm2h_r15_3fa2 / M61_r10_9c0e。"""
    if dom:
        sp = dom["spec"]
        head = {"admin": "ADM", "catch": "CAT", "draw": "DRAW", "upload": "UP"}[sp["source"]]
        first = dom["what"].split(" ", 1)[-1].split("，")[0].split("、")[0].split("（")[0]
        nm = re.sub(r"[^A-Za-z0-9]+", "", first)[:14] if sp["source"] in ("admin", "catch") else ""
        if nm and len(sp.get("ids") or []) > 1:
            nm = nm[:11] + f"x{len(sp['ids'])}"
        head = nm or head
        if head[0].isdigit():
            head = "D" + head
    else:
        head = tag
    h = hashlib.sha1(json.dumps([dom["geojson"] if dom else tag, res, rain, dur, tif, sww], sort_keys=True, default=str).encode()).hexdigest()[:4]
    return f"{head}_{rain:g}mm{dur:g}h_r{res:g}_{h}".replace(".", "p")[:40]


def fingerprint(kind, tag, dom, params, states, then_v3) -> str:
    key = [kind, tag, dom["geojson"] if dom else None, {k: v for k, v in params.items() if k != "force"}, sorted(states or []), then_v3]
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]


def submit(kind: str, tag: str = "", states: list[str] | None = None, then_v3: bool = False, reason: str = "", requested_by: dict | None = None,
           params: dict | None = None, cfg: dict | None = None) -> dict:
    """提交一条申请（状态 pending，要管理端批准）。params 见 plan()；不给 = 和以前一样只按情景 + 态。"""
    P = {"kind": kind, "tag": tag, "states": states, "then_v3": then_v3, **(params or {})}
    if kind == "selftest":
        P = {"kind": "selftest"}
    pl = plan(P, cfg, write=False)
    if pl.get("duplicate_of"):
        return {**load(pl["duplicate_of"]), "duplicate": True}
    if pl.get("domain"):
        pl = plan(P, cfg, write=True)                            # 确定要提交了才把计算域落盘
    run = pl["run"]
    j = {"id": uuid.uuid4().hex[:12], "kind": kind, "tag": run, "run": run, "base": pl.get("base"), "preset": pl.get("tag"),
         "states": pl.get("states") or [], "then_v3": pl.get("then_v3", False), "steps": pl["steps"], "params": pl["params"],
         "fingerprint": pl.get("fingerprint"), "post": pl.get("post"), "km2": pl.get("km2"), "n_tri": pl.get("n_tri"),
         "domain": {k: v for k, v in (pl.get("domain") or {}).items() if k != "geojson"} or None,
         "warnings": pl.get("warnings", []), "notes": pl.get("notes", []),
         "title": KINDS[kind] if kind == "selftest" else f"{KINDS[kind]} · {run}" + (f" · {'/'.join(pl['states'])}" if pl.get("states") and kind == "compute" else ""),
         "reason": reason, "est_h": pl["est_h"], "requested_by": requested_by or {}, "created": _now(), "status": "pending",
         "history": [{"ts": _now(), "status": "pending", "by": (requested_by or {}).get("agent") or (requested_by or {}).get("who") or "?"}]}
    return save(j)


def pipe_args(j: dict) -> list[str]:
    """任务 → c1_pipeline 的选项（运行名、底稿情景、计算域文件、雨、网格、强制重做）。老任务（没有 params）返回 []。"""
    P = j.get("params") or {}
    if not P:
        return []
    a = ["--tag", j.get("run") or j["tag"]]
    if j.get("base"):
        a += ["--base", j["base"]]
    if (j.get("domain") or {}).get("file"):
        a += ["--domain", str(ROOT / j["domain"]["file"])]                  # 绝对路径时 ROOT / 绝对 = 绝对
    if j.get("base") or j.get("domain"):
        a += ["--rain", f"{P['rain']:g}", "--dur", f"{P['dur']:g}"]
    a += ["--res", f"{P['res']:g}"]
    if P.get("tif_res"):
        a += ["--tif-res", f"{P['tif_res']:g}"]
    if P.get("sww_res"):
        a += ["--sww-res", f"{P['sww_res']:g}"]
    if P.get("force"):
        a += ["--force"]
    return a


def list_jobs(limit: int = 200) -> list[dict]:
    if not DIR.exists():
        return []
    js = []
    for p in DIR.glob("*.json"):
        try:
            js.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    js.sort(key=lambda j: j.get("created", ""), reverse=True)
    return [refresh(j) for j in js[:limit]]


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"], capture_output=True, text=True)
        return str(int(pid)) in r.stdout
    try:
        os.kill(int(pid), 0); return True
    except OSError:
        return False


def _containers(prefix: str) -> list[dict]:
    try:
        r = subprocess.run(["docker", "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    return [{"name": a, "status": b} for a, b in (ln.split("|", 1) for ln in r.stdout.splitlines() if "|" in ln)]


def refresh(j: dict) -> dict:
    """执行进程已退出但没写回结果（机器重启、被杀）→ 标 failed；运行中的补上各态进度。"""
    if j.get("status") in ("running", "paused"):
        if not _pid_alive(j.get("pid")) and j.get("status") == "running":
            j2 = load(j["id"]) or j                          # 执行进程可能刚写完结果
            if j2.get("status") == "running":
                j2.update(status="failed", finished=_now(), note="执行进程不在了，也没写回结果（被结束或机器重启）")
                j2.setdefault("history", []).append({"ts": _now(), "status": "failed", "by": "system"})
                save(j2)
            j = j2
        j["progress"] = progress_of(j)
    return j


def progress_of(j: dict) -> list[dict]:
    out = []
    logs = work_dir() / "c2d" / "logs"
    for st in STATES:
        lg = logs / f"C1_{j['tag']}_{st}.log"
        if any(s.startswith("compute:") and st in s.split(":")[-1].split(",") for s in j.get("steps", [])) and lg.exists():
            end = (j.get("params") or {}).get("sim_h") or (28.0 if j["tag"] == "D24" else 6.0)     # 分母 = 这条任务自己的模拟时长
            out.append({"state": st, **parse_log(lg, float(end))})
    return out


_T = re.compile(r"t=\s*([\d.]+)h.*?淹没\s+([\d.,]+)\s*ha.*?最深\s+([\d.]+)\s*m")


def parse_log(lg: pathlib.Path, end_h: float) -> dict:
    try:
        with lg.open("rb") as f:
            f.seek(max(0, lg.stat().st_size - 6000)); tail = f.read().decode("utf-8", "replace").replace("\r", "\n")
    except OSError:
        return {}
    m = None
    for m in _T.finditer(tail):
        pass
    d = {"log": str(lg), "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(lg.stat().st_mtime))}
    if m:
        t = float(m.group(1)); d.update(t_h=t, end_h=end_h, pct=round(100 * t / end_h, 1), wet_ha=float(m.group(2).replace(",", "")), hmax_m=float(m.group(3)))
        w = re.search(r"\[(\d+)s", tail[m.end():m.end() + 400])
        if w and t > 0.5:                                         # 剩余墙钟粗估：按到现在的平均速度外推（雨峰后更慢，偏乐观）
            d["wall_h"] = round(int(w.group(1)) / 3600, 2); d["eta_h"] = round(int(w.group(1)) / 3600 * (end_h - t) / t, 1)
    if "水量对账" in tail:
        d["finished"] = True
    if "Traceback" in tail:
        d["error"] = tail[tail.rfind("Traceback"):][:600]
    return d


def external_runs() -> list[dict]:
    """机器上所有一二维容器（含命令行后台链起的）：名字、状态、情景 / 态、进度。只读。"""
    try:
        r = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.Names}}|{{.Status}}|{{.Command}}"], capture_output=True, text=True, timeout=20)
    except Exception as ex:
        return [{"error": f"docker 不可用：{str(ex)[:100]}"}]
    out = []
    for ln in r.stdout.splitlines():
        parts = ln.split("|", 2)
        if len(parts) < 3 or "couple2way" not in parts[2]:
            continue
        m = re.search(r"couple2way_v2\.py\s+(\S+)\s+(\S+)\s+\S+\s+([\d.]+)", parts[2])
        name, tagname, st, end = parts[0], (m.group(1) if m else "?"), (m.group(2) if m else "?"), (float(m.group(3)) if m else 6.0)
        lg = work_dir() / "c2d" / "logs" / f"{tagname}_{st}.log"
        out.append({"container": name, "status": parts[1], "paused": "Paused" in parts[1], "name": tagname, "state": st, "via_queue": name.startswith("c1pipe_"), **(parse_log(lg, end) if lg.exists() else {})})
    return out


# ---------------------------------------------------------------- 管理端动作（只从管理端调用，调用方已校验管理员口令）
def _transition(jid: str, allowed: tuple, new: str, by: str, note: str = "") -> dict:
    j = load(jid)
    if j is None:
        raise ValueError(f"没有任务 {jid}")
    if j["status"] not in allowed:
        raise ValueError(f"任务现在是「{j['status']}」，不能改成「{new}」")
    j["status"] = new; j.setdefault("history", []).append({"ts": _now(), "status": new, "by": by, **({"note": note} if note else {})})
    return save(j)


def approve(jid: str, by: str = "admin", note: str = "") -> dict:
    """by 是批准人的身份（统一身份模式下 = sdip:<主体号>）。有管理权限就能批，含自己提交的（2026-10-02 用户定：只看角色）；
    谁提交、谁批准都记在 requested_by 与 history 里，事后可查。"""
    running = [x for x in list_jobs() if x["status"] in ("running", "paused") and x["id"] != jid]
    j = _transition(jid, ("pending",), "approved", by, note)
    if running:                                                   # 同时只跑一条：CPU 就这么多；排在后面，前一条结束时自动接上
        j["note"] = f"已批准，排在 {running[0]['id']} 之后"; return save(j)
    busy = [] if j.get("kind") == "selftest" else busy_external()
    if busy:                                                      # 队列外有计算在跑：照样起执行进程，它先等，机器空了再开算（_wait_external）
        j["note"] = "等队列外的计算让出机器：" + "、".join(f"{r['container']}（{r['name']} {r['state']}）" for r in busy)
    return start(j)


def reject(jid: str, by: str = "admin", note: str = "") -> dict:
    return _transition(jid, ("pending",), "rejected", by, note)


def start(j: dict) -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    log = DIR / f"{j['id']}.log"
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if os.name == "nt" else 0          # DETACHED_PROCESS：服务重启不带走它
    p = subprocess.Popen([sys.executable, "-m", "swbrain.agents.computejobs", "_exec", j["id"]], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=(os.name != "nt"), env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    j.update(status="running", started=_now(), pid=p.pid, log=str(log))
    j.setdefault("history", []).append({"ts": _now(), "status": "running", "by": "system"})
    return save(j)


def _docker(verb: str, j: dict) -> list[str]:
    names = [c["name"] for c in _containers(f"c1pipe_{j['tag']}_")]
    for n in names:
        subprocess.run(["docker", verb, n], capture_output=True, timeout=60)
    return names


def pause(jid: str, by: str = "admin") -> dict:
    j = _transition(jid, ("running",), "paused", by)
    j["paused_containers"] = _docker("pause", j); return save(j)


def resume(jid: str, by: str = "admin") -> dict:
    j = _transition(jid, ("paused",), "running", by)
    _docker("unpause", j); return save(j)


def cancel(jid: str, by: str = "admin", note: str = "") -> dict:
    j0 = load(jid)
    if j0 is None:
        raise ValueError(f"没有任务 {jid}")
    if j0["status"] == "paused":
        _docker("unpause", j0)
    j = _transition(jid, ("pending", "approved", "running", "paused"), "cancelled", by, note)
    if j0["status"] in ("running", "paused"):
        _docker("stop", j)
        if j0.get("exec_pid") or j0.get("pid"):
            pid = j0.get("exec_pid") or j0.get("pid")
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
            else:
                try:
                    os.killpg(int(pid), 15)
                except OSError:
                    pass
    j["finished"] = _now(); return save(j)


def log_tail(jid: str, n: int = 200) -> str:
    lg = DIR / f"{jid}.log"
    if not lg.exists():
        return ""
    txt = lg.read_text(encoding="utf-8", errors="replace").replace("\r", "\n")
    return "\n".join([ln for ln in txt.splitlines() if ln.strip()][-n:])


def verify_summary() -> dict | None:
    """快速验证的判定表（out/demo/verify.json）摘要：过了几项、没过的是哪几项、用时。没有文件返回 None。"""
    try:
        d = json.loads(VERIFY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = d.get("checks") or []                              # tools/demo_case.py 写的：{ok, checks: [{check, ok, value, rule}], t_run_s, when}
    bad = [c.get("check") for c in items if not c.get("ok")]
    return {"n": len(items), "passed": len(items) - len(bad), "failed": bad, "all_ok": bool(d.get("ok")) and bool(items) and not bad,
            "t_run_s": d.get("t_run_s"), "when": d.get("when"), "file": "out/demo/verify.json"}


WAIT_S = 60                                                     # 等队列外计算让出机器时的轮询间隔


def busy_external() -> list[dict]:
    """机器上不是经队列起的、正在算的一二维容器（命令行后台链起的 c1m_* / c1_D24_* 等）。暂停着的不算：它不占 CPU，
    算它会让一条暂停几天的计算挡住整条队列；docker 不可用也不算（查不到就不挡）。"""
    return [r for r in external_runs() if not r.get("error") and not r.get("via_queue") and not r.get("paused")]


def _wait_external(jid: str, f) -> bool:
    """真正开算前等队列外的计算算完（同时只跑一条，命令行起的也算一条）。返回 False = 等的时候被取消了。
    等待中任务仍是 running（执行进程在），note 写明在等谁；管理端暂停 = 让出来了也先别开算。快速验证不等：2 分钟、不吃满 CPU。"""
    said = None
    while True:
        j = load(jid) or {}
        if j.get("status") not in ("running", "paused"):
            return False
        busy = busy_external()
        if not busy and j["status"] == "running":
            if said is not None:
                j.pop("note", None); save(j); f.write(f"[{_now()}] 机器空了，开始\n"); f.flush()
            return True
        who = "、".join(f"{r['container']}（{r['name']} {r['state']}）" for r in busy) or "管理端暂停"
        if who != said:
            j["note"] = f"等队列外的计算让出机器：{who}" if busy else "已暂停（还没开算）"; save(j)
            f.write(f"[{_now()}] 等待：{j['note']}\n"); f.flush(); said = who
        time.sleep(WAIT_S)


def _exec(jid: str) -> int:
    """执行进程本体：等机器空（队列外的计算算完）→ 跑 c1_pipeline，写回结果；再看有没有已批准、排着队的，接着起。"""
    j = load(jid)
    log = DIR / f"{jid}.log"
    st = j.get("kind") == "selftest"
    cmd = [sys.executable, str(PIPE), "selftest"] if st else [sys.executable, str(PIPE), "run", *j["steps"], *pipe_args(j)]
    with log.open("a", encoding="utf-8") as f:
        if not st and not _wait_external(jid, f):
            f.write(f"[{_now()}] 等待时被取消，没有开算\n")
            return 1
        f.write(f"[{_now()}] 开始：python tools/c1_pipeline.py {' '.join(map(str, cmd[2:]))}\n"); f.flush()
        rc = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")).returncode
        f.write(f"[{_now()}] 结束，退出码 {rc}\n")
    j = load(jid)
    if j["status"] not in FINAL:
        j.update(status="done" if rc == 0 else "failed", finished=_now(), rc=rc)
        if st:
            j["verify"] = verify_summary()                     # 判定表摘要挂到任务上：智能体问进度时能直接答「14 项过了几项」
        j.setdefault("history", []).append({"ts": _now(), "status": j["status"], "by": "system"})
        save(j)
    nxt = sorted((x for x in list_jobs() if x["status"] == "approved"), key=lambda x: x.get("created", ""))
    if nxt:
        start(nxt[0])
    return rc


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "_exec":
        raise SystemExit(_exec(sys.argv[2]))
    print(json.dumps(list_jobs(), ensure_ascii=False, indent=1))
