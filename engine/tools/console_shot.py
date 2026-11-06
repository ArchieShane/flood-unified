# -*- coding: utf-8 -*-
"""控制台截图（无头 Edge + CDP）：给在线报告配图，也用来检查页面有没有脚本报错。

为什么放在仓库里（2026-09-29）：原来的测试脚本在会话临时目录，被整个清空了。
用法：python tools/console_shot.py <输出.jpg> "<查询串，如 tab=line2p&v=c1&r=61&st=L2P>" [--js "<截图前执行的脚本>"] [--wait 秒]
要求：控制台服务在 http://127.0.0.1:8766 运行；访问密钥从 swbrain 配置读取（不打印）。
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("query"); ap.add_argument("--js", default=""); ap.add_argument("--wait", type=float, default=10.0)
    ap.add_argument("--port", type=int, default=9340); ap.add_argument("--size", default="1500,1000")
    ap.add_argument("--base", default="http://127.0.0.1:8766/", help="控制台地址；静态服务（只看 C1）如 http://127.0.0.1:8771/")
    ap.add_argument("--admin", action="store_true", help="先以管理员登录（从配置读 [serve].admin_key 写进 cookie，不打印），截管理端用")
    a = ap.parse_args(argv)
    import websocket
    from swbrain import db
    key = str((db.load_config(None).get("serve") or {}).get("access_key") or "")
    prof = tempfile.mkdtemp(prefix="edge_cdp_")
    w, h = map(int, a.size.split(","))
    pr = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={a.port}", "--remote-allow-origins=*",
                           f"--user-data-dir={prof}", f"--window-size={w},{h}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tabs = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json")); break
            except Exception:
                time.sleep(0.3)
        ws = websocket.create_connection([t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"], timeout=240)
        n = [0]; errs = []

        def cmd(m, **p):
            n[0] += 1; ws.send(json.dumps({"id": n[0], "method": m, "params": p}))
            while True:
                r = json.loads(ws.recv())
                if r.get("method") == "Runtime.exceptionThrown":
                    errs.append((r["params"]["exceptionDetails"].get("exception") or {}).get("description", "")[:300])
                if r.get("id") == n[0]:
                    return r.get("result", {})
        cmd("Runtime.enable"); cmd("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        if a.admin:
            ak = str((db.load_config(None).get("serve") or {}).get("admin_key") or "")
            cmd("Network.enable"); cmd("Network.setCookie", name="swb_admin", value=urllib.parse.quote(ak), domain="127.0.0.1", path="/", httpOnly=True)
        q = dict(urllib.parse.parse_qsl(a.query)); q["key"] = key
        base, _, frag = a.base.partition("#")
        cmd("Page.navigate", url=base + "?" + urllib.parse.urlencode(q) + ("#" + frag if frag else ""))
        time.sleep(a.wait)
        if a.js:
            cmd("Runtime.evaluate", expression=a.js, returnByValue=True); time.sleep(4)
        data = cmd("Page.captureScreenshot", format="jpeg", quality=82)["data"]
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_bytes(base64.b64decode(data))
        print(f"→ {a.out}；脚本报错 {len(errs)} 条" + ("".join("\n  " + e for e in errs) if errs else ""))
        return 1 if errs else 0
    finally:
        pr.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
