"""第 6、7 步：启用拓扑 → 按区域分块校验 → 属性规则 → 汇总错误 → 子网控制器、更新子网 → 子网统计。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s5_validate.py <工作目录> [起始步]

1 topology   Pro arcpy.un.EnableNetworkTopology（错误上限 5,000,000）
2 grid       UDMS CreateQuadTrees（按管线密度分块，每块 ≤ 20,000 条）
3 validate   UDMS ValidateByPolygon（逐块校验网络拓扑）
4 rules      UDMS EvaluateRulesByPolygon（逐块跑校验类属性规则）
5 summarize  UDMS SummarizeUNErrors、SummarizeAttributeRules → 06_validate/
6 subnet     出水口作子网控制器（汇型，端子 Inlet Port）。子网名 = 连通系统：管端坐标（0.01 m）并查集，一个连通系统一个名；
             Pro ImportSubnetworkControllers → ValidateNetworkTopology → UpdateSubnetwork（全部子网，出错继续）
7 aggregate  UDMS SubnetworkAggregator（各子网要素统计）
8 report     读移动库里的脏区 / 错误表 → 06_validate/s5_report.json（按错误码、按类计数，ISCONNECTED 统计）
"""
import collections
import csv
import re
import json
import os
import sqlite3
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
UDMS = r"D:/阿布扎比/客户数据/arcgis/UtilityDataManagementSupport.atbx"
TIER = "Stormwater Management Area"
STEPS = ("topology", "grid", "validate", "rules", "summarize", "subnet", "aggregate", "report")


def main(work, start="topology"):
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    mg = os.path.join(work, "05_un", "Stormwater_AD.geodatabase")
    fd = next(p for p in (os.path.join(mg, "main.UtilityNetwork"), os.path.join(mg, "UtilityNetwork")) if arcpy.Exists(p))
    un = next(p for p in (os.path.join(fd, "Stormwater"), os.path.join(fd, "main.Stormwater")) if arcpy.Exists(p))
    out = os.path.join(work, "06_validate")
    os.makedirs(out, exist_ok=True)
    rp = os.path.join(out, "s5_report.json")
    R = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) and start != "topology" else {}
    todo = STEPS[STEPS.index(start):]
    arcpy.ImportToolbox(UDMS)

    def cls(name):
        for p in (os.path.join(fd, name), os.path.join(fd, "main." + name)):
            if arcpy.Exists(p):
                return p

    def save():
        json.dump(R, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

    def step(name, fn):
        if name not in todo:
            return
        print(name, flush=True)
        t = time.time()
        try:
            R[name] = fn() or {}
        except Exception as e:
            R[name] = {"error": str(e)[-3000:]}
        R[name]["s"] = round(time.time() - t)
        print("  ", json.dumps(R[name], ensure_ascii=False, default=str)[:1500], flush=True)
        save()

    grid = os.path.join(out, "grid.gdb", "blocks")

    def f_topology():
        try:
            arcpy.un.DisableNetworkTopology(un)
        except Exception:
            pass
        r = arcpy.un.EnableNetworkTopology(un, 5000000)
        return {"messages": r.getMessages()[-2000:]}

    def f_grid():
        g = os.path.join(out, "grid.gdb")
        if not arcpy.Exists(g):
            arcpy.management.CreateFileGDB(out, "grid.gdb")
        r = arcpy.udms.CreateQuadTrees([cls("StormwaterLine")], grid, 20000, None, "CREATE_NON_EMPTY", "CREATE_RECTANGLES")
        return {"blocks": int(arcpy.management.GetCount(grid)[0]), "messages": r.getMessages()[-800:]}

    def f_validate():
        r = arcpy.udms.ValidateByPolygon(un, grid)
        return {"messages": r.getMessages()[-3000:]}

    def f_rules():
        r = arcpy.udms.EvaluateRulesByPolygon(mg, grid, "VALIDATION_RULES", "SYNC")
        return {"messages": r.getMessages()[-3000:]}

    def f_summarize():
        for d in ("un_errors", "attr_rules"):
            os.makedirs(os.path.join(out, d), exist_ok=True)
        a = arcpy.udms.SummarizeUNErrors(un, os.path.join(out, "un_errors"))
        b = arcpy.udms.SummarizeAttributeRules(mg, os.path.join(out, "attr_rules"))
        return {"un_errors": a.getMessages()[-2500:], "attr_rules": b.getMessages()[-2500:]}

    def f_subnet():
        line, dev = cls("StormwaterLine"), cls("StormwaterDevice")
        par = {}

        def root(x):
            while par.setdefault(x, x) != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        key = lambda p: (round(p.X, 2), round(p.Y, 2))
        # 第十四轮：线—线在中间折点相连，要求这里有别的线的端点或有点（UN 的规则）；两线只在各自中间折点交叉不连通。
        # 第十二轮只按管端连 → 错误 29；第十三轮所有折点都连 → 误合并。
        geoms = [g for (g,) in arcpy.da.SearchCursor(line, ["SHAPE@"]) if g is not None]
        hubs = {key(g.firstPoint) for g in geoms} | {key(g.lastPoint) for g in geoms}
        hubs |= {key(g.firstPoint) for (g,) in arcpy.da.SearchCursor(dev, ["SHAPE@"]) if g is not None}
        hubs |= {key(g.firstPoint) for (g,) in arcpy.da.SearchCursor(cls("StormwaterJunction"), ["SHAPE@"]) if g is not None}
        for g in geoms:
            a = root(key(g.firstPoint))
            par[a] = root(key(g.lastPoint))
            for part in g:
                for p in part:
                    if p is not None and key(p) in hubs:
                        par[root(key(p))] = root(a)
        codes = {s["Name"]: c for c, s in arcpy.da.ListSubtypes(dev).items()}
        ag = codes["Outfall"]
        rows, names = [], {}
        for gid, aid, g in arcpy.da.SearchCursor(dev, ["GLOBALID", "assetid", "SHAPE@"], f"ASSETGROUP = {ag}"):
            k = key(g.firstPoint)
            s = names.setdefault(root(k) if k in par else ("isolated", k), f"SMA-{len(names) + 1:04d}")
            rows.append([re.sub(r"[^A-Za-z0-9_]", "_", f"OUT_{aid or gid}")[:64], gid, "StormwaterDevice", "Outfall", "Non-Permit Outfall", "Inlet Port", TIER, s, "", ""])
        # 第五轮：内陆系统的 System Outflow（通过关联接在渗井 / BMP 点上；按锚点所在连通系统命名，子网名前缀 SMI）
        if "System Outflow" in codes:
            names_i = {}
            for gid, aid, ax, ay in arcpy.da.SearchCursor(dev, ["GLOBALID", "assetid", "anchor_x", "anchor_y"], f"ASSETGROUP = {codes['System Outflow']}"):
                k = (round(ax, 2), round(ay, 2)) if ax is not None else None
                r = root(k) if k in par else ("isolated", aid)
                s = names.get(r) or names_i.setdefault(r, f"SMI-{len(names_i) + 1:04d}")   # 第十三轮：和出水口同一系统的，用出水口的子网名
                rows.append([re.sub(r"[^A-Za-z0-9_]", "_", f"SO_{aid or gid}")[:64], gid, "StormwaterDevice", "System Outflow", "Outflow", "Inlet Port", TIER, s, "", ""])
        ctrl = os.path.join(out, "controllers_in.csv")
        with open(ctrl, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["SUBNETWORKCONTROLLERNAME", "FEATUREGLOBALID", "FEATURECLASSNAME", "FEATUREASSETGROUP", "FEATUREASSETTYPE",
                        "FEATURETERMINAL", "TIERNAME", "SUBNETWORKNAME", "DESCRIPTION", "NOTES"])
            w.writerows(rows)
        res = {"controllers": len(rows), "subnetworks": len(set(r[7] for r in rows)),
               "isolated_outfalls": sum(1 for k in names if isinstance(k, tuple) and k[0] == "isolated")}
        arcpy.un.ImportSubnetworkControllers(un, ctrl)
        arcpy.un.ValidateNetworkTopology(un)
        r = arcpy.un.UpdateSubnetwork(un, "Stormwater", TIER, all_subnetworks_in_tier="ALL_SUBNETWORKS_IN_TIER", continue_on_failure="CONTINUE_ON_FAILURE")
        res["update_messages"] = r.getMessages()[-3000:]
        return res

    def f_aggregate():
        os.makedirs(os.path.join(out, "subnet_agg"), exist_ok=True)
        r = arcpy.udms.SubnetworkAggregator(un, os.path.join(out, "subnet_agg"))
        return {"messages": r.getMessages()[-2000:]}

    def f_report():
        con = sqlite3.connect(mg)
        tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        res = {}
        dt = [t for t in tabs if t.startswith("UN_") and t.endswith("_DirtyAreas")]
        if dt:
            res["dirty_areas"] = con.execute(f"select count(*) from {dt[0]}").fetchone()[0]
            # 脏区的 ERRORMESSAGE 形如「9,[RuleID(s) 1552],9,{GUID};…」：每段开头是 Utility Network 错误号
            byc = collections.Counter()
            n_err = 0
            for (msg,) in con.execute(f"select ERRORMESSAGE from {dt[0]} where ERRORCODE <> 0"):
                n_err += 1
                for seg in (msg or "").split(";"):
                    m = re.match(r"\s*(\d+),", seg)
                    if m:
                        byc[int(m.group(1))] += 1
            res["dirty_areas_with_error"] = n_err
            res["errors_by_un_code"] = dict(byc.most_common())
        ar = collections.Counter()
        for t in tabs:
            if t.startswith("GDB_Validation") and t.endswith("Errors"):
                for rn, msg in con.execute(f'select RuleName, ErrorMessage from "{t}"'):
                    ar[f"{rn}：{msg}"] += 1
        res["attribute_rule_errors"] = dict(ar.most_common(60))
        res["isconnected"] = {}
        for c in ("StormwaterLine", "StormwaterDevice", "StormwaterJunction"):
            t = c if c in tabs else next((x for x in tabs if x.endswith(c)), None)
            if t:
                res["isconnected"][c] = dict(con.execute(f"select ISCONNECTED, count(*) from {t} group by 1").fetchall())
        lt = "StormwaterLine" if "StormwaterLine" in tabs else next(x for x in tabs if x.endswith("StormwaterLine"))
        res["lines_by_subnetwork_state"] = dict(con.execute(f"select case when smasubnetworkname in ('Unknown', '') or smasubnetworkname is null then '无子网' else '有子网' end, count(*) from {lt} group by 1").fetchall())
        con.close()
        return res

    for n, fn in (("topology", f_topology), ("grid", f_grid), ("validate", f_validate), ("rules", f_rules), ("summarize", f_summarize),
                  ("subnet", f_subnet), ("aggregate", f_aggregate), ("report", f_report)):
        step(n, fn)
    R["seconds_total"] = round(time.time() - t0)
    save()


if __name__ == "__main__":
    main(*sys.argv[1:3])
