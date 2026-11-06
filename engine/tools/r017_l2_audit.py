"""result-017 的 L2 / L2+planned 核查（2026-10-20）：拿客户原始资料对 result-017 的塘、泵站和客户方案版本。

  python tools/r017_l2_audit.py ponds  [--r017 D:/swbrain_work/r017/result-017]
  python tools/r017_l2_audit.py pumps  [--r017 ...]
  python tools/r017_l2_audit.py client [--test <客户数据>/test]        # 解析 SewerGEMS 导出，几分钟
  python tools/r017_l2_audit.py all

  ponds   result-017 每个 L2 .inp 里的 PND_* 塘（坐标取 [COORDINATES]，库容取 L2 耦合运行记录）
          对 Parsons 改造后模型的 PO 塘（Parsons_Model Data Export/26092026/*/*pond*.shp）
          和已建成塘（Parsons_Model Data Export/Existing Storage/*/*.shp）：塘坐标 50 m 内有面 = 对上；
          另查每个计算域里有没有已建成塘、L2 塘里库容相同的重复塘
  pumps   result-017 各区 common/existing_pumps.json 与 L1 .inp 的 [PUMPS]
  client  客户 SewerGEMS 导出（RD-A/C/F/G/I）每个 .inp 的蓄水 / 泵 / 出水口（按网结构分组）和 .txt 的流量连续性；
          Parsons SHP 模型（Musaffah_RD09 / RB-01 / RB3）的塘、泵、出水口个数
结果写 out/r017_audit/<子命令>.json，屏幕打印摘要。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "r017_audit"
R017 = Path("D:/swbrain_work/r017/result-017")
sys.path.insert(0, str(ROOT))
from swbrain import client_data  # noqa: E402
TEST = client_data.TEST
PARSONS = Path("D:/阿布扎比/Parsons_Model Data Export")
MATCH_M = 50.0
REGIONS = ["MBZ", "RB-1", "RB-3", "RD-A", "RD-C", "RD-F", "RD-G", "RD-I",
           "MI-8", "RD-2", "RD-14", "YAS_OUTFALL", "RBE1-OF1", "Al_Riyadh_ponds", "Qasr_Al_Bahr-1"]
SG = ["RD-A", "RD-C", "RD-F", "RD-G", "RD-I"]
SHP = {"Musaffah_RD09": ("Ponds", "Pumps", "Outfalls"), "RB-01": ("Ponds", "Pumps", "Outfalls"), "RB3": ("POND", "Pump", "OF")}
SHP_DIR = {"Musaffah_RD09": "Musaffah_RD09_testzip版"}   # 10-30 重下的 Parsons/Musaffah_RD09 是 10-08 的另一版（5,098 管 / 28 出口）；原结果用 test.zip 版
HA_M = 1e4            # SWMM 报告 hectare-m → m³


def J(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def rdir(r017, reg):
    return Path(r017) / f"{reg}_61mm1h_30m"


def inp_section(path, name):
    """逐行读 .inp 里一节（客户 .inp 上百 MB，不整篇读）"""
    rows, on = [], False
    with open(path, encoding="utf-8", errors="ignore") as f:
        for ln in f:
            s = ln.strip()
            if s.startswith("["):
                if on:
                    break
                on = s.upper() == f"[{name}]"
                continue
            if on and s and not s.startswith(";"):
                rows.append(s.split())
    return rows


# ------------------------------------------------------------------ 塘
def ref_ponds():
    import geopandas as gpd
    import pandas as pd
    import pyogrio
    parts = []
    for f in glob.glob(str(PARSONS / "26092026" / "*" / "*.shp")):
        if "pond" in os.path.basename(f).lower():
            g = pyogrio.read_dataframe(f)
            parts.append(pd.DataFrame({"geometry": list(g.geometry), "src": "Parsons方案:" + Path(f).parent.name}))
    for f in glob.glob(str(PARSONS / "Existing Storage" / "*" / "*.shp")):
        g = pyogrio.read_dataframe(f)
        parts.append(pd.DataFrame({"geometry": list(g.geometry), "src": "已建成:" + Path(f).parent.name}))
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=32640)


def ponds(a):
    import geopandas as gpd
    from shapely.geometry import Point
    S = ref_ponds()
    print(f"参照塘 {len(S)} 个：", S.src.value_counts().to_dict())
    res = {}
    for reg in REGIONS:
        d = rdir(a.r017, reg)
        inp = glob.glob(str(d / "L2_built_storage" / "1d" / "*.inp"))
        if not inp:
            continue
        xy = {r[0]: Point(float(r[1]), float(r[2])) for r in inp_section(inp[0], "COORDINATES") if r[0].startswith("PND_")}
        log = J(glob.glob(str(d / "L2_built_storage" / "2d" / "*.json"))[0]).get("ponds", [])
        vol = {"PND_" + p["unitid"]: p.get("vol_design_m3") for p in log}
        listed = [p.get("unitid") for p in J(d / "common" / "built_ponds.json").get("ponds", [])]
        dom = gpd.read_file(d / "common" / "domain.geojson").to_crs(32640).union_all()
        ex_in = sorted(set(S.src[[g.intersects(dom) for g in S.geometry]].pipe(lambda s: s[s.str.startswith("已建成")])))
        rows = []
        for k, p in xy.items():
            dist = S.geometry.distance(p)
            hits = sorted(set(S.src[dist <= MATCH_M]))
            i = dist.idxmin()
            rows.append({"node": k, "vol_m3": vol.get(k), "match": hits,
                         "nearest": None if hits else {"src": S.src[i], "dist_m": round(float(dist[i]))}})
        by_vol = {}
        for r in rows:
            by_vol.setdefault(r["vol_m3"], []).append(r["node"])
        dup = {str(v): ns for v, ns in by_vol.items() if v and len(ns) > 1}
        kind = {}
        for r in rows:
            t = r["match"][0].split(":")[0] if r["match"] else "对不上"
            kind[t] = kind.get(t, 0) + 1
        res[reg] = {"listed_in_built_ponds_json": len(listed), "in_L2_inp": len(rows), "kind": kind,
                    "existing_ponds_in_domain": ex_in, "same_volume_groups": dup, "ponds": rows}
        print(f"{reg:16} 清单 {len(listed):>2} / L2 实跑 {len(rows):>2}  {kind}  计算域内已建成塘图层：{ex_in or '无'}"
              + (f"  库容相同的塘：{dup}" if dup else ""))
    return res


# ------------------------------------------------------------------ 泵站
def pumps(a):
    res = {}
    for reg in REGIONS:
        d = rdir(a.r017, reg)
        f = d / "common" / "existing_pumps.json"
        if not f.exists():
            continue
        inp = glob.glob(str(d / "L1_existing_network" / "1d" / "*.inp"))[0]
        built = {r[0] for r in inp_section(inp, "PUMPS")}
        st = []
        for s in J(f)["stations"]:
            node = "PS_" + str(s["ps_id"])
            st.append({"ps_id": s["ps_id"], "unitid": s["unitid"], "comment": s.get("comment"), "q_src": s.get("q_src"),
                       "total_lps": (s["n_pumps"] or 0) * (s["q_each_lps"] or 0) if s.get("n_pumps") is not None else None,
                       "modelled": node in built})
        res[reg] = st
        for s in st:
            print(f"{reg:16} {str(s['ps_id']):10} {'建了' if s['modelled'] else '没建':4} 合计 {s['total_lps']} L/s  {s['comment']}  [{s['q_src']}]")
    return res


# ------------------------------------------------------------------ 客户资料
def parse_client_inp(p):
    keep = {"STORAGE": [], "PUMPS": [], "OUTFALLS": []}
    cnt, h, sec = {}, hashlib.md5(), None
    with open(p, encoding="latin-1") as f:
        for ln in f:
            s = ln.strip()
            if s.startswith("["):
                sec = s.strip("[]").upper(); continue
            if not s or s.startswith(";"):
                continue
            if sec in ("JUNCTIONS", "CONDUITS", "STORAGE", "PUMPS", "OUTFALLS"):
                cnt[sec] = cnt.get(sec, 0) + 1
                h.update(s.encode())
            if sec in keep:
                keep[sec].append(" ".join(s.split()))
    return {"count": cnt, "net_hash": h.hexdigest()[:8], **{k.lower(): v for k, v in keep.items()}}


def parse_client_rpt(p):
    t = Path(p).read_text(encoding="latin-1")
    if "Flow Routing Continuity" not in t:
        return None
    fr = t.split("Flow Routing Continuity")[1][:1800]

    def g(k):
        m = re.search(re.escape(k) + r"\s*\.*\s*([\d.\-]+)\s+([\d.\-]+)", fr)
        return round(float(m.group(1)) * HA_M) if m else None
    err = re.search(r"Continuity Error \(%\)\s*\.*\s*([\d.\-]+)", fr)
    return {"external_inflow_m3": g("External Inflow"), "flooding_m3": g("Flooding Loss"),
            "external_outflow_m3": g("External Outflow"), "continuity_err_pct": float(err.group(1)) if err else None}


def client(a):
    import pyogrio
    res = {"sewergems": {}, "parsons_shp": {}}
    for reg in SG:
        base = client_data.model(reg) if Path(a.test) == TEST else Path(a.test) / reg / reg
        groups = {}
        for p in sorted(glob.glob(str(base / "**" / "*.inp"), recursive=True)):
            run = Path(p).stem.split("_")[-1]
            i = parse_client_inp(p)
            rpt = Path(p).with_suffix(".txt")
            g = groups.setdefault(i["net_hash"], {**{k: i[k] for k in ("count", "storage", "pumps", "outfalls")}, "runs": {}})
            g["runs"][run] = parse_client_rpt(rpt) if rpt.exists() else None
            print(f"{reg} {run} 网 {i['net_hash']} {i['count']}", flush=True)
        res["sewergems"][reg] = groups
    for reg, (po, pu, of) in SHP.items():
        d = client_data.model(SHP_DIR.get(reg, reg)) if Path(a.test) == TEST else Path(a.test) / reg
        r = {k: len(pyogrio.read_dataframe(d / f"{n}.shp", read_geometry=False)) for k, n in (("ponds", po), ("pumps", pu), ("outfalls", of))}
        res["parsons_shp"][reg] = r
        print(reg, r)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["ponds", "pumps", "client", "all"])
    ap.add_argument("--r017", default=str(R017))
    ap.add_argument("--test", default=str(TEST))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for w in (["ponds", "pumps", "client"] if a.what == "all" else [a.what]):
        r = globals()[w](a)
        (OUT / f"{w}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {OUT / (w + '.json')}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
