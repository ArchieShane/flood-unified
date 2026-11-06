"""本体运行时：把已有的计算结果物化成本体对象，提供取对象 / 查对象集 / 沿关系走 / 规则求值 / 执行动作。

后端（backend）就是 serve.State（或测试里的替身）：已经装好管网状态、情景包、调度、热点详情、世界模型对照等；
运行时不重算任何水力学，只做「对象化」与「动作编排」，每个动作的结果都是紧凑字典 + 一句人话小结（中英），并记进动作日志。
"""
from __future__ import annotations
import difflib
import json
import math
import re
import threading
import time
from urllib.parse import quote
from pathlib import Path

import numpy as np
import pandas as pd

from . import schema as S
from .. import scenarios
from ..gwmcompare import _xy
from .actions import DispatchActions, LifecycleActions, PointsActions, SystemActions, PlanActions, CatchmentActions, HydroActions
from .common import ActionError, OBJ_EN, OBJ_ZH, _f, _fmt, _traffic_brief, _v2_brief, clean, norm_fp_id          # re-export：老的 from .runtime import clean / ActionError 照旧

ROOT = Path(__file__).resolve().parents[2]
_FP_RE = re.compile(r"\b(ADM|AAM|DRM|HS)\s*[-_ ]?\s*(\d{1,4})\b", re.I)


_ST_RE = [(re.compile(r"L2\+A\+B"), "L2+suggested"), (re.compile(r"L2\+A\+C"), "L2+planned+C"), (re.compile(r"L2\+A(?![+A-Za-z0-9])"), "L2+planned")]


def st_disp(o):
    """态的显示名（2026-09-28 用户定）：L2+A → L2+planned，L2+A+B → L2+suggested，L2+B 不改。
    只改给人看的摘要与结果；ui（控制台深链接）里的键不动。"""
    if isinstance(o, str):
        for r, w in _ST_RE:
            o = r.sub(w, o)
        return o
    if isinstance(o, dict):
        return {st_disp(k) if isinstance(k, str) else k: st_disp(v) for k, v in o.items()}
    if isinstance(o, list):
        return [st_disp(v) for v in o]
    return o


class Runtime(PointsActions, DispatchActions, LifecycleActions, SystemActions, PlanActions, CatchmentActions, HydroActions):
    def __init__(self, backend, cfg: dict | None = None):
        self.b = backend
        self.cfg = cfg if cfg is not None else backend.cfg
        AG = self.cfg.get("agent") or {}
        self.obs_path = ROOT / str(AG.get("observations_file", "out/observations.jsonl"))
        self.log_path = ROOT / str(AG.get("action_log", "out/ontology_actions.jsonl"))
        self._lock = threading.RLock()
        self._cache: dict = {}
        self._local = threading.local()


    @property
    def root(self) -> Path:
        return ROOT                                                 # 取模块级 ROOT（测试会 monkeypatch 它），动作里用 self.root 算相对路径

    def _rel(self, p) -> str:
        p = Path(p)
        return p.relative_to(self.root).as_posix() if str(p).startswith(str(self.root)) else p.as_posix()

    # ---------------------------------------------------------------- 情景
    @property
    def default_scenario(self) -> str | None:
        return getattr(self._local, "scenario", None)

    @default_scenario.setter
    def default_scenario(self, v):
        self._local.scenario = v

    def scenario_keys(self) -> list[str]:
        return list(scenarios.keys(self.cfg))

    def scenario_key(self, s=None) -> str:
        keys = self.scenario_keys()
        if s is None or str(s).strip() == "" or str(s).strip().lower() in ("current", "active", "now", "default", "当前", "当前情景", "默认", "现在"):
            d = self.default_scenario or getattr(self.b, "active_rp", None)
            return str(d) if d is not None and str(d) in keys else ("10" if "10" in keys else keys[0])
        t = str(s).strip().lower()
        if t in keys:
            return t
        m = re.search(r"20\d\d-\d\d", t)
        if m and m.group(0) in keys:
            return m.group(0)
        digits = re.findall(r"\d+", t)
        if ("cc" in t or "气候" in t or "climate" in t or "+20" in t) and digits and f"{digits[0]}cc" in keys:
            return f"{digits[0]}cc"
        if digits and digits[0] in keys:
            return digits[0]
        raise ActionError(f"不认识的情景 {s!r}；可用：{keys}")

    # ---------------------------------------------------------------- 装载（都带缓存）
    def _memo(self, key, fn):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            v = fn(); self._cache[key] = v
            return v

    def invalidate(self, prefix: str | None = None):
        with self._lock:
            for k in [k for k in self._cache if prefix is None or str(k[0] if isinstance(k, tuple) else k).startswith(prefix)]:
                self._cache.pop(k, None)

    def bundle(self, key: str) -> dict:
        return self._memo(("bundle", key), lambda: self.b.scenario_bundle(key))

    def load_flood_points(self) -> pd.DataFrame:
        def _load():
            p = ROOT / "out" / "state" / "hotspots_enriched.parquet"
            df = pd.read_parquet(p) if p.exists() else getattr(self.b, "hotspots", None)
            if df is None:
                return pd.DataFrame(columns=["hotspot_id"])
            df = df.copy()
            if "fp_id" not in df.columns:
                df["fp_id"] = df["hotspot_id"].astype(str)
            if "coord_source" not in df.columns:
                df["coord_source"] = "xlsx"
            return df.reset_index(drop=True)
        return self._memo(("fp_df",), _load)

    def _fp_index(self) -> dict[str, dict]:
        def _ix():
            df = self.load_flood_points()
            es_zone = self._zone_of_gid()
            out = {}
            for r in df.to_dict("records"):
                r = clean(r); fp = str(r["fp_id"]); r["fp_id"] = fp
                r["zone_name"] = es_zone.get(int(r["edge_gid"])) if r.get("edge_gid") is not None else None
                r["zone_kind"] = "network" if r.get("zone_name") else None
                if not r.get("zone_name"):
                    # AAM / DRM（以及没匹配到管段的 ADM 点）没有管网片区：用客户清单的 area（区 / 城镇）当片区名，kind=area；片区概览只给点的统计
                    a = r.get("area")
                    if a is not None and str(a).strip() and str(a).strip().lower() != "nan":
                        r["zone_name"] = str(a).strip(); r["zone_kind"] = "area"
                out[fp] = r
            return out
        return self._memo(("fp_ix",), _ix)

    def _zone_of_gid(self) -> dict[int, str]:
        def _z():
            es = getattr(self.b, "es", None)
            if es is None or "gid" not in es.columns:
                return {}
            return dict(zip(es["gid"].astype(int).values, es["zone_name"].values))
        return self._memo(("gid_zone",), _z)

    def load_assignments(self, key: str) -> dict[str, list[dict]]:
        def _a():
            dp = (self.bundle(key) or {}).get("dispatch") or {}
            out: dict[str, list[dict]] = {}
            for r in dp.get("plan") or []:
                r = clean(r); fp = str(r.get("fp_id"))
                a = {"assignment_id": f"{key}@{r.get('unit_id')}@{fp}", "scenario_key": key, "wave": r.get("wave"), "unit_id": r.get("unit_id"), "model": r.get("model"), "fp_id": fp, "depot_id": r.get("from"),
                     "depart_min": r.get("depart_min"), "arrive_min": r.get("arrive_min"), "pumping_from_min": r.get("pumping_from_min"), "done_min": r.get("done_min"), "rate_m3h": r.get("rate_m3h"),
                     "relay_stages": int(_f(r.get("relay_stages"), 0) or 0), "within_target": bool(r.get("within_target")), "_type": "Assignment"}
                a["_pk"] = a["assignment_id"]; a["_title"] = fp
                out.setdefault(fp, []).append(a)
            return out
        return self._memo(("assign", key), _a)

    def load_gwm_hits(self, key: str) -> dict[str, dict]:
        def _g():
            try:
                r = self.b.gwm_points(key)
            except Exception:
                return {}
            if not r or r.get("error"):
                return {}
            return {str(h["hotspot_id"]): clean(h) | {"run_id": r.get("run_id")} for h in r.get("hotspot_hits") or []}
        return self._memo(("gwm_hits", key), _g)

    def load_states(self, key: str) -> dict[str, dict]:
        def _s():
            dp = (self.bundle(key) or {}).get("dispatch") or {}
            feats = ((dp.get("points") or {}).get("features")) or []
            assign = self.load_assignments(key); hits = self.load_gwm_hits(key)
            tk = dp.get("tankers") or {}
            tank_pts = {(p if isinstance(p, str) else (p or {}).get("fp_id")) for p in (tk.get("points") or [])} if isinstance(tk.get("points"), list) else set()
            out = {}
            for f in feats:
                p = clean(f.get("properties") or {}); fp = str(p.get("fp_id"))
                rows = assign.get(fp, [])
                st = {k: p.get(k) for k in ("volume_m3", "max_depth_m", "duration_min", "spill_m3", "source", "top_kind", "facilities", "level", "level_priority", "target_drain_min", "priority", "dispatchable", "triage",
                                              "discharge_to", "discharge_dist_m", "relay_needed", "volume_now_m3", "inflow_now_m3h", "required_rate_m3h", "note", "note_en", "land_use", "client_criticality")}
                st.update({"state_id": f"{fp}@{key}", "fp_id": fp, "scenario_key": key, "served": bool(p.get("served")), "units_assigned": len(rows), "tanker_served": fp in tank_pts,
                           "first_arrive_min": (min(_f(r["arrive_min"], 9e9) for r in rows) if rows else None), "done_min": (max(_f(r["done_min"], 0) for r in rows) if rows and all(r.get("done_min") is not None for r in rows) else None),
                           "within_target": (all(bool(r.get("within_target")) for r in rows) if rows else None), "_type": "FloodState", "_pk": f"{fp}@{key}", "_title": fp})
                h = hits.get(fp)
                if h:
                    st.update({"gwm_core": h.get("fp_id"), "gwm_dist_m": h.get("dist_m"), "gwm_depth_m": h.get("depth_m"), "gwm_run_id": h.get("run_id")})
                else:
                    st.update({"gwm_core": None, "gwm_dist_m": None, "gwm_depth_m": None})
                st["world_model_confirmed"] = S.r_world_model(st, {})
                out[fp] = st
            return out
        return self._memo(("states", key), _s)

    def scenario_object(self, key: str, compute: bool = True) -> dict:
        cat = {s["key"]: s for s in scenarios.catalog(self.cfg)}
        s = clean({k: v for k, v in (cat.get(key) or scenarios.get(self.cfg, key)).items() if k in ("key", "kind", "label", "label_en", "i15_mmh", "p24_mm", "source", "source_en", "factor")})
        s["key"] = key
        b = self.bundle(key) if compute or ("bundle", key) in self._cache else None
        if b:
            sc = b.get("scenario") or {}; dp = b.get("dispatch") or {}; k = ((dp.get("kpis") or {}).get("solver") or {}); nf = sc.get("network_flood") or {}
            s.update({"n_points": k.get("points_total") or len(((dp.get("points") or {}).get("features")) or []), "points_by_level": (dp.get("levels") or {}).get("points") or k.get("points_by_level"), "n_served": k.get("points_served"), "units_used": k.get("units_used"),
                      "critical_within_target": k.get("critical_within_target"), "critical_total": k.get("critical_total"), "n_edges_over": sc.get("n_edges_over"), "surcharge_share": sc.get("surcharge_share"),
                      "spill_total_m3": nf.get("total_m3"), "decision_time_min": dp.get("decision_time_min"), "cost_total_aed": (dp.get("cost") or {}).get("total_aed"), "first_arrival_median_min": k.get("first_arrival_median_min")})
            s["alert_level"] = S.r_alert_level(s, {"cfg": self.cfg})
        s.update({"_type": "Scenario", "_pk": key, "_title": s.get("label")})
        return clean(s)

    def load_units(self, key: str) -> dict[str, dict]:
        def _u():
            dp = (self.bundle(key) or {}).get("dispatch") or {}
            models = {m["model"]: m for m in dp.get("fleet_models") or []}
            out = {}
            for r in dp.get("plan") or []:
                uid = str(r.get("unit_id")); m = models.get(r.get("model"), {})
                out[uid] = {"unit_id": uid, "model": r.get("model"), "kind": "tanker" if "tank" in str(r.get("model")).lower() else "pump", "rate_m3h": r.get("rate_nominal_m3h", m.get("rate_m3h")), "setup_min": m.get("setup_min"),
                            "depot_id": r.get("from"), "duty_aed_h": m.get("duty_aed_h"), "_type": "PumpUnit", "_pk": uid, "_title": uid}
            return clean(out)
        return self._memo(("units", key), _u)

    def load_depots(self, key: str) -> dict[str, dict]:
        def _d():
            dp = (self.bundle(key) or {}).get("dispatch") or {}
            out = {}
            for d in dp.get("depots") or []:
                did = str(d.get("depot_id"))
                out[did] = {"depot_id": did, "name": d.get("name"), "kind": "depot", "area": d.get("area"), "lon": d.get("lon"), "lat": d.get("lat"), "capacity_units": d.get("capacity_units"), "plan_a": d.get("plan_a"), "_type": "Depot", "_pk": did, "_title": d.get("name")}
            for lvl, pre in (dp.get("preposition") or {}).items():
                for s in (pre or {}).get("sites") or []:
                    sid = str(s.get("site_id"))
                    if sid not in out:
                        out[sid] = {"depot_id": sid, "name": s.get("name"), "kind": "staging", "area": None, "lon": s.get("lon"), "lat": s.get("lat"), "capacity_units": s.get("units_suggested"), "plan_a": None, "covers": {}, "_type": "Depot", "_pk": sid, "_title": s.get("name")}
                    out[sid].setdefault("covers", {})[lvl] = list(s.get("covers") or [])
            return clean(out)
        return self._memo(("depots", key), _d)

    def load_assets(self) -> dict[str, dict]:
        """资产台账：客户 L1/L2/L3 分级里的实体资产（swbrain.assets）。"""
        def _f():
            from .. import assets as FA
            out = {}
            for r in FA.load(self.root):
                q = dict(r)
                q.update({"_type": "Asset", "_pk": r["asset_id"], "_title": r.get("name")})
                out[r["asset_id"]] = clean(q)
            return out
        return self._memo(("assets",), _f)

    # ---- 汇水区与分析范围（2026-09-29 加；数据层 swbrain.drainage_units）
    def _units(self):
        def _u():
            from .. import drainage_units as DU
            U = DU.load_units(self.root); idx = DU.PointIndex(U)
            fps: dict[str, list[str]] = {}
            for fp, r in self._fp_index().items():
                for cid in idx.containing(r.get("lon"), r.get("lat")):
                    fps.setdefault(cid, []).append(fp)
            return U, idx, fps
        return self._memo(("units",), _u)

    def load_catchments(self) -> dict[str, dict]:
        def _c():
            from .. import drainage_units as DU
            U, _idx, fps = self._units(); out = {}
            for cid, u in U.items():
                q = DU.public(u); q["fp_ids"] = sorted(fps.get(cid, [])); q["n_flood_points"] = len(q["fp_ids"])
                q.update({"_type": "Catchment", "_pk": cid, "_title": u.get("name")}); out[cid] = clean(q)
            return out
        return self._memo(("catchments",), _c)

    def _domains_raw(self):
        def _d():
            from .. import drainage_units as DU
            return DU.load_domains(self.root)
        return self._memo(("domains_raw",), _d)

    def load_domains(self) -> dict[str, dict]:
        def _d():
            from pyproj import Transformer
            from shapely.geometry import Point
            from .. import drainage_units as DU
            T = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True); out = {}
            ix = self._fp_index()
            for did, d in self._domains_raw().items():
                g = d["_geom"]; inside, missing = [], []
                for fp, r in ix.items():
                    if r.get("lon") is None or r.get("lat") is None:
                        continue
                    x, y = T.transform(float(r["lon"]), float(r["lat"]))
                    if g.contains(Point(x, y)):
                        inside.append(fp)
                    elif fp in d["expected_fp_ids"]:
                        missing.append({"fp_id": fp, "dist_m": round(g.distance(Point(x, y)))})
                q = DU.public(d); q["fp_ids"] = sorted(inside); q["_missing"] = missing
                q.update({"_type": "AnalysisDomain", "_pk": did, "_title": d["name"]}); out[did] = clean(q)
            return out
        return self._memo(("domains",), _d)

    def catchments_of(self, fp: str) -> list[dict]:
        """积水点所在的汇水区单元，按来源可信度排序（客户图层 > KMZ > flood 库 39 面）。"""
        from .. import drainage_units as DU
        r = self._fp_index().get(fp)
        if not r:
            return []
        _U, idx, _fps_ = self._units(); C = self.load_catchments()
        ids = idx.containing(r.get("lon"), r.get("lat"))
        return sorted((C[i] for i in ids if i in C), key=lambda c: (DU.SOURCES[c["source"]]["rank"], -(c.get("area_ha") or 0) * 0))

    def catchment_ids(self, s) -> list[str]:
        """名称 / 编号 → 汇水区单元编号（同名可能有几套来源，都给）。"""
        C = self.load_catchments(); t = str(s or "").strip()
        if t in C:
            return [t]
        low = t.lower().replace(" ", "")
        hits = [k for k, c in C.items() if low in {str(c.get(f) or "").lower().replace(" ", "") for f in ("name", "new_number", "old_number", "title")}]
        if hits:
            from .. import drainage_units as DU
            return sorted(hits, key=lambda k: DU.SOURCES[C[k]["source"]]["rank"])
        raise ActionError(f"不认识的汇水区 {s!r}")

    def load_proposals(self) -> dict[int, dict]:
        def _p():
            out = {}
            for pid, p in (getattr(self.b, "by_pid", None) or {}).items():
                q = {k: p.get(k) for k in ("pid", "kind", "kind_en", "title", "title_en", "zone_name", "gid", "terminal", "priority", "confidence", "when_class", "when", "status", "regulatory_flag", "client_hotspot_id", "client_criticality", "benefit", "benefit_en")}
                q.update({"_type": "Proposal", "_pk": int(pid), "_title": p.get("title")}); out[int(pid)] = clean(q)
            from .. import lifecycle as LC
            for pid, ov in LC.proposal_status_overlay(self.cfg).items():             # set_proposal_status 推进过的状态（out/proposal_status.jsonl）
                if pid in out:
                    out[pid].update({k: v for k, v in ov.items() if v is not None})
            return out
        return self._memo(("props",), _p)

    def load_gwm_cores(self, key: str) -> dict[str, dict]:
        def _c():
            try:
                r = self.b.gwm_points(key)
            except Exception:
                return {}
            if not r or r.get("error"):
                return {}
            out = {}
            for f in (r.get("geojson") or {}).get("features") or []:
                p = clean(f.get("properties") or {}); cid = f"{r.get('run_id')}:{p.get('fp_id')}"
                out[str(p.get("fp_id"))] = {"core_id": cid, "run_id": r.get("run_id"), **{k: p.get(k) for k in ("fp_id", "lon", "lat", "max_depth_m", "volume_m3", "area_m2", "first_wet_min", "peak_min", "agreement", "hs_ids", "sys_ids", "note", "note_en")}, "_type": "GwmCore", "_pk": cid, "_title": p.get("fp_id")}
            return out
        return self._memo(("cores", key), _c)

    def load_alerts(self) -> list[dict]:
        d = ROOT / "out" / "alerts"
        out = []
        for p in sorted(d.glob("advisory_*.json")) if d.exists() else []:
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            fc = j.get("forecast") or {}; sc = j.get("scenario") or {}
            out.append({"alert_id": p.stem, "issued_at": j.get("issued_at"), "p24_mm": fc.get("p24_mm"), "lead_h": fc.get("lead_h"), "scenario_key": sc.get("key") or j.get("scenario_key"), "level": j.get("level"), "level_en": j.get("level_en"), "headline": j.get("headline"), "headline_en": j.get("headline_en"),
                        "n_actions": len(j.get("actions") or []), "_type": "Alert", "_pk": p.stem, "_title": j.get("headline")})
        return out

    def load_observations(self) -> list[dict]:
        def _o():
            out = []
            if self.obs_path.exists():
                for line in self.obs_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            o = json.loads(line); o.update({"_type": "Observation", "_pk": o.get("obs_id"), "_title": o.get("fp_id")}); out.append(o)
                        except Exception:
                            pass
            return out
        return self._memo(("obs",), _o)

    # ---------------------------------------------------------------- 生命周期：降雨事件 × 积水经历
    def load_events(self) -> dict[str, dict]:
        def _e():
            from .. import lifecycle as LC
            evs = LC.events(self.cfg)
            for e in evs.values():
                e.update({"_type": "RainEvent", "_pk": e["event_id"], "_title": e.get("label") or e["event_id"]})
            return evs
        return self._memo(("events",), _e)

    def event_key(self, s=None, prefer_open: bool = True) -> str:
        """事件编号归一：2024-04 / 2024年4月 / April 2024 / EV-…；缺省 = 未关闭的预报 / 进行中事件（prefer_open），否则最近一场历史事件。"""
        from .. import lifecycle as LC
        evs = self.load_events()
        if s is None or not str(s).strip() or str(s).strip().lower() in ("latest", "current", "open", "最近", "这场", "当前", "现在", "now"):
            opened = sorted([e for e in evs.values() if e.get("kind") in ("forecast", "live") and not e.get("closed_at")], key=LC.sort_key)
            if opened and prefer_open:
                return opened[-1]["event_id"]
            hist = sorted([e for e in evs.values() if e.get("kind", "historical") == "historical"], key=LC.sort_key)
            if hist:
                return hist[-1]["event_id"]
            if evs:
                return sorted(evs.values(), key=LC.sort_key)[-1]["event_id"]
            raise ActionError("没有任何降雨事件")
        t = str(s).strip()
        if t in evs:
            return t
        low = t.lower()
        m = re.search(r"(20\d\d)\D{0,3}(\d{1,2})", t)
        if m:
            k = f"{m.group(1)}-{int(m.group(2)):02d}"
            if k in evs:
                return k
        y = re.search(r"(20\d\d)", t)                                   # April 2024 / 2024 年 4 月 / Apr-2024
        if y:
            months = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
            mo = next((i + 1 for i, mn in enumerate(months) if mn in low), None)
            mc = re.search(r"(\d{1,2})\s*月", t)
            mo = mo or (int(mc.group(1)) if mc else None)
            if mo:
                k = f"{y.group(1)}-{int(mo):02d}"
                if k in evs:
                    return k
        for e in evs.values():
            if low and (low in str(e.get("label") or "").lower() or low in str(e.get("label_en") or "").lower() or low == str(e.get("date") or "").lower()):
                return e["event_id"]
        raise ActionError(f"不认识的事件 {s!r}；可用：{sorted(evs)}")

    def _states_for_event(self, ev: dict):
        k = ev.get("scenario_key")
        if not k or not self._has_backend_states() or str(k) not in self.scenario_keys():
            return None
        try:
            return self.load_states(str(k))
        except Exception:
            return None

    def load_episodes(self) -> dict[str, dict]:
        def _ep():
            from .. import lifecycle as LC, feeds as _feeds
            evs = self.load_events()
            try:
                inc = list(_feeds.load(self.cfg, "incidents").get("rows") or [])
            except Exception:
                inc = []
            known = {fp for fp, r in self._fp_index().items() if r.get("edge_gid") is not None}      # 模型评价得了的点（对上管网）；别的点模型没意见
            meta = {fp: f"{r.get('area') or ''} | {r.get('location') or ''}" for fp, r in self._fp_index().items()}
            return LC.build_episodes(self.cfg, evs, LC.episode_records(self.cfg), self.load_observations(), inc, self._states_for_event, fp_known=known, fp_meta=meta)
        return self._memo(("episodes",), _ep)

    def _episodes_by_fp(self) -> dict[str, list[dict]]:
        def _ix():
            from .. import lifecycle as LC
            evs = self.load_events(); out: dict[str, list[dict]] = {}
            for e in self.load_episodes().values():
                out.setdefault(e["fp_id"], []).append(e)
            for v in out.values():
                v.sort(key=lambda e: LC.sort_key(evs.get(e["event_id"]) or {}))
            return out
        return self._memo(("episodes_by_fp",), _ix)

    def episodes_of(self, fp: str) -> list[dict]:
        return list(self._episodes_by_fp().get(fp, []))

    def episodes_in(self, eid: str) -> list[dict]:
        return [e for e in self.load_episodes().values() if e["event_id"] == eid]

    def point_lifecycle(self, fp: str) -> dict:
        from .. import lifecycle as LC
        return LC.point_summary(self.episodes_of(fp), self.load_events())

    def _proposals_of(self, fp: str, r: dict | None = None) -> list[dict]:
        gid = (r or {}).get("edge_gid")
        return [p for p in self.load_proposals().values() if p.get("client_hotspot_id") == fp or (gid is not None and p.get("gid") == gid)]

    def event_object(self, eid: str) -> dict | None:
        from .. import lifecycle as LC
        e = self.load_events().get(eid)
        if e is None:
            return None
        v = LC.verification(self.episodes_in(eid), self.cfg)
        o = dict(e); o.update({"n_episodes": v["n_episodes"], "n_observed_flooded": v["n_observed_flooded"], "n_model_flooded": v["n_model_flooded"], "pod": v["pod"], "far": v["far"], "verification": v["verdict"], "_verification": v})
        return clean(o)

    def _invalidate_lifecycle(self):
        for k in ("events", "episodes", "episodes_by_fp", "props"):
            self.invalidate(k)

    # ---------------------------------------------------------------- 对象访问
    def get(self, otype: str, pk, scenario: str | None = None) -> dict | None:
        if otype not in S.OT:
            raise ActionError(f"没有对象类型 {otype}")
        key = self.scenario_key(scenario) if S.OT[otype].scoped or otype == "Scenario" else None
        if otype == "FloodPoint":
            fp = norm_fp_id(pk) or str(pk); r = self._fp_index().get(fp)
            if r is None:
                return None
            o = dict(r); o.update({"_type": "FloodPoint", "_pk": fp, "_title": r.get("location") or fp, "matched": fp in self.load_states(key or self.scenario_key(None)) if self._has_backend_states() else None})
            try:
                from .. import lifecycle as LC
                ls = self.point_lifecycle(fp)
                o.update({"n_events": ls["n_events"], "n_flooded": ls["n_flooded"], "recurrence": ls["recurrence"], "recurrence_model": ls["recurrence_model"], "last_flooded": ls["last_flooded"],
                          "lifecycle_status": ls["lifecycle_status"], "treatment_status": LC.treatment_status(self._proposals_of(fp, r))})
            except Exception as ex:                                     # 生命周期层出错不影响看点本身
                o["lifecycle_error"] = str(ex)[:120]
            return o
        if otype == "FloodState":
            fp = norm_fp_id(str(pk).split("@")[0]) or str(pk).split("@")[0]
            if "@" in str(pk):
                key = self.scenario_key(str(pk).split("@")[1])
            return self.load_states(key).get(fp)
        if otype == "Scenario":
            return self.scenario_object(self.scenario_key(pk))
        if otype == "Reach":
            es = getattr(self.b, "es", None)
            if es is None:
                return None
            row = es[es["gid"] == int(pk)]
            if not len(row):
                return None
            r = clean(row.iloc[0].to_dict()); keep = [p.name for p in S.OT["Reach"].properties]
            o = {k: r.get(k) for k in keep}; o["compliant"] = S.r_reach_compliance(o, {}); o.update({"_type": "Reach", "_pk": int(pk), "_title": r.get("unitid")})
            return o
        if otype == "Zone":
            zs = getattr(self.b, "zs", None)
            name = self.zone_name(pk)
            row = zs[zs["zone_name"] == name] if zs is not None else None
            pts = [r for r in self._fp_index().values() if r.get("zone_name") == name]
            if row is not None and len(row):
                o = clean(row.iloc[0].to_dict()); o["kind"] = "network"; o["n_flood_points"] = len(pts)
            elif pts:
                o = self._area_zone(name, pts)
            else:
                return None
            o.update({"_type": "Zone", "_pk": name, "_title": name})
            return o
        if otype == "PumpUnit":
            return self.load_units(key).get(str(pk))
        if otype == "Depot":
            return self.load_depots(key).get(str(pk))
        if otype == "Assignment":
            for rows in self.load_assignments(key).values():
                for a in rows:
                    if a["assignment_id"] == pk:
                        return a
            return None
        if otype == "Proposal":
            return self.load_proposals().get(int(pk))
        if otype == "GwmCore":
            cores = self.load_gwm_cores(key); k2 = str(pk).split(":")[-1]
            return cores.get(k2)
        if otype == "Alert":
            return next((a for a in self.load_alerts() if a["alert_id"] == pk), None)
        if otype == "Observation":
            return next((o for o in self.load_observations() if o.get("obs_id") == pk), None)
        if otype == "RainEvent":
            try:
                return self.event_object(str(pk) if str(pk) in self.load_events() else self.event_key(pk, prefer_open=False))
            except ActionError:
                return None
        if otype == "FloodEpisode":
            sp = str(pk)
            if "@" not in sp:
                return None
            fp, eid = sp.split("@", 1); fp = norm_fp_id(fp) or fp
            try:
                eid = eid if eid in self.load_events() else self.event_key(eid, prefer_open=False)
            except ActionError:
                return None
            return self.load_episodes().get(f"{fp}@{eid}")
        if otype == "Catchment":
            try:
                ids = self.catchment_ids(pk)
            except ActionError:
                return None
            return self.load_catchments().get(ids[0])
        if otype == "AnalysisDomain":
            return self.load_domains().get(str(pk).upper())
        if otype == "Asset":
            return self.load_assets().get(str(pk))
        return None

    def _has_backend_states(self) -> bool:
        return hasattr(self.b, "scenario_bundle")

    def search(self, otype: str, filters: dict | None = None, order_by: str | None = None, desc: bool = True, limit: int = 20, scenario: str | None = None) -> list[dict]:
        """对象集查询：等值过滤（值为 list 表示 in），排序，截断。FloodState 查询自动带上积水点静态属性。"""
        filters = dict(filters or {})
        if otype == "FloodState":
            key = self.scenario_key(scenario); ix = self._fp_index()
            rows = [{**ix.get(fp, {}), **st} for fp, st in self.load_states(key).items()]
        elif otype == "FloodPoint":
            rows = [self.get("FloodPoint", fp) for fp in self._fp_index()]
        elif otype == "Scenario":
            rows = [self.scenario_object(k, compute=False) for k in self.scenario_keys()]
        elif otype == "Zone":
            rows = [z for z in (self.get("Zone", n) for n in self.zone_names()) if z is not None]      # 管网片区 + 客户清单区域片区；NaN 名跳过
        elif otype == "Proposal":
            rows = list(self.load_proposals().values())
        elif otype == "Assignment":
            rows = [a for rs in self.load_assignments(self.scenario_key(scenario)).values() for a in rs]
        elif otype in ("PumpUnit", "Depot", "GwmCore"):
            rows = list({"PumpUnit": self.load_units, "Depot": self.load_depots, "GwmCore": self.load_gwm_cores}[otype](self.scenario_key(scenario)).values())
        elif otype == "Alert":
            rows = self.load_alerts()
        elif otype == "Observation":
            rows = self.load_observations()
        elif otype == "RainEvent":
            rows = [x for x in (self.event_object(k) for k in self.load_events()) if x]
        elif otype == "FloodEpisode":
            rows = list(self.load_episodes().values())
        elif otype == "Catchment":
            rows = list(self.load_catchments().values())
        elif otype == "AnalysisDomain":
            rows = list(self.load_domains().values())
        elif otype == "Reach":
            es = getattr(self.b, "es", None)
            if es is None:
                return []
            sub = es
            for k, v in filters.items():
                if k in sub.columns:
                    sub = sub[sub[k].isin(v)] if isinstance(v, list) else sub[sub[k] == v]
            if order_by in sub.columns:
                sub = sub.sort_values(order_by, ascending=not desc)
            return [self.get("Reach", g) for g in sub["gid"].head(limit)]
        else:
            raise ActionError(f"没有对象类型 {otype}")
        for k, v in filters.items():
            if v is None:
                continue
            rows = [r for r in rows if (r.get(k) in v if isinstance(v, list) else r.get(k) == v)]
        if order_by:
            rows.sort(key=lambda r: (_f(r.get(order_by)) is None, -(_f(r.get(order_by)) or 0) if desc else (_f(r.get(order_by)) or 0)))
        return rows[: max(int(limit), 1)]

    def links(self, otype: str, pk, link: str, scenario: str | None = None) -> list[dict]:
        L = S.LT.get(link)
        if L is None or L.src != otype:
            raise ActionError(f"{otype} 没有关系 {link}")
        key = self.scenario_key(scenario)
        o = self.get(otype, pk, scenario)
        if o is None:
            return []
        fp = o.get("fp_id")
        if link == "states":
            return [st for k in self.scenario_keys() if ("states", k) in self._cache for st in [self.load_states(k).get(fp)] if st]
        if link == "point":
            return [x for x in [self.get("FloodPoint", fp)] if x]
        if link == "scenario":
            return [self.scenario_object(o.get("scenario_key") or key)]
        if link == "drains_via":
            if o.get("edge_gid") is None:
                return []
            x = self.get("Reach", int(o["edge_gid"]))
            return [x] if x else []
        if link in ("zone", "reach_zone"):
            zn = o.get("zone_name")
            if not zn:
                return []
            try:
                x = self.get("Zone", zn)
            except ActionError:
                x = None
            return [x] if x else []
        if link == "assignments":
            return self.load_assignments(o.get("scenario_key") or key).get(fp, [])
        if link == "unit":
            return [x for x in [self.get("PumpUnit", o.get("unit_id"), o.get("scenario_key"))] if x]
        if link == "depot":
            return [x for x in [self.get("Depot", o.get("depot_id"), o.get("scenario_key"))] if x]
        if link == "proposals":
            gid = o.get("edge_gid")
            return [p for p in self.load_proposals().values() if p.get("client_hotspot_id") == fp or (gid is not None and p.get("gid") == gid)]
        if link == "zone_proposals":
            return sorted([p for p in self.load_proposals().values() if p.get("zone_name") == o.get("zone_name")], key=lambda p: -(_f(p.get("priority")) or 0))
        if link == "gwm_core":
            core = o.get("gwm_core")
            return [x for x in [self.get("GwmCore", core, o.get("scenario_key"))] if core and x]
        if link == "observations":
            return [x for x in self.load_observations() if x.get("fp_id") == fp]
        if link == "alerts":
            return [a for a in self.load_alerts() if str(a.get("scenario_key")) == str(o.get("key"))]
        if link == "staging_sites":
            k2 = o.get("scenario_key") or key
            return [d for d in self.load_depots(k2).values() if d.get("kind") == "staging" and fp in (d.get("covers") or {}).get("红", [])]
        if link == "episodes":
            return self.episodes_of(fp)
        if link == "ep_point":
            return [x for x in [self.get("FloodPoint", fp)] if x]
        if link == "ep_event":
            return [x for x in [self.get("RainEvent", o.get("event_id"))] if x]
        if link == "ep_state":
            ev = self.load_events().get(o.get("event_id")) or {}; k2 = ev.get("scenario_key")
            st = self.load_states(str(k2)).get(fp) if (k2 and self._has_backend_states() and str(k2) in self.scenario_keys()) else None
            return [st] if st else []
        if link == "event_episodes":
            return self.episodes_in(o.get("event_id"))
        if link == "event_scenario":
            k2 = o.get("scenario_key")
            return [self.scenario_object(str(k2), compute=False)] if k2 and str(k2) in self.scenario_keys() else []
        if link == "scenario_event":
            return [x for x in (self.event_object(k) for k, e in self.load_events().items() if str(e.get("scenario_key")) == str(o.get("key"))) if x]
        if link == "catchments":
            return self.catchments_of(fp)
        if link in ("catchment_points", "domain_points"):
            return [x for x in (self.get("FloodPoint", f) for f in (o.get("fp_ids") or [])) if x]
        if link == "catchment_assets":
            from pyproj import Transformer
            from shapely.geometry import Point
            U, _ix_, _fps_ = self._units(); g = U[o["catchment_id"]]["_geom"]; T = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
            return [a for a in self.load_assets().values() if a.get("lon") is not None and a.get("lat") is not None and g.contains(Point(*T.transform(float(a["lon"]), float(a["lat"]))))]
        if link == "domain_units":
            C = self.load_catchments()
            return [C[k] for k in (o.get("unit_ids") or []) if k in C]
        return []

    def rules(self, otype: str, obj: dict, scenario: str | None = None) -> list[dict]:
        """对一个对象求所有适用规则：derive 给值并与存量比对，validate/trigger 给是否触发。"""
        if obj is None:
            return []
        ctx = {"cfg": self.cfg, "runtime": self, "scenario": self.scenario_key(scenario)}
        o = dict(obj)
        if otype == "FloodPoint":
            st = self.load_states(ctx["scenario"]).get(o.get("fp_id")) if self._has_backend_states() else None
            obs = [x for x in self.load_observations() if x.get("fp_id") == o.get("fp_id")]
            o["_state"] = st; o["_latest_observation"] = obs[-1] if obs else None
            try:
                o["_episodes"] = self.episodes_of(o.get("fp_id")); o["_events"] = self.load_events(); o["_proposals"] = self._proposals_of(o.get("fp_id"), o)
            except Exception:
                o["_episodes"], o["_events"], o["_proposals"] = [], {}, []
            try:
                o["_catchments"] = self.catchments_of(o.get("fp_id")) if self._units()[0] else None      # 一套汇水区都没装上时不判
            except Exception:                                            # 汇水区数据层出错不影响其余规则
                o["_catchments"] = None
        elif otype == "Catchment":
            from .. import drainage_units as DU
            U, _ix_, _fps_ = self._units()
            o["_overlaps"] = DU.overlaps_same_source(U, o["catchment_id"]) if o.get("catchment_id") in U else []
        elif otype == "RainEvent":
            o["_episodes"] = self.episodes_in(o.get("event_id"))
        elif otype == "FloodEpisode":
            from .. import lifecycle as LC
            ev = self.load_events().get(o.get("event_id")) or {}; o["_event"] = ev
            obs = [x for x in self.load_observations() if x.get("fp_id") == o.get("fp_id") and LC.in_window(x.get("observed_at"), ev.get("start"), ev.get("end"))]
            o["_latest_observation"] = obs[-1] if obs else None
        out = []
        for r in S.RULES:
            if r.applies_to != otype:
                continue
            try:
                v = r.fn(o, ctx)
            except Exception as ex:
                v = {"error": str(ex)[:120]}
            item = {"rule": r.api, "kind": r.kind, "zh": r.zh, "en": r.en, "value": clean(v), "logic_zh": r.logic_zh, "logic_en": r.logic_en, "source": r.source}
            if r.kind == "derive":
                stored = None
                for p in S.OT[otype].properties:
                    if p.derived_by == r.api:
                        stored = o.get(p.name); break
                item["stored"] = clean(stored)
                item["consistent"] = (None if stored is None or v is None or isinstance(v, dict) else bool(stored == v))
            else:
                item["fired"] = v is not None
            out.append(item)
        return out

    # ---------------------------------------------------------------- 参数解析辅助
    def zone_names(self) -> list[str]:
        """管网片区（zs）在前，客户清单区域归并出的片区在后。"""
        zs = getattr(self.b, "zs", None)
        net = [str(z) for z in zs["zone_name"].dropna().tolist() if str(z).strip() and str(z).lower() != "nan"] if zs is not None else []
        area = sorted({str(r["zone_name"]) for r in self._fp_index().values() if r.get("zone_kind") == "area" and r.get("zone_name")})
        return net + [a for a in area if a not in net]

    @staticmethod
    def _area_zone(name: str, pts: list[dict]) -> dict:
        """客户清单区域（area）归并出来的片区：AAM / DRM 与没匹配到管段的 ADM 点没有管网片区，用客户表里的 area 当片区。只有点的统计，没有管长 / 风险分等管网指标。"""
        regions = sorted({str(r.get("region")) for r in pts if r.get("region")})
        dist = {"ADM": "Abu Dhabi", "AAM": "Al Ain", "DRM": "Al Dhafra"}
        crit: dict[str, int] = {}
        for r in pts:
            crit[str(r.get("criticality"))] = crit.get(str(r.get("criticality")), 0) + 1
        lons = [float(r["lon"]) for r in pts if r.get("lon") is not None]; lats = [float(r["lat"]) for r in pts if r.get("lat") is not None]
        return {"zone_name": name, "kind": "area", "region": (regions[0] if len(regions) == 1 else None), "district_name": "/".join(dist.get(x, x) for x in regions),
                "n_flood_points": len(pts), "n_with_reach": sum(1 for r in pts if r.get("edge_gid") is not None), "criticality_counts": crit,
                "lon": (sum(lons) / len(lons) if lons else None), "lat": (sum(lats) / len(lats) if lats else None),
                "source": "按客户清单 area 字段归并；没有管网片区指标（管长 / 风险分 / 超载 / 蓄渗缺口）", "source_en": "grouped by the client list's area field; no network-zone metrics (length / risk / overload / storage deficit)"}

    def zone_name(self, s) -> str:
        names = self.zone_names(); t = str(s or "").strip()
        if t in names:
            return t
        low = {n.lower(): n for n in names}
        if t.lower() in low:
            return low[t.lower()]
        hits = [n for n in names if t.lower() in n.lower() or n.lower() in t.lower()]
        if len(hits) == 1 or (hits and len(t) >= 4):
            return sorted(hits, key=len)[0]
        close = difflib.get_close_matches(t, names, n=3, cutoff=0.6)
        if len(close) == 1:
            return close[0]
        raise ActionError(f"不认识的片区 {s!r}" + (f"，是不是：{close}" if close else ""))

    def rule_api(self, s) -> str:
        t = str(s or "").strip().lower()
        if t in S.RL:
            return t
        alias = {"等级": "emergency_level", "级": "emergency_level", "level": "emergency_level", "时限": "target_drain_time", "target": "target_drain_time", "可派": "dispatchable", "dispatch": "dispatchable", "接力": "relay_factor", "relay": "relay_factor",
                 "达标": "within_target", "预警": "alert_level", "alert": "alert_level", "法定": "reach_compliance", "compliance": "reach_compliance", "标准": "reach_compliance", "坐标": "coordinate_suspect", "coordinate": "coordinate_suspect", "管网": "no_network_but_terminal",
                 "升级": "needs_escalation", "escalat": "needs_escalation", "观测": "observation_conflict", "observation": "observation_conflict", "世界模型": "world_model_confirmed", "world": "world_model_confirmed", "罐车": "tanker_viable", "tanker": "tanker_viable"}
        for k, v in alias.items():
            if k in t:
                return v
        for r in S.RULES:
            if t in r.zh.lower() or t in r.en.lower():
                return r.api
        raise ActionError(f"不认识的规则 {s!r}；有：{[r.api for r in S.RULES]}")

    # ---------------------------------------------------------------- 动作
    def validate(self, A: S.ActionType, params: dict) -> dict:
        P: dict = {}
        unknown = [k for k in params if A.param(k) is None]
        if unknown:
            raise ActionError(f"动作 {A.api} 没有参数 {unknown}")
        for p in A.params:
            v = params.get(p.name, p.default)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                if p.required:
                    raise ActionError(f"缺参数 {p.name}（{p.zh}）")
                P[p.name] = self.scenario_key(None) if p.type == "scenario" else (None if p.default is None else p.default)
                continue
            if p.type == "int":
                v = int(float(v))
            elif p.type == "float":
                v = float(v)
            elif p.type == "bool":
                v = (str(v).lower() in ("1", "true", "yes", "y", "是")) if not isinstance(v, bool) else v
            elif p.type == "enum":
                v = str(v)
                if v not in (p.choices or ()):
                    raise ActionError(f"{p.name} 应为 {list(p.choices or ())}，收到 {v!r}")
            elif p.type == "fp_id":
                fp = norm_fp_id(v)
                if fp is None or fp not in self._fp_index():
                    raise ActionError(f"没有积水点 {v!r}")
                v = fp
            elif p.type == "scenario":
                v = self.scenario_key(v)
            elif p.type == "zone":
                v = self.zone_name(v)
            elif p.type == "pid":
                v = int(float(v))
                if v not in self.load_proposals():
                    raise ActionError(f"没有建议 pid {v}")
            elif p.type == "rule":
                v = self.rule_api(v)
            else:
                v = str(v)
            if isinstance(v, float) and not math.isfinite(v):
                raise ActionError(f"{p.name} 不是有效数字")   # NaN 的比较恒为 False，会直接穿过下面的 lo/hi（2026-09-23 修）
            if p.lo is not None and isinstance(v, (int, float)) and v < p.lo:
                raise ActionError(f"{p.name} 不能小于 {p.lo}")
            if p.hi is not None and isinstance(v, (int, float)) and v > p.hi:
                raise ActionError(f"{p.name} 不能大于 {p.hi}")
            P[p.name] = v
        return P

    def act(self, api: str, params: dict | None = None, actor: str = "agent", lang: str = "zh") -> dict:
        A = S.AT.get(api)
        if A is None:
            raise ActionError(f"没有动作 {api}；有：{list(S.AT)}")
        t0 = time.perf_counter()
        try:
            if actor != "console":                                   # 管理端停用了的动作，智能体（含 DeepSeek Harness 经 MCP 来的）一律不能调
                from ..agents import registry as _reg
                if api not in _reg.allowed_actions(getattr(A, "owner", "dispatch")) or not _reg.effective(getattr(A, "owner", "dispatch"))["enabled"]:
                    raise ActionError(f"动作 {api} 已在管理端停用")
                # 自建智能体（2026-10-02）：它挑的是别的智能体名下的现成动作，所以除了上面那层（动作的主人停没停用它），
                # 还要判它自己：这个自建智能体启用着、且这个动作在它挑的那组里。调用方标签形如 agent:<id> / dsh:<id>
                m = re.search(r"(?:^|[:/])(?:agent|dsh):([a-z][a-z0-9_]{1,30})$", actor)
                cid = m.group(1) if m else None
                if cid and _reg.is_custom(cid):
                    if not _reg.effective(cid)["enabled"]:
                        raise ActionError(f"智能体 {cid} 已在管理端停用")
                    if api not in _reg.allowed_actions(cid):
                        raise ActionError(f"动作 {api} 不在智能体 {cid} 挑的那组动作里")
            from .. import identity as _id                         # 统一身份：按发起这次请求 / 对话的人的职能判（智能体调的也一样，不因为是智能体就放行）
            who = _id.current()
            if who is not None:
                if who.readonly and A.side_effect != "none":       # 只读入口（Harness 原生 Web UI）：会写数据的动作一律拒
                    actor = f"{who.actor}/{actor}"                 # 被拒也记下是谁被拒的
                    raise ActionError(f"{A.zh}会写数据；这个入口（Harness 原生 Web UI）只读，请到控制台用平台账号登录后按职能做")
                need = _id.action_function(api, params)
                if A.side_effect != "none" and who.refresh is not None:
                    fresh = who.refresh()
                    if fresh is None:
                        actor = f"{who.actor}/{actor}"
                        raise ActionError("会话已结束，请重新登录后再做")
                    fresh.refresh = who.refresh; who = fresh        # 写动作前现查平台：撤销立即生效
                actor = f"{who.actor}/{actor}"                     # 身份标签只落一次（原来在刷新前也落了一次，会叠成 sdip:x/sdip:x）
                if not who.has(need):
                    raise ActionError(f"{A.zh}要职能 {need}（{_id.FUNCTIONS.get(need, need)}），{who.name or who.principal_id} 没有")
            P = self.validate(A, params or {})
            res = getattr(self, f"_act_{api}")(P)
            ui = res.pop("_ui", None) or {}
            summ = {k: st_disp(v) for k, v in {"zh": getattr(self, f"_sum_{api}")(res, P, "zh"), "en": getattr(self, f"_sum_{api}")(res, P, "en")}.items()}
            out = {"action": api, "zh": A.zh, "en": A.en, "params": clean(P), "ok": True, "result": st_disp(clean(res)), "summary": summ.get(lang) or summ["zh"], "summary_zh": summ["zh"], "summary_en": summ["en"], "ui": clean(ui), "console_link": self.console_link(ui), "side_effect": A.side_effect}
        except ActionError as ex:
            out = {"action": api, "zh": A.zh, "en": A.en, "params": clean(params or {}), "ok": False, "error": str(ex), "summary": f"{A.zh}：{ex}" if lang == "zh" else f"{A.en}: {ex}", "ui": {}, "side_effect": A.side_effect}
        except Exception as ex:
            out = {"action": api, "zh": A.zh, "en": A.en, "params": clean(params or {}), "ok": False, "error": f"{type(ex).__name__}: {str(ex)[:200]}", "summary": f"{A.zh}执行出错：{str(ex)[:120]}" if lang == "zh" else f"{A.en} failed: {str(ex)[:120]}", "ui": {}, "side_effect": A.side_effect}
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self._log(actor, out)
        return out

    def _log(self, actor: str, out: dict):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "actor": actor, "action": out["action"], "params": out.get("params"), "ok": out.get("ok"), "ms": out.get("ms"), "summary": out.get("summary_zh") or out.get("summary"), "side_effect": out.get("side_effect")}
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---- 各动作 ----
    def _fp_line(self, o: dict, lang: str) -> str:
        crit = o.get("criticality") or "—"; loc = o.get("location") or ""
        if lang == "zh":
            cz = {"Very High": "极高", "High": "高", "Medium": "中", "Low": "低"}.get(crit, crit)
            return f"{o['fp_id']}（{loc}，{o.get('region') or ''} {o.get('area') or ''}，重要度 {cz}，根因 {o.get('root_cause') or '—'}，客户标{'有' if o.get('network_available') else '无'}管网）"
        return f"{o['fp_id']} ({loc}, {o.get('region') or ''} {o.get('area') or ''}, criticality {crit}, root cause {o.get('root_cause_raw') or o.get('root_cause') or '—'}, client network {'yes' if o.get('network_available') else 'no'})"

    # ---- 地图联动：高亮点位 + 控制台深链接 ----
    @staticmethod
    def _hl(rows, kind: str, id_key: str = "fp_id", label_key: str | None = None) -> list[dict]:
        out = []
        for r in rows or []:
            if r is None or r.get("lon") is None or r.get("lat") is None:
                continue
            out.append({"id": str(r.get(id_key)), "lon": float(r["lon"]), "lat": float(r["lat"]), "kind": kind, "label": (str(r.get(label_key)) if label_key and r.get(label_key) else str(r.get(id_key)))})
        return out

    def _fp_coords(self, fp: str) -> dict | None:
        o = self._fp_index().get(fp)
        return {"fp_id": fp, "lon": o.get("lon"), "lat": o.get("lat"), "location": o.get("location")} if o and o.get("lon") is not None else None

    @staticmethod
    def console_link(ui: dict) -> str | None:
        """把 ui 提示编成控制台深链接（相对路径，浏览器里已登录的口令 cookie 直接可用）。"""
        if not ui:
            return None
        q = []
        if ui.get("tab"):
            q.append(f"tab={ui['tab']}")
        if ui.get("rp"):
            q.append(f"rp={ui['rp']}")
        if ui.get("hs"):
            q.append(f"hs={ui['hs']}")
        if ui.get("gwm"):
            q.append("gwm=1")
        for k in ("l2p", "st", "base", "m"):                 # 防线 2+ 的视图 / 态 / 对比基准 / 地图模式（控制台读 v、st、base、m）
            if ui.get(k):
                q.append(f"{'v' if k == 'l2p' else k}={quote(str(ui[k]))}")
        hl = [h for h in (ui.get("highlight") or []) if h.get("kind") != "fac"]   # 设施编号控制台按积水点解析不了，只在页内高亮
        if hl:
            q.append("hl=" + ",".join(h["id"] for h in hl[:40])); q.append("hlk=" + str(hl[0].get("kind") or "points"))
        return "/?" + "&".join(q) if q else None

    def _batch_targets(self, P) -> tuple[str, list[str]]:
        key = P["scenario"]
        ids = [norm_fp_id(x) for x in str(P.get("fp_ids") or "").replace("，", ",").split(",") if x.strip()]
        ids = [i for i in ids if i]
        if not ids:
            filt = {k: P[k] for k in ("level", "criticality") if P.get(k)}
            if P.get("zone"):
                filt["zone_name"] = P["zone"]
            if not filt:
                filt["level"] = "III"
            rows = self.search("FloodState", filt, order_by="priority", limit=int(P.get("limit") or 5), scenario=key)
            ids = [r["fp_id"] for r in rows]
        cap = max(int(P.get("limit") or 5), len(ids)) if P.get("fp_ids") else int(P.get("limit") or 5)      # 明确给了编号就都算（最多 20）
        return key, ids[: min(cap, 20)]

    def _dispatch_payload(self, key: str, P: dict) -> dict:
        """情景调度载荷：给了决策时刻 / 非缺省目标 / 预算就现算（走 dispatch_at 的缓存），否则用情景包里的。"""
        ob = (P.get("objective") or "balanced"); rb = bool(P.get("robust")); want = P.get("decision_time_min") is not None or ob != "balanced" or P.get("budget_aed") is not None or rb
        if want and hasattr(self.b, "_ensure_ctx"):
            from .. import dispatch as _D1
            ctx, _ = self.b._ensure_ctx(key)
            if ctx is None:
                return {}
            t = float(P["decision_time_min"]) if P.get("decision_time_min") is not None else float(_D1.default_decision_time(ctx["fp"], self.cfg))
            try:
                return self.b.dispatch_at(t, ctx=ctx, key=key, objective=ob, budget_aed=P.get("budget_aed"), robust=rb) or {}
            except TypeError:                                      # 旧后端没有 objective / robust 参数
                return self.b.dispatch_at(t, ctx=ctx, key=key) or {}
        return (self.bundle(key) or {}).get("dispatch") or {}

    @staticmethod
    def _robust_sentence(rb, lang):
        if not isinstance(rb, dict) or not rb.get("by_scenario"):
            return ""
        W = rb.get("weights") or {}; L = rb.get("labels") or {}; LE = rb.get("labels_en") or {}
        def lab(k):
            return (L.get(k) if lang == "zh" else (LE.get(k) or L.get(k))) or k
        rows = []
        for k, e in rb["by_scenario"].items():
            if e.get("error"):
                continue
            b = e.get("base") or {}
            if lang == "zh":
                rows.append(f"{lab(k)}（权重 {W.get(k)}）达标 {e.get('within_target_worse')}/{e.get('points_in_plan')}" + (f"（单情景的单 {b.get('within_target_worse')}）" if b else "") + f"、还差 {e.get('extra_12in_equiv_total')} 台 12 寸当量" + (f"（单情景的单差 {b.get('extra_12in_equiv_total')}）" if b else ""))
            else:
                rows.append(f"{lab(k)} (weight {W.get(k)}) on-target {e.get('within_target_worse')}/{e.get('points_in_plan')}" + (f" (single-scenario plan {b.get('within_target_worse')})" if b else "") + f", {e.get('extra_12in_equiv_total')} × 12in short" + (f" (single-scenario {b.get('extra_12in_equiv_total')})" if b else ""))
        ex = rb.get("expected") or {}; exb = rb.get("expected_base") or {}
        if lang == "zh":
            s = " 鲁棒方案（处置包收益按情景权重取期望，一张单同时对几种雨）：" + "；".join(rows) + "。"
            if ex:
                s += f" 加权期望：达标 {_fmt(ex.get('within_target'), 1)} 点、缺口 {_fmt(ex.get('extra_12in_equiv_total'), 1)} 台" + (f"，单情景的单为 {_fmt(exb.get('within_target'), 1)} 点 / {_fmt(exb.get('extra_12in_equiv_total'), 1)} 台" if exb else "") + "。帕累托三方案与分区对照同样按鲁棒目标算。"
            return s
        s = " Robust plan (package benefit = weighted expectation over scenarios; one plan for several rains): " + "; ".join(rows) + "."
        if ex:
            s += f" Weighted expectation: {_fmt(ex.get('within_target'), 1)} points on target, {_fmt(ex.get('extra_12in_equiv_total'), 1)} units short" + (f" (single-scenario plan {_fmt(exb.get('within_target'), 1)} / {_fmt(exb.get('extra_12in_equiv_total'), 1)})" if exb else "") + ". Pareto and silo comparisons use the robust objective too."
        return s

    @staticmethod
    def _traffic_sentence(tr, lang):
        if not isinstance(tr, dict) or not tr.get("provider"):
            return ""
        prov = tr.get("provider")
        if lang == "zh":
            src = {"google": "Google Routes 实时 / 预测路况", "simulated": "模拟时段曲线（无 Google 密钥，页面标模拟）", "none": "关（系数 1）"}.get(prov, prov)
            return f" 行程按{src}放大：驻地拥堵系数 {_fmt(tr.get('factor_min'), 2)}–{_fmt(tr.get('factor_max'), 2)}，出发 {tr.get('departure_local')}。"
        src = {"google": "Google Routes live / predictive traffic", "simulated": "a simulated time-of-day curve (no Google key; marked simulated)", "none": "no traffic (factor 1)"}.get(prov, prov)
        return f" Travel times scaled by {src}: depot congestion factors {_fmt(tr.get('factor_min'), 2)}–{_fmt(tr.get('factor_max'), 2)}, departure {tr.get('departure_local')}."

    # ============================================================================
    # L2 设施装载器（2026-10-22）
    # ============================================================================

    def load_ponds(self, scenario_key: str | None = None, pond_ids: list[str] | None = None) -> dict[str, dict]:
        """
        装载调蓄池（Pond）对象。

        数据来源优先级：
        1. L2 优化候选库（tools/l2opt 生成的候选）
        2. 客户资产台账（Asset kind=pond）
        3. 咨询公司设计（20261019/test/*/Ponds.shp）

        Returns:
            {pond_id: Pond对象字典}
        """
        ponds = {}

        # TODO: 从候选库读取（当前占位实现）
        # 示例：从客户Asset中读取现有塘，转换为Pond格式
        assets = self.load_assets()
        for aid, asset in assets.items():
            if asset.get('kind') == 'pond':
                ponds[aid] = {
                    'pond_id': aid,
                    'name': asset.get('name', aid),
                    'pond_type': 'detention',  # 假设都是滞留池
                    'lon': asset.get('lon'),
                    'lat': asset.get('lat'),
                    'effective_volume_m3': asset.get('capacity_m3', 0),
                    'footprint_m2': asset.get('footprint_m2', 0),
                    'status': 'built' if asset.get('level') == 'L1' else 'planned',
                    'source': 'client_register',
                    'geometry': asset.get('geometry'),
                    # 其他属性暂用默认值或None
                    'safety_margin_pct': None,
                    'compliant': None,  # 需要运行compliance检查
                }

        # 按 pond_ids 过滤
        if pond_ids:
            ponds = {k: v for k, v in ponds.items() if k in pond_ids}

        return ponds

    def load_manholes(self, manhole_ids: list[str] | None = None) -> dict[str, dict]:
        """
        装载检查井（Manhole）对象。

        数据来源：
        1. L2 优化候选生成时自动布置的检查井
        2. 咨询公司设计（20261019/test/*/Manholes.shp）

        Returns:
            {manhole_id: Manhole对象字典}
        """
        manholes = {}

        # TODO: 当前占位实现，实际应从候选库或设计文件读取
        # 示例：从咨询公司设计读取（需要时实现）

        return manholes

    def load_gravity_pipes(self, pipe_ids: list[str] | None = None) -> dict[str, dict]:
        """
        装载重力管（GravityPipe）对象。

        数据来源：
        1. L2 优化候选中的重力管
        2. 咨询公司设计（20261019/test/*/Conduits.shp）

        Returns:
            {pipe_id: GravityPipe对象字典}
        """
        pipes = {}

        # TODO: 占位实现

        return pipes

    def load_pump_stations(self, ps_ids: list[str] | None = None) -> dict[str, dict]:
        """
        装载泵站（PumpStation）对象。

        数据来源：
        1. L2 优化候选中的泵站
        2. 客户资产台账（Asset kind=pump_station）
        3. 咨询公司设计（20261019/test/*/Pumps.shp + Wetwells.shp）

        Returns:
            {ps_id: PumpStation对象字典}
        """
        pump_stations = {}

        # TODO: 从候选库和资产台账读取
        # 示例：从客户Asset中读取现有泵站
        assets = self.load_assets()
        for aid, asset in assets.items():
            if asset.get('kind') == 'pump_station':
                pump_stations[aid] = {
                    'ps_id': aid,
                    'name': asset.get('name', aid),
                    'lon': asset.get('lon'),
                    'lat': asset.get('lat'),
                    'ps_type': 'surface',  # 默认地面泵站
                    'pump_count': asset.get('n_pumps', 1),
                    'status': 'built' if asset.get('level') == 'L1' else 'planned',
                    'source': 'client_register',
                    'geometry': asset.get('geometry'),
                    # 其他属性暂为None
                    'compliant': None,
                }

        if ps_ids:
            pump_stations = {k: v for k, v in pump_stations.items() if k in ps_ids}

        return pump_stations

    def load_pressure_pipes(self, pipe_ids: list[str] | None = None) -> dict[str, dict]:
        """装载压力管（PressurePipe）对象。"""
        return {}  # TODO

    def load_inlets(self, inlet_ids: list[str] | None = None) -> dict[str, dict]:
        """装载雨水口（Inlet）对象。"""
        return {}  # TODO

    def load_outfalls(self, outfall_ids: list[str] | None = None) -> dict[str, dict]:
        """装载出水口（Outfall）对象。"""
        return {}  # TODO

    def load_valves(self, valve_ids: list[str] | None = None) -> dict[str, dict]:
        """装载阀门（Valve）对象。"""
        return {}  # TODO

    def load_weirs(self, weir_ids: list[str] | None = None) -> dict[str, dict]:
        """装载堰（Weir）对象。"""
        return {}  # TODO


    # ---- 路况 / 外部源 ----
