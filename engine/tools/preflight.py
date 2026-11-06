# -*- coding: utf-8 -*-
"""推演启动前的环境检查（在容器里跑，只用标准库）：这台机器现在能不能满足这次推演。每项给 PASS / WARN / FAIL，有 FAIL 返回非 0，start.sh 就不会启动。

检查：规则文件和名称齐全、区域目录和数据文件齐全且和 manifest 的 sha256 一致、区域登记（region.toml）、雨型（要的历时都有）、
      工作目录（路径无中文、可写、剩余空间）、NAS（可写、剩余空间、目录约定）、CPU / 内存 够不够并行这么多个算例。
用法：python tools/preflight.py --region RDA --scenarios S33D1,S61D24 [--work /work] [--nas /nas] [--state L1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[tuple[str, str, str]] = []
REGION_FILES = ["region.toml", "domain.geojson", "network.gpkg", "dtm_5m.tif", "catchments.gpkg", "aecom_manholes.gpkg", "catchment_outlets.csv",
                "makani.gpkg", "makani_points.gpkg", "aecom_elements.json", "aecom_exchange_nodes.csv", "manifest.json"]
# v2 数据包（region.toml [network] format = "v2"，tools/region_pack_v2.py）：管 / 井 / 泵等在 network_v2.gpkg，泵与湿井取自数据里的字段，不再要 network.gpkg / aecom_elements.json
REGION_FILES_V2 = ["region.toml", "domain.geojson", "network_v2.gpkg", "manifest_v2.json", "dtm_5m.tif", "catchments.gpkg", "aecom_manholes.gpkg", "catchment_outlets.csv",
                   "makani.gpkg", "aecom_exchange_nodes.csv", "dem_edits.json"]
STATES = ("L1", "L2", "L2planned")


def add(level: str, name: str, detail: str = ""):
    RESULTS.append((level, name, detail))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def free_gb(p: Path) -> float:
    return shutil.disk_usage(p).free / 1e9


def writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / f".preflight_{os.getpid()}"
        t.write_text("x"); t.unlink()
        return True
    except OSError:
        return False


def mem_available_gb() -> float | None:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) / 1048576
    except OSError:
        return None
    return None


def parse_scn(tag: str):
    m = re.fullmatch(r"S(\d+(?:\.\d+)?)D(\d+)", tag)
    return (float(m.group(1)), int(m.group(2))) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--scenarios", default="", help="S33D1,S61D24,…（缺省只检查数据）")
    ap.add_argument("--state", default="L1", help="没有 @态 的场景用的态")
    ap.add_argument("--allow-unmodeled", action="store_true", help="L2 / L2planned 里有缺参数建不了的设施时也照跑")
    ap.add_argument("--work", default=os.environ.get("SWBRAIN_SWMM_WORK", "/work"))
    ap.add_argument("--nas", default="/nas")
    ap.add_argument("--server", default=None, help="servers.toml 里的 id；缺省按主机名 / 核数匹配")
    a = ap.parse_args()

    # ---- 规则与登记
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import model_rules as MR
        import run_params as RP
        rules = MR.load()
        add("PASS", "公共规则文件", f"{rules['meta']['series']} 系列 {rules['meta']['version']}")
        miss = RP.missing_labels(rules)
        add("PASS" if not miss else "FAIL", "规则名称齐全（页面「推演参数」用）", "" if not miss else f"缺 {len(miss)} 项：{miss[:3]}")
    except Exception as e:  # noqa: BLE001
        add("FAIL", "公共规则文件", f"{type(e).__name__}: {e}")
        rules = None
    st = rules["states"] if rules else {}
    toks = [t.strip() for t in a.scenarios.split(",") if t.strip()]
    tstate = {t: (t.partition("@")[2] or a.state) for t in toks} or {"（无场景）": a.state}
    for t, stt in tstate.items():
        if stt not in STATES:
            add("FAIL", f"态 {stt}（{t}）", "只认 L1 / L2 / L2planned")
    used = sorted({v for v in tstate.values() if v in STATES}, key=STATES.index)
    add("PASS", "推演态（按数据 ASSET_STAGE）", "；".join(f"{x}：{ {'L1': 'Existing', 'L2': 'Existing + Temporary', 'L2planned': 'Existing + Temporary + Planned'}[x] }" for x in used) or a.state)
    if any(x != "L1" for x in used):
        add("WARN", "L2 / L2planned 的设施数据",
            "临时泵（13 个点）缺流量 / 扬程 / 曲线，Geocell（23 个）缺进出口连接和流量，Planned 泵 PMP-21 缺曲线和启停水位：这些设施不放进模型（不生效），照常跑；"
            "页面设施图层里标「未建模（数据缺参数）」，点 L2 / L2planned 能看到这一态新增的设备")

    # ---- 区域
    rd = ROOT / "data" / "regions" / a.region
    if not rd.is_dir():
        add("FAIL", f"区域目录 data/regions/{a.region}", "不存在（先 scripts/new_region.sh 建）")
    else:
        v2 = False
        try:
            v2 = tomllib.loads((rd / "region.toml").read_text(encoding="utf-8")).get("network", {}).get("format") == "v2"
        except Exception:  # noqa: BLE001
            pass
        for f in (REGION_FILES_V2 if v2 else REGION_FILES):
            p = rd / f
            add("PASS" if p.is_file() else "FAIL", f"区域文件 {f}", "" if p.is_file() else "缺（tools/region_pack.py 生成）")
        try:
            reg = tomllib.loads((rd / "region.toml").read_text(encoding="utf-8"))
            need = ([("region", "id"), ("region", "domain"), ("inputs", "catchments"), ("inputs", "manholes")] if v2 else
                    [("region", "id"), ("region", "domain"), ("network", "outfalls"), ("inputs", "catchments"), ("inputs", "manholes"), ("inputs", "aecom_inp")])
            bad = [f"{s}.{k}" for s, k in need if not reg.get(s, {}).get(k)]
            add("PASS" if not bad else "FAIL", "region.toml 必填项", "" if not bad else f"缺：{bad}")
            g = reg.get("grid", {}).get("res_m")
            add("PASS" if g else "WARN", "二维网格精度（region.toml [grid] res_m）", f"{g} m（run_region --grid 缺省 15；成果里标注）" if g else "没写，成果按 --grid 参数标注")
            if reg["region"]["id"] != a.region:
                add("FAIL", "region.toml 的 id", f"{reg['region']['id']} ≠ 目录名 {a.region}")
        except Exception as e:  # noqa: BLE001
            add("FAIL", "region.toml", f"{type(e).__name__}: {e}")
        mf = rd / "manifest.json"
        if mf.is_file():
            files = json.loads(mf.read_text(encoding="utf-8")).get("files", {})
            changed = [k for k, v in files.items() if v.get("sha256") and (rd / k).is_file() and sha(rd / k) != v["sha256"]]
            add("PASS" if not changed else "WARN", "数据文件与 manifest 一致（sha256）", "" if not changed else f"被改过：{changed}")
        far = None
        try:
            import csv
            far = sum(1 for r in csv.DictReader(open(rd / "catchment_outlets.csv", encoding="utf-8")) if r.get("flag") == "far")
        except OSError:
            pass
        if far:
            add("WARN", "子汇水区偏远出口", f"{far} 个出口距离 GDB 节点超过阈值，按最近节点挂接，会列入页面「推演参数」待核对")

    # ---- 雨型
    common = ROOT / "data" / "common" / "rain_mussafah.json"
    if a.scenarios:
        if not common.is_file():
            add("FAIL", "雨型 data/common/rain_mussafah.json", "缺（tools/region_pack.py --common）")
        else:
            pick = json.loads(common.read_text(encoding="utf-8")).get("scenario_pick", {})
            for tag in a.scenarios.split(","):
                p = parse_scn(tag.strip().partition("@")[0])
                if not p:
                    add("FAIL", f"场景 {tag}", "格式应为 S<雨量mm>D<历时h>，如 S61D24")
                else:
                    add("PASS" if f"{p[1]}h" in pick else "FAIL", f"场景 {tag} 的雨型", f"{p[0]} mm / {p[1]} h" if f"{p[1]}h" in pick else f"雨型里没有 {p[1]} h 的曲线")

    # ---- 工作目录与 NAS
    w, n = Path(a.work), Path(a.nas)
    chk = tomllib.loads((ROOT / "config" / "servers.toml").read_text(encoding="utf-8")) if (ROOT / "config" / "servers.toml").is_file() else {}
    th = chk.get("check", {})
    if re.search(r"[一-鿿]", str(w)):
        add("FAIL", "工作目录路径", f"{w} 含中文（ANUGA / SWMM 不支持）")
    for label, d, key in (("工作目录", w, "min_free_gb_work"), ("NAS", n, "min_free_gb_nas")):
        if not d.exists():
            add("FAIL", f"{label} {d}", "不存在（没映射 / NAS 没挂上？）")
        elif not writable(d):
            add("FAIL", f"{label} {d}", "写不进去（权限？）")
        else:
            g, lim = free_gb(d), float(th.get(key, 50))
            add("PASS" if g >= lim else "FAIL", f"{label}剩余空间", f"{g:.0f} GB（要求 ≥ {lim:.0f} GB）")
    if n.exists() and writable(n):
        lay = chk.get("nas", {})
        for k in ("result", "deliveries", "shp_dat", "jobs"):
            (n / lay.get(k, k)).mkdir(parents=True, exist_ok=True)
        add("PASS", "NAS 目录约定", " / ".join(lay.get(k, k) for k in ("result", "deliveries", "shp_dat", "jobs")))

    # ---- CPU / 内存 / 并行
    cores = os.cpu_count() or 0
    d = chk.get("defaults", {})
    tpr, rsv = int(d.get("threads_per_run", 5)), int(d.get("reserve_cores", 4))
    slots = max(0, (cores - rsv) // tpr)
    n_scn = len([s for s in a.scenarios.split(",") if s.strip()]) if a.scenarios else 0
    add("PASS" if slots >= 1 else "FAIL", "CPU", f"{cores} 核，每个算例 {tpr} 线程、留 {rsv} 核 → 可同时跑 {slots} 个")
    if n_scn:
        add("PASS" if slots >= n_scn else "WARN", "并行度", f"{n_scn} 个场景，这台同时能跑 {slots} 个" + ("" if slots >= n_scn else "，其余排队（或分给别的服务器）"))
    mem = mem_available_gb()
    need_gb = float(th.get("min_free_ram_gb", 8)) * max(1, min(n_scn or 1, slots))
    if mem is not None:
        add("PASS" if mem >= need_gb else "WARN", "可用内存", f"{mem:.0f} GB（按 {min(n_scn or 1, slots)} 个并行 × {th.get('min_free_ram_gb', 8)} GB 预留 = {need_gb:.0f} GB）")

    # ---- 输出
    width = max(len(n_) for _, n_, _ in RESULTS)
    for lv, name, det in RESULTS:
        print(f"[{lv:4s}] {name:<{width}}  {det}")
    nf = sum(1 for lv, *_ in RESULTS if lv == "FAIL"); nw = sum(1 for lv, *_ in RESULTS if lv == "WARN")
    print(f"\n检查结果：{len(RESULTS) - nf - nw} 项通过，{nw} 项警告，{nf} 项失败 → " + ("可以启动" if nf == 0 else "不能启动"))
    return 1 if nf else 0


if __name__ == "__main__":
    raise SystemExit(main())
