"""服务端状态：装载 out/state、按情景算上下文与调度、落盘缓存、求解子进程、任务表、本体与智能体。
HTTP 那一层在 serve.py，接口表在 routes.py；这里只管「算什么、缓存什么」。"""
from __future__ import annotations
import argparse
import json
import math
import time
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote
import numpy as np
import pandas as pd
from . import db, whatif, hydraulics as hy, refdata, simulate, dispatch, travel, export, contracts, gwm, floodpoints, replay, scenarios, networkflood, alerts, hotspotdetail, terrain, report, gwmcompare, recession, hotspots as hsm, traffic as trf, feeds, artifactcache as ac, jobs as jobsmod, assumptions as asm, configcheck
from .ontology import runtime as ontrt, agent as ontagent, schema as ontschema
from .contracts import InterventionSet

ROOT = Path(__file__).resolve().parents[1]


class State:
    def __init__(self, cfg: dict):
        t0 = time.time()
        self.cfg = cfg
        st = ROOT / "out" / "state"
        self.es = pd.read_parquet(st / "es.parquet")
        self.cs = pd.read_parquet(st / "cs.parquet")
        self.zs = pd.read_parquet(st / "zs.parquet")
        self.props = json.loads((st / "props.json").read_text(encoding="utf-8"))
        self.by_pid = {int(p["pid"]): p for p in self.props}
        self.summary = json.loads((ROOT / "out" / "summary.json").read_text(encoding="utf-8"))
        self.scenarios = {k: float(v) for k, v in cfg["model"]["scenarios"].items()}
        self.act = self.es[self.es["is_active"]].reset_index(drop=True)
        self.total_km = float(self.act["len_m"].sum()) / 1000.0
        self.sur = whatif.SurrogateEvaluator(self.es, self.cs, cfg, self.scenarios)
        # 全网溢流路由：DAG 分层一次算好（缓存 out/state/netflood_prep.npz），每个情景 0.5 s
        self.nf_prep = None; self.nf_cache: dict[str, dict] = {}
        try:
            self.nf_prep = networkflood.prepare(self.es, log=print)
        except Exception as ex:
            print("  溢流路由未准备：", str(ex)[:160], flush=True)
        # 落盘缓存（out/cache/<代码+配置+状态指纹>/）、求解子进程池、任务表：见 artifactcache.py / jobs.py
        SV = cfg.get("serve") or {}
        self.cache = ac.Cache(cfg, enabled=bool(SV.get("disk_cache", True)))
        self.state_manifest = ac.read_state_manifest()                  # out/state/manifest.json：这批状态的来源、行数、配置 / 代码指纹
        if self.state_manifest and self.state_manifest.get("config") and self.state_manifest["config"] != self.cache.info["config"]:
            print(f"  注意：out/state 是用另一份配置算的（清单 {self.state_manifest['config']} vs 现在 {self.cache.info['config']}），数字可能对不上，建议重跑 python -m swbrain.run", flush=True)
        self.pool = jobsmod.SolverPool(int(SV.get("solver_workers", 2)))
        self.jobs = jobsmod.Jobs(threads=int(SV.get("job_threads", 4)))
        # 调度上下文：路网 + 行程矩阵一次算好，之后按决策时刻重解只要几十毫秒
        self.dispatch_ctx = None
        self.ref = self.road = self.fleet = None
        self.rp_cache: dict[str, dict] = {}          # 情景 key → 调度上下文
        self.active_rp = None
        try:
            ref = refdata.load_cache()
            # 排除 flood_points.system-*.json：那是本系统自己写出去的产物（floodpoints.write），
            # 不排除的话启动时装的「初始积水点」就是上一次服务最后算过的那个情景，重启一次换一个分母
            # （run.py:202 与 artifactcache.py:54 早就这么排了，这里漏了）。2026-09-23 修。
            fp_files = sorted((f for f in (ROOT / cfg["exchange"]["dir"] / "predictions").glob("*flood_points*.json") if not f.name.startswith("flood_points.system-")), key=lambda p: p.stat().st_mtime)
            fp = contracts.load_flood_points(fp_files[-1]) if fp_files else json.loads((ROOT / cfg["exchange"]["dir"] / "examples" / "flood_points.simulated.json").read_text(encoding="utf-8"))
            ff = ROOT / cfg["exchange"]["dir"] / "fleet.json"
            fleet = contracts.load_fleet(ff) if ff.exists() else json.loads((ROOT / cfg["exchange"]["dir"] / "examples" / "fleet.simulated.json").read_text(encoding="utf-8"))
            road = travel.RoadNetwork(ref["edges"], ref["vertices"])
            self.hotspots = hsm.load(ROOT / cfg["hotspots"]["file"]) if "hotspots" in cfg else None
            self.flood_source = str(cfg["dispatch"].get("flood_source", "auto"))
            self.matched = None
            if self.hotspots is not None and self.flood_source in ("system", "auto") and floodpoints.available():
                try:
                    self.matched = floodpoints.match_hotspots(cfg)      # 与重现期无关，只算一次
                except Exception as ex:
                    print("本系统积水点匹配失败，回退世界模型：", str(ex)[:200])
            if not ff.exists() and self.hotspots is not None and (self.hotspots["region"] == "ADM").any():
                fa = ROOT / cfg["exchange"]["dir"] / "fleet.plan_a.json"
                want_regions = list(((cfg.get("dispatch") or {}).get("resources") or {}).get("regions") or ["ADM"])
                fleet = json.loads(fa.read_text(encoding="utf-8")) if fa.exists() else None
                if fleet is None or list(fleet.get("regions") or ["ADM"]) != want_regions:          # 缓存的车队与配置的市政范围不一致就重生成（三家市政都上）
                    fleet = hsm.fleet_from_plan_a(self.hotspots, ref["facilities"], cfg)
                    try:
                        fa.write_text(json.dumps(fleet, ensure_ascii=False, indent=1), encoding="utf-8")
                    except Exception:
                        pass
            self.ref, self.road, self.fleet = ref, road, fleet
            # 启动情景按配置定，不看「盘上哪个预测文件最新」。
            # 2026-09-23 之前是后者：排除 system-* 之前拿到的是本系统上一次算过的产物（重启一次换一个分母），
            # 排除之后拿到最新的世界模型文件（情景 50），而 config 写的是 flood_source = system——
            # 于是 50 年一遇的调度单全空（世界模型水深只涨不退，全部点判「入流大于可派排量」）。
            cat = scenarios.keys(cfg)
            want = str((cfg.get("serve") or {}).get("startup_scenario") or "").strip()
            rp0 = want if want in cat else (cat[0] if cat else None)
            cached = self.cache.get(f"ctx_{rp0}") if rp0 is not None else None
            if cached is not None:
                self.dispatch_ctx = cached; self.dispatch_ctx["fleet"] = fleet
                self.active_rp = str(rp0); self.rp_cache[str(rp0)] = self.dispatch_ctx
                print(f"  初始情景 {rp0} 的上下文来自盘上缓存 {self.cache.sig}", flush=True)
            else:
                # 缓存里没有：先用预测文件撑起一个上下文，服务能起来；但它只当自己用，
                # 绝不写进共享的 ctx_<key>，也不认领 active_rp 之外的情景。
                sc0 = fp.get("scenario") or {}
                rp_file = sc0.get("scenario_key") or sc0.get("return_period_yr")
                self.dispatch_ctx = dispatch.build_context(fp, fleet, ref["facilities"], self.cs, road, cfg, self.hotspots)
                self.dispatch_ctx["hotspot_stats"] = hsm.stats(self.hotspots, self.cs, fp, cfg) if self.hotspots is not None and len(self.hotspots) else None
                self.dispatch_ctx["fleet"] = fleet
                # 认领的情景只能是配置定的那个。拿预测文件的情景键当 active_rp 会让预热跳过它
                # （warm_up 会跳过 active_rp），于是那个情景永远停在预测文件的口径上——
                # config 写 flood_source=system，50 年一遇却一直是世界模型的点、调度单全空。2026-09-24 修。
                if rp0 is not None:
                    self.active_rp = str(rp0)
                if getattr(self, "matched", None) is not None and str(rp_file) != str(rp0):
                    self.dispatch_ctx = None               # 本系统能自己算积水点：别拿预测文件的上下文冒充别的情景
                    print(f"  初始情景 {rp0}：盘上还没有 ctx_{rp0}，预热时按 flood_source 装载", flush=True)
                else:
                    print(f"  初始情景 {rp0}：上下文来自预测文件 {rp_file}", flush=True)
        except Exception as ex:
            print("  调度上下文未装载：", str(ex)[:160], flush=True)
        self.loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.load_s = round(time.time() - t0, 1)
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()              # 同一情景的上下文只算一次（预热线程与用户请求可能同时要）
        self._want: list[str] = []; self._want_lock = threading.Lock()          # 用户正在等的情景：预热下一个先算它
        self._pay_inflight: dict = {}; self._pay_lock = threading.Lock()       # 正在解的调度：同参数的第二个请求等第一个，不再重解
        self.pay_cache: dict[tuple, dict] = {}          # _pay_key(情景, 决策时刻, 目标, 预算, 路况 id, 鲁棒) → 调度结果与调度单
        self.focus_cache: dict[tuple, dict] = {}        # 单点最优严格版的对比，另放一处：它的键形状不同，跟 pay_cache 混在一起就永远轮不到被清理
        self.warm = {"done": False, "keys": [], "errors": {}, "seconds": {}}
        self.gwm_cache: dict[str, dict] = {}            # 情景 key → 世界模型三方对照（按推演文件清单签名失效）
        # 路况：Google Routes（有密钥）/ 模拟时段曲线；系数按驻地套到行程矩阵上。每次刷新换一个 id，调度缓存键里带它
        TP = trf.params(cfg)
        self.traffic = {"id": 0, "meta": None, "at": 0.0, "P": TP, "cache": trf.Cache(ROOT / str(TP.get("cache_file") or "out/traffic_cache.json"), float(TP.get("cache_ttl_min", 15))), "refine": {}}
        self.tctx_cache: dict[tuple, dict] = {}
        self.ontology = ontrt.Runtime(self)             # 本体运行时：把上面这些结果物化成积水点为核心的对象 / 关系 / 规则 / 动作
        self.agent = ontagent.Agent(self.ontology, cfg)  # 交互入口：大模型或规则规划器，都只能做本体动作
        # 多智能体（2026-09-30）：主智能体把问题分派给调度研判（= 上面的 agent）/ 一二维计算 / 报告交付 / 数据质检；[agents].enabled = false 退回单智能体
        self.multi = None
        if ((cfg.get("agents") or {}).get("enabled", True)):
            from .agents.orchestrator import MultiAgent
            self.multi = MultiAgent(self.agent, self.ontology, cfg)

    # ---- 路况 ----
    def traffic_refresh(self, depart=None, t_min: float = 0.0, force: bool = False) -> dict:
        """算（或复用）驻地路况系数。Google 只在 TTL 过了或 force 时再请求（按元素计费）；模拟源随时算。返回 meta。"""
        T = self.traffic; P = T["P"]
        dep = trf.parse_departure(depart, t_min, P)
        ttl = float(P.get("cache_ttl_min", 15)) * 60.0
        if not force and T["meta"] is not None and time.time() - T["at"] <= ttl and (T["meta"].get("_dep_bucket") == int(dep.timestamp() // 900)):
            return T["meta"]
        ctx = self.dispatch_ctx if self.dispatch_ctx is not None else next(iter(self.rp_cache.values()), None)
        if ctx is None:
            return {"error": "调度上下文未装载", "provider": trf.provider(P)}
        meta = trf.depot_factors(ctx, P, dep, trf.api_key(P), T["cache"])
        if P.get("per_window", True) and not meta.get("error") and meta.get("provider") != "none":
            try:                                                         # 后续滚动窗口各取一次系数（早晚高峰进时间轴）；Google 时后续窗口抽样更少
                meta.update(trf.factor_series(ctx, P, dep, float(t_min or 0.0), trf.api_key(P), T["cache"], base=meta))
            except Exception as ex:
                meta["series_error"] = str(ex)[:160]
        meta["_dep_bucket"] = int(dep.timestamp() // 900); meta["refreshed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        T["meta"] = meta; T["at"] = time.time(); T["id"] += 1; T["refine"] = {}
        self.tctx_cache.clear()
        keep = T["id"] - 2                                              # 解按路况 id 分开缓存；只丢两代以前的，避免来回切决策时刻时反复重算
        self.pay_cache = {k: v for k, v in self.pay_cache.items() if not (isinstance(k, tuple) and len(k) == 6 and isinstance(k[4], int) and k[4] < keep)}
        self.focus_cache = {k: v for k, v in self.focus_cache.items() if not (isinstance(k, tuple) and len(k) == 5 and isinstance(k[4], int) and k[4] < keep)}
        return meta

    def _tctx(self, ctx: dict, t_min: float | None = None) -> dict:
        """套了当前路况系数的上下文（按 (ctx 身份, 路况 id) 缓存）。给了 t_min：第一次按「现在 + 决策时刻」出发取路况，之后若该决策时刻落在时变序列覆盖范围之外就按它重取（同一出发时段的起终点对走缓存）；
        t_min=None（重规划 / 压力测试 / 鲁棒里的其他情景）只套现有系数。"""
        if ctx is None:
            return ctx
        P = self.traffic["P"]
        meta = self.traffic["meta"]
        if P.get("auto_refresh", True):
            if meta is None:
                self.traffic_refresh(t_min=float(t_min or 0.0))
            elif t_min is not None and meta.get("series") and not meta.get("error"):
                lo = float(meta.get("t0_min") or 0.0); hi = lo + float(meta.get("window_min") or 60.0) * max(len(meta["series"]) - 1, 0)
                if not (lo - 1e-6 <= float(t_min) <= hi + 1e-6):
                    self.traffic_refresh(t_min=float(t_min))
        meta = self.traffic["meta"]
        if not meta or meta.get("error"):
            return ctx
        ck = (id(ctx), self.traffic["id"])
        v = self.tctx_cache.get(ck)
        if v is None:
            v = trf.apply(ctx, meta, P); self.tctx_cache[ck] = v
        return v

    def traffic_status(self, refresh: bool = False, depart=None, t_min: float | None = None, key: str | None = None) -> dict:
        """给控制台 / 智能体：源、系数、出发时刻、（Google 时）当前调度单的逐对精算。"""
        key = str(key or self.active_rp or "10")
        t = float(t_min) if t_min is not None else (dispatch.default_decision_time(self.dispatch_ctx["fp"], self.cfg) if self.dispatch_ctx else 0.0)
        meta = self.traffic_refresh(depart, t, force=bool(refresh)) if (refresh or self.traffic["meta"] is None) else self.traffic["meta"]
        out = {"status": trf.status(self.cfg), "meta": ({k: v for k, v in (meta or {}).items() if not k.startswith("_")}), "traffic_id": self.traffic["id"]}
        if meta and not meta.get("error"):
            F = meta.get("factors") or {}
            fs = sorted(((d, v) for d, v in F.items() if isinstance(v, dict)), key=lambda kv: -float(kv[1].get("factor", 1.0)))
            names = {str(d["depot_id"]): d.get("name") for d in ((self.fleet or {}).get("depots") or [])}
            out["by_depot"] = [{"depot_id": d, "name": names.get(d), **v} for d, v in fs]
            out["factor_min"] = round(min((float(v.get("factor", 1.0)) for _, v in fs), default=1.0), 3); out["factor_max"] = round(max((float(v.get("factor", 1.0)) for _, v in fs), default=1.0), 3)
            if meta.get("series"):
                ser = meta["series"]; depots_mean = {k: round(float(np.mean(list(v.values()))), 3) for k, v in ser.items() if v}
                out["series"] = {"t0_min": meta.get("t0_min"), "window_min": meta.get("window_min"), "mean_by_t": depots_mean, "min": meta.get("series_min"), "max": meta.get("series_max"), "elements": meta.get("series_elements")}
            if trf.provider(self.traffic["P"]) == "google":
                rk = (key, float(t)); rf = self.traffic["refine"].get(rk)
                if rf is None:
                    ctx, _ = self._ensure_ctx(key)
                    if ctx is not None:
                        pay = self.dispatch_at(t, ctx=ctx, key=key)
                        plan = pd.DataFrame(pay.get("plan") or [])
                        rf = trf.refine_plan(plan, ctx, self.traffic["P"], trf.parse_departure(depart, t, self.traffic["P"]), trf.api_key(self.traffic["P"]), self.traffic["cache"])
                        self.traffic["refine"][rk] = rf
                out["refine"] = rf
        return out

    # ---- 鲁棒目标（多情景期望）----
    def robust_spec(self, key: str, t_min: float, objective: str | None = None) -> dict | None:
        RB = (self.cfg.get("dispatch") or {}).get("robust") or {}
        W = dict(RB.get("scenarios") or {})
        if not RB.get("enabled", True) or not W:
            return None
        from . import dispatch2
        ctxs = {}
        for k in list(W.keys()) + ([key] if key not in W else []):
            c, _ = self._ensure_ctx(str(k))
            if c is not None:
                ctxs[str(k)] = self._tctx(c, None)
        labels = {k: (scenarios.get(self.cfg, k) or {}).get("label") for k in ctxs}
        labels_en = {k: (scenarios.get(self.cfg, k) or {}).get("label_en") for k in ctxs}
        base = self.dispatch_at(t_min, ctx=self._ensure_ctx(key)[0], key=key, objective=objective)
        base_plan = pd.DataFrame(base.get("plan") or []) if base and not base.get("error") else None
        spec = dispatch2.robust_spec(key, W, ctxs, labels, base_plan)
        spec["labels_en"] = labels_en
        return spec

    def dispatch_at(self, t_min: float, ctx: dict | None = None, key: str | None = None, objective: str | None = None, budget_aed: float | None = None, robust: bool = False) -> dict:
        """按决策时刻求解；同一 (情景, 时刻, 目标, 预算, 路况 id, 鲁棒) 只算一次，之后直接给缓存（预热线程用 ctx/key 参数，不碰当前情景）。行程矩阵套当前路况系数。"""
        ctx = ctx if ctx is not None else self.dispatch_ctx; key = key if key is not None else self.active_rp
        if ctx is None:
            return {"error": "调度上下文未装载（缺 out/state 路网缓存或积水区文件）"}
        ctx0 = ctx; ctx = self._tctx(ctx, float(t_min))
        ob = (objective or "").strip().lower() or None
        ck = self._pay_key(key, t_min, ob, budget_aed, robust)
        hit = self.pay_cache.get(ck)
        dk = self._pay_disk_key(key, t_min, ob, budget_aed, robust)
        if hit is None and key is not None:
            disk = self.cache.get(dk)
            if isinstance(disk, dict) and "pay" in disk:
                hit = {**disk, "pay": {**disk["pay"], "cached": "disk"}}; self.pay_cache[ck] = disk
        owner = None
        if hit is None:
            # 同一 (情景, 时刻, 目标, 预算, 路况, 鲁棒) 正在解：等它解完拿结果，不另起一份（dsh 工具超时重试、预热与用户同时要，原来都会重解）
            with self._pay_lock:
                ev = self._pay_inflight.get(ck)
                if ev is None:
                    owner = self._pay_inflight[ck] = threading.Event()
            if owner is None:
                ev.wait(timeout=1800)
                hit = self.pay_cache.get(ck)
        if hit is None:
            try:
                hit = self._solve_pay(ctx, key, t_min, ob, budget_aed, robust)
                if key is not None:
                    self.pay_cache[ck] = hit; self.cache.put(dk, hit)
            finally:
                if owner is not None:
                    with self._pay_lock:
                        self._pay_inflight.pop(ck, None)
                    owner.set()
        else:
            hit = {**hit, "pay": {**hit["pay"], "cached": hit["pay"].get("cached") or True}}
        if ctx0 is self.dispatch_ctx and not robust:
            self.last_plan = hit["plan"]; self.last_decision_min = hit["t"]
        return hit["pay"]

    def _solve_pay(self, ctx, key, t_min, ob, budget_aed, robust) -> dict:
        """只解、不碰缓存（缓存的键只在 dispatch_at 里由 _pay_key 产出）。"""
        t0 = time.perf_counter()
        spec = self.robust_spec(str(key), float(t_min), ob) if robust else None
        r = self.pool.run(dispatch.solve_dispatch, ctx, self.cfg, t_min, objective=ob, budget_aed=budget_aed, robust=spec)   # 子进程里解，主进程不被 GIL 卡住
        r["fleet"] = ctx["fleet"]
        pay = export.dispatch_payload(r, ctx["fp"])
        pay["computed_ms"] = round((time.perf_counter() - t0) * 1000, 1); pay["computed_at"] = time.strftime("%H:%M:%S"); pay["cached"] = False
        pay["traffic"] = ctx.get("traffic"); pay["robust"] = bool(robust and spec)
        if robust and spec and isinstance(pay.get("v2"), dict) and isinstance(pay["v2"].get("robust"), dict):
            pay["v2"]["robust"]["labels_en"] = spec.get("labels_en") or {}
        return {"pay": pay, "plan": r["plan"], "t": float(r["decision_time_min"])}

    def _pay_key(self, key, t_min, ob=None, budget_aed=None, robust: bool = False) -> tuple:
        """内存里 pay_cache 的键。只此一处产出——2026-09-23 之前有两处手写成 (key, t) 两元组，永远查不到。"""
        return (str(key), float(t_min), (ob or "").strip().lower() or "balanced", float(budget_aed or 0), self.traffic["id"], bool(robust))

    def _pay_disk_key(self, key, t_min, ob, budget_aed, robust) -> str:
        return self.cache.key_for("pay", str(key), round(float(t_min), 1), ob or "balanced", float(budget_aed or 0), ac.traffic_hash(self.traffic.get("meta")), bool(robust))

    def dispatch_cached(self, t_min: float, key: str | None = None, objective: str | None = None, budget_aed: float | None = None, robust: bool = False) -> dict | None:
        """只查缓存（内存或盘上），不算。给异步接口用：有就直接给，没有再排任务。"""
        key = key if key is not None else self.active_rp
        ob = (objective or "").strip().lower() or None
        ck = self._pay_key(key, t_min, ob, budget_aed, robust)
        hit = self.pay_cache.get(ck)
        if hit is None and self.cache.has(self._pay_disk_key(key, t_min, ob, budget_aed, robust)):
            return self.dispatch_at(t_min, key=key, objective=ob, budget_aed=budget_aed, robust=robust)
        return {**hit["pay"], "cached": True} if hit else None

    # ---- 情景上下文：算一次缓存一次，预热线程与用户请求共用 ----
    def _ensure_ctx(self, key: str) -> tuple[dict | None, dict]:
        """某情景的积水点 + 调度上下文；已缓存直接给，否则算（同一时刻只有一个线程在算）。不改 active_rp。"""
        info = {}
        ctx = self.rp_cache.get(key)
        if ctx is not None or self.road is None:
            return ctx, info
        mine = threading.current_thread().name != "warm-up"
        if mine:                                                 # 用户在等：登记，让预热线程下一个就算它（而不是排在整列后面）
            with self._want_lock:
                self._want.append(key)
        try:
            return self._ensure_ctx_locked(key, info)
        finally:
            if mine:
                with self._want_lock:
                    if key in self._want:
                        self._want.remove(key)

    def _ensure_ctx_locked(self, key: str, info: dict) -> tuple[dict | None, dict]:
        with self.build_lock:
            ctx = self.rp_cache.get(key)
            if ctx is not None:
                return ctx, info
            cached = self.cache.get(f"ctx_{key}")
            if cached is not None:
                cached["fleet"] = self.fleet; self.rp_cache[key] = cached; info["flood_source"] = "cache"; info["cache_sig"] = self.cache.sig
                return cached, info
            try:
                t1 = time.perf_counter()
                if getattr(self, "matched", None) is not None:
                    fp, ctx = self.pool.run(jobsmod.worker_system_scenario, self.cfg, key, self.matched, self.fleet, self.cs, getattr(self, "hotspots", None))   # 过程线 + Dijkstra 在子进程
                    info["flood_source"] = "system"; info["system_stats"] = fp["stats"]; info["gwm_ms"] = round((time.perf_counter() - t1) * 1000)
                else:
                    fp, r = gwm.rollout_for_return_period(self.cfg, key)
                    gwm.write_flood_points(fp, self.cfg)
                    info["flood_source"] = "prediction"; info["gwm_run_id"] = r["runId"]; info["gwm_metrics"] = r.get("metrics"); info["gwm_ms"] = round((time.perf_counter() - t1) * 1000)
                    t2 = time.perf_counter()
                    ctx = dispatch.build_context(fp, self.fleet, self.ref["facilities"], self.cs, self.road, self.cfg, getattr(self, "hotspots", None))
                    ctx["hotspot_stats"] = hsm.stats(self.hotspots, self.cs, fp, self.cfg) if getattr(self, "hotspots", None) is not None and len(self.hotspots) else None
                    ctx["fleet"] = self.fleet
                    info["context_ms"] = round((time.perf_counter() - t2) * 1000)
                self.rp_cache[key] = ctx; self.cache.put(f"ctx_{key}", ctx)
            except Exception as ex:
                info["gwm_error"] = str(ex)[:300]
        return ctx, info

    def warm_order(self) -> list[str]:
        """预热顺序：[serve].warm_first 里列的先（演示与值班最常问的情景），其余按情景目录。"""
        allk = list(scenarios.keys(self.cfg))
        first = []
        for x in (self.cfg.get("serve") or {}).get("warm_first") or []:
            try:
                k = self.ontology.scenario_key(str(x)) if getattr(self, "ontology", None) is not None else str(x)
            except Exception:
                k = str(x)
            if k in allk and k not in first:
                first.append(k)
        return first + [k for k in allk if k not in first]

    def _warm_done(self, k: str) -> bool:
        try:
            return self.cache.has(self._pay_disk_key(k, dispatch.default_decision_time(self.rp_cache[k]["fp"], self.cfg), None, None, False))
        except Exception:
            return False

    def _next_warm(self, pending: list[str]) -> str | None:
        """取下一个要预热的情景：用户正在等的先（_want，由 _ensure_ctx 登记），其余按 pending 顺序。"""
        with self._want_lock:
            for k in list(self._want):
                if k in pending:
                    pending.remove(k); return k
        return pending.pop(0) if pending else None

    def warm_up(self, log=print):
        """后台把目录里的全部情景算好（积水点、行程矩阵、缺省决策时刻的调度），演示切情景就不用等。
        顺序见 warm_order；有用户在等某个还没算的情景时，它插到队首（_next_warm）。"""
        keys = [k for k in self.warm_order() if k not in self.rp_cache]
        if self.active_rp is not None and self.dispatch_ctx is not None:
            keys = [k for k in keys if k != self.active_rp]      # 它的上下文已经装好了才跳过；空着就得预热，否则永远装不上
        if self.dispatch_ctx is not None and self.active_rp is not None:          # 当前情景的缺省调度也先算一遍
            t0 = time.perf_counter()
            self.dispatch_at(dispatch.default_decision_time(self.dispatch_ctx["fp"], self.cfg), ctx=self.dispatch_ctx, key=self.active_rp)
            self.warm["seconds"][self.active_rp] = round(time.perf_counter() - t0, 1); self.warm["keys"].append(self.active_rp)
        eager = str((self.cfg.get("serve") or {}).get("warm_dispatch", "all")).lower()          # all = 每个情景都解一遍（缺省：首问不再现解）；cached = 只解盘上有的
        self.warm["order"] = list(keys); pending = list(keys)
        while True:
            k = self._next_warm(pending)
            if k is None:
                break
            if k in self.rp_cache and self._warm_done(k):
                continue                                         # 用户请求时已经顺带算好
            self.warm["current"] = k
            t0 = time.perf_counter()
            ctx, info = self._ensure_ctx(k)
            if ctx is None:
                self.warm["errors"][k] = info.get("gwm_error", "no context"); log(f"  预热 {k}: 失败 {self.warm['errors'][k][:120]}", flush=True); continue
            t_def = dispatch.default_decision_time(ctx["fp"], self.cfg)
            if eager == "all" or self.cache.has(self._pay_disk_key(k, t_def, None, None, False)):
                self.dispatch_at(t_def, ctx=ctx, key=k); how = "含调度"
            else:
                how = "上下文（调度按需）"
            self.warm["seconds"][k] = round(time.perf_counter() - t0, 1); self.warm["keys"].append(k)
            if self.dispatch_ctx is None and str(k) == str(self.active_rp):
                self.dispatch_ctx = ctx                          # 启动时没装上的当前情景，预热到它就补上
            log(f"  预热 {k}: {self.warm['seconds'][k]} s（{how}，{info.get('flood_source')}）", flush=True)
        self.warm["current"] = None; self.warm["done"] = True
        log(f"预热完成：{len(self.warm['keys'])} 个情景，合计 {sum(self.warm['seconds'].values()):.0f} s", flush=True)

    # ---- 退水重演：一个积水点，无泵 vs 按调度单派泵 vs 手动派泵 ----
    def replay_point(self, fp_id: str, pumps_spec: str | None = None, t_min: float | None = None) -> dict:
        if self.dispatch_ctx is None:
            return {"error": "调度上下文未装载"}
        pts0 = self.dispatch_ctx["pts0"]
        hit = pts0[pts0["fp_id"].astype(str) == str(fp_id)]
        if not len(hit):
            return {"error": f"没有积水点 {fp_id}"}
        p = hit.iloc[0]; hyd = p.get("hydrograph") or []
        models = self.dispatch_ctx["fleet"]["models"]
        if pumps_spec:
            pumps = replay.parse_manual(pumps_spec, models); source = "manual"
        else:
            if getattr(self, "last_plan", None) is None or (t_min is not None and abs(float(t_min) - getattr(self, "last_decision_min", -1)) > 1e-6):
                self.dispatch_at(float(t_min) if t_min is not None else dispatch.default_decision_time(self.dispatch_ctx["fp"], self.cfg))
            rows = self.last_plan[self.last_plan["fp_id"].astype(str) == str(fp_id)] if len(self.last_plan) else self.last_plan
            pumps = replay.pumps_from_plan(rows); source = "plan"
        r = replay.replay(hyd, pumps, max_depth_m=float(p.get("max_depth_m") or 0) or None)
        r["point"] = {"fp_id": str(p["fp_id"]), "note": p.get("note"), "note_en": p.get("note_en"), "max_depth_m": p.get("max_depth_m"), "volume_m3": p.get("volume_m3"),
                      "target_drain_min": (float(p["target_drain_min"]) if p.get("target_drain_min") is not None and not pd.isna(p.get("target_drain_min")) else (p.get("target_drain_min_pt") or None)),     # 与调度 / 退水方案同一口径：min(设施时限, 客户重要度时限)
                      "target_client_min": p.get("target_drain_min_pt") or None, "land_use": p.get("land_use"), "source": p.get("source")}
        r["pumps_source"] = source; r["decision_time_min"] = getattr(self, "last_decision_min", None); r["models"] = [{"model": m["model"], "rate_m3h": m["rate_m3h"]} for m in models]
        return r

    # ---- 重现期：一个开关同时驱动第一道（管网筛查）与第二三道（世界模型 → 调度） ----
    def return_period(self, yr) -> dict:
        """yr 是情景 key（"10"、"10cc"、"2024-04"…，兼容整数）。"""
        t0 = time.perf_counter()
        key = str(yr); S = scenarios.get(self.cfg, key)
        I = self.cfg.get("idf") or {}
        out = {"yr": key, "key": key, "kind": S["kind"], "label": S["label"], "label_en": S["label_en"], "i15_mmh": S["i15_mmh"], "p24_mm": S["p24_mm"],
               "idf_source": S.get("source") or I.get("source"), "idf_source_en": S.get("source_en") or I.get("source_en")}
        out["scenario"] = self.scenario(float(S["i15_mmh"]) / float(S["factor"] or 1.0), factors=scenarios.zone_factors(self.cfg, S, self.act["zone_name"]), key=key)
        yr = key
        out["gwm_cached"] = yr in self.rp_cache
        ctx, info = self._ensure_ctx(yr); out.update(info)
        if ctx is not None:
            self.dispatch_ctx = ctx; self.active_rp = yr
            out.setdefault("gwm_run_id", ctx["fp"].get("run_id"))
            out["dispatch"] = self.dispatch_at(dispatch.default_decision_time(ctx["fp"], self.cfg))
        elif self.dispatch_ctx is not None:
            out["dispatch"] = self.dispatch_at(dispatch.default_decision_time(self.dispatch_ctx["fp"], self.cfg))
        out["total_ms"] = round((time.perf_counter() - t0) * 1000)
        return out

    # ---- 任意雨强：向量化重算，几十毫秒 ----
    def scenario(self, i_mmh: float, factors=None, key: str | None = None) -> dict:
        """任意 15 分钟雨强的管网筛查；factors 给每段管的雨强倍数（事件情景按片区不同）。"""
        t0 = time.perf_counter()
        m = self.cfg["model"]
        a = self.act
        c_ = a["runoff_c"].values if "runoff_c" in a.columns else m["runoff_c"]
        f_ = np.asarray(factors, dtype=float) if factors is not None else 1.0
        i_eff = i_mmh * f_
        q = c_ * (i_eff / 1000.0 / 3600.0) * a["area_m2"].values
        ratio = q / np.where(a["q_full"].values > 1e-9, a["q_full"].values, np.nan)
        over = ratio > self.cfg["decision"]["ratio_threshold"]
        over_km = float(a["len_m"].values[over].sum()) / 1000.0
        # 受影响进水口：按终点取最大上游进水口数（同一终点下的多段不重复计）
        df = pd.DataFrame({"terminal": a["terminal"].values[over], "n": a["n_inlets_up"].values[over], "zone": a["zone_name"].values[over], "len": a["len_m"].values[over]})
        affected = int(df.groupby("terminal")["n"].max().sum()) if len(df) else 0
        zl = a.groupby("zone_name")["len_m"].sum()
        byz = (df.groupby("zone")["len"].sum() / 1000.0).to_frame("over_km").join((zl / 1000.0).rename("len_km"), how="left") if len(df) else pd.DataFrame(columns=["over_km", "len_km"])
        byz["share"] = byz["over_km"] / byz["len_km"]
        byz = byz[byz["len_km"] >= self.cfg["decision"]["zone_min_km"]].sort_values("share", ascending=False).head(15)
        i_show = round(float(np.median(i_eff)) if factors is not None else i_mmh, 1)
        res = {"i_mmh": i_show, "i_base_mmh": i_mmh, "scenario_key": key, "i_range_mmh": ([round(float(np.min(i_eff)), 1), round(float(np.max(i_eff)), 1)] if factors is not None else None),
                "surcharge_km": round(over_km, 1), "surcharge_share": round(over_km / self.total_km, 4), "affected_inlets": affected,
                "n_edges_over": int(over.sum()), "total_km": round(self.total_km, 1),
                "zones": [{"zone_name": z, "over_km": round(float(r["over_km"]), 1), "len_km": round(float(r["len_km"]), 1), "share": round(float(r["share"]), 3)} for z, r in byz.iterrows()],
                "computed_ms": round((time.perf_counter() - t0) * 1000, 1), "computed_at": time.strftime("%H:%M:%S"), "method": "推理公式 Q=C·i·A 对每段管的曼宁满管能力，当场向量化重算 %d 段" % len(a),
                "method_en": "Rational method Q=C·i·A against each pipe's Manning full-flow capacity, recomputed on the fly for %d reaches" % len(a)}
        res["network_flood"] = self.network_flood(key, i_mmh)
        return res

    def scenario_bundle(self, key: str) -> dict:
        """某情景的筛查 + 调度 + 全网溢流，一次拿齐，不改当前情景（预警提示用）。"""
        S = scenarios.get(self.cfg, key)
        ctx, info = self._ensure_ctx(key)
        sc = self.scenario(float(S["i15_mmh"]) / float(S["factor"] or 1.0), factors=scenarios.zone_factors(self.cfg, S, self.act["zone_name"]), key=key)
        dp = self.dispatch_at(dispatch.default_decision_time(ctx["fp"], self.cfg), ctx=ctx, key=key) if ctx is not None else None
        return {"key": key, "label": S["label"], "label_en": S["label_en"], "p24_mm": S["p24_mm"], "scenario": sc, "dispatch": dp, "error": info.get("gwm_error")}

    def alert(self, p24_mm: float | None = None, key: str | None = None, lead_h: float = 24.0, area: str | None = None, source: str = "manual") -> dict:
        """预报 24 h 雨量（或直接给情景 key）→ 情景 → 等级 → 提示单。"""
        if key is None:
            if p24_mm is None:
                return {"error": "要 p24（预报 24 h 雨量 mm）或 scenario"}
            pick = alerts.pick_scenario(self.cfg, float(p24_mm))
        else:
            S = scenarios.get(self.cfg, key); pick = {"key": S["key"], "kind": S["kind"], "p24_mm": float(S["p24_mm"]), "label": S["label"], "label_en": S["label_en"], "delta_mm": 0.0}
            p24_mm = float(p24_mm) if p24_mm is not None else float(S["p24_mm"])
        b = self.scenario_bundle(pick["key"]); b["delta_mm"] = pick["delta_mm"]
        return alerts.advisory(self.cfg, b, {"p24_mm": float(p24_mm), "lead_h": float(lead_h), "area": area, "source": source, "issued_at": time.strftime("%Y-%m-%d %H:%M")})

    # ---- 单热点详情：匹配管段、上游汇流树、DTM 洼地面、三条退水曲线 ----
    def _upstream_tree(self, gid: int, max_edges: int = 600) -> dict | None:
        if not hasattr(self, "_pred"):
            act = self.es[self.es["is_active"].astype(bool) & self.es["u"].notna() & self.es["v"].notna()]
            self._pred: dict[int, list[int]] = {}; self._edge_uv: dict[int, tuple[int, int]] = {}; self._edge_attr: dict[int, tuple] = {}
            for g_, u_, v_, ln, dn in zip(act["gid"].values, act["u"].values, act["v"].values, act["len_m"].fillna(0).values, act["pipe_diameter"].values):
                self._pred.setdefault(int(v_), []).append(int(g_)); self._edge_uv[int(g_)] = (int(u_), int(v_)); self._edge_attr[int(g_)] = (float(ln), (None if pd.isna(dn) else float(dn)))
            vp = ROOT / "out" / "state" / "vertices.parquet"
            self._vll = pd.read_parquet(vp, columns=["id", "lon", "lat"]).set_index("id") if vp.exists() else None
        if gid not in self._edge_uv or self._vll is None:
            return None
        seen = set(); stack = [self._edge_uv[gid][0]]; edges = []
        while stack and len(edges) < max_edges:
            n = stack.pop()
            for g_ in self._pred.get(n, []):
                if g_ in seen:
                    continue
                seen.add(g_); edges.append(g_); stack.append(self._edge_uv[g_][0])
        ll = self._vll; feats = []
        for g_ in edges:
            u_, v_ = self._edge_uv[g_]
            if u_ in ll.index and v_ in ll.index:
                feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[float(ll.at[u_, "lon"]), float(ll.at[u_, "lat"])], [float(ll.at[v_, "lon"]), float(ll.at[v_, "lat"])]]},
                              "properties": {"gid": g_, "dn": self._edge_attr[g_][1]}})
        return {"n_edges": len(edges), "len_km": round(sum(self._edge_attr[g_][0] for g_ in edges) / 1000, 2), "truncated": len(edges) >= max_edges, "geojson": {"type": "FeatureCollection", "features": feats}}

    def hotspot_detail(self, hid: str, dn_steps: int = 2, key: str | None = None) -> dict:
        """单热点详情。key 指定情景时按该情景的调度上下文与全局单算曲线 / 换管（不改激活情景）；不指定用激活情景。
        2026-09-22 演练发现：以前一律用激活情景，智能体问 10cc 的换管 what-if 拿到的却是 50 年的数。"""
        hs = getattr(self, "hotspots", None)
        if hs is None:
            return {"error": "无客户热点清单"}
        enr = ROOT / "out" / "state" / "hotspots_enriched.parquet"
        if not hasattr(self, "_hs_enriched"):
            self._hs_enriched = pd.read_parquet(enr) if enr.exists() else None
        src = self._hs_enriched if self._hs_enriched is not None else hs
        row_hs = src[src["hotspot_id"].astype(str) == str(hid)]
        if not len(row_hs):
            return {"error": f"没有热点 {hid}"}
        h = {k: (None if (isinstance(v, float) and pd.isna(v)) else (v.item() if hasattr(v, "item") else v)) for k, v in row_hs.iloc[0].to_dict().items()}
        m = self.matched[self.matched["hotspot_id"].astype(str) == str(hid)] if getattr(self, "matched", None) is not None else None
        mrow = {k: (None if (isinstance(v, float) and pd.isna(v)) else (v.item() if hasattr(v, "item") else v)) for k, v in m.iloc[0].to_dict().items()} if m is not None and len(m) else {}
        egid = mrow.get("edge_gid") if mrow.get("edge_gid") is not None else h.get("edge_gid")
        reach = {}
        if egid is not None:
            e = self.es[self.es["gid"] == int(egid)]
            if len(e):
                cols = [c for c in ("gid", "unitid", "pipe_diameter", "slope", "n_manning", "q_full", "len_m", "zone_name", "area_m2", "tc_min", "i_cap_mmh", "ratio_design", "compliant", "design_class", "nf_spill_m3_10", "nf_backwater_10") if c in e.columns]
                reach = {k: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)) for k, v in e.iloc[0][cols].to_dict().items()}
        up = self._upstream_tree(int(egid)) if reach else None
        pond = None
        if terrain.available(self.cfg) and h.get("lon") is not None:
            try:
                pond = terrain.depression_polygon(self.cfg, float(h["lon"]), float(h["lat"]))
            except Exception as ex:
                pond = {"error": str(ex)[:160]}
        key = str(key) if key else self.active_rp; S = scenarios.get(self.cfg, key) if key else None
        cur = None
        # 情景上下文：激活情景用现成的 dispatch_ctx / last_plan；指定别的情景就装它自己的上下文与全局单（缓存在 ctx_cache / pay_cache）
        if key and key != self.active_rp:
            ctx_k, _ = self._ensure_ctx(key); pts0 = ctx_k["pts0"] if ctx_k is not None else None

            def plan_rows():
                if ctx_k is None:
                    return None
                t = dispatch.default_decision_time(ctx_k["fp"], self.cfg); self.dispatch_at(t, ctx=ctx_k, key=key)
                hit_p = self.pay_cache.get(self._pay_key(key, t)); df = hit_p["plan"] if hit_p is not None else None
                return df[df["fp_id"].astype(str) == str(hid)] if df is not None and len(df) else None
        else:
            pts0 = self.dispatch_ctx["pts0"] if self.dispatch_ctx is not None else None

            def plan_rows():
                if getattr(self, "last_plan", None) is None:
                    self.dispatch_at(dispatch.default_decision_time(self.dispatch_ctx["fp"], self.cfg))
                return self.last_plan[self.last_plan["fp_id"].astype(str) == str(hid)] if len(self.last_plan) else None
        if S is not None and reach and mrow.get("area_ha") is not None and mrow.get("tc_min") is not None:
            row = {"area_ha": mrow["area_ha"], "c": mrow.get("c"), "tc_min": mrow["tc_min"], "pond_m3": mrow.get("pond_m3"), "area": mrow.get("area")}
            hit = pts0[pts0["fp_id"].astype(str) == str(hid)] if pts0 is not None else None
            try:
                if hit is not None and len(hit) and hit.iloc[0].get("hydrograph"):
                    base_hyd = hit.iloc[0]["hydrograph"]
                    rows = plan_rows()
                    cur = hotspotdetail.curves(self.cfg, S, row, reach, base_hyd, rows, dn_steps); cur["no_gap"] = False
                else:
                    qcap = float(mrow.get("qcap") or reach.get("q_full") or 0.0)
                    base_hyd, v0 = hotspotdetail.hydrograph_for_capacity(self.cfg, S, row, qcap)
                    cur = hotspotdetail.curves(self.cfg, S, row, reach, base_hyd, None, dn_steps); cur["no_gap"] = v0 <= 0
            except Exception as ex:
                cur = {"error": str(ex)[:160]}
        elif S is not None and pts0 is not None:
            # 没匹配到管段（客户标无管网，按 5 m DTM 洼地估算）：过程线仍在，给无泵 / 派泵两条曲线，没有换管方案
            hit = pts0[pts0["fp_id"].astype(str) == str(hid)]
            if len(hit) and hit.iloc[0].get("hydrograph"):
                try:
                    rows = plan_rows()
                    cur = hotspotdetail.curves_no_reach(hit.iloc[0]["hydrograph"], rows)
                except Exception as ex:
                    cur = {"error": str(ex)[:160]}
        keep_h = ("hotspot_id", "region", "area", "location", "criticality", "network_available", "root_cause", "lon", "lat", "planA_pumps", "planA_tankers", "idd_planned_flag", "nf_spill_m3_10", "nf_backwater_10", "edge_gid")
        keep_m = ("edge_gid", "qcap", "area_ha", "tc_min", "c", "pond_m3", "match", "class", "dtm_z", "depth_max_m", "pond_volume_m3", "spill_z")
        return {"hotspot": {k: h.get(k) for k in keep_h}, "matched": {k: mrow.get(k) for k in keep_m}, "reach": reach, "upstream": up, "pond": pond, "curves": cur,
                "scenario_key": key, "scenario_label": (S["label"] if S else None), "scenario_label_en": (S["label_en"] if S else None)}

    # ---- v2 附加能力：重规划 / 单点严格对比 / 压力测试 / 预置到位 ----
    def _v2_base(self, key: str, t_min: float | None = None, objective: str | None = None) -> tuple[dict | None, dict | None, float]:
        ctx, _ = self._ensure_ctx(key)
        if ctx is None:
            return None, None, 0.0
        t = float(t_min) if t_min is not None else dispatch.default_decision_time(ctx["fp"], self.cfg)
        pay = self.dispatch_at(t, ctx=ctx, key=key, objective=objective)
        ob = (objective or "").strip().lower() or None
        ck = self._pay_key(key, t, ob)
        hit = self.pay_cache.get(ck)
        return self._tctx(ctx), {"pay": pay, "plan": (hit["plan"] if hit else pd.DataFrame(pay.get("plan") or []))}, t

    def replan(self, key: str, t_now: float, dry_points=None, failed_units=None, incidents=None, objective: str | None = None) -> dict:
        """现场重规划：报退干的点、故障的泵、993 报案 → 从 t_now 起重解，已出发的锁定。观测文件里 status=dry 的点自动并入 dry_points，exchange/incidents.json 自动并入 incidents。"""
        from . import dispatch2
        ctx, base, t0 = self._v2_base(key, None, objective)
        if ctx is None:
            return {"error": "调度上下文未装载"}
        dry = set(str(x) for x in (dry_points or []))
        obs_f = ROOT / str(((self.cfg.get("agent") or {}).get("observations_file")) or "out/observations.jsonl")
        if obs_f.exists():
            for line in obs_f.read_text(encoding="utf-8").splitlines():
                try:
                    o = json.loads(line)
                    if str(o.get("status")) == "dry":
                        dry.add(str(o.get("fp_id")))
                except Exception:
                    pass
        inc = list(incidents or [])
        feed = feeds.load(self.cfg, "incidents")
        inc += list(feed.get("rows") or [])
        r = self.pool.run(dispatch2.replan, ctx, self.cfg, base["plan"], t0, float(t_now), sorted(dry), list(failed_units or []), inc, (objective or "balanced"))
        r["replan"]["incidents_feed"] = {k: feed.get(k) for k in ("source", "url", "as_of", "fetched_at", "error") if feed.get(k) is not None} | {"rows": len(feed.get("rows") or [])}
        plan = r["plan"]
        trt = float(((self.cfg.get("dispatch") or {}).get("manual_levels") or {}).get("trt_min", 120))
        k = dispatch.kpis(plan, r["pts"], t0, trt) if len(plan) else {}
        rows = json.loads(export._j(plan.to_dict(orient="records"))) if len(plan) else []
        return {"scenario": key, "decision_time_min": t0, "t_now": float(t_now), "objective": r["objective"], "replan": r["replan"], "kpis": k, "cost": r["cost"], "windows": r["windows"],
                "plan": rows, "utilisation": r["utilisation"], "unserved_reasons": r["unserved_reasons"], "base_kpis": (base["pay"].get("kpis") or {}).get("solver")}

    def focus_compare(self, key: str, fp_id: str, t_min: float | None = None, objective: str | None = None) -> dict:
        from . import dispatch2
        ctx, base, t0 = self._v2_base(key, t_min, objective)
        if ctx is None:
            return {"error": "调度上下文未装载"}
        ck = (str(key), float(t0), str(fp_id), (objective or "balanced"), self.traffic["id"])
        hit = self.focus_cache.get(ck)
        if hit is None:
            hit = self.pool.run(dispatch2.focus_compare, ctx, self.cfg, t0, str(fp_id), None, (objective or "balanced"))
            self.focus_cache[ck] = hit
        return hit

    def stress_test(self, key: str, worse_key: str | None = None, t_min: float | None = None, objective: str | None = None) -> dict:
        """把 key 情景的调度单放到 worse_key（缺省：目录里下一个更重的设计情景）上重演。"""
        from . import dispatch2
        ctx, base, t0 = self._v2_base(key, t_min, objective)
        if ctx is None:
            return {"error": "调度上下文未装载"}
        keys = [k for k in scenarios.keys(self.cfg)]
        if not worse_key:
            cat = sorted([scenarios.get(self.cfg, k) for k in keys if scenarios.get(self.cfg, k).get("kind") in ("rp", "rp_cc")], key=lambda x: float(x["p24_mm"]))
            p_now = float(scenarios.get(self.cfg, key)["p24_mm"]); worse = [c for c in cat if float(c["p24_mm"]) > p_now + 0.5]
            worse_key = worse[0]["key"] if worse else None
        if not worse_key or worse_key == key:
            return {"error": "没有更重的情景可比"}
        ctx_w, _ = self._ensure_ctx(worse_key)
        if ctx_w is None:
            return {"error": f"情景 {worse_key} 的上下文未装载"}
        ctx_w = self._tctx(ctx_w)
        S = scenarios.get(self.cfg, worse_key)
        r = self.pool.run(dispatch2.stress_test, ctx_w, self.cfg, base["plan"], t0, label=S.get("label"))
        r.update({"scenario": key, "worse_key": worse_key, "worse_label": S.get("label"), "worse_label_en": S.get("label_en"), "worse_p24_mm": S.get("p24_mm"), "decision_time_min": t0, "objective": objective or "balanced"})
        return r

    def staging_status(self, key: str | None = None, alert: str = "橙") -> dict:
        """需要（本系统预置方案）vs ERP 计划（预案 A，11/1 到位）vs 现场 check-in（exchange/checkins.json）→ 预置到位率。没有 check-in 就标 plan only。"""
        key = str(key or self.active_rp or "10")
        ctx, _ = self._ensure_ctx(key)
        if ctx is None:
            return {"error": "调度上下文未装载"}
        pay = self.dispatch_at(dispatch.default_decision_time(ctx["fp"], self.cfg), ctx=ctx, key=key)
        pre = (pay.get("preposition") or {}).get(alert) or {}
        depots = pay.get("depots") or []
        if not depots:
            return {"error": "没有驻地"}
        dl = np.array([[float(d["lon"]) * np.cos(np.radians(24.4)), float(d["lat"])] for d in depots])
        need = {d["depot_id"]: 0 for d in depots}; covers = {d["depot_id"]: 0 for d in depots}
        hs = getattr(self, "hotspots", None)
        zone_of = dict(zip(hs["hotspot_id"].astype(str), zip(hs["region"].astype(str), hs["area"].astype(str)))) if hs is not None and len(hs) else {}
        dep_by_area = {}; dep_by_region = {}
        for d in depots:
            for a in str(d.get("area") or "").split("/"):
                if a.strip():
                    dep_by_area.setdefault((str(d.get("region") or "ADM"), a.strip()), d["depot_id"])
            dep_by_region.setdefault(str(d.get("region") or "ADM"), []).append(d)
        for s_ in pre.get("sites") or []:
            did = None
            if s_.get("site_id") in need:
                did = s_["site_id"]
            else:                                                       # 预置驻地（民防站）→ 它覆盖的点多数属于哪个片区 / 市政 → 该片区的预案 A 驻地（承包商）
                zs = [zone_of.get(str(f)) for f in (s_.get("covers") or []) if zone_of.get(str(f))]
                if zs:
                    from collections import Counter
                    (reg, area), _ = Counter(zs).most_common(1)[0]
                    did = dep_by_area.get((reg, area))
                    if did is None and dep_by_region.get(reg):
                        cand_d = dep_by_region[reg]; q = np.array([float(s_["lon"]) * np.cos(np.radians(24.4)), float(s_["lat"])])
                        did = min(cand_d, key=lambda d: (float(d["lon"]) * np.cos(np.radians(24.4)) - q[0]) ** 2 + (float(d["lat"]) - q[1]) ** 2)["depot_id"]
                if did is None:
                    q = np.array([float(s_["lon"]) * np.cos(np.radians(24.4)), float(s_["lat"])]); did = depots[int(np.argmin(((dl - q) ** 2).sum(axis=1)))]["depot_id"]
            need[did] += int(s_.get("units_suggested") or 0); covers[did] += int(s_.get("n_covers") or len(s_.get("covers") or []))
        checkins = {}; chk_meta = None
        feed = feeds.load(self.cfg, "checkins")
        if feed.get("rows") or feed.get("error") or feed.get("url"):
            chk_meta = {k: feed.get(k) for k in ("schema", "as_of", "source", "url", "fetched_at", "error") if feed.get(k) is not None}
            for r in feed.get("rows") or []:
                did = r.get("depot_id") or r.get("site_id")
                if did in need:
                    try:
                        checkins[did] = checkins.get(did, 0) + int(r.get("units") or 0)
                    except (TypeError, ValueError):
                        pass
        rows = []
        for d in depots:
            did = d["depot_id"]; erp = int(d.get("capacity_units") or sum(int(v) for v in (d.get("units") or {}).values()))
            ci = checkins.get(did); n = need[did]
            rows.append({"depot_id": did, "name": d.get("name"), "area": d.get("area"), "region": d.get("region"), "need_units": n, "erp_plan_units": erp, "checked_in_units": ci,
                         "gap_vs_plan": erp - n, "readiness_pct": (None if ci is None or n <= 0 else round(100.0 * min(ci, n) / n, 0)), "covers": covers[did]})
        tot_need = sum(r["need_units"] for r in rows); tot_erp = sum(r["erp_plan_units"] for r in rows); tot_ci = (sum(v for v in checkins.values()) if checkins else None)
        return {"scenario": key, "alert": alert, "plan_only": not bool(checkins), "checkins_meta": chk_meta, "rows": rows,
                "totals": {"need_units": tot_need, "erp_plan_units": tot_erp, "checked_in_units": tot_ci, "readiness_pct": (None if tot_ci is None or tot_need <= 0 else round(100.0 * min(tot_ci, tot_need) / tot_need, 0)), "depots_short_vs_plan": sum(1 for r in rows if r["gap_vs_plan"] < 0)},
                "basis": "需要 = 本系统按预警等级的预置方案（驻地 → 最近预案 A 驻地归并）；ERP 计划 = 客户预案 A 泵数（11/1 到位，plan only）；check-in = exchange/checkins.json（承包商到场登记，契约 swbrain.checkins/1）",
                "basis_en": "Need = this system's staging plan by alert level (sites folded to the nearest Plan A depot); ERP plan = client Plan A pump counts (in place by 1 Nov, plan only); check-in = exchange/checkins.json (contractor arrival log, contract swbrain.checkins/1)"}

    def recession(self, fp_id: str, key: str | None = None, t_min: float | None = None, with_upsize: bool = True) -> dict:
        """一个积水点「什么时候退水、怎样最快」：自然 / 全局最优给它的 / 单点最优 / 预置 / 罐车 / 换管，以及单点最优对全局的代价。"""
        key = str(key or self.active_rp or "10")
        ctx, _ = self._ensure_ctx(key)
        if ctx is None:
            return {"error": "调度上下文未装载"}
        t = float(t_min) if t_min is not None else dispatch.default_decision_time(ctx["fp"], self.cfg)
        pay = self.dispatch_at(t, ctx=ctx, key=key)
        hit = self.pay_cache.get(self._pay_key(key, t))
        plan_df = hit["plan"] if hit is not None else pd.DataFrame(pay.get("plan") or [])
        up = None
        if with_upsize:
            try:
                det = self.hotspot_detail(fp_id, 2, key=key)
                cur = det.get("curves") or {}
                if (det.get("matched") or {}).get("edge_gid") is not None and cur.get("upsized"):
                    up = dict(cur); up["reach_len_m"] = (det.get("reach") or {}).get("len_m")
            except Exception:
                up = None
        try:
            return recession.options(ctx, self.cfg, fp_id, t, plan_df, pay.get("plan"), upsize=up, scenario=scenarios.get(self.cfg, key))
        except KeyError as ex:
            return {"error": str(ex).strip("'")}

    def gwm_points(self, key: str | None = None, run: bool = False) -> dict:
        """某情景的世界模型推演积水 × 客户热点 × 本系统积水点对照；推演文件不变就给缓存。run=True 且该重现期没有推演文件时现场推一次（容器可达才行，数十秒）。"""
        key = str(key or self.active_rp or "10")
        files = gwmcompare.gwm_files(self.cfg)
        if run:
            f, how = gwmcompare.pick_file(self.cfg, key, files)
            if f is None or how != "return_period":
                if not key.isdigit():
                    return {"error": "该情景没有重现期，不能现场推演；只能取雨量最接近的已有推演", "available_rps": sorted({x["rp"] for x in files if x["rp"] is not None})}
                try:
                    fp, _r = gwm.rollout_for_return_period(self.cfg, int(key)); gwm.write_flood_points(fp, self.cfg)
                    files = gwmcompare.gwm_files(self.cfg); self.gwm_cache.pop(key, None)
                except Exception as ex:
                    return {"error": f"世界模型现场推演失败：{str(ex)[:160]}", "available_rps": sorted({x["rp"] for x in files if x["rp"] is not None})}
        sig = tuple((f["path"], f["mtime"]) for f in files)
        hit = self.gwm_cache.get(key)
        if hit is not None and hit["sig"] == sig:
            return hit["pay"]
        ctx, _info = self._ensure_ctx(key)
        sys_fp = ctx.get("fp") if ctx else None
        pay = gwmcompare.public(self.cfg, key, getattr(self, "hotspots", None), sys_fp, files)
        self.gwm_cache[key] = {"sig": sig, "pay": pay}
        return pay

    def report_html(self, key: str | None = None, lang: str = "zh") -> str:
        """某情景的自包含 HTML 报告：现状 / 三道防线 / 预警提示 / 可信度，全用已算好的对象排版。"""
        key = key or self.active_rp or "10"
        b = self.scenario_bundle(key)
        adv = None
        try:
            adv = alerts.advisory(self.cfg, b, {"p24_mm": float(b["p24_mm"]), "lead_h": 24.0, "source": "scenario", "issued_at": time.strftime("%Y-%m-%d %H:%M")}, write=False)
        except Exception as ex:
            adv = {"error": str(ex)[:120]}
        cmp_p = ROOT / "console" / "data" / "dmt_compare.json"
        cmp_ = json.loads(cmp_p.read_text(encoding="utf-8")) if cmp_p.exists() else None
        note = "口令保护的控制台：" + ("有" if (self.cfg.get("serve") or {}).get("access_key") else "无")
        return report.render_html(self.cfg, self.summary, b, adv, cmp_, lang=lang, access_note=None)

    def network_flood(self, key: str | None, i_mmh: float | None = None) -> dict | None:
        """全网溢流量：目录里的情景按 key 缓存；专家滑杆的任意雨强按 10 年 IDF 等比缩放算。"""
        if self.nf_prep is None:
            return None
        try:
            if key is not None:
                r = self.nf_cache.get(key)
                if r is None:
                    r = networkflood.run(self.cfg, self.nf_prep, key); self.nf_cache[key] = r
            else:
                base = scenarios.get(self.cfg, "10")
                r = networkflood.run(self.cfg, self.nf_prep, {"key": f"i{i_mmh:g}", "kind": "rp", "base_rp": 10, "factor": float(i_mmh) / float(base["i15_mmh"]), "label": "", "label_en": ""})
            return networkflood.public(r)
        except Exception as ex:
            return {"error": str(ex)[:200]}

    def zone(self, name: str) -> dict:
        z = self.zs[self.zs["zone_name"] == name]
        props = [p for p in self.props if p.get("zone_name") == name]
        row = json.loads(z.to_json(orient="records", force_ascii=False))[0] if len(z) else None
        return {"zone": row, "proposals": props, "n_proposals": len(props)}

    def proposal(self, pid: int) -> dict:
        p = self.by_pid.get(pid)
        if p is None:
            return {"error": f"没有 pid {pid}"}
        n = self.cfg["source"]["pipe"]["schema_network"]
        sql = []
        if p.get("gid") is not None:
            sql.append(f"select gid, unitid, pipe_diameter, invert_up, invert_dn, len_m, pipe_material, dir_source, zone_name from {n}.sw_edge where gid = {int(p['gid'])};")
        if p.get("terminal") is not None:
            sql.append(f"select id, node_table, unitid from {n}.sw_edge_vertices_pgr where id = {int(p['terminal'])};")
            sql.append(f"select * from {n}.sw_trace_upstream_node({int(p['terminal'])}) limit 50;  -- 上游追溯，源库自带函数")
        return {"proposal": p, "verify_sql": sql, "source_db": f"{self.cfg['source']['pipe']['host']}:{self.cfg['source']['pipe']['port']}/{self.cfg['source']['pipe']['dbname']}"}

    def whatif_one(self, pid: int) -> dict:
        p = self.by_pid.get(pid)
        if p is None:
            return {"error": f"没有 pid {pid}"}
        t0 = time.perf_counter()
        ivs = whatif.proposal_to_interventions(p, self.cs, self.cfg, list(self.scenarios)[0])
        if not ivs:
            return {"pid": pid, "note": "这类建议（核实/维护/调度）不改网络，没有 what-if 可算", "note_en": "This proposal (verify/maintenance/operations) does not change the network; nothing to simulate", "interventions": []}
        modeled = {"pipe_upsize", "add_storage", "add_pump", "tide_gate", "connect"}
        if not any(iv.type in modeled for iv in ivs):
            types = ", ".join(sorted({iv.type for iv in ivs}))
            return {"pid": pid, "interventions": [iv.__dict__ for iv in ivs], "note": "代理评估器不模拟拓扑修正与调度类干预（%s），这类要交预测侧评估；已写进 what-if 请求" % types,
                    "note_en": "The surrogate evaluator does not simulate topology fixes or operational actions (%s); these go to the prediction side and are already in the what-if request" % types}
        s = InterventionSet(set_id=f"p{pid}", interventions=ivs, proposals=[pid])
        with self.lock:
            ev = self.sur.evaluate(s)
        cost = sum(whatif.cost_of(iv, self.cfg, self.es) for iv in ivs)
        return {"pid": pid, "interventions": [iv.__dict__ for iv in ivs], "kpis_before": self.sur.base, "kpis_after": ev["kpis"], "delta": ev["delta"],
                "cost_aed": round(cost, 0), "computed_ms": round((time.perf_counter() - t0) * 1000, 1), "computed_at": time.strftime("%H:%M:%S"),
                "method": "代理评估器：把干预套到 26 万段的管网状态上重算能力比、蓄渗缺口、高潮暴露",
                "method_en": "Surrogate evaluator: applies the intervention to the 260k-reach network state and recomputes capacity ratios, storage deficit and tide exposure"}

    def trace(self, gid: int, max_hops: int = 400) -> dict:
        g = self.sur.g
        e = self.sur.es
        if gid not in e.index:
            return {"error": f"没有 gid {gid}"}
        u, v = int(e.loc[gid, "u"]), int(e.loc[gid, "v"])
        down, cur, seen = [], v, set()
        while cur not in seen and len(down) < max_hops:
            seen.add(cur); outs = list(g.successors(cur))
            if not outs: break
            nxt = max(outs, key=lambda x: e.loc[g[cur][x]["gid"], "area_m2"]); down.append(int(g[cur][nxt]["gid"])); cur = nxt
        up_nodes = {u}; frontier = [u]; up = []
        while frontier and len(up) < 5000:
            nf = []
            for n_ in frontier:
                for pn in g.predecessors(n_):
                    if pn not in up_nodes:
                        up_nodes.add(pn); nf.append(pn); up.append(int(g[pn][n_]["gid"]))
            frontier = nf
        return {"gid": gid, "downstream_gids": down, "downstream_km": round(float(e.loc[down, "len_m"].sum()) / 1000, 2) if down else 0.0,
                "upstream_gids_n": len(up), "upstream_km": round(float(e.loc[up, "len_m"].sum()) / 1000, 2) if up else 0.0,
                "terminal_kind": e.loc[gid, "terminal_kind"]}
