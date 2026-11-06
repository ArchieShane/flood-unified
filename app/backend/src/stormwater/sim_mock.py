"""仿真进度的后台模拟：算力侧还没接上时，替它推进 queued → running → done。

真实流程里进度由算力侧经 `PATCH /api/simulations/{id}/status` 回写；目前没有算力侧，
新建的任务排在种子里那条 62% 的 running 后面永远不动。本模块在 API 进程里起一个守护线程，
按固定节拍模拟回写，走的仍是 `simulation.update_status()`，状态流转规则一条不绕开。

口径：
- 同一对 H100 上串行：任一时刻最多一个 running；它跑完才把最早排队的那个拉起来。
- 每个任务从 0 到 100 % 用 `SIM_MOCK_RUN_MINUTES` 分钟（默认 10），按节拍线性加；
  已有进度的（种子里的 62 %）从当前值接着走。
- 拉起时 ended_at 记「此刻 + 模拟耗时」并标为估计值；跑完由 `update_status` 记实际结束时刻。
- 只推进状态与进度，**不编结果**：模拟跑完的任务没有 run_result、没有结果图层。
- 多个 worker / --reload 并存时靠事务级 advisory lock 保证只有一个在推进。
- `SIM_MOCK_PROGRESS=0` 关闭（接上真实算力侧后必须关，否则两边抢着写）。
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta

from . import simulation as sim

log = logging.getLogger(__name__)

#: advisory lock 的键，随便取一个不和别处撞的常数
LOCK_KEY = 0x5117_0C4
TICK_S = float(os.getenv("SIM_MOCK_TICK_SECONDS", "5"))
RUN_MINUTES = float(os.getenv("SIM_MOCK_RUN_MINUTES", "10"))


def enabled() -> bool:
    return os.getenv("SIM_MOCK_PROGRESS", "1").lower() not in ("0", "false", "no", "off")


def tick(conn, step_pct: float, run_minutes: float) -> None:
    """推进一拍。拿不到锁（别的 worker 正在推进）就什么都不做。"""
    if not conn.execute("select pg_try_advisory_xact_lock(%s) as ok", [LOCK_KEY]).fetchone()["ok"]:
        return
    running = conn.execute(
        "select id, progress_pct from run where status = 'running' order by started_at nulls last, id"
    ).fetchall()
    for r in running:
        pct = float(r["progress_pct"] or 0) + step_pct
        if pct >= 100:
            sim.update_status(conn, r["id"], {"status": "done"})
            log.info("模拟进度：任务 %s 完成", r["id"])
        else:
            sim.update_status(conn, r["id"], {"progress_pct": round(pct, 1)})
    if any(float(r["progress_pct"] or 0) + step_pct < 100 for r in running):
        return
    nxt = conn.execute(
        "select id from run where status = 'queued' order by created_at, id limit 1").fetchone()
    if nxt:
        now = datetime.now().astimezone()
        sim.update_status(conn, nxt["id"], {
            "status": "running", "progress_pct": 0, "started_at": now,
            "ended_at": now + timedelta(minutes=run_minutes), "ended_is_estimate": True,
        })
        log.info("模拟进度：任务 %s 开跑", nxt["id"])


def _loop(stop: threading.Event) -> None:
    step = 100 * TICK_S / (RUN_MINUTES * 60)
    while not stop.wait(TICK_S):
        try:
            with sim.connect() as conn:
                tick(conn, step, RUN_MINUTES)
        except Exception as e:   # 库连不上时只记日志，下拍再试，不拖垮 API
            log.warning("模拟进度这一拍失败：%s", e)


def start() -> threading.Event | None:
    """起守护线程；返回用于停止的 Event。关闭时返回 None。"""
    if not enabled():
        return None
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="sim-mock-progress", daemon=True).start()
    log.info("模拟进度已开启：每 %.0f s 一拍，单任务 %.0f min", TICK_S, RUN_MINUTES)
    return stop
