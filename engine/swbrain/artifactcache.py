"""算过的东西落盘：情景上下文、调度载荷等，按「代码指纹 + 配置指纹 + 状态指纹」分目录存 out/cache/<sig>/。

为什么：serve.State 以前全在内存，改一行代码重启就要预热 12–15 分钟。现在同一份代码 / 配置 / 状态下算过的条目直接从盘上读，重启几十秒可用。
指纹：
  code   模型层模块（dispatch、dispatch2、floodpoints、networkflood、replay、resources、travel、traffic、recession、standards、whatif、hotspots、terrain、scenarios、alerts、gwm、export、contracts…）源码的 sha1；
         本体 / 服务 / 控制台不算在内——改摘要文字、改页面不会让缓存作废。
  config 配置去掉 [source] / [output] / [serve] / [agent]（密钥、端口、模型路由与算法无关）后的 JSON 的 sha1。
  state  out/state 下每个文件的 (名字, 大小, mtime)，加 exchange/fleet*.json、热点清单、最新 predictions 文件。
任何一项变了就是新目录；旧目录只留最近 3 个。写入用临时文件 + 改名，读不到 / 坏了就当没有。"""
from __future__ import annotations
import hashlib
import io
import tokenize
import json
import os
import pickle
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_MODULES = ("dispatch", "dispatch2", "floodpoints", "networkflood", "hydraulics", "replay", "resources", "travel", "traffic", "recession", "standards", "whatif", "decisions",
                 "hotspots", "terrain", "scenarios", "alerts", "gwm", "gwmcompare", "export", "contracts", "catchments", "refdata", "simulate", "indicators", "hotspotdetail", "graph", "verification",
                 "tools.hotspot_workbook")      # 客户热点工作簿的解析（匹配半径 / 面积 / 汇流时间）直接决定缓存内容，2026-09-23 补进来
CONFIG_SKIP = ("source", "output", "serve", "agent", "feeds", "lifecycle",
               "ponds", "assets")   # [ponds] 只影响第二道防线的规划、[assets] 只影响资产台账的取数范围，都不参与上下文生成；
                          # 让它进指纹的话，调一次塘的参数就把要 75 秒重建的热缓存打掉一次


def _sha(parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8")); h.update(b"\x00")
    return h.hexdigest()


def _code_tokens(src: str) -> str:
    """源码去掉注释与纯排版换行后的 token 流。改注释不该让缓存作废（重算一轮 10 分钟）。"""
    try:
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL):
                continue
            if tok.type == tokenize.NEWLINE:
                out.append("\n"); continue
            if tok.type in (tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
                continue
            out.append(tok.string)
        return "\x1f".join(out)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return src                                    # 词法都过不去就按原样哈希，宁可多失效一次


def code_fingerprint(root: Path = ROOT) -> str:
    parts = []
    for m in MODEL_MODULES:
        f = root / "swbrain" / f"{m}.py"
        if not f.exists():
            f = root / "swbrain" / (m.replace(".", "/") + ".py")      # 支持 tools.hotspot_workbook 这种子包路径
        if f.exists():
            parts.append(m); parts.append(_code_tokens(f.read_text(encoding="utf-8", errors="replace")))
    return _sha(parts)[:12]


def config_fingerprint(cfg: dict) -> str:
    slim = {k: v for k, v in (cfg or {}).items() if k not in CONFIG_SKIP}
    return _sha([json.dumps(slim, sort_keys=True, ensure_ascii=False, default=str)])[:12]


def state_manifest(cfg: dict, root: Path = ROOT) -> list[dict]:
    files = []
    st = root / "out" / "state"
    if st.exists():
        files += sorted(f for f in st.glob("*") if f.name != "manifest.json")      # 清单自己不进指纹（写它本身会改 mtime）
    ex = root / str((cfg.get("exchange") or {}).get("dir") or "exchange")
    files += sorted(ex.glob("fleet*.json"))
    # 预测侧给的积水点算输入；flood_points.system-*.json 是本系统自己写的产物（floodpoints.write），不能进指纹，否则算一个情景就把缓存作废一次
    preds = sorted([f for f in (ex / "predictions").glob("*flood_points*.json") if not f.name.startswith("flood_points.system-")], key=lambda p: p.stat().st_mtime) if (ex / "predictions").exists() else []
    if preds:
        files.append(preds[-1])
    hs = (cfg.get("hotspots") or {}).get("file")
    if hs:
        p = Path(hs); p = p if p.is_absolute() else root / p
        if p.exists():
            files.append(p)
    out = []
    for f in files:
        try:
            s = f.stat(); out.append({"name": f.name, "size": s.st_size, "mtime": int(s.st_mtime)})
        except OSError:
            continue
    return out


def git_commit(root: Path = ROOT) -> str | None:
    h = root / ".git" / "HEAD"
    try:
        ref = h.read_text(encoding="utf-8").strip()
        if ref.startswith("ref: "):
            return (root / ".git" / ref[5:]).read_text(encoding="utf-8").strip()[:12]
        return ref[:12]
    except OSError:
        return None


def write_state_manifest(cfg: dict, counts: dict, root: Path = ROOT, note: str | None = None) -> Path:
    """批处理结束时写 out/state/manifest.json：这批状态从哪来、多少行、配置与代码是什么。服务装载时读它。"""
    import datetime as _dt
    src = {k: {kk: v.get(kk) for kk in ("host", "dbname", "schema") if kk in (v or {})} for k, v in (cfg.get("source") or {}).items()}
    for s in src.values():
        s.pop("password", None)
    p = root / "out" / "state" / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"built_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "note": note, "counts": counts,
                             "config": config_fingerprint(cfg), "code": code_fingerprint(root), "commit": git_commit(root),
                             "sources": src, "python": os.sys.version.split()[0]}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return p


def read_state_manifest(root: Path = ROOT) -> dict | None:
    p = root / "out" / "state" / "manifest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def signature(cfg: dict, root: Path = ROOT) -> dict:
    code = code_fingerprint(root); conf = config_fingerprint(cfg); man = state_manifest(cfg, root)
    state = _sha([json.dumps(man, sort_keys=True)])[:12]
    return {"sig": _sha([code, conf, state])[:16], "code": code, "config": conf, "state": state, "files": man, "python": os.sys.version.split()[0]}


def traffic_hash(meta: dict | None) -> str:
    """路况系数的指纹（驻地系数 + 时变序列，保留 3 位）；没有路况 = none。调度载荷的盘上键要带它：同样的雨、不同的堵法，解不一样。"""
    if not meta or meta.get("error"):
        return "none"
    F = {k: round(float((v or {}).get("factor", 1.0)), 3) for k, v in (meta.get("factors") or {}).items()}
    S = {k: {d: round(float(f), 3) for d, f in (v or {}).items()} for k, v in (meta.get("series") or {}).items()}
    return _sha([meta.get("provider"), json.dumps(F, sort_keys=True), json.dumps(S, sort_keys=True)])[:10]


class Cache:
    def __init__(self, cfg: dict, root: Path = ROOT, enabled: bool = True, keep: int = 3):
        self.enabled = bool(enabled); self.root = root
        self.info = signature(cfg, root); self.sig = self.info["sig"]
        self.dir = root / "out" / "cache" / self.sig
        self.hits = 0; self.misses = 0; self.writes = 0
        if self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                (self.dir / "manifest.json").write_text(json.dumps({**self.info, "created": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=1), encoding="utf-8")
                self._prune(keep)
            except OSError:
                self.enabled = False

    def _prune(self, keep: int):
        base = self.dir.parent
        dirs = sorted([d for d in base.iterdir() if d.is_dir() and d != self.dir], key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[max(keep - 1, 0):]:
            shutil.rmtree(d, ignore_errors=True)

    @staticmethod
    def key_for(*parts) -> str:
        return _sha([json.dumps(p, sort_keys=True, default=str) for p in parts])[:20]

    def _path(self, name: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_.@" else "_" for ch in str(name))[:120]
        return self.dir / f"{safe}.pkl"

    def has(self, name: str) -> bool:
        return self.enabled and self._path(name).exists()

    def get(self, name: str):
        if not self.enabled:
            return None
        p = self._path(name)
        if not p.exists():
            self.misses += 1; return None
        try:
            with p.open("rb") as f:
                obj = pickle.load(f)
            self.hits += 1; return obj
        except Exception:
            self.misses += 1
            try:
                p.unlink()
            except OSError:
                pass
            return None

    def put(self, name: str, obj) -> bool:
        if not self.enabled:
            return False
        p = self._path(name); tmp = p.with_suffix(".tmp")
        try:
            with tmp.open("wb") as f:
                pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, p); self.writes += 1
            return True
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def stats(self) -> dict:
        n = 0; size = 0
        if self.enabled and self.dir.exists():
            for f in self.dir.glob("*.pkl"):
                n += 1; size += f.stat().st_size
        return {"enabled": self.enabled, "sig": self.sig, "dir": str(self.dir.relative_to(self.root)) if self.enabled else None, "entries": n, "size_mb": round(size / 1e6, 1), "hits": self.hits, "misses": self.misses, "writes": self.writes,
                "code": self.info["code"], "config": self.info["config"], "state": self.info["state"]}
