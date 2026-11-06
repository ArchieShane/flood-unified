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

DATA_ROOT = Path(os.environ.get("VIEWER_DATA_ROOT", "/viewer-data"))
WEB_ROOT = Path(os.environ.get("WEB_ROOT", Path(__file__).resolve().parent.parent / "web"))
PORT = int(os.environ.get("PORT", "8080"))
TILES_DIR = Path(os.environ.get("VIEWER_TILES_DIR", "/tiles"))     # 离线底图：{z}/{x}/{y}.png，存在才启用
TILE_URL = os.environ.get("VIEWER_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")   # 在线底图由客户浏览器直接取，服务器不联网；设为空 = 纯色底图
SCAN_TTL = int(os.environ.get("SCAN_TTL", "120"))          # 目录缓存秒数；也可 GET /api/rescan 立即重扫
# 下架的雨量（mm，逗号分隔）：10-10 客户把 32 mm 更正为 33 mm，NAS 上的 32 mm 旧结果保留不删，但不展示，列在 /api/health 的 skipped 里
EXCLUDE_MM = {int(x) for x in os.environ.get("EXCLUDE_MM", "32").replace(" ", "").split(",") if x}
EXCLUDE_GRID = {int(x) for x in os.environ.get("EXCLUDE_GRID", "15").replace(" ", "").split(",") if x}   # 只对这些网格（m）下架：5 m 的 32 mm 保留
REGION_LABELS = {"c1": "C1 热点群", "ms": "Mussafah_00", "rg_yas": "Yas bridges interchange", "rg_bat": "Al Bateen", "rg_bny": "Bani Yas"}
# ADM 汇水区 30 m 推演（tools/ad_frames.py 写的 flood2d_ad_<区>_m61h1g30）：区域代码 ad_<区>，没有设施清单 / 雨型 / 热点过程线
REGION_LABELS.update({"ad_" + k.lower(): "ADM · " + v for k, v in {
    "MI8": "MI-8", "RD2": "RD-2", "YASO": "YAS OUTFALL", "RB1": "RB-1", "RBE1": "RBE1-OF1", "MBZ": "MBZ", "RD14": "RD-14",
    "RB3": "RB-3", "RDA": "RD-A", "RDF": "RD-F", "RDC": "RD-C", "QAB1": "Qasr Al Bahr-1", "RDI": "RD-I", "RDG": "RD-G", "ALR": "Al Riyadh City – all ponds"}.items()})
# 艾因 250 m 推演（tools/aa_post.py / pilot_frames 写的 flood2d_aa_alain_m61h1，要改名成 flood2d_aa_alain_m61h1g250 才会按 250 m 网格归类）
REGION_LABELS["aa_alain"] = "Al Ain 艾因"
try:
    REGION_LABELS.update(json.loads(os.environ.get("REGION_LABELS", "{}")))
except ValueError:
    pass

DIR_RE = re.compile(r"^flood2d_(?P<reg>.+?)_m(?P<mm>\d+)(?:h(?P<h>\d+))?(?:g(?P<g>\d+))?$")
STATS_FILES = ("c1_stats.json", "ms_stats.json", "rg_stats.json", "ad_stats.json", "aa_stats.json")
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
        if m["reg"].startswith("ad_") and m["reg"] not in REGION_LABELS:          # ADM 新汇水区不用改 server.py：显示名取统计文件里的 title（ad_post / ad_frames 写进去的）
            try:
                REGION_LABELS[m["reg"]] = "ADM · " + str(read_json(stats).get("title") or m["reg"][3:].upper())
            except (OSError, ValueError, AttributeError):
                pass
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
    if e["region"].startswith(("ad_", "aa_")):
        return None                                           # ADM 30 m / 艾因 250 m 推演没有雨型 / 过程线，别误取 C1 同名情景的（艾因的 tag 是 M61H1G250，去掉 G 后会撞 C1 的 M61H1）
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
           "base": "/api/viewer/data/" + e["rel"] + "/", "manifest": cached_json(d / "manifest.json"), "cs": cached_json(d / e["stats_file"]),
           "stats": cached_json(d / "stats.json") if (d / "stats.json").is_file() else None,
           "hyeto": side_lookup(s["side"]["hyeto"], e), "hs": None}
    hs = side_lookup(s["side"]["hs_series"], e)
    if hs is not None:
        out["hs"] = {**hs, "hotspots": s["side"]["hs_series"].get("hotspots")}
    return out, None


