"""配置单一来源：装载时校验 config.toml 的键与 config.example.toml 是否一致（漂移两方向都报），并能从 config.toml 脱敏生成 config.example.toml。

  python -m swbrain.configcheck            # 报漂移：config 多出的键（没同步到示例）、示例有而 config 缺的键；退出码 1 = 有漂移
  python -m swbrain.configcheck --sync     # 用 config.toml 覆盖生成 config.example.toml：主机 / 口令 / 密钥 / 本机路径这些键保留示例里的占位值

serve 启动时调 check()，有漂移只打印警告，不阻止启动。"""
from __future__ import annotations
import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = ("password", "access_key", "api_key", "token", "secret")
LOCAL_KEYS = ("host", "coords_kmz", "dir", "file", "dsh_bin", "dsh_bin_web", "home", "home_web")


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def check(config_path: Path | None = None, example_path: Path | None = None) -> dict:
    cp = config_path or ROOT / "config.toml"; ep = example_path or ROOT / "config.example.toml"
    if not cp.exists() or not ep.exists():
        return {"ok": True, "note": "缺 config.toml 或 config.example.toml，跳过", "extra": [], "missing": []}
    c = _flatten(tomllib.loads(cp.read_text(encoding="utf-8"))); e = _flatten(tomllib.loads(ep.read_text(encoding="utf-8")))
    # 情景事件表这类「数据节」允许两边条目不同：只比顶层之外的结构键，事件名 / 片区名不算漂移
    def norm(k: str) -> str:
        return re.sub(r"^(scenarios\.events\.[^.]+|scenarios\.cc\.[^.]+|scenarios\.events\.[^.]+\.[^.]+\.[^.]+)", lambda m: m.group(0).rsplit(".", 1)[0] + ".*" if m.group(0).count(".") >= 2 else m.group(0), k)
    ck = {norm(k) for k in c}; ek = {norm(k) for k in e}
    extra = sorted(ck - ek); missing = sorted(ek - ck)
    return {"ok": not extra and not missing, "extra": extra, "missing": missing, "n_config": len(c), "n_example": len(e)}


def _is_local_or_secret(key: str, value) -> bool:
    last = key.rsplit(".", 1)[-1].lower()
    if any(s in last for s in SECRET_KEYS):
        return True
    if last in LOCAL_KEYS and isinstance(value, str) and (re.match(r"^[A-Za-z]:[\\/]", value) or re.match(r"^\d+\.\d+\.\d+\.\d+$", value) or value.startswith("\\\\")):
        return True
    return False


def sync_example(config_path: Path | None = None, example_path: Path | None = None) -> dict:
    """逐行复制 config.toml 到 config.example.toml；值为密钥 / 口令 / 本机路径 / 内网主机的键，保留示例原值（示例里没有就写占位）。注释与顺序原样。"""
    cp = config_path or ROOT / "config.toml"; ep = example_path or ROOT / "config.example.toml"
    old_example = _flatten(tomllib.loads(ep.read_text(encoding="utf-8"))) if ep.exists() else {}
    lines = cp.read_text(encoding="utf-8").splitlines()
    out = []; section = ""; replaced = []
    for ln in lines:
        m_sec = re.match(r"^\s*\[+([^\]]+)\]+\s*$", ln)
        if m_sec:
            section = m_sec.group(1).strip().strip('"'); out.append(ln); continue
        m_kv = re.match(r"^(\s*)([A-Za-z0-9_\-\"']+)\s*=\s*(.+?)\s*$", ln)
        if m_kv and not ln.lstrip().startswith("#"):
            key = m_kv.group(2).strip('"'); full = f"{section}.{key}" if section else key
            try:
                val = tomllib.loads(f"v = {m_kv.group(3)}")["v"]
            except Exception:
                val = m_kv.group(3)
            if _is_local_or_secret(full, val):
                keep = old_example.get(full)
                if keep is None:
                    keep = "CHANGE_ME" if any(s in key.lower() for s in SECRET_KEYS) else f"<{key}>"
                rendered = f'"{keep}"' if isinstance(keep, str) else str(keep).lower() if isinstance(keep, bool) else str(keep)
                out.append(f"{m_kv.group(1)}{m_kv.group(2)} = {rendered}"); replaced.append(full); continue
        out.append(ln)
    ep.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return {"written": str(ep), "kept_placeholders": replaced}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--sync", action="store_true"); ap.add_argument("--config"); ap.add_argument("--example")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cp = Path(a.config) if a.config else None; ep = Path(a.example) if a.example else None
    if a.sync:
        r = sync_example(cp, ep); print(f"已生成 {r['written']}；保留占位的键：{', '.join(r['kept_placeholders'])}")
    r = check(cp, ep)
    if r.get("note"):
        print(r["note"]); return 0
    if r["ok"]:
        print(f"配置一致：{r['n_config']} 个键"); return 0
    if r["extra"]:
        print("config.toml 有、示例没有（忘了同步）：", ", ".join(r["extra"]))
    if r["missing"]:
        print("示例有、config.toml 没有（用了代码缺省）：", ", ".join(r["missing"]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
