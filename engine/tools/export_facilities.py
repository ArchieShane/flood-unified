# -*- coding: utf-8 -*-
"""L2+ 设施数据：把一态相对 L1 **新增**的设施从 .inp 里拆出来，做成人和 GIS 都能直接读的文件。

交付目录里设施原先只有核对 / 理由 json 和箱涵轮廓——泵的参数、闸门与排空泵的启停规则、
进水管走向都只写在 .inp 里，要会读 SWMM 格式才看得到。这里按「这一态比 L1 多了什么」逐项导出：

  设施/蓄水设施.geojson     塘 / 箱涵 / 集水井：轮廓（有设计轮廓用设计的，没有就按顶面面积画示意方形并标明）、
                            底 / 顶标高、最大水深、库容（按 SWMM 的面积曲线积分）
  设施/管线闸堰.geojson     新增的管、闸孔（orifice）、堰、泵（泵画成吸水井 → 出水点的连线）
  设施/泵.csv               额定点、泵曲线、初始状态、所属设施
  设施/控制规则.csv / .txt  新增的 RULE：条件与动作（txt 是原文）
  对比_缺口分析/设施总表.xlsx  各态一张总览 + 明细

用法：python tools/export_facilities.py            # Mussafah_00 交付目录
"""
from __future__ import annotations

import csv
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

LINK_SECS = ("CONDUITS", "ORIFICES", "WEIRS", "PUMPS", "OUTLETS")
# 流量单位 → m³/s。Mussafah 客户模型是 LPS，RB_02 / 全市是 CMS——泵曲线里的流量要按模型自己的单位换
Q_TO_CMS = {"CMS": 1.0, "LPS": 1e-3, "MLD": 1e3 / 86400, "CFS": 0.0283168, "GPM": 6.309e-5, "MGD": 0.0438126}


def parse_inp(path: pathlib.Path) -> dict:
    """{节: [行的 token 列表]}；CONTROLS 另存原文行。注释和空行丢掉。"""
    S: dict[str, list] = {}; raw_ctrl: list[str] = []; cur = None
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*\[(\w+)\]", ln)
        if m:
            cur = m.group(1).upper(); S.setdefault(cur, []); continue
        if not cur or not ln.strip() or ln.lstrip().startswith(";"):
            continue
        if cur == "CONTROLS":
            raw_ctrl.append(ln.rstrip())
        else:
            S[cur].append(ln.split())
    S["_CONTROLS_RAW"] = raw_ctrl
    return S


def rules(raw: list[str]) -> dict[str, list[str]]:
    out, name = {}, None
    for ln in raw:
        m = re.match(r"^\s*RULE\s+(\S+)", ln, re.I)
        if m:
            name = m.group(1); out[name] = [ln.strip()]
        elif name:
            out[name].append(ln.strip())
    return out


def curves(S) -> dict[str, dict]:
    out = {}
    for r in S.get("CURVES", []):
        nm = r[0]
        typed = not _num(r[1])                        # 类型关键字只在第一行；一行里可以有好几对点
        c = out.setdefault(nm, {"type": r[1] if typed else "", "pts": []})
        vals = r[2:] if typed else r[1:]
        for i in range(0, len(vals) - 1, 2):
            c["pts"].append((float(vals[i]), float(vals[i + 1])))
    return out


def _num(s) -> bool:
    try:
        float(s); return True
    except ValueError:
        return False


def storage_geom(r, C) -> dict:
    """[STORAGE] 一行 → 底标高、最大水深、顶面面积、库容（面积对水深积分）。"""
    inv, dmax = float(r[1]), float(r[2])
    kind = r[4].upper()
    if kind == "FUNCTIONAL":
        a, b, c = float(r[5]), float(r[6]), float(r[7])
        area = lambda d: a * d ** b + c if d > 0 or b == 0 else c
    else:                                                    # TABULAR
        pts = sorted(C.get(r[5], {"pts": [(0, 0)]})["pts"])
        area = lambda d: float(_interp(d, pts))
    n = 50
    vol = sum((area(dmax * i / n) + area(dmax * (i + 1) / n)) / 2 * dmax / n for i in range(n))
    return {"invert_m": inv, "max_depth_m": dmax, "top_m": inv + dmax, "area_top_m2": area(dmax),
            "volume_m3": vol, "shape": kind}


def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(x1 - x0, 1e-12)
    return pts[-1][1]


def diff(base: dict, st: dict) -> dict:
    add = {}
    for sec in ("JUNCTIONS", "STORAGE") + LINK_SECS:
        have = {r[0] for r in base.get(sec, [])}          # 集合只建一次：RB_02 有 2 万多个节点，逐行重建是 O(n²)
        add[sec] = [r for r in st.get(sec, []) if r[0] not in have]
    # 按内容比，不按名字：Mussafah 的 L1 是从 Parsons 原件剥出来的，规则名还在、条件和动作被清空了
    rb, rs = rules(base["_CONTROLS_RAW"]), rules(st["_CONTROLS_RAW"])
    add["RULES"] = {k: v for k, v in rs.items() if rb.get(k) != v}
    return add


def export_state(base_inp: pathlib.Path, st_inp: pathlib.Path, out_dir: pathlib.Path, label: str,
                 footprints: dict | None = None, notes: dict | None = None, extra: dict | None = None,
                 log=print) -> dict:
    """footprints：{设施 id: GeoJSON 几何（WGS84）}；notes：{设施 id: 来源 / 说明}；extra：{设施 id: {附加字段}}。

    volume_m3 是模型里的**几何库容**（面积曲线积到最大水深，含超高）；设计上的可用库容另由 extra 带进来
    （箱涵：可用水深 2.0 m + 0.3 m 超高，超高不算可用库容，所以几何库容比可用库容大 15%）。"""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    B, S = parse_inp(base_inp), parse_inp(st_inp)
    A = diff(B, S)
    C = curves(S)
    opt = {r[0].upper(): r[1] for r in S.get("OPTIONS", []) if len(r) > 1}
    qf = Q_TO_CMS.get(opt.get("FLOW_UNITS", "CMS").upper(), 1.0)
    xy = {r[0]: (float(r[1]), float(r[2])) for r in S.get("COORDINATES", [])}
    vert: dict[str, list] = {}
    for r in S.get("VERTICES", []):
        vert.setdefault(r[0], []).append((float(r[1]), float(r[2])))
    wgs = lambda p: [round(v, 7) for v in tr.transform(*p)]
    footprints = footprints or {}; notes = notes or {}
    out_dir.mkdir(parents=True, exist_ok=True)

    # 蓄水设施
    stor = []
    for r in A["STORAGE"]:
        g = storage_geom(r, C)
        sid = r[0]
        if sid in footprints:
            geom, src = footprints[sid], "设计轮廓"
        elif sid in xy:
            h = math.sqrt(max(g["area_top_m2"], 1.0)) / 2; x, y = xy[sid]
            ring = [wgs((x + dx, y + dy)) for dx, dy in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]
            geom, src = {"type": "Polygon", "coordinates": [ring]}, "示意轮廓：按顶面面积画的方形，不是设计图"
        else:
            continue
        kind = "箱涵" if sid.startswith("NP") else ("集水井" if re.match(r"^W-?\d", sid) else "塘")
        stor.append({"id": sid, "kind": kind, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in g.items()},
                     "x": round(xy.get(sid, (0, 0))[0], 2), "y": round(xy.get(sid, (0, 0))[1], 2),
                     "outline": src, "note": notes.get(sid, ""), **(extra or {}).get(sid, {}), "_geom": geom})
    _gj(out_dir / "蓄水设施.geojson", [(s["_geom"], {k: v for k, v in s.items() if k != "_geom"}) for s in stor])

    # 管线、闸孔、堰、泵
    xs = {r[0]: r for r in S.get("XSECTIONS", [])}
    links = []
    for sec in LINK_SECS:
        for r in A[sec]:
            lid, n1, n2 = r[0], r[1], r[2]
            if n1 not in xy or n2 not in xy:
                continue
            pts = [xy[n1]] + vert.get(lid, []) + [xy[n2]]
            p = {"id": lid, "type": {"CONDUITS": "管", "ORIFICES": "闸孔", "WEIRS": "堰", "PUMPS": "泵", "OUTLETS": "出流口"}[sec],
                 "from": n1, "to": n2}
            if sec == "CONDUITS":
                p.update({"length_m": float(r[3]), "roughness_n": float(r[4]),
                          "in_offset_m": float(r[5]), "out_offset_m": float(r[6])})
            elif sec == "ORIFICES":
                p.update({"orifice_type": r[3], "offset_m": float(r[4]), "cd": float(r[5]), "flap_gate": r[6] if len(r) > 6 else ""})
            elif sec == "WEIRS":
                p.update({"weir_type": r[3], "crest_offset_m": float(r[4]), "cd": float(r[5])})
            elif sec == "PUMPS":
                p.update({"curve": r[3], "init_status": r[4] if len(r) > 4 else ""})
            if lid in xs:
                q = xs[lid]; p.update({"shape": q[1], "geom1_m": float(q[2]), "geom2_m": float(q[3]) if len(q) > 3 and _num(q[3]) else None})
            p["length_plan_m"] = round(sum(math.dist(a, b) for a, b in zip(pts, pts[1:])), 1)
            links.append((p, pts))
    _gj(out_dir / "管线闸堰.geojson", [({"type": "LineString", "coordinates": [wgs(q) for q in pts]}, p) for p, pts in links])

    # 泵
    pumps = []
    for p, _ in links:
        if p["type"] != "泵":
            continue
        cv = C.get(p["curve"], {"type": "?", "pts": []})
        ys = [b * qf for _, b in cv["pts"]]
        xlab = {"PUMP1": "容积 m³", "PUMP2": "水深 m", "PUMP3": "扬程 m", "PUMP4": "水深 m"}.get(cv["type"].upper(), "x")
        pumps.append({"id": p["id"], "from": p["from"], "to": p["to"], "curve": p["curve"], "curve_type": cv["type"],
                      "curve_points": f"{xlab} → 流量 m³/s：" + "; ".join(f"{a:g}→{b * qf:.4g}" for a, b in cv["pts"]),
                      "q_max_m3s": round(max(ys), 4) if ys else None, "init_status": p.get("init_status", "")})
    _csv(out_dir / "泵.csv", pumps, ["id", "from", "to", "curve", "curve_type", "curve_points", "q_max_m3s", "init_status"])

    # 控制规则
    R = A["RULES"]
    rows = []
    for nm, ls in R.items():
        cond = " ".join(l for l in ls[1:] if re.match(r"^(IF|AND|OR)\b", l, re.I))
        act = " ".join(l for l in ls[1:] if re.match(r"^(THEN|ELSE)\b", l, re.I))
        pri = next((l.split()[-1] for l in ls if l.upper().startswith("PRIORITY")), "")
        rows.append({"rule": nm, "condition": cond, "action": act, "priority": pri})
    _csv(out_dir / "控制规则.csv", rows, ["rule", "condition", "action", "priority"])
    (out_dir / "控制规则.txt").write_text("\n\n".join("\n".join(v) for v in R.values()) + "\n", encoding="utf-8")

    unit_q = sum(p["q_max_m3s"] or 0 for p in pumps)
    summ = {"state": label, "n_storage": len(stor), "storage_m3": round(sum(s["volume_m3"] for s in stor)),
            "usable_m3": round(sum(s.get("usable_m3", s["volume_m3"]) for s in stor)),
            "storage_area_m2": round(sum(s["area_top_m2"] for s in stor)),
            "n_pipes": sum(1 for p, _ in links if p["type"] == "管"),
            "pipe_m": round(sum(p.get("length_m", 0) for p, _ in links if p["type"] == "管")),
            "n_orifices": sum(1 for p, _ in links if p["type"] == "闸孔"), "n_weirs": sum(1 for p, _ in links if p["type"] == "堰"),
            "n_pumps": len(pumps), "pump_q_max_m3s": round(unit_q, 3), "n_rules": len(rows),
            "n_new_junctions": len(A["JUNCTIONS"])}
    log(f"  {label}：蓄水 {summ['n_storage']} 处 {summ['storage_m3']:,} m³、管 {summ['n_pipes']} 根 {summ['pipe_m']:,} m、"
        f"闸孔 {summ['n_orifices']}、堰 {summ['n_weirs']}、泵 {summ['n_pumps']}（合计 {summ['pump_q_max_m3s']} m³/s）、规则 {summ['n_rules']}")
    return {"summary": summ, "storage": [{k: v for k, v in s.items() if k != "_geom"} for s in stor],
            "links": [p for p, _ in links], "pumps": pumps, "rules": rows}


def _gj(path, feats):
    path.write_text(json.dumps({"type": "FeatureCollection", "crs_note": "WGS84 经纬度；原始坐标 UTM 40N（EPSG:32640）",
                                "features": [{"type": "Feature", "geometry": g, "properties": p} for g, p in feats]},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def _csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:      # utf-8-sig：Excel 直接打开不乱码
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)


def workbook(path: pathlib.Path, results: dict):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = "总览"
    head = [("state", "态"), ("n_storage", "蓄水设施 处"), ("usable_m3", "可用库容 m³"), ("storage_m3", "几何库容 m³（含超高）"), ("storage_area_m2", "顶面面积 m²"),
            ("n_pipes", "新增管 根"), ("pipe_m", "新增管长 m"), ("n_orifices", "闸孔"), ("n_weirs", "溢流堰"),
            ("n_pumps", "泵 台"), ("pump_q_max_m3s", "泵最大流量合计 m³/s"), ("n_rules", "控制规则 条")]
    ws.append([h for _, h in head])
    for r in results.values():
        ws.append([r["summary"][k] for k, _ in head])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append([]); ws.append(["口径：只列这一态相对 L1 新增的设施。几何库容 = SWMM 面积曲线对最大水深积分（含超高）；"
                              "可用库容 = 设计值（箱涵可用水深 2.0 m，0.3 m 超高不算），没有设计值的等于几何库容。"])

    def sheet(title, key, cols):
        s = wb.create_sheet(title); s.append(["态"] + [h for _, h in cols])
        for c in s[1]:
            c.font = Font(bold=True)
        for lab, r in results.items():
            for row in r[key]:
                s.append([lab] + [row.get(k) for k, _ in cols])
    sheet("蓄水设施", "storage", [("id", "编号"), ("kind", "类型"), ("usable_m3", "可用库容 m³"), ("volume_m3", "几何库容 m³"), ("area_top_m2", "顶面面积 m²"),
                               ("invert_m", "底标高 m"), ("top_m", "顶标高 m"), ("max_depth_m", "最大水深 m"),
                               ("x", "X（UTM 40N）"), ("y", "Y"), ("outline", "轮廓来源"), ("note", "说明")])
    sheet("泵", "pumps", [("id", "编号"), ("from", "吸水端"), ("to", "出水端"), ("curve_type", "曲线类型"),
                         ("curve_points", "曲线点"), ("q_max_m3s", "最大流量 m³/s"), ("init_status", "初始状态")])
    sheet("管线闸堰", "links", [("id", "编号"), ("type", "类型"), ("from", "起"), ("to", "止"), ("shape", "断面"),
                              ("geom1_m", "尺寸 1 m"), ("geom2_m", "尺寸 2 m"), ("length_m", "长度 m"), ("length_plan_m", "平面长度 m"),
                              ("in_offset_m", "进口偏移 m"), ("out_offset_m", "出口偏移 m"), ("offset_m", "闸孔偏移 m"),
                              ("crest_offset_m", "堰顶偏移 m"), ("cd", "流量系数")])
    sheet("控制规则", "rules", [("rule", "规则"), ("condition", "条件"), ("action", "动作"), ("priority", "优先级")])
    for s in wb.worksheets:
        for col in s.columns:
            s.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or "")) for c in col) + 2))
    wb.save(path)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    D = ROOT / "out" / "交付" / "Mussafah_00_61mm2h"
    base = WORK / "Mussafah_baseline_R61D2_72h.inp"
    fp = {}
    gj = D / "L2+B_本系统箱涵" / "设施" / "箱涵占地轮廓与标高.geojson"
    if gj.exists():
        fp = {f["properties"]["id"]: f["geometry"] for f in json.loads(gj.read_text(encoding="utf-8"))["features"]}
    pa = D / "L2+planned" / "设施" / "Parsons设施核对.json"
    na = {r["id"]: r.get("why_type", "") for r in json.loads(pa.read_text(encoding="utf-8"))} if pa.exists() else {}
    print("Mussafah_00 · L2+ 设施（相对 L1 新增）")
    res = {
        "L2+A": export_state(base, WORK / "Mussafah_R61D2_72h.inp", D / "L2+planned" / "设施", "L2+A", notes=na),
        "L2+B": export_state(base, WORK / "Mussafah_L2box_R61D2_72h.inp", D / "L2+B_本系统箱涵" / "设施", "L2+B", footprints=fp,
                             notes={k: "本系统设计，选址与定容理由见 箱涵选址与定容理由.json" for k in fp},
                             extra={f["properties"]["id"]: {"usable_m3": f["properties"]["vol_m3"], "land": f["properties"].get("land"),
                                                             "inflow": f["properties"].get("inflow"), "conveyance_m": f["properties"].get("conveyance_m")}
                                    for f in json.loads(gj.read_text(encoding="utf-8"))["features"]} if gj.exists() else None),
    }
    gab = ROOT / "out" / "three_lines" / "Mussafah.L2AB.R61D2.ponds.geojson"
    if (WORK / "Mussafah_L2AB_R61D2_72h.inp").exists() and gab.exists():
        fab = {f["properties"]["id"]: f for f in json.loads(gab.read_text(encoding="utf-8"))["features"]}
        res["L2+A+B"] = export_state(base, WORK / "Mussafah_L2AB_R61D2_72h.inp", D / "L2+suggested" / "设施", "L2+A+B",
                                     footprints={k: f["geometry"] for k, f in fab.items()},
                                     notes={**na, **{k: "本系统设计（在 Parsons 之上选址）" for k in fab}},
                                     extra={k: {"usable_m3": f["properties"]["vol_m3"], "land": f["properties"].get("land"),
                                                "inflow": f["properties"].get("inflow"), "conveyance_m": f["properties"].get("conveyance_m")} for k, f in fab.items()})
    workbook(D / "对比_缺口分析" / "设施总表.xlsx", res)
    print(f"→ {D / '对比_缺口分析' / '设施总表.xlsx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
