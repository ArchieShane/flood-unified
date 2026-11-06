# -*- coding: utf-8 -*-
"""C1 模型版 · 多场雨一起排队跑（5 m：服务器 2，c1_run_g5.py；15 m 新雨型：服务器 3，c1_run_15m.py）。

原名 c1_g5_batch.py（2026-10-07 给服务器 2 的 5 m 写的），加了 --runner 后 15 m 也用它，10-07 改名；
缺省 runner 的日志 / 锁文件仍叫 c1_g5_batch.*，旧名启动的调度器还在跑时 status 照样看得到、也不会重复起。

c1_run_g5.py 一次只跑一个情景、三态一起阻塞到算完；两场雨各开一个 `run all` 会同时起 6 个 5 m 容器（超内存），
还会抢着做 prep / R96 L1。这里把两场雨拆成单态任务，按「同时最多 N 个态」排队，只调用 c1_run_g5.py 的单步命令：

  前置   prep + R96 L1（已有就跳过；部署包的工作目录里已带 R96 L1）
  建模   build:<tag>                 两场雨依次建（不占计算槽）
  计算   compute:<tag>:<态>          每态一个容器、8 线程，占一个槽；L2S3 优先
  设计   design:<tag>                该 tag 的 L2P 一算完就设计 L2+suggested v3（只读 L2P 结果）
  后处理 post:<tag>                  四态都算完后做，两场雨的后处理依次做（共用控制台数据）

可以中断后重跑同一条命令：产物已在的步骤跳过；计算容器是后台起的，调度器停了也照算，重跑时认领还在跑的容器、不会重复起。
某一步失败只停它的下游，另一场雨照跑；修好后重跑同一条命令即可。

用法（部署包：容器里；源码方式：项目 .venv 的 Python）：
  python tools/c1_batch.py plan                         # 列任务与依赖、当前哪些已完成
  python tools/c1_batch.py run   [--slots 4 --threads 8]  # 开跑（阻塞到全部结束；服务器上用 tools/chains/c1_g5_server2.sh 放后台）
  python tools/c1_batch.py status                       # 当前进度、在跑的容器、内存
缺省情景 M33H1G5 M96H24G5，可用 --tags 改。
--runner 换单情景脚本（缺省 c1_run_g5.py = 5 m）：15 m 网格的新雨型用 --runner c1_run_15m.py（服务器 3，tools/chains/c1_15m_server3.sh），
情景名单取该脚本的 SCEN；调度日志 / 锁文件按脚本分开（c1_g5_batch.* 与 c1_batch_c1_run_15m.*），两套可以在同一台机器上各跑各的。
"""
from __future__ import annotations

import argparse
import importlib
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c1_pipeline as P  # noqa: E402

G5 = str(HERE / "c1_run_g5.py")
STATES3 = ["L1", "L2", "L2P"]
LOGS = P.C2D / "logs"
LOCK = LOGS / "c1_g5_batch.lock"
BATCH_LOG = LOGS / "c1_g5_batch.log"


def set_runner(name: str) -> None:
    """换单情景脚本；缺省 c1_run_g5.py 时文件名与原来一致（服务器 2 的 status / log 照旧）。"""
    global G5, LOCK, BATCH_LOG
    G5 = str(HERE / name)
    base = "c1_g5_batch" if name == "c1_run_g5.py" else f"c1_batch_{pathlib.Path(name).stem}"
    LOCK, BATCH_LOG = LOGS / f"{base}.lock", LOGS / f"{base}.log"


def log(msg: str) -> None:
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


def mem_avail_gb() -> float | None:
    try:
        for ln in open("/proc/meminfo", encoding="ascii"):
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return None                                          # Windows：不做内存门槛


def live_containers() -> set[str]:
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    return set(r.stdout.split())


def tail(path: pathlib.Path) -> str:
    if not path.exists():
        return "(还没有日志)"
    t = path.read_text(encoding="utf-8", errors="replace").replace("\r", "\n").strip().splitlines()
    return t[-1][:120] if t else ""


class Job:
    def __init__(self, jid, desc, cmd, outs, deps=(), slot=False, mutex=None, container=None, tag=None, always=False):
        self.id, self.desc, self.cmd, self.outs, self.deps = jid, desc, cmd, outs, list(deps)
        self.slot, self.mutex, self.container, self.tag = slot, mutex, container, tag
        self.always = always                                   # 每次都执行一遍（c1_run_g5 自己跳过已有产物的步骤）
        self.proc = None
        self.adopted = False
        self.state = "done" if self.finished() and not always else "wait"     # wait / run / done / fail / blocked
        self.t0 = None

    def finished(self) -> bool:
        return all(pathlib.Path(o).exists() for o in self.outs)


def jobs_for(tags: list[str]) -> list[Job]:
    g5 = lambda spec, t: [P.PY, "-u", G5, "run", spec, "--tag", t]
    W, C = P.WORK, P.C2D
    js = [Job("pre", "prep + R96 L1（L2 / L2+planned 的接收井按它选）", [P.PY, "-u", G5, "run", "prep", "build:R96:L1", "compute:R96:L1", "--tag", tags[0]],
              [C / "twoway_C1_R96_L1.json", C / "C1_R96_L1_2way.rpt"], mutex="build", always=True)]
    for t in tags:
        js.append(Job(f"build:{t}", f"{t} 建 L1 / L2 / L2+planned", g5(f"build:{t}", t),
                      [W / f"C1_{t}_{s}.inp" for s in STATES3] + [C / f"pilot_C1_{t}_{s}.json" for s in STATES3],
                      deps=["pre"], mutex="build", tag=t))
    for t in tags:
        for s in STATES3:
            js.append(Job(f"compute:{t}:{s}", f"{t} {s} 一二维", g5(f"compute:{t}:{s}", t),
                          [C / f"twoway_C1_{t}_{s}.json", C / f"C1_{t}_{s}_2d.npz"], deps=[f"build:{t}"], slot=True,
                          container=f"c1pipe_{t}_{s}", tag=t))
    for t in tags:
        js.append(Job(f"design:{t}", f"{t} 设计 L2+suggested v3", g5(f"design:{t}", t), [W / f"C1_{t}_L2S3.inp"],
                      deps=[f"compute:{t}:L2P"], mutex="build", tag=t))
        js.append(Job(f"compute:{t}:L2S3", f"{t} L2S3 一二维", g5(f"compute:{t}:L2S3", t),
                      [C / f"twoway_C1_{t}_L2S3.json", C / f"C1_{t}_L2S3_2d.npz"], deps=[f"design:{t}"], slot=True,
                      container=f"c1pipe_{t}_L2S3", tag=t))
    for t in tags:
        js.append(Job(f"post:{t}", f"{t} 后处理与交付", g5(f"post:{t}", t), [P.ROOT / f"out/c1_stats_{t}.json"],
                      deps=[f"compute:{t}:{s}" for s in STATES3 + ["L2S3"]], mutex="post", tag=t))
    return js


def ready_order(js: list[Job]) -> list[Job]:
    """候选顺序：L2S3 优先（让一场雨尽早算完、出后处理），其余按建表顺序（33 mm 三态 → 96 mm 三态）。"""
    return sorted(js, key=lambda j: (0 if j.id.endswith(":L2S3") else 1, js.index(j)))


def show(js: list[Job]) -> None:
    mark = {"done": "✓", "run": "▶", "wait": "·", "fail": "✗", "blocked": "－"}
    for j in js:
        extra = ""
        if j.state == "run" and j.t0:
            extra = f"  已 {(time.time() - j.t0) / 3600:.1f} h" + ("（认领的容器）" if j.adopted else "")
        if j.state == "run" and j.container:
            _, t, s = j.id.split(":")
            extra += "  | " + tail(LOGS / f"C1_{t}_{s}.log")
        elif j.state == "run":                               # 建模 / 设计 / 后处理：看它自己的步骤日志
            extra += "  | " + tail(LOGS / f"g5_{j.id.replace(':', '_')}.log")
        print(f"  {mark[j.state]} {j.id:<26} {j.desc}{extra}", flush=True)


def preflight(tags: list[str]) -> None:
    known = importlib.import_module(pathlib.Path(G5).stem).SCEN
    for t in tags:
        if t not in known:
            raise SystemExit(f"{pathlib.Path(G5).name} 不认识情景 {t}（认识 {' / '.join(known)}）")
    r = subprocess.run([P.PY, G5, "check-patches"], capture_output=True, text=True, encoding="utf-8", env=P.env())
    if r.returncode:
        print(r.stdout, r.stderr)
        raise SystemExit(f"{pathlib.Path(G5).name} 的替换点对不上：这台机器上的 tools/ 和开发机不一样，先别跑")
    if subprocess.run(["docker", "image", "inspect", P.IMAGE], capture_output=True).returncode:
        raise SystemExit(f"没有 docker 镜像 {P.IMAGE}")
    import datapaths
    dtm = datapaths.get("dtm")
    log(f"工作目录 {P.WORK}（宿主机 {P.HOST_WORK}）")
    log(f"地形 DTM：{dtm}" + ("" if dtm and dtm.exists() else "  ✗ 文件不存在"))
    if not (dtm and dtm.exists()):
        raise SystemExit("config.toml [terrain].dtm_path 指的 DTM 不存在")
    try:
        import shutil
        free = shutil.disk_usage(P.WORK).free / 1e9
        log(f"工作目录剩余磁盘 {free:,.0f} GB" + ("  ⚠ 偏少（每个 5 m 态的 .npz/.out 约数 GB）" if free < 100 else ""))
    except OSError:
        pass
    m = mem_avail_gb()
    if m is not None:
        log(f"可用内存 {m:.0f} GB")


def take_lock() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().split()[0])
            os.kill(pid, 0)
            raise SystemExit(f"已有调度器在跑（pid {pid}，{LOCK}）。看进度用 status")
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass                                          # 上次的调度器已经不在了
    LOCK.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def run(a) -> int:
    tags = a.tags
    preflight(tags)
    take_lock()
    js = jobs_for(tags)
    by = {j.id: j for j in js}
    env = dict(P.env(), C2D_G5_THREADS=str(a.threads))
    log(f"情景 {' / '.join(tags)}；同时最多 {a.slots} 个态，每态 {a.threads} 线程；新开一个态前要求可用内存 ≥ {a.min_free_gb} GB")
    show(js)
    last_launch, last_show = 0.0, time.time()
    try:
        while True:
            live = live_containers()
            # 1. 收尾：子进程结束 / 认领的容器退出
            for j in js:
                if j.state != "run":
                    continue
                if j.adopted:
                    if j.container in live:
                        continue
                    ok = j.finished()
                else:
                    rc = j.proc.poll()
                    if rc is None:
                        continue
                    ok = rc == 0 and j.finished()
                j.state = "done" if ok else "fail"
                log(f"{'✓' if ok else '✗'} {j.id} {'完成' if ok else '失败'}（{(time.time() - j.t0) / 3600:.1f} h）"
                    + ("" if ok else f"，看 {LOGS / ('g5_' + j.id.replace(':', '_') + '.log')}"
                       + (f" 和 {LOGS / ('C1_' + '_'.join(j.id.split(':')[1:]) + '.log')}" if j.container else "")))
            # 2. 失败往下游传
            changed = True
            while changed:
                changed = False
                for j in js:
                    if j.state == "wait" and any(by[d].state in ("fail", "blocked") for d in j.deps):
                        j.state, changed = "blocked", True
                        log(f"－ {j.id} 不跑了（上游失败）")
            # 3. 开新任务
            busy = sum(1 for j in js if j.state == "run" and j.slot)
            held = {j.mutex for j in js if j.state == "run" and j.mutex}
            # L2P 算完、L2S3 还没开（在设计）：给它留一个槽，免得被另一场雨的态占走、一等就是一整场 28 h
            reserved = sum(1 for t in a.tags if by[f"compute:{t}:L2P"].state == "done" and by[f"compute:{t}:L2S3"].state == "wait")
            for j in ready_order(js):
                if j.state != "wait" or any(by[d].state != "done" for d in j.deps):
                    continue
                if j.finished() and not j.always:
                    j.state = "done"; continue
                if j.mutex and j.mutex in held:
                    continue
                if j.slot:
                    if j.container in live:                # 上次调度器起的容器还在算：认领，不重复起
                        j.state, j.adopted, j.t0 = "run", True, time.time()
                        busy += 1
                        log(f"▶ {j.id}：容器 {j.container} 已在跑，接着等它")
                        continue
                    if busy + (0 if j.id.endswith(":L2S3") else reserved) >= a.slots:
                        continue
                    if time.time() - last_launch < a.stagger:  # 刚起的态还在建网格、内存没涨上来，等一会儿再看
                        continue
                    m = mem_avail_gb()
                    if m is not None and m < a.min_free_gb:
                        if time.time() - last_show > 600:
                            log(f"  可用内存 {m:.0f} GB < {a.min_free_gb} GB，{j.id} 先等")
                        continue
                lg = LOGS / f"g5_{j.id.replace(':', '_')}.log"
                log(f"▶ {j.id} — {j.desc}（日志 {lg.name}）")
                j.proc = subprocess.Popen(j.cmd, cwd=P.ROOT, env=env, stdout=open(lg, "a", encoding="utf-8"), stderr=subprocess.STDOUT)
                j.state, j.t0 = "run", time.time()
                if j.slot:
                    busy += 1; last_launch = time.time()
                    if j.id.endswith(":L2S3"):
                        reserved = max(0, reserved - 1)
                if j.mutex:
                    held.add(j.mutex)
            # 4. 全部结束？
            if all(j.state in ("done", "fail", "blocked") for j in js):
                break
            if time.time() - last_show > a.report_min * 60:
                m = mem_avail_gb()
                log(f"进度（可用内存 {m:.0f} GB）" if m is not None else "进度")
                show(js); last_show = time.time()
            time.sleep(a.poll)
    finally:
        LOCK.unlink(missing_ok=True)
    log("结束")
    show(js)
    bad = [j.id for j in js if j.state != "done"]
    if bad:
        log(f"没完成：{bad}。修好后重跑同一条命令即可（已完成的会跳过）")
    return 1 if bad else 0


LOG_RE = re.compile(r"^\[(\d\d-\d\d \d\d:\d\d:\d\d)\] (▶|✓|✗|－) (\S+?)(?: |：)")


def from_log() -> dict[str, tuple[str, float]]:
    """读调度日志（BATCH_LOG）里最近一次 run 的记录：任务 → (最后的标记 ▶/✓/✗/－, 时间戳)。"""
    lg = BATCH_LOG
    seen: dict[str, tuple[str, float]] = {}
    if not lg.exists():
        return seen
    year = time.localtime().tm_year
    for ln in lg.read_text(encoding="utf-8", errors="replace").splitlines():
        if "同时最多" in ln:                                # 新的一次 run 从这行开始
            seen = {}
            continue
        m = LOG_RE.match(ln)
        if m:
            try:
                ts = time.mktime(time.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                ts = None
            seen[m.group(3)] = (m.group(2), ts)
    return seen


def status(a) -> int:
    js = jobs_for(a.tags)
    live = live_containers()
    alive = None
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().split()[0]); os.kill(pid, 0); alive = pid
        except (ValueError, OSError):
            pass
    seen = from_log()
    for j in js:
        mk, ts = seen.get(j.id, (None, None))
        if j.container and j.container in live and j.state != "done":
            j.state, j.t0 = "run", ts
        elif mk == "▶" and alive and j.state != "done":   # 调度器在跑、日志说开始了还没结束 = 正在做
            j.state, j.t0 = "run", ts
        elif mk == "✓":
            j.state = "done"
        elif mk == "✗" and j.state != "done":
            j.state = "fail"
        elif mk == "－" and j.state != "done":
            j.state = "blocked"
    print(f"调度器：{'在跑（pid %d）' % alive if alive else '没在跑'}；在跑的计算容器："
          f"{sorted(n for n in live if n.startswith('c1pipe_')) or '无'}")
    m = mem_avail_gb()
    if m is not None:
        print(f"可用内存 {m:.0f} GB")
    show(js)
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["plan", "run", "status"])
    ap.add_argument("--tags", nargs="+", default=["M33H1G5", "M96H24G5"])
    ap.add_argument("--runner", default="c1_run_g5.py", help="单情景脚本（tools/ 下）：c1_run_g5.py = 5 m；c1_run_15m.py = 15 m 新雨型")
    ap.add_argument("--slots", type=int, default=4, help="同时算几个态（服务器 2：32 核 / 64 GB → 4）")
    ap.add_argument("--threads", type=int, default=8, help="每个态的线程数（C2D_G5_THREADS）")
    ap.add_argument("--min-free-gb", type=float, default=10, help="新开一个态前要求的可用内存（GB，只在 Linux 上检查）")
    ap.add_argument("--stagger", type=float, default=300, help="两次开新态至少间隔几秒（等前一个的内存涨上来再判断）")
    ap.add_argument("--poll", type=float, default=60)
    ap.add_argument("--report-min", type=float, default=30, help="每隔几分钟打一次进度表")
    a = ap.parse_args()
    set_runner(a.runner)
    if a.cmd == "plan":
        js = jobs_for(a.tags)
        for j in js:
            print(f"  {'✓' if j.state == 'done' else '·'} {j.id:<26} {j.desc}" + (f"   ← {', '.join(j.deps)}" if j.deps else ""))
            print(f"      {' '.join(map(str, j.cmd[1:]))}")
        return 0
    return run(a) if a.cmd == "run" else status(a)


if __name__ == "__main__":
    raise SystemExit(main())
