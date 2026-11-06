# -*- coding: utf-8 -*-
"""洪水推演成果展示服务（只读）：扫描 NAS 上三台服务器同步来的成果，提供「区域 → 格网精度 → 雨量」目录、
单个情景的全部展示数据（逐帧图清单、统计、雨型、热点过程线），以及逐帧 PNG 静态文件。

只用 Python 标准库，没有第三方依赖。数据目录（环境变量 DATA_ROOT，缺省 /data）布局，同步脚本 sync_to_nas.sh 产生：
    <DATA_ROOT>/<主机名>/runtime/console_data/flood2d_<区域>_<情景>/{manifest.json, stats.json, c1_stats.json | ms_stats.json | rg_stats.json, L1/f000.png ...}
    <DATA_ROOT>/<主机名>/runtime/console_data/{c1_hyeto.json, c1_hs_series.json}   （每台只含本机跑过的情景，这里按键合并）
情景目录名 flood2d_<区域>_m<雨量>[h<历时>][g<网格米数>]：M61 = 61 mm / 2 h、15 m；M61H1G5 = 61 mm / 1 h、5 m。
同一情景在多台机器上都有时取 manifest.json 最新的一份，并在 /api/health 里列出冲突。
"""
from __future__ import annotations

import gzip
import json
import mimetypes
import os
import re
import sys
import threading
import time
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/data"))
WEB_ROOT = Path(os.environ.get("WEB_ROOT", Path(__file__).resolve().parent.parent / "web"))
PORT = int(os.environ.get("PORT", "8080"))
TILES_DIR = Path(os.environ.get("TILES_DIR", "/tiles"))     # 离线底图：{z}/{x}/{y}.png，存在才启用
TILE_URL = os.environ.get("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")   # 在线底图由客户浏览器直接取，服务器不联网；设为空 = 纯色底图
SCAN_TTL = int(os.environ.get("SCAN_TTL", "120"))          # 目录缓存秒数；也可 GET /api/rescan 立即重扫
# 下架的雨量（mm，逗号分隔）：10-10 客户把 32 mm 更正为 33 mm，NAS 上的 32 mm 旧结果保留不删，但不展示，列在 /api/health 的 skipped 里
EXCLUDE_MM = {int(x) for x in os.environ.get("EXCLUDE_MM", "32").replace(" ", "").split(",") if x}
EXCLUDE_GRID = {int(x) for x in os.environ.get("EXCLUDE_GRID", "15").replace(" ", "").split(",") if x}   # 只对这些网格（m）下架：5 m 的 32 mm 保留
REGION_LABELS = {"c1": "C1 热点群", "ms": "Mussafah_00", "rg_yas": "Yas bridges interchange", "rg_bat": "Al Bateen", "rg_bny": "Bani Yas"}
try:
    REGION_LABELS.update(json.loads(os.environ.get("REGION_LABELS", "{}")))
except ValueError:
    pass

DIR_RE = re.compile(r"^flood2d_(?P<reg>.+?)_m(?P<mm>\d+)(?:h(?P<h>\d+))?(?:g(?P<g>\d+))?$")
STATS_FILES = ("c1_stats.json", "ms_stats.json", "rg_stats.json")
SUB_DIRS = {"runtime", "console_data", "data"}              # 主机目录之下只往这几层找，不进 work/（几十 GB 的 sww、npz）
KEEP_JSON = ("hs_series", "hyeto")


def read_json(p: Path):
    with open(p, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


# ---------------------------------------------------------------------------- 扫描
def _walk(d: Path, depth: int):
    try:
        ents = sorted(os.scandir(d), key=lambda e: e.name)
    except OSError:
        return
    for e in ents:
        try:
            if not e.is_dir():
                continue
        except OSError:
            continue
        n = e.name
        if n.startswith("flood2d_"):
            yield Path(e.path)
        elif n[0] in "._":
            continue
        elif depth == 0 or (depth <= 3 and n in SUB_DIRS):
            yield from _walk(Path(e.path), depth + 1)


def scan() -> dict:
    t0 = time.time()
    found, skipped, parents = {}, [], set()
    for d in _walk(DATA_ROOT, 0):
        m = DIR_RE.match(d.name)
        if not m:
            continue                                         # flood2d_c1_r61、flood2d_city2d 等旧口径 / 其他项目：不展示
        if int(m["mm"]) in EXCLUDE_MM and int(m["g"] or 15) in EXCLUDE_GRID:
            skipped.append({"dir": str(d.relative_to(DATA_ROOT)), "reason": f"已下架：{m['g'] or 15} m 网格的 {m['mm']} mm 为作废雨量（EXCLUDE_MM / EXCLUDE_GRID）"})
            continue
        man = d / "manifest.json"
        stats = next((d / s for s in STATS_FILES if (d / s).is_file()), None)
        if stats is None:
            stats = next(iter(sorted(d.glob("*_stats.json"))), None)
        if not man.is_file() or stats is None:
            skipped.append({"dir": str(d.relative_to(DATA_ROOT)), "reason": "缺 manifest.json" if not man.is_file() else "缺统计文件（c1_stats.json / ms_stats.json）"})
            continue
        rel = d.relative_to(DATA_ROOT)
        host = rel.parts[0] if len(rel.parts) > 1 else ""
        mm, h, g = int(m["mm"]), int(m["h"] or 2), int(m["g"] or 15)
        e = {"region": m["reg"], "grid": g, "mm": mm, "h": h, "tag": d.name[len("flood2d_" + m["reg"] + "_"):].upper(),
             "dir": str(d), "rel": rel.as_posix(), "host": host, "stats_file": stats.name, "mtime": man.stat().st_mtime}
        found.setdefault((e["region"], g, mm, h), []).append(e)
        parents.add(d.parent)
    best, conflicts = {}, []
    for k, lst in found.items():
        lst.sort(key=lambda x: x["mtime"], reverse=True)
        best[k] = lst[0]
        if len(lst) > 1:
            conflicts.append({"scenario": f"{k[0]} {k[1]}m {k[2]}mm/{k[3]}h", "used": lst[0]["rel"],
                              "others": [x["rel"] for x in lst[1:]]})
    # 雨型 / 热点过程线：各主机只含本机情景，按文件修改时间从旧到新合并（新的盖旧的）
    side = {k: {} for k in KEEP_JSON}
    files = [(p.stat().st_mtime, k, p) for par in parents for k in KEEP_JSON for p in par.glob(f"*_{k}.json")]
    for _, k, p in sorted(files):
        try:
            side[k].update(read_json(p))
        except (OSError, ValueError):
            pass
    regions = {}
    for (reg, g, mm, h), e in best.items():
        r = regions.setdefault(reg, {"id": reg, "label": REGION_LABELS.get(reg, reg.upper()), "grids": {}})
        gr = r["grids"].setdefault(g, {"id": g, "label": f"{g} m 网格", "rains": []})
        gr["rains"].append({"id": f"{mm}-{h}", "mm": mm, "h": h, "label": f"{mm} mm / {h} h", "tag": e["tag"], "host": e["host"]})
    cat = {"regions": []}
    for r in sorted(regions.values(), key=lambda x: x["label"]):
        gs = sorted(r["grids"].values(), key=lambda x: x["id"])
        for gr in gs:
            gr["rains"].sort(key=lambda x: (x["mm"], x["h"]))
        cat["regions"].append({"id": r["id"], "label": r["label"], "grids": gs})
    return {"catalog": cat, "best": best, "side": side, "skipped": skipped, "conflicts": conflicts,
            "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), "took_s": round(time.time() - t0, 2), "n": len(best)}


_lock = threading.Lock()
_state = {"t": 0.0, "v": None}


def state(force: bool = False) -> dict:
    with _lock:
        if force or _state["v"] is None or time.time() - _state["t"] > SCAN_TTL:
            _state["v"], _state["t"] = scan(), time.time()
        return _state["v"]


# ---------------------------------------------------------------------------- 情景
_jcache: dict = {}


def cached_json(p: Path):
    st = p.stat()
    k = (str(p), st.st_mtime_ns, st.st_size)
    if k not in _jcache:
        for old in [x for x in _jcache if x[0] == str(p)]:
            del _jcache[old]
        _jcache[k] = read_json(p)
    return _jcache[k]


def side_lookup(d: dict, e: dict):
    """雨型 / 热点过程线的键：情景名（M61H1）；5 m 版沿用 15 m 同雨的；2 h 雨的老键是 R<雨量>。"""
    base = re.sub(r"G\d+$", "", e["tag"])
    for k in (e["tag"], base, f"R{e['mm']}" if e["h"] == 2 else None):
        if k and isinstance(d.get(k), dict):
            return d[k]
    return None


def scenario(q: dict):
    try:
        key = (q["region"][0], int(q["grid"][0]), *map(int, q["rain"][0].split("-")))
        assert len(key) == 4
    except (KeyError, ValueError, AssertionError):
        return None, "参数应为 region、grid（米）、rain（雨量-历时，如 61-2）"
    s = state()
    e = s["best"].get(key)
    if not e:
        return None, "没有这个情景的成果"
    d = Path(e["dir"])
    out = {"region": e["region"], "grid": e["grid"], "mm": e["mm"], "h": e["h"], "tag": e["tag"], "host": e["host"],
           "base": "/data/" + e["rel"] + "/", "manifest": cached_json(d / "manifest.json"), "cs": cached_json(d / e["stats_file"]),
           "stats": cached_json(d / "stats.json") if (d / "stats.json").is_file() else None,
           "hyeto": side_lookup(s["side"]["hyeto"], e), "hs": None}
    hs = side_lookup(s["side"]["hs_series"], e)
    if hs is not None:
        out["hs"] = {**hs, "hotspots": s["side"]["hs_series"].get("hotspots")}
    return out, None


# ---------------------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = "flood-viewer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if os.environ.get("ACCESS_LOG"):
            super().log_message(fmt, *a)

    def _send(self, code, body: bytes, ctype: str, extra=None, gz=True):
        if gz and len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "") and (ctype.startswith("text/") or "json" in ctype or "javascript" in ctype):
            body = gzip.compress(body, 5)
            extra = {**(extra or {}), "Content-Encoding": "gzip"}
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8", {"Cache-Control": "no-cache"})

    def _file(self, p: Path, maxage: int):
        try:
            st = p.stat()
        except OSError:
            return self._json({"error": "not found"}, 404)
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        hdr = {"ETag": etag, "Last-Modified": formatdate(st.st_mtime, usegmt=True), "Cache-Control": f"public, max-age={maxage}"}
        if self.headers.get("If-None-Match") == etag:
            return self._send(304, b"", "text/plain", hdr, gz=False)
        ims = self.headers.get("If-Modified-Since")
        if ims:
            try:
                if parsedate_to_datetime(ims).timestamp() >= int(st.st_mtime):
                    return self._send(304, b"", "text/plain", hdr, gz=False)
            except (TypeError, ValueError):
                pass
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype, hdr)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        try:
            if path == "/api/catalog":
                s = state()
                return self._json({**s["catalog"], "scanned_at": s["scanned_at"], "n": s["n"]})
            if path == "/api/config":
                local = TILES_DIR.is_dir()
                return self._json({"tiles": "/tiles/{z}/{x}/{y}.png" if local else TILE_URL or None})
            if path.startswith("/tiles/"):
                root = os.path.abspath(TILES_DIR)
                p = Path(os.path.normpath(os.path.join(root, path[len("/tiles/"):].lstrip("/\\"))))
                if not str(p).startswith(root + os.sep) or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not p.is_file():
                    return self._send(404, b"", "text/plain", gz=False)         # 缺的瓦片安静地 404
                return self._file(p, 86400)
            if path == "/api/scenario":
                out, err = scenario(parse_qs(u.query))
                return self._json(out) if out else self._json({"error": err}, 404)
            if path in ("/api/health", "/api/rescan"):
                s = state(force=path == "/api/rescan")
                return self._json({"ok": True, "data_root": str(DATA_ROOT), "data_root_exists": DATA_ROOT.is_dir(), "scenarios": s["n"],
                                   "scanned_at": s["scanned_at"], "scan_took_s": s["took_s"], "skipped": s["skipped"], "conflicts": s["conflicts"]})
            if path.startswith("/data/"):
                root = os.path.abspath(DATA_ROOT)               # 不解析符号链接：NAS 目录里可能有链接，只防 .. 越界
                p = Path(os.path.normpath(os.path.join(root, path[len("/data/"):].lstrip("/\\"))))
                if not str(p).startswith(root + os.sep) or p.suffix.lower() not in (".png", ".json", ".geojson") or not p.is_file():
                    return self._json({"error": "not found"}, 404)
                return self._file(p, 86400)
            if path == "/":
                path = "/index.html"
            p = (WEB_ROOT / path.lstrip("/")).resolve()
            if WEB_ROOT.resolve() not in p.parents or not p.is_file():
                return self._json({"error": "not found"}, 404)
            return self._file(p, 0)                          # 页面文件每次带 ETag 校验，改了就生效
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:                              # 数据文件损坏等：返回 500 而不是断连接
            self._json({"error": f"{type(ex).__name__}: {ex}"}, 500)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    s = state(force=True)
    print(f"数据目录 {DATA_ROOT}（{'存在' if DATA_ROOT.is_dir() else '不存在！'}）：扫到 {s['n']} 个情景，跳过 {len(s['skipped'])}，冲突 {len(s['conflicts'])}，用时 {s['took_s']} s", flush=True)
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
