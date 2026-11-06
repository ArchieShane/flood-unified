# -*- coding: utf-8 -*-
"""英文界面残留中文体检（无头 Edge + CDP）：逐个 范围 × 阶段 打开 lang=en 的控制台，收集页面上可见、含中文的文字。

用法：python tools/i18n_audit.py [--out tmp/i18n_audit.json] [--wait 8] [--scopes city,rda,...] [--stages overview,l1,...]
要求：控制台服务在 http://127.0.0.1:8766 运行；访问密钥从 swbrain 配置读取（不打印）。
输出：每条残留文字 → 出现在哪些页面、所在元素（id / class 路径），按出现次数排序。
"""
from __future__ import annotations

import argparse
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
SCOPES = ["city", "rda", "rb01", "c1", "rb2", "wb", "pilot", "1d"]
STAGES = ["overview", "data", "l1", "l2", "l3", "concl", "more"]

# 页面里收集可见中文文字：文本节点 + title / placeholder / aria-label 属性；SVG 文字也算
COLLECT_JS = r"""(() => {
  const CJK = /[\u4e00-\u9fff]/, out = [];
  const path = el => { const p = []; for(let e = el; e && e !== document.body && p.length < 4; e = e.parentElement){
      p.unshift(e.id ? '#' + e.id : e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/)[0] : '')); }
    return p.join('>'); };
  const vis = el => { if(!el) return false; if(el.closest('#langbtn')) return false;
    const r = el.getClientRects(); if(!r.length) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while((n = w.nextNode())){ const v = n.nodeValue; if(!v || !CJK.test(v)) continue; const p = n.parentElement;
    if(!p || /^(SCRIPT|STYLE|TEXTAREA|NOSCRIPT)$/.test(p.nodeName) || !vis(p)) continue; out.push({t: v.trim().slice(0, 240), at: path(p)}); }
  document.querySelectorAll('[title],[placeholder],[aria-label]').forEach(el => { if(!vis(el)) return;
    ['title', 'placeholder', 'aria-label'].forEach(a => { const v = el.getAttribute(a); if(v && CJK.test(v)) out.push({t: v.trim().slice(0, 240), at: path(el) + '@' + a}); }); });
  if(CJK.test(document.title)) out.push({t: document.title, at: 'document.title'});
  return out; })()"""


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "tmp" / "i18n_audit.json"))
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--port", type=int, default=9341)
    ap.add_argument("--base", default="http://127.0.0.1:8766/")
    ap.add_argument("--scopes", default=",".join(SCOPES))
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--extra", default="", help="另加的查询串，用 ; 分隔，如 'tab=ontology;tab=line2p&v=c1&st=L2P'")
    a = ap.parse_args(argv)
    import websocket
    from swbrain import db
    key = str((db.load_config(None).get("serve") or {}).get("access_key") or "")
    prof = tempfile.mkdtemp(prefix="edge_cdp_")
    pr = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={a.port}", "--remote-allow-origins=*",
                           f"--user-data-dir={prof}", "--window-size=1600,1000", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        cmd("Runtime.enable"); cmd("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        pages = [f"scope={s}&stage={g}" for s in a.scopes.split(",") for g in a.stages.split(",")]
        pages += [q for q in a.extra.split(";") if q]
        hits: dict[str, dict] = {}
        for qs in pages:
            q = dict(urllib.parse.parse_qsl(qs)); q["lang"] = "en"; q["key"] = key
            e0 = len(errs)
            cmd("Page.navigate", url=a.base + "?" + urllib.parse.urlencode(q)); time.sleep(a.wait)
            res = cmd("Runtime.evaluate", expression=COLLECT_JS, returnByValue=True).get("result", {}).get("value") or []
            for r in res:
                h = hits.setdefault(r["t"], {"text": r["t"], "pages": [], "at": set()})
                if qs not in h["pages"]: h["pages"].append(qs)
                h["at"].add(r["at"])
            print(f"{qs:32s} 中文 {len(res):4d} 条" + (f"  脚本报错 {len(errs) - e0}" if len(errs) > e0 else ""), flush=True)
        rows = sorted(({**h, "at": sorted(h["at"])[:5]} for h in hits.values()), key=lambda h: -len(h["pages"]))
        out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"n_unique": len(rows), "errors": errs, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {out}；不同的中文文字 {len(rows)} 条；脚本报错 {len(errs)} 条")
        return 0
    finally:
        pr.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
