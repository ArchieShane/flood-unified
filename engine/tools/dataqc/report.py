# -*- coding: utf-8 -*-
"""生成《雨水管网与 DTM 数据质量报告》HTML：数字全部读自本次运行结果，不手填。

还原自 2026-09-27 的 生成报告_管线与DTM.py。当时写死在生成器里的 DMT 专属内容（dq 对照表、
复核中修正的平台问题、「客户原始资产库有 34,141 个检查井」一类说明）不再内置，改成可选的
--extra JSON 注入（键：dq / bugs / notes / advice），换一份数据不会把上一份的结论带过来。
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import re

from . import thresholds as T

HERE = os.path.dirname(os.path.abspath(__file__))
f = lambda n: "—" if n is None else format(int(round(n)), ",")
p = lambda a, b: ("%.1f%%" % (100 * a / b)) if b else "—"

DTM_LABELS = {"R1": "坐标系", "R2": "高程基准", "R3": "像元", "R4": "NoData 值", "R5": "有效覆盖", "R6": "高程值域",
        "R7": "金字塔", "R8": "无数据格", "R9": "单点尖峰（比 8 邻格都高出 2 m 以上）", "R10": "单点深坑（比 8 邻格都低 2 m 以上）",
        "R11": "平地（与 8 邻格高差都小于 1 cm）", "R12": "低于 −5 m 的连片洼地", "R13": "陡坎（与邻格高差超过 4 m）", "R14": "坡度"}


def _texts():
    return json.load(open(os.path.join(HERE, "texts_zh.json"), encoding="utf-8"))


def _per_layer(chk, no_field_layers=()):
    v = sorted(chk.get("byLayer", {}).items(), key=lambda a: -a[1])
    return "、".join("%s %s%s" % (k.upper(), f(n), "（该层无此字段）" if k in no_field_layers else "") for k, n in v)


def _verdict_pipe(C, summary, no_field):
    c8 = collections.Counter()
    for s in C["C8"]["ids"]:
        m = re.search(r"_([A-Z]+)_\d+$", s.split("→", 1)[-1])
        c8[m.group(1) if m else "其他格式"] += 1
    pts = []
    if C["C8"]["count"]:
        pts.append("<b>管段两端的井找不到</b>：每条管段都记录了它上游、下游各接哪个井（井的编号）。按这些编号去井的图层里找，有 %s 处"
                   "（占全部 %s 处的 %s）找不到对应的井%s。后果是：这些位置取不到井底、井盖标高，也无法按编号核对管段接在哪个井上" % (
                       f(C["C8"]["count"]), f(C["C8"]["total"]), p(C["C8"]["count"], C["C8"]["total"]),
                       ("，其中 %s 处要找的是检查井（MH）" % f(c8["MH"])) if c8.get("MH") else ""))
    if C["C4"]["count"]:
        pts.append("<b>管网不连通</b>：按管线的实际位置拼接，管网断成 %s 个互不相连的片区；其中 %s 条管段（%s）所在的片区里没有任何出水口、"
                   "泵站、渗井或蓄水池，建模时这些片区的雨水排不出去" % (f(C["C2"]["count"]), f(C["C4"]["count"]), p(C["C4"]["count"], C["C4"]["total"])))
    if C["A4"]["count"] or C["A10"]["count"] or C["A9"]["count"]:
        no_field_layers = [k.upper() for k in no_field.get("A10", ())]
        pts.append("<b>标高缺失</b>：%s 条管段（%s）缺上游或下游的管底标高，管底标高决定坡度与流向；%s 个井缺井盖标高、%s 个缺井底标高%s" % (
            f(C["A4"]["count"]), p(C["A4"]["count"], C["A4"]["total"]), f(C["A10"]["count"]), f(C["A9"]["count"]),
            ("，其中 %s 层整层没有这两项" % "、".join(no_field_layers)) if no_field_layers else ""))
    if C["A7"]["count"]:
        pts.append("<b>材质缺失</b>：%s 条管段（%s）没有材质，模型要根据材质确定管壁的粗糙程度" % (f(C["A7"]["count"]), p(C["A7"]["count"], C["A7"]["total"])))
    if C.get("D8", {}).get("total") or C.get("A19", {}).get("total"):
        pts.append("<b>对照 DMT 2022 设计手册</b>（问的是合不合现行设计标准，不是数据录错）：%s 条管段（%s）覆土小于手册要求的 1.2 m，"
                   "%s 条（%s）管径小于手册推荐的 300 mm。现有资产不一定按现行版手册建造，只作提示" % (
                       f(C["D8"]["count"]), p(C["D8"]["count"], C["D8"]["total"]), f(C["A19"]["count"]), p(C["A19"]["count"], C["A19"]["total"])))
    pts.append("管线的形状：损坏的几何 %s 条、长度为零 %s 条、重复的管段 %s 条" % (f(C["B1"]["count"]), f(C["B3"]["count"]), f(C["B7"]["count"])))
    dup = sum(C[k]["count"] for k in ("C8", "C4", "A4", "A1", "B1", "B2", "D1", "D6") if k in C)
    level = "bad" if dup > 0.05 * max(summary["links"], 1) else "mid" if dup else "good"
    verdict = {"bad": "可以作为一维模型的基础，但目前不能直接用来建模", "mid": "基本可用，建模前要处理下列问题",
               "good": "建模必需的属性、几何与连通没有发现影响建模的问题"}[level]
    return {"title": "管线数据", "level": level, "verdict": verdict, "points": pts}, c8
def _dtm_parts(R, scope_note, has_lines):
    """DTM：检查表、统计与极值表、高程分布、洼地表、结论卡片。"""
    ck = {counter["code"]: counter for counter in R["checks"]}
    rows = []
    for counter in R["checks"]:
        name = DTM_LABELS.get(counter["code"], counter["code"])
        if counter["code"] == "R8":
            name = ("管线两侧 %g m 内的无数据格" % R["buffer"]) if has_lines else "统计范围内的无数据片"
        rows.append({"code": counter["code"], "name": name, "value": counter["value"], "sev": counter["sev"], "note": counter["note"]})
    q = R["quantiles"]
    res = R["res"]
    area = R["cells"] * res * res / 1e6
    st = [
        ["统计范围", scope_note, "面积 %.2f km²，%s 格（%g m 格网）" % (area, f(R["cells"]), res)],
        ["有效格", f(R["valid"]), "占 %s" % p(R["valid"], R["cells"])],
        ["最低", "%.2f m" % R["min"] if R["min"] is not None else "—",
         "位置 %s, %s（UTM 40N）" % (f(R["minXY"][0]), f(R["minXY"][1])) if R["minXY"] else ""],
        ["最高", "%.2f m" % R["max"] if R["max"] is not None else "—",
         "位置 %s, %s（UTM 40N）" % (f(R["maxXY"][0]), f(R["maxXY"][1])) if R["maxXY"] else ""],
        ["均值 ± 标准差", "%.2f ± %.2f m" % (R["mean"], R["std"]) if R["mean"] is not None else "—", ""],
        ["分位数 0.1% / 1% / 5%", " / ".join("%s" % q.get(k) for k in ("p0.1", "p1", "p5")) + " m", "1 cm 精度；看最低值是否孤立"],
        ["分位数 25% / 50% / 75%", " / ".join("%s" % q.get(k) for k in ("p25", "p50", "p75")) + " m", ""],
        ["分位数 95% / 99% / 99.9%", " / ".join("%s" % q.get(k) for k in ("p95", "p99", "p99.9")) + " m", "看最高值是否孤立"],
        ["低于 0 m 的格", f(R["below0"]), "约 %.3f km²" % (R["below0"] * res * res / 1e6)],
        ["坡度 中位 / 90% / 99%", " / ".join("%s" % R["slopeQuantiles"].get(k) for k in ("p50", "p90", "p99")) + " %", "Horn 法，8 邻格都有效的格"],
    ]
    lows = R["lows"]
    lowTitle = "统计范围内低于 −5 m 的连片洼地"
    lowIntro = ("共 %d 处（按最低值列出最深的 10 处）。可能是下穿通道、基坑、取土坑，也可能是数据错误；本报告不做推断，需对照现场或影像核实。"
                "若是真实的下穿通道，它们正是二维模型里最先积水的位置。" % len(lows)) if lows else "没有低于 −5 m 的连片区。"
    pts = []
    pts.append("分辨率 %g m，%s；统计范围内有效格 %s" % (res, ck["R1"]["value"].split("（")[0], p(R["valid"], R["cells"])))
    pts.append("高程 %.2f ～ %.2f m，中位 %s m；0.1%%～99.9%% 分位在 %s ～ %s m" % (R["min"], R["max"], q.get("p50"), q.get("p0.1"), q.get("p99.9")))
    pts.append("单点尖峰 %s 处、单点深坑 %s 处、与邻格差 4 m 以上的陡坎 %s 格" % (f(R["spikes"]), f(R["pits"]), f(R["cliff"])))
    if ck["R2"]["sev"] != "ok":
        pts.append("<b>没有说明高程基准</b>，也没有采集时间和精度说明；与管线台账的基准关系只能比对得出")
    if lows:
        pts.append("有 <b>%d 处低于 −5 m 的连片洼地</b>（最深 %.1f m），需要核实" % (len(lows), lows[0]["min"]))
    pts.append("二维城市内涝模拟还需要建筑物数据；请确认这份 DTM 是否已去掉建筑物")
    hole = (R["footprintNoData"] if has_lines else R["nodata"])
    level = "bad" if hole else "mid" if (ck["R2"]["sev"] != "ok" or lows or R["spikes"] or R["pits"]) else "good"
    verdict = {"bad": "有无数据的洞，二维模型在那里没有地面", "mid": "覆盖与完整性满足建模需要；下列事项需要客户说明或核实",
               "good": "覆盖、完整性与异常值都没有发现问题"}[level]
    card = {"title": "DTM（地形）", "level": level, "verdict": verdict, "points": pts}
    return rows, st, lows, lowTitle, lowIntro, card


def build_report(out_html, net_result=None, net_meta=None, dtm=None, dtm_scope_note="", verify=None, thr=None, extra=None, title=None, pipe_name=None):
    """网结果：network.体检 的返回；网元：{'layers': {层: 数}, 'nodeKinds': {层: 种类}, 'noField': {code: [层]}}；
    dtm：raster.检查 的返回；复核：arcgis_verify 的对照结果；额外：--extra JSON。"""
    txt = _texts()
    extra = extra or {}
    thr = thr or T.Thresholds()
    D = {"verdicts": [], "overview": [], "pipeChecks": [], "dtmChecks": [], "eChecks": [], "advice": {}, "cross": [], "bugs": extra.get("bugs", []),
         "dq": extra.get("dq", []), "defs": [], "thresholds": [], "low": [], "dtmStats": [], "elevBins": []}
    has_net, has_dtm = net_result is not None, dtm is not None
    D["title"] = title or ("雨水管网与 DTM 数据质量报告" if has_net and has_dtm else "雨水管网数据质量报告" if has_net else "DTM 数据质量报告")
    D["subtitle"] = "面向一维／二维水动力建模的输入数据质量分析"
    meta = []
    if has_net:
        meta.append(("管线", pipe_name or os.path.basename(net_meta.get("path", ""))))
    if has_dtm:
        meta.append(("地形", dtm["meta"]["name"]))
        if dtm_scope_note:
            meta.append(("DTM 统计范围", dtm_scope_note))
    meta.append(("分析日期", datetime.date.today().isoformat()))
    if has_net:
        meta.append(("连通判定容差", "%g m" % net_result["summary"]["tolerance"]))
    D["meta"] = meta
    pipe = []
    if has_net:
        C = net_result["checks"]
        no_field = net_meta.get("noField", {})
        notes = dict(extra.get("notes", {}))
        for k in ("A4", "A9", "A10", "A12"):
            if C.get(k, {}).get("count") and k not in notes:
                notes[k] = "按图层：" + _per_layer(C[k], no_field.get(k, ()))
        if C.get("C4", {}).get("count") and "C4" not in notes:
            notes["C4"] = "全网断成 %s 块，最大一块只占 %.1f%% 的管段" % (f(net_result["summary"]["components"]), 100 * net_result["summary"]["largestShare"])
        notes.setdefault("C11", "与悬挂端点（C1）不同：端点碰到了别的管，但那条管在接入处没有打断，模型里两者不相连")
        notes.setdefault("D7", "阈值 %g m 出自通用工程经验，用来找标高录错的管段，待客户确认；DMT 2022 设计手册规定的最小覆土是 %g m，对照结果见 D8" % (thr.min_cover, thr.manual_min_cover))
        notes.setdefault("D8", "分母是井盖与管底标高都齐全的实心管；穿孔管（地下水渗排）按手册第 5 章另有规定，不计入。本项不判数据对错")
        notes.setdefault("A19", "分母是有管径的重力实心管；穿孔管按手册第 5 章最小 160 mm，不计入。本项不判数据对错")
        for counter in T.ALL_CODES:
            if counter[0] == "E" or counter not in C:
                continue
            v = C[counter]
            pipe.append({"code": counter, "group": counter[0], "name": txt[counter]["name"], "impact": txt[counter]["impact"], "fix": txt[counter]["fix"],
                         "route": T.route_of(counter), "severity": T.severity(counter), "count": v["count"], "total": v["total"],
                         "samples": [s for s in v["ids"] if s and s.strip()][:3], "note": notes.get(counter), "notDone": v.get("notDone")})
        D["pipeChecks"] = pipe
        D["thresholds"] = thr.as_rows(T.PIPE_KEYS)
        card, c8 = _verdict_pipe(C, net_result["summary"], no_field)
        D["verdicts"].append(card)
        for layer, n in net_meta["layers"].items():
            kind = net_meta["nodeKinds"].get(layer)
            D["overview"].append({"data": "管线", "layer": layer.upper(), "count": n, "note": kind or "管段"})
        for layer, reason in net_meta.get("skipped", {}).items():
            D["overview"].append({"data": "管线", "layer": layer.upper(), "count": None, "note": "未参与检查：" + reason})
    if has_dtm:
        rows, st, lows, lowTitle, lowIntro, card = _dtm_parts(dtm, dtm_scope_note, has_net)
        D["dtmChecks"], D["dtmStats"], D["lowTitle"], D["lowIntro"] = rows, st, lowTitle, lowIntro
        D["low"] = lows[:10]
        D["elevBins"] = dtm["elevBins"]
        D["elevNote"] = "统计范围内全部有效格，按高程分箱（范围取 0.1%～99.9% 分位）"
        D["dtmIntro"] = ("DTM 按原始 %g m 分辨率逐格统计，统计范围：%s。" % (dtm["res"], dtm_scope_note or "全图")) + (
            "R8 只看管线两侧 %g m 以内，因为范围里的海域与界外本来就是无数据，不应计为缺陷。" % dtm["buffer"] if has_net else "")
        D["verdicts"].append(card)
        m = dtm["meta"]
        D["overview"].append({"data": "DTM", "layer": m["name"], "count": m["width"] * m["height"],
                              "note": "%g m 格网，%s；统计范围内 %s 格" % (dtm["res"], m["dtype"], f(dtm["cells"]))})
        D["thresholds"] += thr.as_rows(T.DTM_KEYS)
    if has_net and has_dtm and net_result.get("E"):
        E, C = net_result["E"], net_result["checks"]
        eck = [{"code": "E1", "name": txt["E1"]["name"], "severity": "info", "route": "supply", "count": E["n"], "total": E["n"],
                "display": "%+g m" % E["offset"],
                "impact": "管线台账标高 ＝ DTM 地面高程 %+g m（%s 个节点的井盖减 DTM，中位数 %.2f m；%s 在 ±0.5 m 内，%s 在 ±1 m 内）。两者的基准都没有文字说明，这一关系是比对得出的" % (
                    E["offset"], f(E["n"]), E["median"], p(E["within05"], E["n"]), p(E["within1"], E["n"])),
                "fix": txt["E1"]["fix"], "byLayer": {}}]
        for k in ("E2", "E3", "E4", "E5"):
            v = C[k]
            eck.append({"code": k, "name": txt[k]["name"], "severity": T.severity(k), "route": T.route_of(k), "count": v["count"], "total": v["total"],
                        "impact": txt[k]["impact"], "fix": txt[k]["fix"],
                        "byLayer": {a.upper(): b for a, b in list(v.get("byLayer", {}).items())[:4]}})
        D["eChecks"] = eck
        D["hist"] = {"bins": ["<−2", "−2~−1", "−1~−0.5", "−0.5~−0.2", "±0.2", "0.2~0.5", "0.5~1", "1~2", ">2"], "counts": E["hist"]}
        D["histNote"] = "%s 个同时有井盖标高与 DTM 值的节点；横轴为「井盖 −（DTM %+g m）」。青色为 ±0.5 m 以内，黄色为超出" % (f(E["n"]), E["offset"])
        D["verdicts"].append({"title": "管线与 DTM 是否对得上", "level": "mid" if C["E3"]["count"] or C["E2"]["count"] else "good",
                              "verdict": "整体对得上，局部标高需要复测" if C["E3"]["count"] or C["E2"]["count"] else "对得上",
                              "points": ["把井盖标高与 DTM 地面高程换到同一基准后比较：%s 的井相差在 ±0.5 m 以内" % p(E["within05"], E["n"]),
                                         "%s 个井的井盖与地面相差超过 %g m" % (f(C["E2"]["count"]), thr.cover_dtm_tol),
                                         "<b>%s 个井的井底比地面还高</b>：井底标高或该处 DTM 至少有一个有误" % f(C["E3"]["count"]),
                                         "%s 个井的井底在地面以下超过 %g m，需要核实是否为深埋的干管" % (f(C["E4"]["count"]), thr.max_buried)]})
    # 建议：按途径归并，只列有命中的
    adv = {k: list(extra.get("advice", {}).get(k, [])) for k in ("supply", "survey", "desk", "std", "none")}
    for counter in D["pipeChecks"] + D["eChecks"]:
        if counter["count"] and counter["code"] != "E1" and counter["route"] in adv and not counter.get("notDone"):
            adv[counter["route"]].append("<b>%s %s</b>（%s）：%s" % (counter["code"], counter["name"], f(counter["count"]), counter["fix"]))
    if has_dtm:
        ck = {counter["code"]: counter for counter in dtm["checks"]}
        if ck["R2"]["sev"] != "ok":
            adv["supply"].insert(0, "<b>DTM 说明</b>：高程基准、采集时间、精度、是否已去除建筑物；以及建筑物轮廓或 DSM")
        if dtm["lows"]:
            adv["survey"].insert(0, "<b>DTM 低洼核实</b>：%d 处低于 −5 m 的连片洼地，对照现场或影像确认是否为下穿通道、基坑或数据错误" % len(dtm["lows"]))
        if dtm["spikes"] or dtm["pits"]:
            adv["survey"].append("<b>DTM 单点尖峰／深坑</b>（%s／%s）：对照影像确认，属噪声的请数据方滤除" % (f(dtm["spikes"]), f(dtm["pits"])))
    D["advice"] = {k: v for k, v in adv.items() if v}
    if verify:
        D["cross"] = verify["rows"]
        D["crossIntro"] = verify["intro"]
    D["defs"] = [["只读", "全部检查在数据的只读副本上进行，不修改、不回写客户数据"]]
    if has_net:
        D["defs"] += [["连通判定容差", "%g m：管端离节点或另一管端 %g m 以内视为相连" % ((net_result["summary"]["tolerance"],) * 2)],
                      ["标高缺失", "值为空、0、−9999 或低于 −50 m。字段不存在的图层单独注明"],
                      ["原始标高与加工字段", "只用 cover_level／invert_level 一类原始字段；groundelev／wellbottomelev 等数据加工时从 DEM 提取的字段不参与评估"],
                      ["出口", "出水口、渗井、泵站、蓄水池"]]
    D["defs"] += [["阈值", "每个阈值回答两个问题之一：数据错误（值落到这里多半是录错了，出处是通用工程经验，待客户确认）；标准符合（不满足 DMT 2022 设计手册，只作提示）。客户可按依据调整（--thresholds），调整后重跑，报告注明"],
                  ["程度", "影响建模：模型会跑不起来或结果错；需处理：要整改；提示：知道即可"]]
    if has_dtm:
        D["defs"] += [["DTM 无数据", "NoData 值、NaN 与小于 −1e30 的值"],
                      ["尖峰／深坑", "8 邻格都有效的格才判；统计范围的四边不判"]]
        if has_net:
            D["defs"].append(["DTM 与台账基准", "台账井盖 − DTM 地面高程的中位数，取整米"])
    D["footer"] = "本报告由 tools/dataqc 生成。" + (extra.get("footer") or "")
    t = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    js = open(os.path.join(HERE, "template.js"), encoding="utf-8").read()
    out = t.replace("__TITLE__", D["title"]).replace("__SCRIPT__", js).replace(
        "__DATA__", json.dumps(D, ensure_ascii=False, default=float).replace("</", "<\\/"))
    os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
    open(out_html, "w", encoding="utf-8").write(out)
    return D
