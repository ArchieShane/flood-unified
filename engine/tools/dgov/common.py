"""dgov 公共：读配置、路径、哈希。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONF = HERE / "config"
OUT = ROOT / "out" / "dgov"


def load(name):
    return yaml.safe_load((CONF / name).read_text(encoding="utf-8"))


def source(sid):
    s = load("sources.yaml")["sources"][sid]
    s["id"] = sid
    s["abspath"] = (Path(s["path"]) if Path(s["path"]).is_absolute() else ROOT / s["path"]).resolve()
    return s


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def raw_manifest(sid):
    p = OUT / "raw" / sid / "manifest.json"
    if not p.exists():
        raise SystemExit(f"{sid} 还没登记：先跑 python -m tools.dgov.raw register --source {sid}")
    return json.loads(p.read_text(encoding="utf-8"))


def _code_version():
    import subprocess
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "tools/dgov", "tools/dataqc"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return {}
    files = {p.relative_to(ROOT).as_posix(): sha256(p)[:12] for p in sorted((ROOT / "tools" / "dgov").rglob("*")) if p.suffix in (".py", ".yaml")}
    return {"git": head, "uncommitted_changes": bool(dirty), "files": files}


def lineage(step, inputs, outputs, params=None, notes=None):
    """记一条血缘事件（参照 OpenLineage 的 run 事件：job、inputs、outputs、run facets）。
    inputs / outputs：路径列表；每个都记 sha256（目录记文件清单的哈希）。写 out/dgov/lineage/<时间>_<步骤>.json，并追加到 lineage.jsonl。"""
    import datetime as _dt

    def facet(p):
        p = Path(p)
        if p.is_dir():
            return {"path": str(p), "kind": "dir"}
        if not p.exists():
            return {"path": str(p), "kind": "missing"}
        big = p.stat().st_size > 2e9
        return {"path": str(p), "bytes": p.stat().st_size, "sha256": None if big else sha256(p),
                "note": "大于 2 GB：哈希见原始层 manifest" if big else None}
    t = _dt.datetime.now().isoformat(timespec="seconds")
    ev = {"eventTime": t, "job": step, "inputs": [facet(p) for p in inputs], "outputs": [facet(p) for p in outputs],
          "params": params or {}, "code": _code_version(), "notes": notes}
    d = OUT / "lineage"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{t.replace(':', '')}_{step}.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with open(d / "lineage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    return ev


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
