# -*- coding: utf-8 -*-
"""L2+suggested 动作清单（强化学习第 1 步）的公共部分：读治理后模型与结果、合规条目、统一候选格式。
设计：docs/L2S动作清单补全_7类措施候选规则_设计_20261030.md。"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
TOOLS = HERE.parent
ROOT = TOOLS.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(TOOLS))
from build_c1_l2 import read_inp  # noqa: E402
from swbrain.ontology import constraints as K  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
DOM = {d["id"]: d for d in json.loads((ROOT / "swbrain/ontology/constraints/domain.json").read_text(encoding="utf-8"))["items"]}
HAT = 1.3
# 占位单价（USD）：手册没有的项，逐项在 cost_basis 里写明「占位」
PH = {"pipe_m": {0.3: 400, 0.4: 500, 0.5: 600, 0.6: 800, 0.8: 1100, 1.0: 1500, 1.2: 2100, 1.5: 2800}, "box_m2_per_m": 1900, "reinstate_m": 150, "deep_factor": 1.5,
      "pump_per_036": 150_000, "elec_per_pump": 80_000, "wetwell_m3": 1_500, "geocell_m3_perm": 300, "geocell_m3_event": 60,
      "inlet_each": 8_000, "inlet_conn_m": 400, "grate_upgrade_each": 3_000, "soakaway_each": 25_000, "flap_per_m_dn": 40_000,
      "drawdown_event_per_m3": 0.4}
DN = (0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5)


def item(rule, layer, result, note, zh=""):
    """合规条目：rule = WA726-x-xxx / DOM-xx / C_xxx；result = pass / fail / unknown / review"""
    return {"rule": rule, "layer": layer, "result": result, "note": note, "zh": zh}


def status_of(items):
    if any(i["result"] == "fail" for i in items):
        return "不可建"
    return "有条件可建" if any(i["result"] in ("unknown", "review") for i in items) else "可建"


# ---------------- 读模型 ----------------
class Model:
    """治理后一维模型（inp）+ 耦合结果（2way.rpt）+ 二维结果（npz）+ 交换配置（pilot）"""

    def __init__(self, prefix, storm, state):
        self.run = f"{prefix}_{storm}"; self.state = state
        self.inp = WORK / f"{self.run}_{state}.inp"
        S = read_inp(self.inp); self.S = S
        self.xy = {r[0]: (float(r[1]), float(r[2])) for r in S.get("[COORDINATES]", [])}
        self.inv, self.maxd = {}, {}
        for r in S.get("[JUNCTIONS]", []):
            self.inv[r[0]] = float(r[1]); self.maxd[r[0]] = float(r[2])
        for r in S.get("[STORAGE]", []):
            self.inv[r[0]] = float(r[1]); self.maxd[r[0]] = float(r[2])
        for r in S.get("[OUTFALLS]", []):
            self.inv[r[0]] = float(r[1]); self.maxd[r[0]] = 0.0
        self.rim = {k: self.inv[k] + self.maxd[k] for k in self.inv}
        self.xs = {r[0]: r[1:] for r in S.get("[XSECTIONS]", [])}
        self.cond = {}
        for r in S.get("[CONDUITS]", []):
            x = self.xs.get(r[0], ["DUMMY", "0"])
            self.cond[r[0]] = {"n1": r[1], "n2": r[2], "len": float(r[3]), "n": float(r[4]), "off1": float(r[5]), "off2": float(r[6]),
                               "shape": x[0], "d": float(x[1]) if len(x) > 1 else 0.0, "w": float(x[2]) if len(x) > 2 and x[0] != "CIRCULAR" else None}
        self.out_of = {}
        for c, v in self.cond.items():
            self.out_of.setdefault(v["n1"], []).append(c)
        self.pumps = [{"id": r[0], "n1": r[1], "n2": r[2], "curve": r[3], "on": float(r[5]) if len(r) > 5 else 0.0, "off": float(r[6]) if len(r) > 6 else 0.0}
                      for r in S.get("[PUMPS]", [])]
        self.storage = {r[0]: r for r in S.get("[STORAGE]", [])}
        self.outfalls = {r[0]: r for r in S.get("[OUTFALLS]", [])}
        self.curves = {}
        for r in S.get("[CURVES]", []):
            nm = r[0]; vals = r[2:] if len(r) % 2 == 0 and not _num(r[1]) else r[1:]
            self.curves.setdefault(nm, []).extend(float(v) for v in vals if _num(v))
        rpt = C2D / f"{self.run}_{state}_2way.rpt"
        self.rpt = rpt.read_text(encoding="utf-8", errors="ignore") if rpt.exists() else ""
        self.flood = self._table("Node Flooding Summary", lambda q: (q[0], {"hours": float(q[1]), "rate_m3s": float(q[2]), "vol_m3": float(q[5]) * 1000.0}), 6)
        self.depth = self._table("Node Depth Summary", lambda q: (q[0], {"max_d": float(q[3]), "max_hgl": float(q[4])}), 5)
        self.surch = self._table("Conduit Surcharge Summary", lambda q: (q[0], {"both": float(q[1]), "up": float(q[2]), "dn": float(q[3]), "above_normal": float(q[4]),
                                                                                  "cap_limited": float(q[5])}), 6)
        pj = C2D / f"pilot_{self.run}_{state}.json"
        self.pilot = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}

    def _table(self, head, fn, ncol):
        m = re.search(re.escape(head) + r".*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", self.rpt, re.S)
        out = {}
        for ln in (m.group(1).splitlines() if m else []):
            q = ln.split()
            if len(q) >= ncol:
                try:
                    k, v = fn(q); out[k] = v
                except (ValueError, IndexError):
                    pass
        return out

    def cond_line(self, c):
        v = self.cond[c]
        return self.xy.get(v["n1"]), self.xy.get(v["n2"])


def _num(s):
    try:
        float(s); return True
    except ValueError:
        return False


def hotspots(crs=32640):
    """客户热点 + 高后果积水点（SWBRAIN_HOTSPOTS_EXTRA，同优化流程）"""
    H = __import__("hotspot_src").load(crs)
    return [(r.hotspot_id, r.geometry.x, r.geometry.y) for r in H.itertuples()]


def near_hs(x, y, H, r=200.0):
    best = min(((math.hypot(x - hx, y - hy), h) for h, hx, hy in H), default=(1e9, None))
    return best if best[0] <= r else (best[0], None)


def dn_up(d, k):
    """管径升 k 档；超过 1.5 m 返回 None（改箱涵）"""
    s = [x for x in DN if x > d + 1e-6]
    return s[k - 1] if len(s) >= k else None


def pipe_cost(d, length, depth):
    base = PH["pipe_m"].get(d) or PH["box_m2_per_m"] * d * d
    return (base + PH["reinstate_m"]) * length * (PH["deep_factor"] if depth > 4.0 else 1.0)


def write_out(out: pathlib.Path, cands, rejected, meta):
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(json.dumps({"meta": meta, "candidates": cands}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "rejected.json").write_text(json.dumps(rejected, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "compliance.json").write_text(json.dumps({c["id"]: c["compliance"] for c in cands}, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    summ = {"meta": meta, "by_kind": Counter(c["kind"] for c in cands), "by_status": Counter(f"{c['kind']}|{c['status']}" for c in cands),
            "rejected": Counter(r["kind"] for r in rejected),
            "pending": Counter(i["rule"] for c in cands for i in c["compliance"] if i["result"] in ("unknown", "review")),
            "data_flags": Counter(f for c in cands for f in c.get("data_flags", []))}
    (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return summ
