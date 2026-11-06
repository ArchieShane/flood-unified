# -*- coding: utf-8 -*-
"""打部署包：对方拿到后装好 Docker 就能跑，不用装 Python、不用构建（2026-09-30；2026-10-03 改为纯离线 Linux 服务器）。

    python tools/make_release.py                    # 版本号 = 日期 + git 短哈希；输出 dist/swbrain-<版本>/
    python tools/make_release.py --version 1.0.0 --zip
    python tools/make_release.py --skip-build       # 镜像已构建好时只打包
    python tools/make_release.py --with-data        # 同时把要上传的全部数据按目录结构「硬链接」进包目录（不占双份磁盘，须同一磁盘分区）

前提：已跑过 tools/make_db_snapshot.py（生成 docker/db/dumps/*.dump）。

包里有：
  images/swbrain-app-<版本>.tar   系统服务镜像（代码 + 依赖 + 数据快照种子 /app/seed，不含口令；docker/app/Dockerfile）
  images/swbrain-db-<版本>.tar    离线数据库镜像（pipe / makani / liveability 用到的表已烤进去；docker/db/Dockerfile）
  images/anuga-swmm-1.0.tar        一二维计算镜像（docker/anuga-swmm/Dockerfile）
  docker-compose.yml · .env.example · start.sh · rebuild-state.sh      启动、换 DTM 后离线重算
  config.package.toml              配置模板：数据路径已按固定目录结构写好（容器里 /data/…）；数据库指向 compose 内的 db，口令由 start.sh 填
  部署说明.md                      = docs/部署与交付.md
  manifest.json                    每个文件的大小与 sha256、版本、git 提交、构建时间；上传后用 `sha256sum -c`/脚本核对
  （--with-data）data/ work/ runtime/   全部要上传的数据，目录结构 = 宿主机上 DATA_DIR / WORK_DIR / RUNTIME_DIR 的内容
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "docker" / "deploy"
SEED = ROOT / "docker" / "app" / "seed"
DB_DIR = ROOT / "docker" / "db"
# 部署包里容器内的路径（固定目录结构，见下面 DATA_LAYOUT）
PKG_PATHS = {
    ("data", "gdb_zip"): "/data/gdb/DMT_StormWater_AbuDhabi_Processed.gdb.zip",
    ("data", "gdb_dir"): "/data/gdb/DMT_StormWater_AbuDhabi_Processed.gdb",
    ("data", "hotspot_xlsx_dir"): "/data/hotspots/workbooks",
    ("data", "client_models_zip"): "/data/client_models/OneDrive_1_9-26-2026.zip",
    ("data", "client_swmm_export"): "/data/hydraulic_model_export/SWMM_Export",
    ("data", "parsons_root"): "/data/parsons",
    ("terrain", "dtm_path"): "/data/dtm/AUH_DTM_5m_Z40.TIF",
    ("hotspots", "coords_kmz"): "/data/hotspots/506 Hot Spots AUH.kmz",
    ("dmt_model", "dir"): "/data/hydraulic_model_export",
    # 离线数据库：compose 内网的 db 服务；口令占位由 start.sh 替换成 .env 里的 DB_PASSWORD
    ("source.pipe", "host"): "db", ("source.makani", "host"): "db", ("source.liveability", "host"): "db",
    ("source.pipe", "password"): "__DB_PASSWORD__", ("source.makani", "password"): "__DB_PASSWORD__",
    ("source.liveability", "password"): "__DB_PASSWORD__",
}
PKG_PORTS = {("source.pipe", "port"): 5432, ("source.makani", "port"): 5432, ("source.liveability", "port"): 5432}

# 要上传的数据：(本机来源, 包内路径)。来源相对 ROOT，或绝对路径；包内路径相对包目录。
# 目录结构 = 部署后宿主机 DATA_DIR / WORK_DIR / RUNTIME_DIR 里的样子（见 .env.example）。
WORK_SRC = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"C:\freedo\swbrain_work\swmm"))
DATA_LAYOUT = [
    # --- DATA_DIR（只读挂进容器 /data）---
    ("data/drainage_network_gdb.zip", "data/gdb/DMT_StormWater_AbuDhabi_Processed.gdb.zip"),
    ("data/drainage_network_gdb/DMT_StormWater_AbuDhabi_Processed.gdb", "data/gdb/DMT_StormWater_AbuDhabi_Processed.gdb"),
    ("data/DEM", "data/dtm"),                                       # 5 m DTM；DEM.zip 是它的压缩副本，不上传。换更高精度地形就替换这里的文件
    ("data/20260909 ADM Stormwater Hotspot Analysis v4.4.xlsx", "data/hotspots/workbooks/20260909 ADM Stormwater Hotspot Analysis v4.4.xlsx"),
    ("data/20260909_AAM_Stormwater_Hotspot_Analysis_v4.2.xlsx", "data/hotspots/workbooks/20260909_AAM_Stormwater_Hotspot_Analysis_v4.2.xlsx"),
    ("data/20260909_DRM_Stormwater_Hotspot_Analysis_v4.2.xlsx", "data/hotspots/workbooks/20260909_DRM_Stormwater_Hotspot_Analysis_v4.2.xlsx"),
    ("data/FW__Hot_Spots_506", "data/hotspots"),
    ("data/OneDrive_1_9-26-2026.zip", "data/client_models/OneDrive_1_9-26-2026.zip"),
    ("data/OneDrive_1_9-26-2026", "data/client_models/OneDrive_1_9-26-2026"),
    ("data/Mussafah_00.inp", "data/hydraulic_model_export/SWMM_Export/Mussafah_00.inp"),
    ("data/Parsons_Model Data Export", "data/parsons"),
    # --- RUNTIME_DIR/out/交付：已有的一二维交付成果（tools/city_*.py 等从 out/交付/<名> 读）---
    ("data/全市_61mm2h", "runtime/out/交付/全市_61mm2h"),
    ("data/Mussafah_00_61mm2h", "runtime/out/交付/Mussafah_00_61mm2h"),
    ("out/交付", "runtime/out/交付"),
    # --- WORK_DIR：一二维工作目录（已有的模型与计算结果，流程可续算）---
    (str(WORK_SRC), "work"),
]


def sh(cmd, **kw):
    print("  $ " + " ".join(map(str, cmd)), flush=True)
    r = subprocess.run(cmd, **kw)
    if r.returncode:
        raise SystemExit(f"失败（退出码 {r.returncode}）")
    return r


def package_config() -> str:
    """config.example.toml → 部署包配置：只改 PKG_PATHS / PKG_PORTS 里的键（按所在节定位），其余原样。"""
    out, sec = [], None
    for ln in (ROOT / "config.example.toml").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", ln)
        if m:
            sec = m.group(1).strip()
        else:
            k = re.match(r'^(\s*)([A-Za-z_]\w*)(\s*=\s*)("[^"]*"|\d+)(.*)$', ln)
            if k and (sec, k.group(2)) in PKG_PATHS and k.group(4).startswith('"'):
                ln = f'{k.group(1)}{k.group(2)}{k.group(3)}"{PKG_PATHS[(sec, k.group(2))]}"{k.group(5)}'
            elif k and (sec, k.group(2)) in PKG_PORTS and not k.group(4).startswith('"'):
                ln = f"{k.group(1)}{k.group(2)}{k.group(3)}{PKG_PORTS[(sec, k.group(2))]}{k.group(5)}"
        out.append(ln)
    txt = "\n".join(out) + "\n"
    miss = [f"{s}.{k}" for (s, k), v in PKG_PATHS.items() if f'"{v}"' not in txt]
    miss += [f"{s}.{k}" for (s, k), v in PKG_PORTS.items() if not re.search(rf"^\s*{k}\s*=\s*{v}\b", txt, re.M)]
    if miss:
        raise SystemExit(f"config.example.toml 里没找到这些键：{miss}")
    return ("# 部署包配置（tools/make_release.py 由 config.example.toml 生成）：数据路径 = 容器内 /data/…（宿主机 DATA_DIR 挂进来）；\n"
            "# 数据库 = compose 内的 db 服务（离线快照）。访问口令 [serve].access_key / admin_key 部署后自己填，没填管理端关闭。\n") + txt


# 数据快照种子：swbrain.run 的运行产物。从客户库抽出来的东西，打进服务镜像（/app/seed），首次启动补进宿主机映射目录
SEED_SKIP_OUT = {"交付", "cache", "logs", "demo"}               # 交付成果走上传目录；cache 按代码+配置+状态指纹，换了配置就失效，现场重建
SEED_SKIP_EXCHANGE = {"examples", "schemas"}                    # 契约样例与模式已在代码仓库里（镜像的 exchange_seed）


def stage_seed():
    if SEED.exists():
        shutil.rmtree(SEED)
    (SEED / "out").mkdir(parents=True)
    n = 0
    for src, dst in ((ROOT / "console" / "data", SEED / "console_data"),):
        shutil.copytree(src, dst); n += 1
    for p in (ROOT / "out").iterdir():
        if p.name in SEED_SKIP_OUT:
            continue
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, SEED / "out" / p.name)
    ex = SEED / "exchange"; ex.mkdir()
    for p in (ROOT / "exchange").iterdir():
        if p.name in SEED_SKIP_EXCHANGE:
            continue
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, ex / p.name)
    mf = SEED / "out" / "state" / "manifest.json"                # 源库内网地址不带出去
    if mf.exists():
        j = json.loads(mf.read_text(encoding="utf-8"))
        for v in (j.get("sources") or {}).values():
            v["host"] = "offline-snapshot"
        mf.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    sz = sum(f.stat().st_size for f in SEED.rglob("*") if f.is_file())
    print(f"数据快照种子 → {SEED}（{sz / 1e6:.0f} MB）")


def link_tree(src: pathlib.Path, dst: pathlib.Path) -> tuple[int, int]:
    """src → dst 硬链接（同一磁盘分区不占额外空间）；跨分区时退回复制。返回 (文件数, 字节数)。"""
    if src.is_file():
        files = [(src, dst)]
    else:
        files = [(f, dst / f.relative_to(src)) for f in src.rglob("*") if f.is_file()]
    nb = 0
    for f, d in files:
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            continue
        try:
            os.link(f, d)
        except OSError:
            shutil.copy2(f, d)
        nb += f.stat().st_size
    return len(files), nb


def link_data(D: pathlib.Path):
    for rel, to in DATA_LAYOUT:
        s = pathlib.Path(rel) if os.path.isabs(rel) else ROOT / rel
        if not s.exists():
            print(f"  没有：{s}（跳过）"); continue
        n, b = link_tree(s, D / to)
        print(f"  {to:70s} {n:6d} 个文件 {b / 1e9:7.2f} GB")


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="打部署包")
    ap.add_argument("--version", default=None)
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--with-data", action="store_true", help="把要上传的全部数据硬链接进包目录（data/ work/ runtime/）")
    ap.add_argument("--zip", action="store_true", help="另打一个 zip（镜像本身已压缩，zip 省不了多少，主要为了单文件传输）")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args(argv)
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "nogit"
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    ver = a.version or time.strftime("%Y.%m.%d") + "-" + git + ("-dirty" if dirty else "")
    if dirty:
        print(f"注意：工作区有没提交的改动，版本号带 -dirty（{ver}）；正式交付请先提交")
    D = pathlib.Path(a.out) / f"swbrain-{ver}"
    if D.exists():
        shutil.rmtree(D)
    (D / "images").mkdir(parents=True)
    print(f"部署包 → {D}")
    if not a.skip_build:
        missing = [n for n in ("pipe", "makani", "liveability") if not (DB_DIR / "dumps" / f"{n}.dump").exists()]
        if missing:
            raise SystemExit(f"缺数据库转储 docker/db/dumps/{missing}.dump：先跑 python tools/make_db_snapshot.py")
        print("暂存数据快照种子")
        stage_seed()
        print("构建数据库镜像（恢复转储，几分钟）")
        sh(["docker", "build", "-t", f"swbrain-db:{ver}", "docker/db"], cwd=ROOT)
        print("构建系统服务镜像")
        sh(["docker", "build", "--build-context", f"seed={SEED}", "-f", "docker/app/Dockerfile", "-t", f"swbrain-app:{ver}", "."], cwd=ROOT)
        if subprocess.run(["docker", "image", "inspect", "anuga-swmm:1.0"], capture_output=True).returncode:
            print("构建一二维计算镜像")
            sh(["docker", "build", "-t", "anuga-swmm:1.0", "docker/anuga-swmm"], cwd=ROOT)
    print("导出镜像")
    sh(["docker", "save", "-o", str(D / "images" / f"swbrain-app-{ver}.tar"), f"swbrain-app:{ver}"])
    sh(["docker", "save", "-o", str(D / "images" / f"swbrain-db-{ver}.tar"), f"swbrain-db:{ver}"])
    sh(["docker", "save", "-o", str(D / "images" / "anuga-swmm-1.0.tar"), "anuga-swmm:1.0"])
    print("启动文件与说明")
    for f in ("docker-compose.yml", "start.sh", "rebuild-state.sh"):
        shutil.copy2(DEPLOY / f, D / f)
    (D / ".env.example").write_text((DEPLOY / ".env.example").read_text(encoding="utf-8").replace("__VERSION__", ver), encoding="utf-8", newline="\n")
    (D / "config.package.toml").write_text(package_config(), encoding="utf-8", newline="\n")
    shutil.copy2(ROOT / "docs" / "部署与交付.md", D / "部署说明.md")
    (D / "VERSION").write_text(ver + "\n", encoding="utf-8")
    # manifest 先于数据生成：只含启动文件与镜像（数据体积太大，上传后用 rsync/校验工具对一遍即可）
    files = sorted(p for p in D.rglob("*") if p.is_file())
    man = {"version": ver, "git": git, "dirty": dirty, "built": time.strftime("%Y-%m-%d %H:%M:%S"),
           "files": [{"path": p.relative_to(D).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files]}
    (D / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.with_data:
        print("数据（硬链接）")
        link_data(D)
    tot = sum(p.stat().st_size for p in D.rglob("*") if p.is_file())
    print(f"完成：{sum(1 for p in D.rglob('*') if p.is_file())} 个文件，{tot / 1e9:.2f} GB → {D}")
    if a.zip:
        z = shutil.make_archive(str(D), "zip", root_dir=D.parent, base_dir=D.name)
        print(f"zip → {z}（{pathlib.Path(z).stat().st_size / 1e9:.2f} GB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
