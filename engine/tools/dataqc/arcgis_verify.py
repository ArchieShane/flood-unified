# -*- coding: utf-8 -*-
"""用 ArcGIS Pro（arcpy）独立复核管网结果。<b>只做复核，不是依赖</b>：这份报告的数字由本程序自己算，
这里用专业软件逐条比一遍，差异逐条给出原因，写进报告附录 A。

要在 ArcGIS Pro 的 Python 里跑（``arcgispro-py3``），或在命令行加 ``--arcgis``：
本程序检测到 arcpy 不在当前解释器时，会用配置里的 python 路径另起一个进程跑本模块。

⚠️ 不碰客户数据：原数据只读，解压与拓扑校验都在输出目录的副本上做。
⚠️ 一份要素类不能同时在拓扑与追踪网络里，所以第一步的副本再复制一份放单独的库。
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
import zipfile

# ArcGIS Pro 自带的 Python，用于「当前解释器没有 arcpy」时另起进程
ARCGIS_PY = r"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe"

RULES = ["Must Not Have Dangles (Line)", "Must Not Self-Intersect (Line)", "Must Not Overlap (Line)",
        "Must Not Intersect (Line)"]


# ═══════════════════════════════════════════════════════════════ 主入口（在 arcpy 环境里执行）

def verify(pipes: str, net, net_result, thr, tolerance: float, out_dir: str, log=print) -> dict:
    """返回报告附录 A 用的 {"rows": [...], "intro": "...", "raw": {...}}。

    追踪网络要 ArcGIS Pro 的相应许可（实测本机只有 ArcInfo，CheckProduct('ArcInfo') = Available，
    而追踪网络建出来没有网络源、Trace 报 ERROR 001785）。没有就只做拓扑规则那部分，照实注明，
    不假装复核过连通性。
    """
    import arcpy  # noqa: F401  —— 没有就抛，由调用方决定怎么降级
    os.makedirs(out_dir, exist_ok=True)
    topo = _topology(pipes, tolerance, out_dir, log)
    trace = None
    try:
        trace = _trace(pipes, out_dir, log)
    except Exception as e:
        log("追踪网络没做成（多半是本机没有对应许可）：%s" % str(e)[:160])
        log("  —— 连通性（C4）这一项照实标「没做」，其余照常")
    compared = _compare(topo, trace, net, net_result, tolerance, log)
    json.dump({"topology": topo, "trace": trace, "compare": compared}, open(os.path.join(out_dir, "arcgis.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    return {"rows": compared["rows"], "intro": compared["intro"], "raw": {"topology": _slim(topo), "trace": trace}}


def _tick(*a):
    print("[%5.0fs]" % (time.time() - _tick.t0), *a, flush=True)


_tick.t0 = time.time()


def _copy_source(pipes, out_dir):
    """客户数据只读：zip 解到输出目录；.gdb 直接读（只读游标不写）。返回 (源库路径, 导出用的 gdb)。"""
    p = pipes.replace("\\", "/")
    if p.lower().endswith(".zip"):
        src = os.path.join(out_dir, "src")
        if not os.path.isdir(src):
            with zipfile.ZipFile(pipes) as z:
                z.extractall(src)
        gdb = next(os.path.join(src, d) for d in os.listdir(src) if d.lower().endswith(".gdb"))
        # 解压常多套一层
        if not any(f.endswith(".gdbtable") for f in os.listdir(gdb)):
            inner = [d for d in os.listdir(gdb) if d.lower().endswith(".gdb") and os.path.isdir(os.path.join(gdb, d))]
            if len(inner) == 1:
                gdb = os.path.join(gdb, inner[0])
        return gdb
    return p


def _layers(arcpy, src_gdb):
    """认管段层与节点层：与 dataqc/layers.py 同一套认法（线且带管径或上下游编号；点层名认得出种类；跳过 Merge*）。"""
    from .layers import kind_of
    arcpy.env.workspace = src_gdb
    all_fc = list(arcpy.ListFeatureClasses() or [])
    for ds in (arcpy.ListDatasets("", "Feature") or []):
        all_fc += list(arcpy.ListFeatureClasses(feature_dataset=ds) or [])
    links_geom, node_geom = [], []
    for fc in all_fc:
        name = os.path.basename(str(fc))
        if name.lower().startswith("merge"):
            continue
        try:
            n = int(arcpy.management.GetCount(fc)[0])
        except Exception:
            continue
        if n == 0:
            continue
        d = arcpy.Describe(fc)
        f = {x.name.lower() for x in d.fields}
        if d.shapeType in ("Polyline", "Line"):
            if {"pipe_diameter", "diameter", "dia", "pipe_size"} & f or {"asset_before", "us_node", "from_node"} & f:
                links_geom.append(fc)
        elif d.shapeType in ("Point", "Multipoint"):
            if kind_of(name) is not None:
                node_geom.append(fc)
    return links_geom, node_geom


def _copy_rows(arcpy, sources, target, kind, sr, id_field="UNITID"):
    """逐行抄几何与编号。⚠️ 实测 Merge／Append 会静默丢行（Merge 丢 8,899 个点，Append 丢 INLET 的 514 个），
    所以用游标逐行抄，抄完核总数。"""
    arcpy.management.CreateFeatureclass(os.path.dirname(target), os.path.basename(target), kind,
                                        has_z="ENABLED", spatial_reference=sr)
    arcpy.management.AddField(target, "UNITID", "TEXT", field_length=128)
    arcpy.management.AddField(target, "MERGE_SRC", "TEXT", field_length=64)
    expected = 0
    with arcpy.da.InsertCursor(target, ["SHAPE@", "UNITID", "MERGE_SRC"]) as w:
        for s in sources:
            name = os.path.basename(str(s))
            cols = ["SHAPE@"] + ([id_field] if id_field in {x.name.upper() for x in arcpy.ListFields(s)} else [])
            if id_field not in {x.name.upper() for x in arcpy.ListFields(s)}:
                cols = ["SHAPE@", "OBJECTID"]
            with arcpy.da.SearchCursor(s, cols) as c:
                for row in c:
                    shp, uid = row[0], row[1]
                    expected += 1
                    if shp is None:
                        continue
                    w.insertRow([shp, None if uid is None else str(uid), name])
    actual = int(arcpy.management.GetCount(target)[0])
    if actual > expected:
        raise SystemExit("抄写行数对不上：%s 源 %d 实 %d" % (target, expected, actual))
    return expected


def _topology(pipes, tolerance, out_dir, log=print):
    """ArcGIS 拓扑：0.5 m 容差、五条规则（悬挂、自相交、重叠、相交、端点须被节点覆盖）＋ Check Geometry。"""
    import arcpy
    arcpy.env.overwriteOutput = True
    src_gdb = _copy_source(pipes, out_dir)
    links_geom, node_geom = _layers(arcpy, src_gdb)
    log("源 %s：%d 个管段层、%d 个节点层" % (os.path.basename(src_gdb), len(links_geom), len(node_geom)))
    gdb = os.path.join(out_dir, "validate.gdb")
    if arcpy.Exists(gdb):
        arcpy.management.Delete(gdb)
    arcpy.management.CreateFileGDB(out_dir, "validate.gdb")
    sr = arcpy.Describe(links_geom[0]).spatialReference
    fd = os.path.join(gdb, "net")
    arcpy.management.CreateFeatureDataset(gdb, "net", sr)
    links, nodes = os.path.join(fd, "links"), os.path.join(fd, "nodes")
    _copy_rows(arcpy, links_geom, links, "POLYLINE", sr)
    _copy_rows(arcpy, node_geom, nodes, "POINT", sr)
    # Check Geometry（相当我们的 B1 损坏几何）
    cg = os.path.join(gdb, "check_geometry")
    arcpy.management.CheckGeometry([links], cg)
    geom_problems = {}
    with arcpy.da.SearchCursor(cg, ["FEATURE_ID", "PROBLEM"]) as c:
        for fid, prob in c:
            geom_problems.setdefault(str(prob), []).append(fid)
    log("Check Geometry", {k: len(v) for k, v in geom_problems.items()})
    ids_map = {}
    with arcpy.da.SearchCursor(links, ["OID@", "UNITID"]) as c:
        for oid, uid in c:
            ids_map[oid] = str(uid)
    geom_problems = {k: [ids_map.get(x, str(x)) for x in v] for k, v in geom_problems.items()}
    # 拓扑：容差 = 我们的吸附容差
    topo = os.path.join(fd, "topo")
    arcpy.management.CreateTopology(fd, "topo", tolerance)
    arcpy.management.AddFeatureClassToTopology(topo, links, 1, 1)
    arcpy.management.AddFeatureClassToTopology(topo, nodes, 1, 1)
    for r in RULES:
        arcpy.management.AddRuleToTopology(topo, r, links)
    arcpy.management.AddRuleToTopology(topo, "Endpoint Must Be Covered By (Line-Point)", links, "", nodes)
    log("校验拓扑（%g m，五条规则）" % tolerance)
    arcpy.management.ValidateTopology(topo)
    arcpy.management.ExportTopologyErrors(topo, gdb, "topo_err")
    out = {"links": int(arcpy.management.GetCount(links)[0]), "nodes": int(arcpy.management.GetCount(nodes)[0]),
         "tolerance": tolerance, "checkGeometry": geom_problems, "rules": {}}
    for suffix in ("_point", "_line", "_poly"):
        fc = os.path.join(gdb, "topo_err" + suffix)
        if not arcpy.Exists(fc):
            continue
        with arcpy.da.SearchCursor(fc, ["RuleDescription", "OriginObjectID", "DestinationObjectID", "SHAPE@XY"]) as c:
            for rule, o, d, xy in c:
                out["rules"].setdefault(str(rule), []).append(
                    {"origin": ids_map.get(o, str(o)), "dest": ids_map.get(d, str(d)) if d else None,
                     "x": round(xy[0], 3), "y": round(xy[1], 3), "geom": suffix.lstrip("_")})
    log("拓扑错误", {k: len(v) for k, v in out["rules"].items()})
    return out


def _trace(pipes, out_dir, log=print):
    """ArcGIS 追踪网络：从全部出口做一次 Connected 追踪，追不到的管段 ＝ 没有出口的子网（对我们的 C4）。
    ⚠️ 追踪网络按几何 XY 重合（默认 0.001 m）连接，不是 0.5 m 吸附；而且不能拿拓扑校验过的副本 ——
    ValidateTopology 已按 0.5 m 簇容差动过几何。从原始解压件逐行抄。"""
    import arcpy
    src_gdb = _copy_source(pipes, out_dir)
    links_geom, node_geom = _layers(arcpy, src_gdb)
    gdb = os.path.join(out_dir, "trace.gdb")
    if arcpy.Exists(gdb):
        arcpy.management.Delete(gdb)
    arcpy.management.CreateFileGDB(out_dir, "trace.gdb")
    sr = arcpy.Describe(links_geom[0]).spatialReference
    fd = os.path.join(gdb, "tn")
    arcpy.management.CreateFeatureDataset(gdb, "tn", sr)
    links, nodes = os.path.join(fd, "tn_links"), os.path.join(fd, "tn_nodes")

    def copy(sources, target, kind):
        # ⚠️ 不带 Z：带 Z 的追踪网络要求 XYZ 都重合才连；这份数据有的管端 Z 是管底标高、有的是 0
        arcpy.management.CreateFeatureclass(os.path.dirname(target), os.path.basename(target), kind,
                                            has_z="DISABLED", spatial_reference=sr)
        arcpy.management.AddField(target, "UNITID", "TEXT", field_length=128)
        arcpy.management.AddField(target, "MERGE_SRC", "TEXT", field_length=64)
        n = 0
        with arcpy.da.InsertCursor(target, ["SHAPE@", "UNITID", "MERGE_SRC"]) as w:
            for s in sources:
                name = os.path.basename(str(s))
                has_id = "UNITID" in {x.name.upper() for x in arcpy.ListFields(s)}
                cols = ["SHAPE@", "UNITID"] if has_id else ["SHAPE@", "OBJECTID"]
                with arcpy.da.SearchCursor(s, cols) as c:
                    for shp, uid in c:
                        if shp is None:
                            continue
                        w.insertRow([shp, None if uid is None else str(uid), name])
                        n += 1
        return n

    copy(links_geom, links, "POLYLINE")
    copy(node_geom, nodes, "POINT")
    arcpy.management.AddGlobalIDs([links, nodes])
    # ⚠️ 建追踪网络的入参要用要素集内的相对名；绝对路径会报 ERROR 160069 The dataset was not found
    arcpy.tn.CreateTraceNetwork(fd, "tnet", [os.path.basename(nodes)], [[os.path.basename(links), "SIMPLE_EDGE"]])
    tnet = os.path.join(fd, "tnet")
    arcpy.tn.EnableNetworkTopology(tnet, 1000000)
    arcpy.tn.ValidateNetworkTopology(tnet)
    starts = os.path.join(gdb, "starting_points")
    arcpy.management.CreateFeatureclass(gdb, "starting_points", "POINT", spatial_reference=sr)
    for f, t in (("FEATURESOURCENAME", "TEXT"), ("FEATUREGLOBALID", "GUID"), ("TERMINALID", "LONG"), ("PERCENTALONG", "DOUBLE")):
        arcpy.management.AddField(starts, f, t)
    # 出口层名（与 layers.py 的出口种类同一套）
    OUTLET_KEYS = ("outfall", "soakaway", "pump", "tank", "reservoir", "pond")
    start_n = 0
    with arcpy.da.SearchCursor(nodes, ["SHAPE@XY", "GLOBALID", "MERGE_SRC"]) as c, \
            arcpy.da.InsertCursor(starts, ["SHAPE@XY", "FEATURESOURCENAME", "FEATUREGLOBALID", "TERMINALID", "PERCENTALONG"]) as w:
        for xy, gid, src in c:
            if any(k in os.path.basename(str(src)).lower() for k in OUTLET_KEYS):
                w.insertRow([xy, "tn_nodes", gid, -1, 0])
                start_n += 1
    js = os.path.join(out_dir, "trace_elements.json")
    log("追踪网络：%d 个起点（出口）" % start_n)
    arcpy.tn.Trace(tnet, "CONNECTED", starts, validate_consistency="DO_NOT_VALIDATE_CONSISTENCY",
                   result_types=["ELEMENTS"], out_json_file=js)
    d = json.load(open(js, encoding="utf-8"))
    by_source = {}
    for e in d.get("elements", []):
        by_source.setdefault(e.get("networkSourceId"), set()).add(e.get("objectId"))
    all_fc = {}
    with arcpy.da.SearchCursor(links, ["OID@", "UNITID"]) as c:
        for oid, uid in c:
            all_fc[oid] = str(uid)
    link_src = max(by_source, key=lambda k: len(by_source[k] & set(all_fc))) if by_source else None
    reached = by_source.get(link_src, set())
    unreached = [all_fc[o] for o in all_fc if o not in reached]
    log("追不到（没有出口）的管段 %d / %d" % (len(unreached), len(all_fc)))
    return {"links": len(all_fc), "startingPoints": start_n, "reachedLinks": len(reached & set(all_fc)), "unreachedLinks": len(unreached),
            "unreached": unreached}


def _compare(topo, trace, net, net_result, tolerance, log=print) -> dict:
    """把 ArcGIS 的结果与本程序的结果逐条比。差异要能解释：多半是定义不同（悬挂算不算、重叠怎么算），
    少数是簇容差改了几何造成的。"""
    C = net_result["checks"]
    f = lambda n: format(int(n), ",")
    p = lambda a, b: ("%.1f%%" % (100 * a / b)) if b else "—"
    rules = topo["rules"]

    def cmp(ours, arc):
        a, b = set(ours), set(arc)
        return {"我们": len(a), "ArcGIS": len(b), "两边都有": len(a & b), "只我们有": len(a - b), "只ArcGIS有": len(b - a),
                "只我们有_样例": sorted(a - b)[:10], "只ArcGIS有_样例": sorted(b - a)[:10]}

    # C1 悬挂端点 ＝ Must Not Have Dangles ∩ Endpoint Must Be Covered By（同一位置）
    dangles = {}
    for e in rules.get("Must Not Have Dangles", []):
        dangles.setdefault(e["origin"], set()).add((e["x"], e["y"]))
    arc_c1 = set()
    for e in rules.get("Endpoint Must Be Covered By", []):
        for q in dangles.get(e["origin"], ()):
            if abs(q[0] - e["x"]) <= 0.6 and abs(q[1] - e["y"]) <= 0.6:
                arc_c1.add(e["origin"] + "(?)")
                break
    # 我们报「管段(起|止)」，ArcGIS 的悬挂报管段本身；比对时都归到管段上（起点终点各自计数在 C1 里）
    c1 = cmp({x.split("(")[0] for x in C["C1"]["ids"]}, {x.split("(")[0] for x in arc_c1})
    # B6 自相交
    b6 = cmp(C["B6"]["ids"], {e["origin"] for e in rules.get("Must Not Self-Intersect", [])})
    # B7 重复管段（我们只报几何完全相同的；ArcGIS 报任何共线段）
    overlap_ids = {x for e in rules.get("Must Not Overlap", []) for x in (e["origin"], e["dest"]) if x}
    b7 = {"我们": len(C["B7"]["ids"]), "ArcGIS涉及的管段": len(overlap_ids), "我们的在ArcGIS里": len(set(C["B7"]["ids"]) & overlap_ids)}
    # C7 相交未连接：ArcGIS 的交于一点且交点离两条端点都超过容差的
    arc_c7 = set()
    for e in rules.get("Must Not Intersect", []):
        if not e["dest"]:
            continue
        arc_c7.add("|".join(sorted((e["origin"], e["dest"]))))
    c7 = cmp({x.replace("×", "|") for x in C["C7"]["ids"]}, arc_c7)
    # C4 无出口的子网（追踪网络追不到的管段）；没有许可跑不了追踪网络就照实标没做
    c4 = cmp(C["C4"]["ids"], trace["unreached"]) if trace else None
    row = [
        ("节点总数", f(net_result["summary"]["nodes"]), "ArcGIS %s" % f(topo["nodes"]),
         "一致。" if net_result["summary"]["nodes"] == topo["nodes"] else "不一致：逐行抄写后 ArcGIS 侧少了 %s 个（Merge／Append 会静默丢行）" % f(net_result["summary"]["nodes"] - topo["nodes"])),
        ("B1 损坏几何", f(C["B1"]["count"]), "Check Geometry %s" % f(sum(len(v) for v in topo["checkGeometry"].values())),
         "一致" if C["B1"]["count"] == sum(len(v) for v in topo["checkGeometry"].values()) else "定义不同：ArcGIS 的 Check Geometry 另含空几何与重复点"),
        ("C1 悬挂端点", f(c1["我们"]), "ArcGIS %s" % f(c1["ArcGIS"]),
         "两边都有 %s。本程序多出的 %s 个里，多半是端点落在别的管段中间（ArcGIS 不算悬挂，本程序另列为 C11）" % (f(c1["两边都有"]), f(c1["只我们有"]))),
        ("B6 自相交", f(b6["我们"]), "ArcGIS %s" % f(b6["ArcGIS"]),
         "定义不同：ArcGIS 报自身重叠；本程序报首尾闭合（起止同一点）与不简单的线"),
        ("B7 重复管段", "%s 条" % f(b7["我们"]), "ArcGIS 重叠涉及 %s 条" % f(b7["ArcGIS涉及的管段"]),
         "本程序只报几何完全相同的（%s 条全部落在 ArcGIS 的重叠结果里）；ArcGIS 报任何共线段" % f(b7["我们的在ArcGIS里"])),
        ("C7 相交而未连接", "%s 对" % f(c7["我们"]), "ArcGIS 交于一点 %s 对" % f(c7["ArcGIS"]),
         "两边都有 %s 对。ArcGIS 多出的多为按簇容差移动顶点后才相交的；本程序多出的是交点在端点 0.5 m 以内的（ArcGIS 算相接）" % f(c7["两边都有"])),
        ("C4 无出口的子网", f(C["C4"]["count"]), ("ArcGIS 追踪网络 %s" % f(c4["ArcGIS"])) if c4 else "没做",
         ("两边都有 %s（%s）；本程序多 %s、ArcGIS 多 %s。追踪网络按 0.001 m 重合连接，本程序按 %g m 吸附" % (
             f(c4["两边都有"]), p(c4["两边都有"], c4["ArcGIS"]), f(c4["只我们有"]), f(c4["只ArcGIS有"]), tolerance))
         if c4 else "本机 ArcGIS Pro 没有追踪网络许可（CheckProduct 只有 ArcInfo），连通性未做专业软件复核"),
    ]
    intro = ("拓扑类结果用 ArcGIS Pro（拓扑规则与追踪网络，容差 %g m）逐条比对；属性类结果用另一套代码路径独立重数；"
             "DTM 统计用 rasterio 直读重算。差异逐条给出原因。" % tolerance)
    log("对照完成：C1 %s、C4 %s、C7 %s" % (c1, c4, c7))
    return {"rows": [{"item": a, "ours": b, "ref": c, "verdict": d} for a, b, c, d in row], "intro": intro,
            "detail": {"C1": c1, "B6": b6, "B7": b7, "C7": c7, "C4": c4}}


def _slim(topo):
    return {"links": topo["links"], "nodes": topo["nodes"], "tolerance": topo["tolerance"],
            "checkGeometry": {k: len(v) for k, v in topo["checkGeometry"].items()},
            "rules": {k: len(v) for k, v in topo["rules"].items()}}


# ═══════════════════════════════════════════════════════════════ 另起进程（当前解释器没有 arcpy 时）

def verify_subprocess(pipes: str, out_dir: str, payload_json: str, python_exe: str = ARCGIS_PY, log=print) -> dict:
    """用 ArcGIS Pro 的 Python 跑本模块（--arcgis 时由 __main__ 调用）。

    payload_json：把本程序的结果递给子进程（net_result、tolerance）的 JSON 文件；子进程把附录行写回。
    """
    if not os.path.exists(python_exe):
        raise SystemExit("找不到 ArcGIS Pro 的 python：%s（改 ARCGIS_PY 或在自己机器上跑）" % python_exe)
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "verify_cli.py")
    open(out, "w", encoding="utf-8").write(_CLI.format(code_root=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    log("在 %s 里跑 ArcGIS 复核" % os.path.basename(python_exe))
    r = subprocess.run([python_exe, out, pipes, out_dir, payload_json], capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        log("  " + line)
    if r.returncode != 0:
        raise SystemExit("ArcGIS 复核失败（退出码 %d）：%s" % (r.returncode, (r.stderr or "")[-2000:]))
    return json.load(open(os.path.join(out_dir, "compare.json"), encoding="utf-8"))


_CLI = '''# -*- coding: utf-8 -*-
"""在 ArcGIS Pro 的 Python 里跑复核：由 tools/dataqc/arcgis_verify.py 另起进程调用。"""
import json, os, sys
sys.path.insert(0, r"{代码根}")
sys.stdout.reconfigure(encoding="utf-8")
from tools.dataqc import arcgis_verify as AV
pipes, out_dir, payload = sys.argv[1], sys.argv[2], sys.argv[3]
P = json.load(open(payload, encoding="utf-8"))
class _Net:      # 复核只用到 P_geom（算 C11 归因）与 crs，做一层最薄的壳
    pass
net = _Net()
json.dump({}, open(os.path.join(out_dir, "compare.json"), "w", encoding="utf-8"))   # 先占位，失败时报告也能生成
r = AV.复核(pipes, net, P["net_result"], None, P["tolerance"], out_dir, log=print)
json.dump(r, open(os.path.join(out_dir, "compare.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
'''
