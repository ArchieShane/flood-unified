# -*- coding: utf-8 -*-
"""推演进度：run_region.py 把进度写进 <工作目录>/run/<区域>/status.json，`<区域>-server.sh status` 读它显示。只用标准库。

状态文件（原子写，随时可读）：
  {"region","run_id","host","state": starting|running|done|failed|stopped, "started_at","updated_at","message",
   "scenarios": {"S33D1": {"job_id","state": pending|running|done|failed, "stage","stage_idx","n_stages","stage_pct",
                           "progress_pct","sim_h_done","sim_h_total","started_at","updated_at","message"}}}
阶段（STAGES）：prepare 准备输入 → build 建模 → compute 一二维计算 → post 后处理 → export 成果导出（flood2d / shp+dat / params / ponding_ge）。

推演主脚本里这样写：
    from run_status import Status
    st = Status("/work/run/RDA/status.json"); st.init("RDA", ["S33D1", "S61D24"], host="...", job_ids={...})
    st.update("S33D1", stage="compute", stage_pct=0.4, sim_h_done=4.4, sim_h_total=11, message="t=4.4 h")
    st.finish("S33D1"); st.set_state("done")
命令行：python tools/run_status.py show <status.json> [--json]     |    init / update 见上
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

STAGES = [("prepare", "准备输入"), ("build", "建模"), ("compute", "一二维计算"), ("post", "后处理"), ("export", "成果导出")]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


class Status:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _lock(self):
        """多个场景进程同时写进度：用锁文件串行化读 - 改 - 写。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + ".lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, d: dict):
        d["updated_at"] = now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".status.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def init(self, region: str, scenarios: list[str], host: str = "", job_ids: dict | None = None, run_id: str | None = None):
        scn = {t: {"job_id": (job_ids or {}).get(t, t), "state": "pending", "stage": "", "stage_idx": 0, "n_stages": len(STAGES), "stage_pct": 0.0, "progress_pct": 0.0,
                   "sim_h_done": None, "sim_h_total": None, "started_at": None, "updated_at": None, "message": ""} for t in scenarios}
        self._write({"region": region, "run_id": run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S"), "host": host, "state": "starting", "started_at": now(), "message": "", "scenarios": scn})

    def update(self, scenario: str, stage: str | None = None, stage_pct: float | None = None, **kw):
        with self._lock():
            self._update(scenario, stage, stage_pct, **kw)

    def _update(self, scenario, stage, stage_pct, **kw):
        d = self._read()
        s = d["scenarios"][scenario]
        if s["state"] in ("pending", "done", "failed") and stage:
            s["state"] = "running"
        s["started_at"] = s["started_at"] or now()
        if stage:
            s["stage"] = stage
            s["stage_idx"] = [k for k, _ in STAGES].index(stage)
        if stage_pct is not None:
            s["stage_pct"] = max(0.0, min(1.0, stage_pct))
        s["progress_pct"] = round(100 * (s["stage_idx"] + s["stage_pct"]) / s["n_stages"], 1)
        s.update(kw)
        s["updated_at"] = now()
        if d["state"] == "starting":
            d["state"] = "running"
        self._write(d)

    def finish(self, scenario: str, ok: bool = True, message: str = ""):
        with self._lock():
            self._finish(scenario, ok, message)

    def _finish(self, scenario, ok, message):
        d = self._read()
        s = d["scenarios"][scenario]
        s.update(state="done" if ok else "failed", message=message or s.get("message", ""), updated_at=now())
        if ok:
            s.update(progress_pct=100.0, stage_idx=len(STAGES) - 1, stage_pct=1.0)
        self._write(d)

    def set_state(self, state: str, message: str = ""):
        with self._lock():
            self._set_state(state, message)

    def _set_state(self, state, message):
        d = self._read()
        d["state"], d["message"] = state, message
        self._write(d)


def _bar(p: float, w: int = 20) -> str:
    n = int(round(w * p / 100))
    return "█" * n + "░" * (w - n)


def _elapsed(a: str | None) -> str:
    if not a:
        return "—"
    sec = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(a)).total_seconds())
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


def show(path: Path, as_json: bool = False) -> int:
    d = Status(path)._read()
    if not d:
        print(f"还没有进度文件 {path}（没启动过，或推演脚本还没写进度）")
        return 1
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print(f"区域 {d['region']}  运行 {d['run_id']}  主机 {d.get('host', '')}  状态 {d['state']}  已运行 {_elapsed(d.get('started_at'))}  （更新于 {d.get('updated_at', '')}）")
    if d.get("message"):
        print(f"说明：{d['message']}")
    names = dict(STAGES)
    print(f"\n{'场景':<18}{'job_id':<18}{'状态':<9}{'阶段':<12}{'进度':<28}{'模拟时间':<14}{'用时':<9}说明")
    for t, s in d["scenarios"].items():
        sim = f"{(s.get('sim_h_done') or 0.0):.1f}/{s['sim_h_total']:.0f} h" if s.get("sim_h_total") else "—"   # 续跑跳过计算时 sim_h_done 是 None
        print(f"{t:<18}{s['job_id']:<18}{s['state']:<9}{names.get(s['stage'], '—'):<12}{_bar(s['progress_pct'])} {s['progress_pct']:5.1f}%  {sim:<14}{_elapsed(s.get('started_at')):<9}{s.get('message', '')}")
    c = {k: sum(1 for s in d["scenarios"].values() if s["state"] == k) for k in ("pending", "running", "done", "failed")}
    print(f"\n汇总：完成 {c['done']}，运行中 {c['running']}，排队 {c['pending']}，失败 {c['failed']}（共 {len(d['scenarios'])}）")
    return 0


def _job_id(region: str, tag: str) -> str:
    import re
    m = re.fullmatch(r"S(\d+(?:\.\d+)?)D(\d+)", tag)
    return f"{region}_{m.group(1)}mm{m.group(2)}h" if m else tag


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "show":
        raise SystemExit(show(Path(a[1]), "--json" in a))
    if len(a) >= 4 and a[0] == "init":                      # init <status.json> <区域> <S33D1,S61D24> [主机]
        tags = [t for t in a[3].split(",") if t]
        Status(a[1]).init(a[2], tags, host=a[4] if len(a) > 4 else "", job_ids={t: _job_id(a[2], t) for t in tags})
        raise SystemExit(0)
    if len(a) >= 3 and a[0] == "state":                     # state <status.json> <starting|running|done|failed|stopped> [说明]
        Status(a[1]).set_state(a[2], a[3] if len(a) > 3 else "")
        raise SystemExit(0)
    sys.exit(__doc__)
