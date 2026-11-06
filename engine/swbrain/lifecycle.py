"""积水点生命周期层：降雨事件（RainEvent）× 积水经历（FloodEpisode）。

一个积水点的一生不是一次调度：平时它是客户清单上的一个热点；一场雨（历史的 2024-04、2026-03，或预报中 / 进行中的一场）来了，
它经历 预警待命 → 已预置 → 已上报（993）→ 已核实（现场观测）→ 已派单 → 泵已到位 → 抽排中 → 已退干 → 已关闭 → 已复盘；雨过之后，
这一次经历留下来，和模型对它的推演放在一起对账（命中 / 漏报 / 误报），累计成复发率；复发多、又没有工程建议的点要进 IDD；
工程建成并验证有效之后，它才从「积水点」降级或销号。

对象：
  RainEvent    一场真实（或预报中）的雨：历史事件来自 [scenarios.events]（2024-04、2026-03），客户补的历史事件来自 exchange/events.json（swbrain.events/1），
               值班时开的预报 / 进行中事件来自 out/events.jsonl（动作 open_event）。每个事件对上一个情景（Scenario）做模型这一侧。
  FloodEpisode 积水点 × 事件 的一次经历：各阶段时间戳、现场观测到的水深 / 时长、实际派泵与退干、来源，以及模型在该事件情景下对它的推演，
               二者对照出 agreement。来源优先级：客户记录（exchange/episodes.json，swbrain.episodes/1）> 现场观测 / 993 报案 > 本系统推演（source=system_estimate，页面标「推演估计」）。
所有写入只到 out/*.jsonl；exchange/ 下的客户记录不进仓库。"""
from __future__ import annotations
import calendar
import datetime as _dt
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("alerted", "staged", "reported", "verified", "dispatched", "arrived", "pumping", "dry", "closed", "reviewed")
STAGE_ZH = {"unknown": "无记录 / 未推演", "none": "未发生", "estimated": "推演估计", "alerted": "预警待命", "staged": "已预置", "reported": "已上报", "verified": "已核实", "dispatched": "已派单", "arrived": "泵已到位",
            "pumping": "抽排中", "dry": "已退干", "closed": "已关闭", "reviewed": "已复盘"}
STAGE_EN = {"unknown": "no record / not modelled", "none": "did not flood", "estimated": "model estimate", "alerted": "on alert", "staged": "pre-positioned", "reported": "reported", "verified": "verified on site", "dispatched": "dispatched",
            "arrived": "pumps on site", "pumping": "pumping", "dry": "drained", "closed": "closed", "reviewed": "reviewed"}
AGREEMENT_ZH = {"hit": "命中", "miss": "漏报", "false_alarm": "误报", "correct_negative": "都说没积水", "unknown": "无观测"}
AGREEMENT_EN = {"hit": "hit", "miss": "miss", "false_alarm": "false alarm", "correct_negative": "correct negative", "unknown": "no observation"}
PROPOSAL_STATUSES = ("建议", "已批准", "在建", "已建成", "已验证有效", "已否决")
TREATMENT = {"none": "无建议", "proposed": "有建议", "approved": "已批准", "under_construction": "在建", "built": "已建成", "verified": "已验证有效", "rejected": "已否决"}
TREATMENT_EN = {"none": "no proposal", "proposed": "proposed", "approved": "approved", "under_construction": "under construction", "built": "built", "verified": "verified effective", "rejected": "rejected"}
# 公开报道里的地名 → 热点 area / location 里的关键词（英 / 阿；小写匹配）。只用于片区级证据，不是逐点记录
AREA_KEYWORDS = {
    "Khalifa City": ["khalifa city", "مدينة خليفة"],
    "Zayed City": ["zayed city", "مدينة زايد"],
    "Mohammed Bin Zayed City": ["mohammed bin zayed", "mohamed bin zayed", "mbz", "محمد بن زايد"],
    "Shakhbout City": ["shakhbout", "مدينة شخبوط"],
    "Al Shamkha": ["shamkha", "الشامخة"],
    "Al Bandar": ["al bandar", "البندر"],
    "Madinat Zayed": ["madinat zayed", "مدينة زايد"],
    "Mussafah": ["mussafah", "musaffah", "مصفح"],
    "Al Wathba": ["wathba", "الوثبة"],
    "Al Shahama": ["shahama", "الشهامة"],
    "Al Rahba": ["rahba", "الرحبة"],
    "Yas Island": ["yas island", "جزيرة ياس", "ياس"],
    "Bani Yas": ["bani yas", "baniyas", "بني ياس"],
    "Al Reef": ["al reef", "الريف"],
    "Al Raha": ["al raha", "الراحة"],
    "Saadiyat": ["saadiyat", "السعديات"],
    "Al Riyadh": ["al riyadh", "الرياض"],
    "Al Shawamekh": ["shawamekh", "الشوامخ"],
    "Abu Dhabi Island": ["island", "abu dhabi island", "جزيرة أبوظبي"],
    "Abu Dhabi city centre": ["island", "corniche", "الكورنيش", "وسط المدينة"],
    "Abu Dhabi city": ["island", "abu dhabi city"],
    "Al Ain": ["al ain", "العين"],
}
DEFAULTS = {"event_window_days": 3, "events_file": "out/events.jsonl", "episodes_file": "out/episodes.jsonl", "proposal_status_file": "out/proposal_status.jsonl", "min_observed_for_verification": 10}
_FP_RE = re.compile(r"\b(ADM|AAM|DRM|HS)[\s\-_]*0*(\d{1,4})\b", re.I)


def params(cfg: dict) -> dict:
    P = dict(DEFAULTS); P.update((cfg or {}).get("lifecycle") or {})
    return P


def _path(cfg: dict, name: str) -> Path:
    p = Path(str(params(cfg)[name]))
    return p if p.is_absolute() else ROOT / p


def norm_fp(s) -> str | None:
    m = _FP_RE.search(str(s or ""))
    if not m:
        return None
    a, n = m.group(1).upper(), int(m.group(2))
    return f"{'ADM' if a == 'HS' else a}-{n}"


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 事件
def _window(date_str, days: int) -> tuple[str | None, str | None]:
    """'2024-04-16' → 前后 days 天；'2026-03' → 整月；其他 → (None, None)。返回 'YYYY-MM-DD' 字串。"""
    s = str(date_str or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (d - _dt.timedelta(days=days)).isoformat(), (d + _dt.timedelta(days=days)).isoformat()
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return _dt.date(y, mo, 1).isoformat(), _dt.date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()
    return None, None


def in_window(at, start, end) -> bool:
    if not at or not start or not end:
        return False
    d = str(at)[:10]
    return start <= d <= end + "T99"


def rp_equiv_yr(cfg: dict, p24) -> float | None:
    """24 h 雨量 → 年一遇（[idf.return_periods] 的 p24 对数插值；表外线性外推到 1–500 年）。"""
    tab = ((cfg or {}).get("idf") or {}).get("return_periods") or {}
    pts = sorted([(float(v.get("p24_mm")), float(k)) for k, v in tab.items() if str(k).isdigit() and v.get("p24_mm")], key=lambda x: x[0])
    try:
        p = float(p24)
    except (TypeError, ValueError):
        return None
    if len(pts) < 2:
        return None
    xs = [math.log(a) for a, _ in pts]; ys = [math.log(b) for _, b in pts]; x = math.log(max(p, 0.1))
    if x <= xs[0]:
        i = 0
    elif x >= xs[-1]:
        i = len(xs) - 2
    else:
        i = max(j for j in range(len(xs) - 1) if xs[j] <= x)
    y = ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / max(xs[i + 1] - xs[i], 1e-9)
    return round(min(max(math.exp(y), 1.0), 500.0), 1)


def events(cfg: dict) -> dict[str, dict]:
    """历史事件（配置）+ 客户补的事件（feeds events）+ 值班开的事件（out/events.jsonl）。键 = event_id。"""
    P = params(cfg); days = int(P.get("event_window_days", 3))
    out: dict[str, dict] = {}
    for key, ev in ((cfg.get("scenarios") or {}).get("events") or {}).items():
        out[str(key)] = {"event_id": str(key), "kind": "historical", "label": ev.get("label"), "label_en": ev.get("label_en"), "date": ev.get("date"), "p24_mm": ev.get("p24_default_mm"),
                         "p24_by_area": ev.get("p24_by_area") or {}, "scenario_key": str(key), "source": ev.get("source"), "source_en": ev.get("source_en"), "base_rp": ev.get("base_rp"), "origin": "config"}
    rows = []
    try:
        from . import feeds
        rows = list(feeds.load(cfg, "events").get("rows") or [])
    except Exception:
        rows = []
    for r in rows + read_jsonl(_path(cfg, "events_file")):
        eid = str(r.get("event_id") or "").strip()
        if not eid:
            continue
        cur = out.get(eid, {"event_id": eid, "origin": ("client" if r in rows else "log")})
        for k in ("kind", "label", "label_en", "date", "start", "end", "p24_mm", "p24_by_area", "scenario_key", "source", "alert_level", "note", "opened_at", "opened_by", "closed_at", "impacted_areas"):
            if r.get(k) is not None:
                cur[k] = r[k]
        cur.setdefault("kind", "historical")
        out[eid] = cur
    for e in out.values():
        if not e.get("start") or not e.get("end"):
            s, t = _window(e.get("date") or e.get("start"), days)
            e["start"] = e.get("start") or s; e["end"] = e.get("end") or t
        e["rp_equiv_yr"] = rp_equiv_yr(cfg, e.get("p24_mm"))
    return out


def sort_key(e: dict) -> str:
    return str(e.get("start") or e.get("date") or "")


# ---------------------------------------------------------------- 经历
def episode_records(cfg: dict) -> list[dict]:
    """客户记录（feeds episodes，origin=client_record）+ 本系统动作日志（out/episodes.jsonl，origin=log）。"""
    rows = []
    try:
        from . import feeds
        for r in feeds.load(cfg, "episodes").get("rows") or []:
            rows.append({**r, "_origin": "client_record"})
    except Exception:
        pass
    for r in read_jsonl(_path(cfg, "episodes_file")):
        rows.append({**r, "_origin": "log"})
    return rows


def _mark(e: dict, field: str, at):
    """记下最后一次证据的时刻（ISO 字符串按字典序即按时间序）。"""
    if not at:
        return
    at = str(at)
    if e.get(field) is None or at > e[field]:
        e[field] = at


def _new(fp: str, eid: str) -> dict:
    return {"episode_id": f"{fp}@{eid}", "fp_id": fp, "event_id": eid, "stages": {}, "observed_flooded": None, "obs_depth_max_m": None, "obs_duration_min": None,
            "last_wet_at": None, "last_dry_at": None,          # 一场雨里可能退了又涨：状态看最后一次证据是哪种，不是看阶段序

            "units_sent": None, "first_arrival_min": None, "drain_min": None, "sources": set(), "notes": [], "model_flooded": None, "model_depth_m": None, "model_duration_min": None,
            "model_served": None, "model_done_min": None, "model_within_target": None}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _set_stage(e: dict, stage: str, at, earliest: bool = True):
    if stage not in STAGES or not at:
        return
    cur = e["stages"].get(stage)
    at = str(at)
    e["stages"][stage] = at if cur is None else (min(cur, at) if earliest else max(cur, at))


def area_matches(text: str, names: list) -> list[str]:
    """热点的 area + location 文本里对上哪些报道地名。"""
    low = str(text or "").lower(); out = []
    for n in names or []:
        kws = AREA_KEYWORDS.get(str(n)) or [str(n).lower()]
        if any(k in low for k in kws):
            out.append(str(n))
    return out


def build_episodes(cfg: dict, evs: dict[str, dict], records: list[dict], observations: list[dict], incidents: list[dict], states_for_event, fp_known=None, fp_meta: dict | None = None) -> dict[str, dict]:
    """把四类证据合到 积水点×事件 上。states_for_event(event) → {fp_id: FloodState} 或 None（该事件情景没算）。"""
    eps: dict[str, dict] = {}

    def ep(fp, eid):
        k = f"{fp}@{eid}"
        if k not in eps:
            eps[k] = _new(fp, eid)
        return eps[k]
    # 1 记录：客户历史记录与本系统动作日志
    for r in records:
        fp = norm_fp(r.get("fp_id")); eid = str(r.get("event_id") or "").strip()
        if not fp or eid not in evs:
            continue
        e = ep(fp, eid); e["sources"].add(r.get("_origin") or "record")
        if r.get("source"):
            e["sources"].add(str(r["source"]))                          # 客户记录里的细分来源（投诉 / 巡查 / 993 / 承包商 …）也留着
        if r.get("stage") in STAGES:
            _set_stage(e, r["stage"], r.get("at") or evs[eid].get("date") or evs[eid].get("start"), earliest=r["stage"] not in ("dry", "closed", "reviewed"))
        _at = r.get("at") or evs[eid].get("date") or evs[eid].get("start")
        if r.get("stage") == "dry":
            _mark(e, "last_dry_at", _at)
        elif r.get("stage") in ("reported", "verified", "dispatched", "arrived", "pumping"):
            _mark(e, "last_wet_at", _at)
        if r.get("flooded") is not None:
            fl = bool(r["flooded"])
            e["observed_flooded"] = True if (fl or e["observed_flooded"]) else False
            _mark(e, "last_wet_at" if fl else "last_dry_at", _at)
            if fl and not any(s in e["stages"] for s in ("reported", "verified")):
                _set_stage(e, "verified", _at)
        for src, dst, agg in (("depth_m", "obs_depth_max_m", max), ("duration_min", "obs_duration_min", max), ("units", "units_sent", max), ("first_arrival_min", "first_arrival_min", min), ("drain_min", "drain_min", min)):
            v = _num(r.get(src))
            if v is not None:
                e[dst] = v if e[dst] is None else agg(e[dst], v)
        if r.get("note"):
            e["notes"].append(str(r["note"]))
    # 2 现场观测：落在事件窗口内的
    for o in observations or []:
        fp = norm_fp(o.get("fp_id")); at = o.get("observed_at")
        if not fp:
            continue
        for eid, ev in evs.items():
            if not in_window(at, ev.get("start"), ev.get("end")):
                continue
            e = ep(fp, eid); e["sources"].add("observation")
            if o.get("status") in ("flooding", "receding"):
                e["observed_flooded"] = True; _set_stage(e, "verified", at); _mark(e, "last_wet_at", at)
                d = _num(o.get("depth_m"))
                if d is not None:
                    e["obs_depth_max_m"] = d if e["obs_depth_max_m"] is None else max(e["obs_depth_max_m"], d)
            elif o.get("status") == "dry":
                _set_stage(e, "dry", at, earliest=False); _mark(e, "last_dry_at", at)
                if e["observed_flooded"] is None:
                    e["observed_flooded"] = False
    # 3 993 报案：有积水点编号、时间落在窗口内的
    for r in incidents or []:
        fp = norm_fp(r.get("fp_id")); at = r.get("at") or r.get("reported_at")
        if not fp:
            continue
        for eid, ev in evs.items():
            if not in_window(at, ev.get("start"), ev.get("end")):
                continue
            e = ep(fp, eid); e["sources"].add("993"); _set_stage(e, "reported", at); _mark(e, "last_wet_at", at)
            if r.get("verified") is True:
                e["observed_flooded"] = True; _set_stage(e, "verified", at)
    # 4 模型：该事件情景下的推演
    for eid, ev in evs.items():
        st_map = None
        try:
            st_map = states_for_event(ev) if states_for_event else None
        except Exception:
            st_map = None
        if not st_map:
            continue
        for fp, st in st_map.items():
            fp = norm_fp(fp) or str(fp)
            e = ep(fp, eid); e["sources"].add("system_estimate")
            vol = _num(st.get("volume_m3")) or 0.0
            e["model_flooded"] = vol > 0; e["model_depth_m"] = _num(st.get("max_depth_m")); e["model_duration_min"] = _num(st.get("duration_min"))
            e["model_served"] = bool(st.get("served")) if st.get("served") is not None else None; e["model_done_min"] = _num(st.get("done_min")); e["model_within_target"] = st.get("within_target")
        # 情景算过、点又对得上管网却没有状态 = 模型说它不积水
        for k, e in list(eps.items()):
            if e["event_id"] == eid and e["model_flooded"] is None and (fp_known is None or e["fp_id"] in fp_known):
                e["model_flooded"] = False
    # 4.5 片区级证据：公开报道点名的地名对上热点的 area / location → area_reported（不算记录、不进对账）
    for eid, ev in evs.items():
        names = ev.get("impacted_areas") or []
        if not names or not fp_meta:
            continue
        for fp, txt in fp_meta.items():
            hits = area_matches(txt, names)
            if not hits:
                continue
            k = f"{fp}@{eid}"
            e = eps.get(k)
            if e is None:
                if fp_known is not None and fp not in fp_known:
                    e = ep(fp, eid)                                     # 模型没算的点也留一条：只有片区报道
                else:
                    e = ep(fp, eid)
            e["sources"].add("area_report"); e["area_reported"] = True; e["area_report_names"] = hits
    # 5 收尾
    for e in eps.values():
        e.setdefault("area_reported", False); e.setdefault("area_report_names", [])
        e["sources"] = sorted(e["sources"]); e["notes"] = e["notes"][-5:]
        e["reflooded"] = bool(e.get("last_wet_at") and e.get("last_dry_at") and str(e["last_wet_at"]) > str(e["last_dry_at"]))
        e["status"] = derive_status(e, evs.get(e["event_id"]) or {}); e["agreement"] = derive_agreement(e)
        e["status_zh"] = STAGE_ZH.get(e["status"], e["status"]); e["status_en"] = STAGE_EN.get(e["status"], e["status"])
        e["agreement_zh"] = AGREEMENT_ZH.get(e["agreement"]); e["agreement_en"] = AGREEMENT_EN.get(e["agreement"])
        e["source"] = ("client_record" if "client_record" in e["sources"] else "observation" if "observation" in e["sources"] else "993" if "993" in e["sources"] else "log" if "log" in e["sources"] else "area_report" if ("area_report" in e["sources"] and "system_estimate" not in e["sources"]) else "system_estimate")
        e["_type"] = "FloodEpisode"; e["_pk"] = e["episode_id"]; e["_title"] = e["fp_id"]
    return eps


def derive_status(e: dict, ev: dict | None = None) -> str:
    stages = e.get("stages") or {}
    if stages:
        known = [s for s in stages if s in STAGES]
        # 退干之后又有「有水」的证据：这一场雨里退了又涨，不能再报已退干 / 已关闭 / 已复盘。
        # 2026-09-23 之前只按阶段序取最大值、完全不看时间戳，现场 0.6 m 积水的点也能被判「已退干」并关闭。
        wet, dry = e.get("last_wet_at"), e.get("last_dry_at")
        if known and wet and dry and str(wet) > str(dry):
            back = [s for s in known if STAGES.index(s) < STAGES.index("dry")]
            return max(back, key=STAGES.index) if back else "verified"
        return max(known, key=STAGES.index) if known else "unknown"
    if e.get("observed_flooded") is False:
        return "none"
    if e.get("observed_flooded") is True:
        return "verified"
    if e.get("model_flooded"):
        return "alerted" if (ev or {}).get("kind") in ("forecast", "live") else "estimated"
    if e.get("model_flooded") is False:
        return "none"
    return "unknown" if e.get("area_reported") else "estimated"


def derive_agreement(e: dict) -> str:
    obs, mod = e.get("observed_flooded"), e.get("model_flooded")
    if obs is None or mod is None:
        return "unknown"
    if obs and mod:
        return "hit"
    if obs and not mod:
        return "miss"
    if (not obs) and mod:
        return "false_alarm"
    return "correct_negative"


# ---------------------------------------------------------------- 对账、点汇总、工程状态
def verification(eps: list[dict], cfg: dict) -> dict:
    V = (cfg or {}).get("verification") or {}; P = params(cfg)
    obs = [e for e in eps if e.get("observed_flooded") is not None and e.get("model_flooded") is not None]
    hits = sum(1 for e in obs if e["agreement"] == "hit"); miss = sum(1 for e in obs if e["agreement"] == "miss"); fa = sum(1 for e in obs if e["agreement"] == "false_alarm")
    cn = sum(1 for e in obs if e["agreement"] == "correct_negative")
    pod = round(hits / (hits + miss), 3) if hits + miss else None
    far = round(fa / (hits + fa), 3) if hits + fa else None
    pairs = [(e["obs_depth_max_m"], e["model_depth_m"]) for e in obs if e.get("obs_depth_max_m") is not None and e.get("model_depth_m") is not None]
    rmse = round(math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)), 3) if pairs else None
    n_min = int(P.get("min_observed_for_verification", 10)); pod_min = float(V.get("pass_pod_min", 0.8)); far_max = float(V.get("pass_far_max", 0.3)); rmse_max = float(V.get("pass_depth_rmse_m", 0.1))
    if len(obs) < n_min:
        verdict = "insufficient"
    else:
        ok = (pod is None or pod >= pod_min) and (far is None or far <= far_max) and (rmse is None or rmse <= rmse_max)
        verdict = "pass" if ok else "fail"
    return {"n_episodes": len(eps), "n_observed": len(obs), "n_observed_flooded": sum(1 for e in eps if e.get("observed_flooded") is True), "n_model_flooded": sum(1 for e in eps if e.get("model_flooded") is True),
            "hits": hits, "misses": miss, "false_alarms": fa, "correct_negatives": cn, "pod": pod, "far": far, "depth_rmse_m": rmse, "verdict": verdict,
            "thresholds": {"pod_min": pod_min, "far_max": far_max, "depth_rmse_m": rmse_max, "min_observed": n_min}, "recalibrate_after_failures": int(V.get("recalibrate_after_failures", 2))}


def point_summary(eps: list[dict], evs: dict[str, dict]) -> dict:
    hist = sorted([e for e in evs.values() if e.get("kind", "historical") == "historical"], key=sort_key)
    by_ev = {e["event_id"]: e for e in eps}
    rows = []
    for ev in hist:
        e = by_ev.get(ev["event_id"])
        rows.append({"event_id": ev["event_id"], "label": ev.get("label"), "label_en": ev.get("label_en"), "date": ev.get("date") or ev.get("start"), "p24_mm": ev.get("p24_mm"), "rp_equiv_yr": ev.get("rp_equiv_yr"),
                     "status": (e or {}).get("status", "unknown"), "status_zh": STAGE_ZH.get((e or {}).get("status", "unknown")), "status_en": STAGE_EN.get((e or {}).get("status", "unknown")),
                     "observed_flooded": (e or {}).get("observed_flooded"), "model_flooded": (e or {}).get("model_flooded"), "agreement": (e or {}).get("agreement", "unknown"),
                     "obs_depth_max_m": (e or {}).get("obs_depth_max_m"), "model_depth_m": (e or {}).get("model_depth_m"), "obs_duration_min": (e or {}).get("obs_duration_min"), "model_duration_min": (e or {}).get("model_duration_min"),
                     "units_sent": (e or {}).get("units_sent"), "drain_min": (e or {}).get("drain_min"), "source": (e or {}).get("source"), "sources": (e or {}).get("sources"),
                     "area_reported": bool((e or {}).get("area_reported")), "area_report_names": (e or {}).get("area_report_names") or []})
    n_obs = sum(1 for r in rows if r["observed_flooded"] is not None); n_fl = sum(1 for r in rows if r["observed_flooded"] is True)
    n_model = sum(1 for r in rows if r["model_flooded"] is True); n_model_events = sum(1 for r in rows if r["model_flooded"] is not None)   # 模型只对映射了情景的事件有意见
    n_area = sum(1 for r in rows if r["area_reported"])
    last = max((r["date"] or "" for r in rows if r["observed_flooded"] is True), default=None)
    last_model = max((r["date"] or "" for r in rows if r["model_flooded"] is True), default=None)
    live = sorted([e for e in evs.values() if e.get("kind") in ("forecast", "live") and not e.get("closed_at")], key=sort_key)
    cur = None
    if live:
        ev = live[-1]; e = by_ev.get(ev["event_id"])
        cur = {"event_id": ev["event_id"], "label": ev.get("label"), "kind": ev.get("kind"), "status": (e or {}).get("status", "none"), "status_zh": STAGE_ZH.get((e or {}).get("status", "none")), "status_en": STAGE_EN.get((e or {}).get("status", "none"))}
    return {"n_events": len(hist), "n_observed": n_obs, "n_flooded": n_fl, "n_model_flooded": n_model, "n_model_events": n_model_events, "n_area_reported": n_area, "recurrence": (f"{n_fl}/{n_obs}" if n_obs else None), "recurrence_model": (f"{n_model}/{n_model_events}" if n_model_events else None),
            "last_flooded": last or None, "last_flooded_model": last_model or None, "history": rows, "current": cur,
            "lifecycle_status": (cur["status"] if cur else "idle"), "lifecycle_status_zh": (cur["status_zh"] if cur else "平时"), "lifecycle_status_en": (cur["status_en"] if cur else "idle")}


def treatment_status(proposals: list[dict]) -> str:
    order = {"已否决": "rejected", "建议": "proposed", "已批准": "approved", "在建": "under_construction", "已建成": "built", "已验证有效": "verified"}
    rank = ["none", "rejected", "proposed", "approved", "under_construction", "built", "verified"]
    best = "none"
    for p in proposals or []:
        s = order.get(str(p.get("status") or "建议"), "proposed")
        if rank.index(s) > rank.index(best):
            best = s
    return best


def proposal_status_overlay(cfg: dict) -> dict[int, dict]:
    out = {}
    for r in read_jsonl(_path(cfg, "proposal_status_file")):
        try:
            out[int(r.get("pid"))] = {"status": r.get("status"), "status_at": r.get("at"), "status_note": r.get("note"), "status_by": r.get("by")}
        except (TypeError, ValueError):
            pass
    return out
