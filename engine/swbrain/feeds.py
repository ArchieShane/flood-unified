"""外部实时源接入：993 报案（incidents）、承包商到场 check-in（checkins）、桥梁限重台账（bridges）、承包商服务边界（contractors）。

每个源两条路，契约不变（docs/接口约定.md、exchange/examples/*.example.json）：
  拉：[feeds.<name>].url 非空 → GET 该地址（Authorization: Bearer <环境变量 token_env>），按 poll_s 缓存；失败退回文件并在 meta 里记错误。
  推：POST /api/feed/<name>，正文就是契约 JSON → 合并进 exchange/<name>.json（有 id 按 id 去重覆盖，没有按整行去重）。
文件是唯一落地处；exchange/ 下客户数据不进仓库。ADM 应急系统开放接口前，url 留空，值班员用推的方式或直接放文件。
"""
from __future__ import annotations
import datetime as _dt
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("incidents", "checkins", "bridges", "contractors", "events", "episodes")
DEFAULTS = {
    "incidents": {"file": "exchange/incidents.json", "url": "", "token_env": "ADM_INCIDENTS_TOKEN", "poll_s": 60, "schema": "swbrain.incidents/1", "id_key": "id"},
    "checkins": {"file": "exchange/checkins.json", "url": "", "token_env": "ADM_CHECKINS_TOKEN", "poll_s": 60, "schema": "swbrain.checkins/1", "id_key": "id"},
    "bridges": {"file": "exchange/bridges.json", "url": "", "token_env": "ADM_BRIDGES_TOKEN", "poll_s": 3600, "schema": "swbrain.bridges/1", "id_key": "id"},
    "contractors": {"file": "exchange/contractors.json", "url": "", "token_env": "ADM_CONTRACTORS_TOKEN", "poll_s": 3600, "schema": "swbrain.contractors/1", "id_key": "depot_id"},
    "events": {"file": "exchange/events.json", "url": "", "token_env": "ADM_EVENTS_TOKEN", "poll_s": 3600, "schema": "swbrain.events/1", "id_key": "event_id"},
    "episodes": {"file": "exchange/episodes.json", "url": "", "token_env": "ADM_EPISODES_TOKEN", "poll_s": 3600, "schema": "swbrain.episodes/1", "id_key": "id"},
}
_PUSH_LOCK = threading.Lock()          # 推送是读-改-写：两个并发的 POST /api/feed/<name> 会互相覆盖（2026-09-23 修）
_HTTP_CACHE: dict[str, dict] = {}


def params(cfg: dict, name: str) -> dict:
    if name not in DEFAULTS:
        raise KeyError(f"未知的源：{name}（可用：{', '.join(NAMES)}）")
    P = dict(DEFAULTS[name]); P.update(((cfg or {}).get("feeds") or {}).get(name) or {}); P["_name"] = name
    return P


def _path(P: dict) -> Path:
    f = Path(str(P["file"]))
    return f if f.is_absolute() else ROOT / f


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随跳转。urllib 跳转时只剥 content-length / content-type，Authorization 原样带到新主机——
    接 ADM 真实接口后，一个 302 就能把 Bearer token 送到第三方域名（2026-09-23 修）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"外部源不跟随跳转（{code} → {newurl}）", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(url: str, token: str | None, timeout_s: float = 15.0) -> dict:
    scheme = urllib.parse.urlsplit(str(url)).scheme.lower()
    if scheme not in ("http", "https"):                 # file:// 和 UNC 路径会被当成源读进来
        raise ValueError(f"外部源只支持 http/https，给的是 {scheme or '(空)'}")
    if token and scheme != "https":
        raise ValueError("带 token 的外部源必须走 https，否则口令在链路上是明文")
    req = urllib.request.Request(url, headers={"Accept": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with _OPENER.open(req, timeout=timeout_s) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _read_file(P: dict) -> dict:
    f = _path(P)
    if not f.exists():
        return {"rows": [], "source": "none", "file": str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f), "as_of": None, "schema": None, "exists": False}
    try:
        j = json.loads(f.read_text(encoding="utf-8")) or {}
        rows = j.get("rows") if isinstance(j, dict) else j
        rows, bad = validate(P["_name"], list(rows or [])) if P.get("_name") else (list(rows or []), [])
        return {"rows": rows, "invalid": len(bad), "invalid_examples": bad[:5], "source": "file", "file": str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f), "as_of": (j.get("as_of") if isinstance(j, dict) else None),
                "schema": (j.get("schema") if isinstance(j, dict) else None), "exists": True, "mtime": _dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"), "origin": (j.get("source") if isinstance(j, dict) else None)}
    except Exception as ex:
        return {"rows": [], "source": "file", "file": str(f), "as_of": None, "schema": None, "exists": True, "error": f"文件解析失败：{str(ex)[:120]}"}


def load(cfg: dict, name: str, fetch=None, now: float | None = None) -> dict:
    """返回 {"rows", "source": http|file|none, "as_of", "schema", "url", "error", …}。有 url 先拉，拉不到退回文件。"""
    P = params(cfg, name); url = str(P.get("url") or "").strip(); fetch = fetch or _get; now = now if now is not None else time.time()
    if url:
        c = _HTTP_CACHE.get(name)
        if c and now - c["at"] <= float(P.get("poll_s", 60)):
            return {**c["data"], "cached": True}
        token = os.environ.get(str(P.get("token_env") or ""), "").strip() or None
        try:
            j = fetch(url, token)
            rows = j.get("rows") if isinstance(j, dict) else j
            rows, bad = validate(name, list(rows or []))
            data = {"rows": rows, "invalid": len(bad), "invalid_examples": bad[:5], "source": "http", "url": url, "as_of": (j.get("as_of") if isinstance(j, dict) else None), "schema": (j.get("schema") if isinstance(j, dict) else None),
                    "fetched_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "has_token": bool(token)}
            _HTTP_CACHE[name] = {"at": now, "data": data}
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as ex:
            fb = _read_file(P)
            return {**fb, "url": url, "error": f"拉取失败（{str(ex)[:120]}），已退回文件", "http_failed": True}
    return _read_file(P)


BBOX = (51.0, 22.3, 57.0, 26.6)      # 阿布扎比酋长国经纬度范围（lon_min, lat_min, lon_max, lat_max）


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _in_bbox(lon, lat) -> bool:
    return lon is not None and lat is not None and BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def validate(name: str, rows: list) -> tuple[list, list]:
    """按契约逐行校验 → (合格行, [{"row": 序号, "why": 原因, "id": …}])。字段名见 exchange/examples/<name>.example.json。"""
    ok, bad = [], []
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            bad.append({"row": i, "why": "不是对象"}); continue
        why = None
        if name == "incidents":
            lon, lat = _num(r.get("lon")), _num(r.get("lat"))
            if not r.get("fp_id") and (lon is None or lat is None):
                why = "缺 fp_id，也缺 lon/lat"
            elif not r.get("fp_id") and not _in_bbox(lon, lat):
                why = f"经纬度 {lon},{lat} 不在阿布扎比范围（lon 51–57、lat 22.3–26.6；注意顺序是 lon, lat）"
            elif r.get("verified") is not None and not isinstance(r.get("verified"), bool):
                why = "verified 应为 true/false"
        elif name == "checkins":
            u = _num(r.get("units"))
            if not (r.get("depot_id") or r.get("site_id")):
                why = "缺 depot_id / site_id"
            elif u is None or u < 0 or int(u) != u:
                why = "units 应为非负整数"
        elif name == "bridges":
            lon, lat = _num(r.get("lon")), _num(r.get("lat"))
            lim, clr = _num(r.get("limit_t")), _num(r.get("clearance_m"))
            if not _in_bbox(lon, lat):
                why = "缺 lon/lat 或不在阿布扎比范围"
            elif (lim is None or lim <= 0) and (clr is None or clr <= 0):
                why = "limit_t（吨）与 clearance_m（米）至少给一个正数"
        elif name == "contractors":
            if not r.get("contractor"):
                why = "缺 contractor"
            elif not (r.get("depot_id") or r.get("region")):
                why = "缺 depot_id 或 region"
        elif name == "events":
            import re as _re
            if not r.get("event_id"):
                why = "缺 event_id"
            elif not _re.match(r"^\d{4}-\d{2}(-\d{2})?", str(r.get("date") or r.get("start") or "")):
                why = "date / start 应为 YYYY-MM 或 YYYY-MM-DD"
            elif r.get("p24_mm") is not None and (_num(r.get("p24_mm")) is None or _num(r.get("p24_mm")) <= 0):
                why = "p24_mm 应为正数"
            elif r.get("impacted_areas") is not None and not (isinstance(r.get("impacted_areas"), list) and all(isinstance(x, str) for x in r["impacted_areas"])):
                why = "impacted_areas 应为字串数组"
        elif name == "episodes":
            from . import lifecycle as _lc
            if not _lc.norm_fp(r.get("fp_id")):
                why = "缺 fp_id（ADM-62 这种编号）"
            elif not r.get("event_id"):
                why = "缺 event_id（2024-04 这种事件编号）"
            elif r.get("stage") is not None and r.get("stage") not in _lc.STAGES:
                why = f"stage 应为 {'/'.join(_lc.STAGES)} 之一"
            elif r.get("flooded") is not None and not isinstance(r.get("flooded"), bool):
                why = "flooded 应为 true/false"
            elif r.get("depth_m") is not None and (_num(r.get("depth_m")) is None or _num(r.get("depth_m")) < 0):
                why = "depth_m 应为非负数"
        if why:
            bad.append({"row": i, "id": r.get("id") or r.get("depot_id"), "why": why})
        else:
            ok.append(r)
    return ok, bad


def push(cfg: dict, name: str, payload: dict | list, origin: str = "push") -> dict:
    """把推来的契约 JSON 合并进文件。有 id_key 的按 id 覆盖，没有的按整行去重。返回合并统计。"""
    P = params(cfg, name); f = _path(P); idk = P.get("id_key")
    raw = payload.get("rows") if isinstance(payload, dict) else payload
    raw = list(raw or []) if isinstance(raw, list) else []
    new_rows, rejected = validate(name, raw)
    if raw and not new_rows:
        return {"name": name, "received": len(raw), "accepted": 0, "rejected": rejected[:50], "added": 0, "updated": 0, "error": "没有一行通过契约校验，未写入", "schema": P.get("schema"), "example": f"exchange/examples/{name}.example.json"}
    with _PUSH_LOCK:                       # 读-改-写全程持锁
        cur = _read_file(P); rows = list(cur.get("rows") or [])
        index = {}
        for i, r in enumerate(rows):
            k = r.get(idk) if idk and r.get(idk) is not None else json.dumps(r, sort_keys=True, ensure_ascii=False)
            index[str(k)] = i
        added = updated = 0
        for r in new_rows:
            k = r.get(idk) if idk and r.get(idk) is not None else json.dumps(r, sort_keys=True, ensure_ascii=False)
            k = str(k)
            if k in index:
                rows[index[k]] = r; updated += 1
            else:
                index[k] = len(rows); rows.append(r); added += 1
        f.parent.mkdir(parents=True, exist_ok=True)
        out = {"schema": (payload.get("schema") if isinstance(payload, dict) and payload.get("schema") else P.get("schema")), "as_of": (payload.get("as_of") if isinstance(payload, dict) and payload.get("as_of") else _dt.datetime.now().isoformat(timespec="seconds")),
               "source": (payload.get("source") if isinstance(payload, dict) and payload.get("source") else origin), "rows": rows}
        _atomic_write(f, json.dumps(out, ensure_ascii=False, indent=1))
        _HTTP_CACHE.pop(name, None)
        return {"name": name, "file": str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f), "received": len(raw), "accepted": len(new_rows), "rejected": rejected[:50], "n_rejected": len(rejected),
                "added": added, "updated": updated, "total": len(rows), "as_of": out["as_of"]}


def _atomic_write(path, text: str):
    """先写临时文件再 os.replace：写一半被打断也不会留下半截 JSON
    （_read_file 捕获异常后返回空，这个源就静默变成「没有数据」而不是报错）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def status(cfg: dict) -> dict:
    """四个源各自：配了拉还是只有文件、文件有多少行、最近更新、token 有没有。"""
    out = {}
    for n in NAMES:
        P = params(cfg, n); fb = _read_file(P); url = str(P.get("url") or "").strip()
        c = _HTTP_CACHE.get(n)
        out[n] = {"mode": ("pull+push" if url else "push/file"), "url": url or None, "token_set": bool(os.environ.get(str(P.get("token_env") or ""), "").strip()) if url else None, "schema": P.get("schema"),
                  "file": fb.get("file"), "file_exists": fb.get("exists", False), "rows": len(fb.get("rows") or []), "as_of": fb.get("as_of"), "mtime": fb.get("mtime"), "origin": fb.get("origin"),
                  "last_pull": (c["data"].get("fetched_at") if c else None), "poll_s": P.get("poll_s"), "push_endpoint": f"/api/feed/{n}", "error": fb.get("error"), "invalid_rows": fb.get("invalid", 0)}
    return out
