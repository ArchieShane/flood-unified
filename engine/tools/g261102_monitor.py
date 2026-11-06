#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""261101 六区推演的监控 + 自动善后（跑在服务器宿主机上，只用标准库，python3 >= 3.6）。

每隔 --interval 秒（缺省 300）做一圈：
  1. 读本服务器各区域的进度（<工作目录>/run/<区域>/status.json）、推演容器状态、内存、磁盘；
  2. 写 <NAS>/monitor/server<N>.json / .txt，再把三台的合并成 <NAS>/monitor/summary.txt 和 index.html（浏览器自动 60 s 刷新；三台共用 NAS，所以三台的页面内容一样）；
  3. 发现问题写 <NAS>/monitor/alerts.log（同一件事只记一次）：算例失败、容器异常退出、状态 30 分钟不更新（卡住）、内存 > 90 %、磁盘不足、队列没了但还有没跑的；
  4. 自动善后（--no-heal 关掉），只做这两件，每个区域有次数上限：
       · 区域容器没在跑、有算例因二维结果 NaN 失败、而且已装 NaN 补丁（tools/g261101_nanfix.py）→ 自动 `<区域>-server.sh start --allow-unmodeled`（续跑，只重做后处理），最多 2 次；
       · 区域容器没在跑（机器重启 / 容器被杀）、还有没跑完的算例 → 自动续跑，最多 3 次。
     其它原因的失败只报警，不自动重跑。
用法：scripts/g261101-monitor.sh <1|2|3> start | stop | status | once | serve
"""
import argparse
import datetime as dt
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time


def sh(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, universal_newlines=True)
        return p.returncode, p.stdout.strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def now_dt():
    return dt.datetime.now(dt.timezone.utc).astimezone()


def parse_t(s):
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def write_atomic(p, text):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)


def hms(sec):
    sec = int(max(sec, 0))
    return "%dh%02dm" % (sec // 3600, sec % 3600 // 60)


def container_state(name):
    rc, out = sh(["docker", "inspect", "-f", "{{.State.Status}}|{{.State.ExitCode}}", name])
    if rc != 0:
        return "none", None
    st, _, code = out.partition("|")
    return st, int(code) if code.lstrip("-").isdigit() else None


def mem_pct(name):
    rc, out = sh(["docker", "stats", "--no-stream", "--format", "{{.MemPerc}}|{{.MemUsage}}|{{.CPUPerc}}", name], timeout=30)
    if rc != 0 or "|" not in out:
        return None, "", ""
    p, u, c = (out.split("|") + ["", ""])[:3]
    try:
        return float(p.strip("%")), u, c
    except ValueError:
        return None, u, c


def region_info(R, work, now):
    st = read_json(os.path.join(work, "run", R, "status.json"), {}) or {}
    scn = st.get("scenarios", {})
    counts = {"done": 0, "running": 0, "pending": 0, "failed": 0}
    for v in scn.values():
        counts[v.get("state", "pending")] = counts.get(v.get("state", "pending"), 0) + 1
    # ETA：每个模拟小时用掉多少秒（完成的按总用时 / 模拟小时，在跑的按已用 / 已算）
    rates, remain_h = [], 0.0
    for k, v in scn.items():
        tot, done = float(v.get("sim_h_total") or 0), float(v.get("sim_h_done") or 0)
        t0, t1 = parse_t(v.get("started_at") or ""), parse_t(v.get("updated_at") or "")
        if v.get("state") == "done" and tot and t0 and t1:
            rates.append((t1 - t0).total_seconds() / tot)
        elif v.get("state") == "running" and done > 0.5 and t0:
            rates.append((now - t0).total_seconds() / done)
            remain_h += max(tot - done, 0)
        elif v.get("state") == "running":
            remain_h += tot or 0
        elif v.get("state") == "pending":
            m = re.match(r"S\d+D(\d+)", k)          # 排队的算例：模拟小时 = 雨历时 + 雨后 10 h（1 h→11、2 h→12、24 h→34）
            remain_h += float(m.group(1)) + 10.0 if m else 11.0
    rate = sorted(rates)[len(rates) // 2] if rates else None
    return {"region": R, "state": st.get("state", "未启动"), "message": st.get("message", ""), "counts": counts, "total": len(scn), "updated_at": st.get("updated_at"),
            "scenarios": {k: {"state": v.get("state"), "stage": v.get("stage"), "pct": v.get("progress_pct"), "sim_h_done": v.get("sim_h_done"), "sim_h_total": v.get("sim_h_total"),
                              "message": (v.get("message") or "")[:160], "updated_at": v.get("updated_at")} for k, v in scn.items()},
            "rate_s_per_simh": rate, "remain_simh": remain_h}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", type=int, required=True)
    ap.add_argument("--regions", required=True, help="本服务器分到的区域，逗号分隔（按排队顺序）")
    ap.add_argument("--engine", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    ap.add_argument("--nas", default=os.environ.get("NAS_DIR"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR"))
    ap.add_argument("--slots", type=int, default=5)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-heal", action="store_true")
    a = ap.parse_args()
    env = {}
    ef = os.path.join(a.engine, "docker", ".env")
    if os.path.isfile(ef):
        for line in open(ef, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k] = v
    nas = a.nas or env.get("NAS_DIR", "/media/pop-fs/flood_data/result-v2")
    work = a.work or env.get("WORK_DIR", "/data/swbrain_work")
    regions = [r for r in a.regions.split(",") if r]
    mdir = os.path.join(nas, "monitor")
    os.makedirs(mdir, exist_ok=True)
    state_f = os.path.join(mdir, "monitor_state_server%d.json" % a.server)
    seen_f = os.path.join(mdir, "alerts_seen_server%d.json" % a.server)
    host = os.uname()[1]

    def alert(key, text):
        seen = read_json(seen_f, {}) or {}
        if key in seen:
            return
        seen[key] = now_dt().isoformat(timespec="seconds")
        write_atomic(seen_f, json.dumps(seen, ensure_ascii=False))
        with open(os.path.join(mdir, "alerts.log"), "a", encoding="utf-8") as f:
            f.write("[%s] 服务器 %d（%s）：%s\n" % (now_dt().strftime("%Y-%m-%d %H:%M:%S"), a.server, host, text))

    def heal_start(R, why, kind, limit):
        stt = read_json(state_f, {}) or {}
        key = "%s:%s" % (R, kind)
        n = stt.get(key, 0)
        if n >= limit:
            alert("heal-limit-%s" % key, "%s：自动续跑已经试了 %d 次，不再自动重试，需要人看" % (R, n))
            return
        stt[key] = n + 1
        write_atomic(state_f, json.dumps(stt))
        script = os.path.join(a.engine, "data", "regions", R, "%s-server.sh" % R)
        log = os.path.join(mdir, "heal_%s_%s.log" % (R, now_dt().strftime("%m%d_%H%M%S")))
        if os.environ.get("G261101_HEAL_DRY") == "1":      # 演练：只记一笔，不真启动（测试用）
            alert("heal-%s-%d" % (key, n + 1), "%s：%s → 【演练】会自动续跑（第 %d 次），没有真正启动" % (R, why, n + 1))
            return
        # 261102：续跑要接着「当前这一批」场景（status.json 里的键），不能按 region.toml 的默认场景——24 h 场景不在默认里，按默认续跑会漏掉它们
        tags = list(((read_json(os.path.join(work, "run", R, "status.json"), {}) or {}).get("scenarios") or {}).keys())
        cmd = ["bash", script, "start"] + (["--scenarios", ",".join(tags)] if tags else []) + ["--allow-unmodeled"]
        subprocess.Popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT, preexec_fn=os.setsid)
        alert("heal-%s-%d" % (key, n + 1), "%s：%s → 已自动续跑（第 %d 次，输出 %s）" % (R, why, n + 1, os.path.basename(log)))

    while True:
        now = now_dt()
        out = {"server": a.server, "host": host, "time": now.isoformat(timespec="seconds"), "regions": [], "alerts_now": []}
        du = shutil.disk_usage(work if os.path.isdir(work) else "/")
        du2 = shutil.disk_usage(nas)
        out["disk_free_gb"] = {"work": round(du.free / 1e9), "nas": round(du2.free / 1e9)}
        if du.free < 100e9:
            alert("disk-work-%s" % now.strftime("%m%d"), "工作目录剩余 %d GB（< 100 GB）" % (du.free / 1e9))
        if du2.free < 200e9:
            alert("disk-nas-%s" % now.strftime("%m%d"), "NAS 剩余 %d GB（< 200 GB）" % (du2.free / 1e9))
        qpid = os.path.join(work, "run", "g261101-queue.pid")
        queue_alive = False
        try:
            pid = int(open(qpid).read().strip())
            os.kill(pid, 0)
            queue_alive = True
        except Exception:  # noqa: BLE001
            pass
        out["queue_alive"] = queue_alive
        any_running = False
        for R in regions:
            info = region_info(R, work, now)
            cs, code = container_state("flood-job-%s" % R)
            info["container"] = cs
            info["exit_code"] = code
            if cs == "running":
                any_running = True
                p, u, c = mem_pct("flood-job-%s" % R)
                info["mem_pct"], info["mem"], info["cpu"] = p, u, c
                if p is not None and p > 90:
                    alert("mem-%s-%s" % (R, now.strftime("%m%d%H")), "%s 容器内存 %.0f %%（%s）" % (R, p, u))
                upd = parse_t(info.get("updated_at") or "")
                if upd and (now - upd).total_seconds() > 1800:
                    alert("stale-%s-%s" % (R, upd.strftime("%m%d%H%M")), "%s 的进度已经 %d 分钟没更新（可能卡住）" % (R, (now - upd).total_seconds() / 60))
            c = info["counts"]
            for k, v in info["scenarios"].items():
                if v["state"] == "failed":
                    alert("fail-%s-%s" % (R, k), "%s %s 失败：%s" % (R, k, v["message"]))
            unfinished = c["pending"] + c["running"]
            nan_failed = [k for k, v in info["scenarios"].items() if v["state"] == "failed" and "NaN" in (v["message"] or "")]
            if cs not in ("running", "paused", "restarting") and info["total"]:
                if cs == "exited" and code not in (0, None):
                    alert("exit-%s-%s" % (R, info.get("updated_at")), "%s 容器异常退出（退出码 %s）" % (R, code))
                if not a.no_heal:
                    if nan_failed and os.path.isfile(os.path.join(a.engine, "tools", "g261101_nanfix.py")):
                        heal_start(R, "%d 个算例因二维结果 NaN 失败，已装 NaN 补丁" % len(nan_failed), "nan", 2)
                    elif unfinished:
                        heal_start(R, "容器没在跑，还有 %d 个算例没跑完" % unfinished, "resume", 3)
                elif unfinished or nan_failed:
                    alert("needs-%s-%s" % (R, info.get("updated_at")), "%s 容器没在跑，还有 %d 个没跑完 / %d 个 NaN 失败（自动善后已关）" % (R, unfinished, len(nan_failed)))
            out["regions"].append(info)
        # 没有任何区域在跑、队列也没了，但名单里有没启动过的区域
        never = [i["region"] for i in out["regions"] if i["state"] == "未启动"]
        if never and not any_running and not queue_alive:
            alert("never-%s" % "-".join(never), "队列已经不在，但 %s 还没启动过（本机没有在跑的区域）" % "、".join(never))
        out["idle"] = bool(regions) and not any_running and all(i["state"] in ("done",) or (i["total"] and i["counts"]["pending"] + i["counts"]["running"] == 0) for i in out["regions"])
        write_atomic(os.path.join(mdir, "server%d.json" % a.server), json.dumps(out, ensure_ascii=False, indent=1))
        render(mdir)
        if a.once:
            print(open(os.path.join(mdir, "summary.txt"), encoding="utf-8").read())
            return 0
        time.sleep(a.interval)


def render(mdir):
    servers = []
    for f in sorted(glob.glob(os.path.join(mdir, "server*.json"))):
        d = read_json(f)
        if d:
            servers.append(d)
    now = now_dt()
    lines = ["261102 八区推演监控 / 261101 监控（261102 版：续跑按当前批场景）  生成于 %s" % now.strftime("%Y-%m-%d %H:%M:%S"), ""]
    rows = []
    for s in servers:
        age = (now - parse_t(s["time"])).total_seconds() if parse_t(s["time"]) else 0
        head = "服务器 %d（%s）  监控更新 %s%s  磁盘剩余 工作目录 %s GB / NAS %s GB  队列%s" % (
            s["server"], s["host"], s["time"][11:19], "  ⚠ 监控 %d 分钟没更新" % (age / 60) if age > 900 else "", s["disk_free_gb"]["work"], s["disk_free_gb"]["nas"], "运行中" if s["queue_alive"] else "无")
        lines.append(head)
        rows.append(("srv", head))
        for r in s["regions"]:
            c = r["counts"]
            eta = ""
            if r["rate_s_per_simh"] and r["remain_simh"]:
                slots = max(min(c["running"] + c["pending"], 8), 1)
                eta = "  预计还需约 %s" % hms(r["remain_simh"] * r["rate_s_per_simh"] / slots)
            mem = "  内存 %s" % r["mem"] if r.get("mem") else ""
            ln = "  %-8s %s | 完成 %d 进行 %d 排队 %d 失败 %d（共 %d）| 容器 %s%s%s" % (r["region"], r["state"], c["done"], c["running"], c["pending"], c["failed"], r["total"], r["container"], eta, mem)
            lines.append(ln)
            rows.append(("reg", r))
            for k, v in r["scenarios"].items():
                if v["state"] == "running":
                    lines.append("      %-18s %s %s %s/%s h  %s" % (k, v["stage"], ("%.0f%%" % v["pct"]) if v["pct"] is not None else "", v["sim_h_done"], v["sim_h_total"], ""))
                elif v["state"] == "failed":
                    lines.append("      %-18s 失败：%s" % (k, v["message"]))
        if s.get("idle"):
            lines.append("  → 本服务器分到的区域都跑完了（空闲）")
        lines.append("")
    al = os.path.join(mdir, "alerts.log")
    alerts = open(al, encoding="utf-8").read().splitlines()[-30:] if os.path.isfile(al) else []
    lines.append("最近的报警 / 自动善后记录（最新在下）：")
    lines += ["  " + x for x in alerts] or ["  （没有）"]
    write_atomic(os.path.join(mdir, "summary.txt"), "\n".join(lines) + "\n")
    # HTML
    h = ['<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="60"><title>261101 推演监控</title>'
         '<style>body{font-family:-apple-system,Segoe UI,sans-serif;margin:20px;background:#fafafa;color:#222}h2{margin:18px 0 6px}table{border-collapse:collapse;margin:4px 0 12px;font-size:13px}'
         'td,th{border:1px solid #ddd;padding:3px 8px;text-align:left}.bar{background:#e5e5e5;width:120px;height:10px;display:inline-block;border-radius:5px;overflow:hidden}'
         '.bar i{display:block;height:10px;background:#3b82f6}.bad{color:#b91c1c}.ok{color:#15803d}pre{background:#fff;border:1px solid #ddd;padding:8px;font-size:12px;overflow:auto}</style>',
         "<h1>261102 八区推演监控 / 261101 监控（261102 版：续跑按当前批场景）</h1><p>生成于 %s（页面每 60 秒自动刷新）</p>" % html.escape(now.strftime("%Y-%m-%d %H:%M:%S"))]
    for s in servers:
        h.append("<h2>服务器 %d（%s）</h2><p>监控更新 %s ｜ 磁盘剩余 工作目录 %s GB / NAS %s GB ｜ 队列%s%s</p>" % (
            s["server"], html.escape(s["host"]), s["time"][11:19], s["disk_free_gb"]["work"], s["disk_free_gb"]["nas"], "运行中" if s["queue_alive"] else "无", " ｜ <b class='ok'>已空闲</b>" if s.get("idle") else ""))
        h.append("<table><tr><th>区域</th><th>状态</th><th>完成</th><th>进行</th><th>排队</th><th>失败</th><th>容器</th><th>内存</th><th>预计还需</th></tr>")
        for r in s["regions"]:
            c = r["counts"]
            eta = hms(r["remain_simh"] * r["rate_s_per_simh"] / max(min(c["running"] + c["pending"], 8), 1)) if r["rate_s_per_simh"] and r["remain_simh"] else "—"
            h.append("<tr><td>%s</td><td>%s</td><td>%d / %d</td><td>%d</td><td>%d</td><td class='%s'>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                r["region"], html.escape(r["state"]), c["done"], r["total"], c["running"], c["pending"], "bad" if c["failed"] else "", c["failed"], r["container"], html.escape(r.get("mem") or ""), eta))
            run = [(k, v) for k, v in r["scenarios"].items() if v["state"] in ("running", "failed")]
            if run:
                h.append("<tr><td></td><td colspan=8>" + "<br>".join(
                    ("%s：%s %s <span class='bar'><i style='width:%d%%'></i></span> %s/%s h" % (html.escape(k), html.escape(v["stage"] or ""), "", v["pct"] or 0, v["sim_h_done"], v["sim_h_total"])) if v["state"] == "running"
                    else "<span class='bad'>%s 失败：%s</span>" % (html.escape(k), html.escape(v["message"])) for k, v in run[:40]) + "</td></tr>")
        h.append("</table>")
    h.append("<h2>报警 / 自动善后记录</h2><pre>%s</pre>" % html.escape("\n".join(alerts) or "（没有）"))
    write_atomic(os.path.join(mdir, "index.html"), "".join(h))


if __name__ == "__main__":
    sys.exit(main())
