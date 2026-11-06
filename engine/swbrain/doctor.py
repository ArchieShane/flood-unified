# -*- coding: utf-8 -*-
"""安装自检：这台机器能跑本系统的哪几种用法、还缺什么（2026-09-30，交付给别人时先跑它）。

    python -m swbrain.doctor            # 全部检查（数据库连接各试 5 秒）
    python -m swbrain.doctor --no-db    # 不试数据库
    python -m swbrain.doctor --json     # 结构化输出（给脚本 / 管理端用）

只读：不写任何文件、不改配置、不起计算。口令与密钥只报「有 / 没有」，从不打印内容。
四种用法与各自的前提（README「先看这里」）：
  快速验证      Python 依赖 + docker 镜像 + 纯英文工作目录（不要客户数据、数据库、密钥）
  一二维计算    快速验证的全部 + config.toml [data] / [terrain] / [hotspots] 指向的客户数据都在
  完整系统      config.toml 的 [source.*] 客户库能连上（重跑 swbrain.run 要）+ [output] 本地库；
                或者已有运行产物（out/state/、console/data/summary.json）——控制台服务启动读的是它们
  大模型规划    DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY 之一 + deepseek-harness-sdk（没有就用规则规划器）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import platform
import re
import shutil
import socket
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "anuga-swmm:1.0"
PKGS = ["numpy", "pandas", "scipy", "shapely", "geopandas", "pyogrio", "pyproj", "rasterio", "networkx", "pyarrow", "psycopg", "openpyxl",
        "lxml", "ortools", "docx", "pyflwdir", "yaml", "swmm.toolkit", "pymupdf", "matplotlib", "websocket"]
DATA = [  # (配置位置, 说明, 哪种用法要)
    ("data.gdb_zip", "客户雨水管网 GDB（压缩包）", "一二维"),
    ("data.gdb_dir", "客户雨水管网 GDB（解压版）", "一二维"),
    ("terrain.dtm_path", "5 m DTM", "一二维 / 完整系统（地形功能）"),
    ("data.hotspot_xlsx_dir", "客户热点工作簿目录", "一二维"),
    ("hotspots.coords_kmz", "热点坐标修正 KMZ", "一二维 / 完整系统"),
    ("data.client_models_zip", "客户 22 个水力模型 zip", "一二维"),
    ("data.client_swmm_export", "客户 SWMM 导出目录（含 Mussafah_00.inp）", "一二维"),
    ("data.parsons_root", "Parsons 交付解压目录", "一二维"),
    ("dmt_model.dir", "客户水力模型导出（Mussafah_00 / RB3 / Shaliela 三态页）", "完整系统（三态页）"),
]
PRODUCTS = [("out/state/es.parquet", "管网状态（swbrain.run）"), ("out/state/cs.parquet", "汇水区状态（swbrain.run）"),
            ("console/data/summary.json", "控制台总览（swbrain.run）"), ("console/data/hotspots.geojson", "客户热点图层")]


def _get(cfg, dotted):
    d = cfg
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _placeholder(v) -> bool:
    return not v or (isinstance(v, str) and (v.startswith("<") or v in ("CHANGE_ME",) or "_DB_HOST" in v))


def check(db: bool = True) -> dict:
    R: list[dict] = []

    def add(group, item, ok, detail="", fix="", level="err"):
        R.append({"group": group, "item": item, "ok": bool(ok), "detail": detail, "fix": fix, "level": "ok" if ok else level})

    # ---- 环境
    add("环境", "Python ≥ 3.11", sys.version_info >= (3, 11), platform.python_version(), "装 Python 3.11 以上（开发机 3.14）")
    miss = [m for m in PKGS if importlib.util.find_spec(m.split(".")[0]) is None or (m == "swmm.toolkit" and importlib.util.find_spec("swmm") is None)]
    add("环境", "Python 依赖", not miss, "齐" if not miss else "缺 " + ", ".join(miss), "pip install -r requirements.lock.txt（或 requirements.txt）")
    dk = shutil.which("docker")
    img = False
    if dk:
        try:
            img = subprocess.run([dk, "image", "inspect", IMAGE], capture_output=True, timeout=30).returncode == 0
        except Exception:
            img = False
    add("环境", "docker", bool(dk), dk or "没装", "装 Docker Desktop（Windows / Mac）或 docker engine（Linux）")
    add("环境", f"计算镜像 {IMAGE}", img, "有" if img else "没有", "docker build -t anuga-swmm:1.0 docker/anuga-swmm")
    wk = os.environ.get("SWBRAIN_SWMM_WORK")
    add("环境", "SWBRAIN_SWMM_WORK（一二维工作目录）", bool(wk) and not any(ord(c) > 127 for c in (wk or "")) and " " not in (wk or ""),
        wk or "没设（脚本退回 D:\\swbrain_work\\swmm）", "设成纯英文、无空格的目录，如 D:\\swbrain_work\\swmm（SWMM 不认中文路径）；安装脚本会设", level="warn" if not wk else "err")
    # ---- 配置
    cp = ROOT / "config.toml"
    cfg = {}
    if cp.exists():
        try:
            cfg = tomllib.loads(cp.read_text(encoding="utf-8")); add("配置", "config.toml", True, "能读")
        except Exception as ex:
            add("配置", "config.toml", False, f"读不了：{str(ex)[:120]}", "对照 config.example.toml 修")
    else:
        add("配置", "config.toml", False, "没有", "cp config.example.toml config.toml，再按 docs/部署与交付.md 填")
    if cfg:
        try:
            from . import configcheck
            cc = configcheck.check()
            add("配置", "与 config.example.toml 键一致", cc.get("ok", True), "一致" if cc.get("ok", True) else f"多 {len(cc.get('extra', []))} / 缺 {len(cc.get('missing', []))} 个键",
                "python -m swbrain.configcheck --sync", level="warn")
        except Exception as ex:
            add("配置", "与 config.example.toml 键一致", False, str(ex)[:100], "", level="warn")
        sv = cfg.get("serve") or {}
        add("配置", "控制台访问口令 [serve].access_key", bool(str(sv.get("access_key") or "").strip()), "已设" if sv.get("access_key") else "没设（服务只监听本机）",
            "要给局域网用就设一个", level="warn")
        add("配置", "管理员口令 [serve].admin_key", bool(str(sv.get("admin_key") or "").strip()), "已设" if sv.get("admin_key") else "没设（管理端关闭）", "要用管理端就设一个（与访问口令不同）", level="warn")
    # ---- 数据
    for key, desc, use in DATA:
        v = _get(cfg, key)
        if _placeholder(v):
            add("数据", desc, False, f"{key} 没填", f"把你拿到的数据放好，路径填进 config.toml 的 {key}（{use}要）", level="warn")
            continue
        p = pathlib.Path(v)
        add("数据", desc, p.exists(), str(p), f"路径不存在：检查 config.toml 的 {key}（{use}要）", level="warn")
    # ---- 数据库
    for name in ("pipe", "makani", "flood", "liveability"):
        s = _get(cfg, f"source.{name}") or {}
        if _placeholder(s.get("host")) or _placeholder(s.get("password")):
            add("数据库", f"客户库 {name}", False, "没填", f"[source.{name}] 填主机、端口、账号（重跑 swbrain.run 要；只看控制台可以不连）", level="warn"); continue
        if not db:
            add("数据库", f"客户库 {name}", True, "（--no-db 没试）", level="warn"); continue
        add("数据库", f"客户库 {name}", *_try_db(s), level="warn")
    o = cfg.get("output") or {}
    if not _placeholder(o.get("password")) and db:
        add("数据库", "本地输出库 [output]", *_try_db(o), level="warn")
    else:
        add("数据库", "本地输出库 [output]", False, "没填" if _placeholder(o.get("password")) else "（--no-db 没试）", "swbrain.run 写 PostGIS 要；只看控制台可以不连", level="warn")
    # ---- 运行产物（控制台服务启动读这些）
    for rel, desc in PRODUCTS:
        where = "部署包：放进 RUNTIME_DIR 的 out/ 与 console_data/，再 docker compose restart" if os.environ.get("SWBRAIN_SWMM_WORK_HOST") else "解压到仓库根目录"
        add("运行产物", desc, (ROOT / rel).exists(), rel, f"跑 python -m swbrain.run（要客户库），或把你拿到的运行产物{where}", level="warn")
    # ---- 大模型
    keys = [k for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY") if _env_key(k)]
    add("大模型", "密钥", bool(keys), "有 " + "、".join(keys) if keys else "没有（智能体用规则规划器）", "设环境变量 DEEPSEEK_API_KEY（或 OPENAI / ANTHROPIC），不要写进文件", level="warn")
    add("大模型", "deepseek-harness-sdk", importlib.util.find_spec("deepseek_harness") is not None, "", "pip install deepseek-harness-sdk", level="warn")
    # ---- 端口
    port = int(((cfg.get("serve") or {}).get("port")) or 8766)
    busy = _port_busy(port)
    add("端口", f"{port}（控制台）", True, "已被占用（多半是服务已在跑）" if busy else "空闲", level="ok")
    # ---- 结论
    ok = {r["item"]: r["ok"] for r in R}
    env_ok = ok.get("Python ≥ 3.11") and ok.get("Python 依赖") and ok.get(f"计算镜像 {IMAGE}")
    data_ok = all(r["ok"] for r in R if r["group"] == "数据" and "三态页" not in r["item"])
    prod_ok = all(r["ok"] for r in R if r["group"] == "运行产物")
    db_ok = all(r["ok"] for r in R if r["group"] == "数据库")
    modes = {"快速验证": bool(env_ok), "一二维计算": bool(env_ok and data_ok), "完整系统（看控制台）": bool(ok.get("Python 依赖") and ok.get("config.toml") and prod_ok),
             "完整系统（重跑 swbrain.run）": (bool(ok.get("Python 依赖") and db_ok) if db else None), "大模型规划": bool(keys and ok.get("deepseek-harness-sdk"))}
    return {"checks": R, "modes": modes, "platform": f"{platform.system()} {platform.release()}", "python": platform.python_version()}


def _env_key(k: str) -> bool:
    if os.environ.get(k):
        return True
    try:
        from . import envkeys
        return bool(envkeys.get(k))
    except Exception:
        return False


def _try_db(s: dict):
    try:
        import psycopg
        with psycopg.connect(host=s.get("host"), port=s.get("port"), user=s.get("user"), password=s.get("password"), dbname=s.get("dbname"), connect_timeout=5) as c:
            c.execute("select 1")
        return True, f"{s.get('host')}:{s.get('port')}/{s.get('dbname')} 能连", ""
    except Exception as ex:
        msg = re.sub(r"password\S*", "password=***", str(ex).splitlines()[0])[:140]
        return False, f"{s.get('host')}:{s.get('port')}/{s.get('dbname')} 连不上：{msg}", "查网络 / 账号；只看控制台可以不连"


def _port_busy(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="安装自检")
    ap.add_argument("--no-db", action="store_true", help="不试数据库连接")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    r = check(db=not a.no_db)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1)); return 0
    print(f"排水系统安装自检 · {r['platform']} · Python {r['python']}")
    g = None
    for c in r["checks"]:
        if c["group"] != g:
            g = c["group"]; print(f"\n{g}")
        mark = "✓" if c["ok"] else ("✗" if c["level"] == "err" else "·")
        print(f"  {mark} {c['item']}" + (f"：{c['detail']}" if c["detail"] else "") + (f"\n      → {c['fix']}" if not c["ok"] and c["fix"] else ""))
    print("\n能用的用法")
    for m, v in r["modes"].items():
        print(f"  {'?' if v is None else '✓' if v else '✗'} {m}" + ("（--no-db：没试数据库）" if v is None else ""))
    print("\n✗ 是必须修的；· 是某种用法才要的（见上面「能用的用法」）。详见 docs/部署与交付.md")
    return 0 if r["modes"]["快速验证"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
