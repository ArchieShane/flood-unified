"""长任务出锁：求解进子进程（绕开 GIL），HTTP 拿任务 id 轮询。

SolverPool  进程池包装：run(fn, *args) 在子进程里跑并等结果（调用方是 HTTP 线程或预热线程，主进程的 GIL 不被 HiGHS / pandas 占住，别的请求照常响应）。
            workers <= 0 退回同进程执行；子进程池坏了自动重建一次再退回同进程。
Jobs        任务表：submit(kind, params, fn) 立即返回 {job_id, status}，fn 在线程里跑（fn 内部再走 SolverPool）；同样 (kind, params) 正在跑就复用，不重复求解；
            get(id) 给状态与结果；完成的任务保留 ttl_s 秒。
worker_*    只能在子进程里跑的重活：情景上下文构建要路网，路网按进程懒加载一次。"""
from __future__ import annotations
import threading
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

_W: dict = {}          # 子进程内的懒加载缓存（路网、参考数据）


# ---------------------------------------------------------------- 子进程里的重活
def _worker_refs():
    if "road" not in _W:
        from . import refdata, travel
        ref = refdata.load_cache()
        _W["ref"] = ref; _W["road"] = travel.RoadNetwork(ref["edges"], ref["vertices"])
    return _W["ref"], _W["road"]


def worker_build_context(fp: dict, fleet: dict, cs, cfg: dict, hotspots) -> dict:
    """dispatch.build_context 的子进程版：路网只在本进程加载一次。"""
    from . import dispatch, hotspots as hsm
    ref, road = _worker_refs()
    ctx = dispatch.build_context(fp, fleet, ref["facilities"], cs, road, cfg, hotspots)
    ctx["hotspot_stats"] = hsm.stats(hotspots, cs, fp, cfg) if hotspots is not None and len(hotspots) else None
    ctx["fleet"] = fleet
    return ctx


def worker_system_scenario(cfg: dict, key: str, matched, fleet: dict, cs, hotspots) -> tuple[dict, dict]:
    """本系统积水点 + 调度上下文，一次算完（floodpoints.build 的过程线 + Dijkstra 行程矩阵）。"""
    from . import floodpoints
    fp = floodpoints.build(cfg, key, matched)
    floodpoints.write(fp, cfg)
    return fp, worker_build_context(fp, fleet, cs, cfg, hotspots)


# ---------------------------------------------------------------- 进程池
class SolverPool:
    def __init__(self, workers: int = 2, log=print):
        self.workers = int(workers or 0); self.log = log
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock(); self.rebuilt = 0; self.inline_runs = 0; self.pool_runs = 0

    def _get(self) -> ProcessPoolExecutor | None:
        if self.workers <= 0:
            return None
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(max_workers=self.workers)
            return self._pool

    def run(self, fn, *args, **kw):
        pool = self._get()
        if pool is None:
            self.inline_runs += 1
            return fn(*args, **kw)
        try:
            fut = pool.submit(fn, *args, **kw); r = fut.result(); self.pool_runs += 1
            return r
        except BrokenProcessPool as ex:
            self.log(f"  求解子进程池坏了（{str(ex)[:80]}），重建一次", flush=True)
            with self._lock:
                try:
                    self._pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                self._pool = None; self.rebuilt += 1
            if self.rebuilt <= 3:
                pool = self._get()
                if pool is not None:
                    fut = pool.submit(fn, *args, **kw); r = fut.result(); self.pool_runs += 1
                    return r
            self.inline_runs += 1
            return fn(*args, **kw)

    def stats(self) -> dict:
        return {"workers": self.workers, "alive": self._pool is not None, "pool_runs": self.pool_runs, "inline_runs": self.inline_runs, "rebuilt": self.rebuilt}

    def shutdown(self):
        with self._lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True); self._pool = None


# ---------------------------------------------------------------- 任务表
class Jobs:
    def __init__(self, threads: int = 4, ttl_s: float = 3600.0, max_keep: int = 200):
        self._ex = ThreadPoolExecutor(max_workers=int(threads), thread_name_prefix="job")
        self._jobs: dict[str, dict] = {}; self._lock = threading.Lock(); self.ttl = float(ttl_s); self.max_keep = int(max_keep)

    def _prune(self):
        now = time.time()
        done = [j for j in self._jobs.values() if j["status"] in ("done", "error") and now - (j.get("finished") or now) > self.ttl]
        for j in done:
            self._jobs.pop(j["job_id"], None)
        if len(self._jobs) > self.max_keep:
            for j in sorted(self._jobs.values(), key=lambda x: x.get("created", 0))[: len(self._jobs) - self.max_keep]:
                if j["status"] in ("done", "error"):
                    self._jobs.pop(j["job_id"], None)

    def submit(self, kind: str, params: dict, fn) -> dict:
        """同样 (kind, params) 还在排队 / 运行 → 复用那个任务。"""
        import json
        sig = json.dumps({"kind": kind, "params": params}, sort_keys=True, default=str)
        with self._lock:
            self._prune()
            for j in self._jobs.values():
                if j["_sig"] == sig and j["status"] in ("queued", "running"):
                    return self.snapshot(j)
            jid = uuid.uuid4().hex[:12]
            j = {"job_id": jid, "kind": kind, "params": params, "status": "queued", "created": time.time(), "started": None, "finished": None, "result": None, "error": None, "_sig": sig}
            self._jobs[jid] = j

        def _run():
            j["status"] = "running"; j["started"] = time.time()
            try:
                j["result"] = fn(); j["status"] = "done"
            except Exception as ex:
                j["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"; j["trace"] = traceback.format_exc()[-1500:]; j["status"] = "error"
            j["finished"] = time.time()
        self._ex.submit(_run)
        return self.snapshot(j)

    def snapshot(self, j: dict, with_result: bool = False) -> dict:
        now = time.time()
        out = {"job_id": j["job_id"], "kind": j["kind"], "status": j["status"], "params": j["params"], "queued_s": round((j["started"] or now) - j["created"], 1),
               "elapsed_s": round(((j["finished"] or now) - j["started"]), 1) if j["started"] else 0.0, "error": j.get("error")}
        if with_result and j["status"] == "done":
            out["result"] = j["result"]
        return out

    def get(self, jid: str, with_result: bool = True) -> dict | None:
        j = self._jobs.get(str(jid))
        return self.snapshot(j, with_result) if j else None

    def list(self) -> list[dict]:
        with self._lock:
            return [self.snapshot(j) for j in sorted(self._jobs.values(), key=lambda x: -x["created"])][:50]
