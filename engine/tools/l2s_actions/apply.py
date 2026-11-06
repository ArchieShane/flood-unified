# -*- coding: utf-8 -*-
"""把选中的措施档（inp_patch）写进 inp（设计 0.3：统一由这里写，不各写各的）。
apply(src_inp, [(candidate, grade_index), ...], dst_inp) → 二维交换配置的补丁（新雨水口要进交换点表、格栅换大要逐节点面积），由调用方并进 pilot。"""
from __future__ import annotations

import math
import pathlib
import re


def _sections(text):
    order, sec, cur = [], {}, "__head__"
    sec[cur] = []; order.append(cur)
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s.upper()
            if cur not in sec:
                sec[cur] = []; order.append(cur)
            continue
        sec[cur].append(ln)
    return order, sec


def _join(order, sec):
    out = []
    for k in order:
        if k != "__head__":
            out.append(k)
        out.extend(sec[k]); out.append("")
    return "\n".join(out) + "\n"


def _rows(sec, k):
    return [ln for ln in sec.get(k, []) if ln.strip() and not ln.strip().startswith(";")]


def _ensure(order, sec, k, after=None):
    if k not in sec:
        sec[k] = []
        order.insert(order.index(after) + 1 if after in order else len(order), k)


def apply(src_inp, picks, dst_inp):
    order, sec = _sections(pathlib.Path(src_inp).read_text(encoding="utf-8", errors="ignore"))
    pilot = {"add_inlets": [], "grate_scale": {}}
    nid = [0]

    def new(prefix):
        nid[0] += 1
        return f"{prefix}{nid[0]:03d}"
    for cand, gi in picks:
        g = cand["grades"][gi]; P = g["inp_patch"]; kind = cand["kind"]
        if "xsections" in P:                                     # 管道扩容：改断面与糙率，管底不动
            xs = {ln.split()[0]: i for i, ln in enumerate(sec["[XSECTIONS]"]) if ln.strip() and not ln.strip().startswith(";")}
            cd = {ln.split()[0]: i for i, ln in enumerate(sec["[CONDUITS]"]) if ln.strip() and not ln.strip().startswith(";")}
            for c, v in P["xsections"].items():
                sec["[XSECTIONS]"][xs[c]] = f"{c}  {v['shape']}  {v['g1']}  {v['g2']}  0  0  1"
                q = sec["[CONDUITS]"][cd[c]].split(); q[4] = f"{v['n']:.4f}"; sec["[CONDUITS]"][cd[c]] = "  ".join(q)
        if "pumps" in P:                                         # 泵站扩容
            pp = P["pumps"]; rows = sec["[PUMPS]"]
            if pp["add_copy_of"]:
                src = next(ln for ln in rows if ln.split() and ln.split()[0] == pp["add_copy_of"]).split()
                src[0] = new("PU_ADD_"); rows.append("  ".join(src))
            else:
                curves = {ln.split()[3] for ln in rows if ln.split() and ln.split()[1] == pp["station"]}
                cr = sec["[CURVES]"]
                for i, ln in enumerate(cr):
                    q = ln.split()
                    if q and q[0] in curves:
                        nums = [j for j, v in enumerate(q) if re.fullmatch(r"-?[\d.]+", v)]
                        for j in nums[1::2]:                     # PUMP3 / PUMP4：（扬程, 流量）对，放大流量
                            q[j] = f"{float(q[j]) * pp['scale_flow']:.4f}"
                        cr[i] = "  ".join(q)
        if "storage" in P:                                       # 土工格室：STORAGE + 堰（节点 → 格室）+ 带拍门小孔（格室 → 节点）
            s = P["storage"]; sid = new("GC_")
            _ensure(order, sec, "[STORAGE]", "[JUNCTIONS]"); _ensure(order, sec, "[WEIRS]", "[CONDUITS]"); _ensure(order, sec, "[ORIFICES]", "[CONDUITS]")
            sec["[STORAGE]"].append(f"{sid}  {s['invert']}  {s['depth']}  0  FUNCTIONAL  0  0  {s['area']}  0  0")
            sec["[WEIRS]"].append(f"{sid}_W  {s['node']}  {sid}  TRANSVERSE  {s['weir_crest_rel']}  1.84  NO  0  0  NO")
            sec["[ORIFICES]"].append(f"{sid}_O  {sid}  {s['node']}  BOTTOM  0  0.6  YES  0")
            sec["[XSECTIONS]"].append(f"{sid}_W  RECT_OPEN  0.6  1.0  0  0")
            sec["[XSECTIONS]"].append(f"{sid}_O  CIRCULAR  {s['orifice_d']}  0  0  0")
            sec["[COORDINATES]"].append(f"{sid}  {s['x']}  {s['y']}")
        if "inlets" in P:                                        # 增设雨水口：新节点 + 连接管（DN300，坡度 ≥ 1 %）；交换点表另补
            jrows = {ln.split()[0]: ln.split() for ln in _rows(sec, "[JUNCTIONS]")}
            for it in P["inlets"]:
                iid = new("INL_ADD_"); to = it["to"]; t = jrows.get(to)
                if t is None:
                    continue
                inv_to = float(t[1]); rim_to = inv_to + float(t[2])
                inv = max(inv_to + 0.01 * it["len"], inv_to); dep = max(rim_to - inv, 0.6)
                sec["[JUNCTIONS]"].append(f"{iid}  {inv:.3f}  {dep:.3f}  0  0  0")
                sec["[CONDUITS]"].append(f"{iid}_C  {iid}  {to}  {it['len']}  0.0120  0  0  0  0")
                sec["[XSECTIONS]"].append(f"{iid}_C  CIRCULAR  0.3  0  0  0  1")
                sec["[COORDINATES]"].append(f"{iid}  {it['x']:.2f}  {it['y']:.2f}")
                pilot["add_inlets"].append({"id": iid, "x": it["x"], "y": it["y"]})
        if "grate_scale" in P:
            pilot["grate_scale"].update(P["grate_scale"])
        if "soakaway" in P:
            if P["soakaway"]["ksat_mmh"] is None:
                raise ValueError(f"{cand.get('id')}：渗井缺渗透系数（M-13），不能写入模型")
        if "outfall_gate" in P:
            o = P["outfall_gate"]; rows = sec["[OUTFALLS]"]
            for i, ln in enumerate(rows):
                q = ln.split()
                if q and q[0] == o:
                    q[-1] = "YES" if q[-1].upper() in ("NO", "YES") else q[-1]; rows[i] = "  ".join(q)
        if "storage_init" in P:
            rows = sec["[STORAGE]"]
            for i, ln in enumerate(rows):
                q = ln.split()
                if q and q[0] in P["storage_init"]:
                    q[3] = f"{P['storage_init'][q[0]]:.3f}"; rows[i] = "  ".join(q)
    pathlib.Path(dst_inp).write_text(_join(order, sec), encoding="utf-8")
    return pilot
