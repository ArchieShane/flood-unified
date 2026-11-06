"""落盘缓存（指纹、读写、坏文件、修剪）与任务表 / 进程池（同进程回落、去重、状态流转）。不启服务。"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd

from swbrain import artifactcache as AC, jobs as J


def _cfg(tmp_path, extra=None):
    c = {"model": {"scenarios": {"x": 1}}, "dispatch": {"a": 1}, "serve": {"access_key": "secret"}, "source": {"pipe": {"password": "p"}}, "exchange": {"dir": str(tmp_path / "exchange")}}
    if extra:
        c.update(extra)
    return c


def test_fingerprints_and_cache_roundtrip(tmp_path):
    root = tmp_path; (root / "swbrain").mkdir(); (root / "out" / "state").mkdir(parents=True)
    (root / "swbrain" / "dispatch.py").write_text("x = 1\n", encoding="utf-8"); (root / "out" / "state" / "es.parquet").write_bytes(b"abc")
    cfg = _cfg(tmp_path)
    s1 = AC.signature(cfg, root)
    assert len(s1["sig"]) == 16 and s1["files"][0]["name"] == "es.parquet"
    # [serve] / [source] 变了不影响指纹；[dispatch] 变了影响
    assert AC.signature(_cfg(tmp_path, {"serve": {"access_key": "other"}}), root)["sig"] == s1["sig"]
    assert AC.signature(_cfg(tmp_path, {"dispatch": {"a": 2}}), root)["sig"] != s1["sig"]
    # 模型模块源码变了影响；状态文件变了影响
    (root / "swbrain" / "dispatch.py").write_text("x = 2\n", encoding="utf-8"); s2 = AC.signature(cfg, root); assert s2["sig"] != s1["sig"] and s2["code"] != s1["code"]
    (root / "out" / "state" / "es.parquet").write_bytes(b"abcd"); s3 = AC.signature(cfg, root); assert s3["state"] != s2["state"]
    c = AC.Cache(cfg, root)
    assert c.enabled and (c.dir / "manifest.json").exists() and c.dir.parent.name == "cache"
    df = pd.DataFrame({"a": [1, 2]}); obj = {"pay": {"n": 1}, "plan": df, "t": 210.0}
    assert c.put("pay_x", obj) and c.has("pay_x")
    back = c.get("pay_x"); assert back["pay"] == {"n": 1} and back["plan"].equals(df) and c.hits == 1
    assert c.get("nope") is None and c.misses == 1
    (c.dir / "pay_x.pkl").write_bytes(b"garbage")                      # 坏文件 → 当没有并删掉
    assert c.get("pay_x") is None and not (c.dir / "pay_x.pkl").exists()
    st = c.stats(); assert st["enabled"] and st["writes"] == 1 and st["sig"] == c.sig
    # 修剪：造 4 个旧目录，只留 3 个（含当前）
    for i in range(4):
        d = c.dir.parent / f"old{i}"; d.mkdir(); (d / "manifest.json").write_text("{}"); time.sleep(0.01)
    c2 = AC.Cache(cfg, root, keep=3)
    assert len([d for d in c2.dir.parent.iterdir() if d.is_dir()]) <= 3


def test_traffic_hash_and_disabled_cache(tmp_path):
    assert AC.traffic_hash(None) == "none" and AC.traffic_hash({"error": "x"}) == "none"
    h1 = AC.traffic_hash({"provider": "simulated", "factors": {"A": {"factor": 1.2341}}}); h2 = AC.traffic_hash({"provider": "simulated", "factors": {"A": {"factor": 1.2349}}})
    assert h1 != "none" and h1 != h2 and h1 == AC.traffic_hash({"provider": "simulated", "factors": {"A": {"factor": 1.2341001}}})      # 三位取整后相同
    c = AC.Cache(_cfg(tmp_path), tmp_path, enabled=False)
    assert not c.enabled and not c.put("a", 1) and c.get("a") is None and not c.has("a")


def _square(x):
    return x * x


def test_pool_inline_and_jobs_flow():
    pool = J.SolverPool(workers=0)
    assert pool.run(_square, 7) == 49 and pool.stats()["inline_runs"] == 1
    jobs = J.Jobs(threads=2, ttl_s=60)
    ev = {"n": 0}

    def slow():
        time.sleep(0.3); ev["n"] += 1; return {"ok": True}
    j1 = jobs.submit("dispatch", {"t": 210}, slow); j2 = jobs.submit("dispatch", {"t": 210}, slow)
    assert j1["job_id"] == j2["job_id"] and j1["status"] in ("queued", "running")           # 同参数复用
    j3 = jobs.submit("dispatch", {"t": 300}, slow); assert j3["job_id"] != j1["job_id"]
    for _ in range(50):
        if jobs.get(j1["job_id"])["status"] == "done":
            break
        time.sleep(0.05)
    g = jobs.get(j1["job_id"]); assert g["status"] == "done" and g["result"] == {"ok": True} and g["elapsed_s"] >= 0.2
    assert jobs.get("nope") is None and len(jobs.list()) == 2

    def boom():
        raise ValueError("bad input")
    jb = jobs.submit("stress", {}, boom)
    for _ in range(50):
        if jobs.get(jb["job_id"])["status"] == "error":
            break
        time.sleep(0.05)
    assert jobs.get(jb["job_id"])["error"].startswith("ValueError")
    time.sleep(0.4); assert ev["n"] == 2                                                     # 同参数只跑了一次 + t=300 一次


def test_pool_subprocess_roundtrip():
    pool = J.SolverPool(workers=1)
    try:
        assert pool.run(_square, 9) == 81 and pool.stats()["pool_runs"] == 1 and pool.stats()["alive"]
    finally:
        pool.shutdown()


def test_state_manifest_ignores_own_flood_point_outputs(tmp_path):
    """本系统写回 exchange/predictions 的 flood_points.system-*.json 不能进状态指纹，否则每算一个情景就自作废。"""
    root = tmp_path; (root / "swbrain").mkdir(); (root / "out" / "state").mkdir(parents=True)
    (root / "swbrain" / "dispatch.py").write_text("x = 1\n", encoding="utf-8")
    pred = root / "exchange" / "predictions"; pred.mkdir(parents=True)
    cfg = _cfg(tmp_path)
    s1 = AC.signature(cfg, root)
    (pred / "flood_points.system-10cc.json").write_text('{"a": 1}', encoding="utf-8")          # 自己的产物：指纹不变
    assert AC.signature(cfg, root)["state"] == s1["state"]
    (pred / "flood_points.gwm-run7.json").write_text('{"b": 2}', encoding="utf-8")             # 预测侧给的：指纹要变
    s2 = AC.signature(cfg, root); assert s2["state"] != s1["state"]
    assert [f["name"] for f in s2["files"]] == ["flood_points.gwm-run7.json"]


# ---- pay_cache 的键 ----
# 2026-09-23 评审发现：写入方用 6 元组，state.py 里有两处查询手写成 (key, t) 两元组，必然查不到。
# 后果是指定非激活情景看单热点时，「按调度单派泵」那条曲线永远不出来。

def test_pay_key_shape_and_defaults():
    from swbrain.state import State

    class Stub:
        traffic = {"id": 3}

    s = Stub()
    k = State._pay_key(s, "10cc", 210)
    assert len(k) == 6 and k == ("10cc", 210.0, "balanced", 0.0, 3, False)
    assert State._pay_key(s, "10cc", 210) == State._pay_key(s, "10cc", 210.0, None, None, False)   # 缺省要和显式传一致
    assert State._pay_key(s, "10cc", 210, "FASTEST ") == ("10cc", 210.0, "fastest", 0.0, 3, False) # 目标大小写与空白归一
    assert State._pay_key(s, "10cc", 210, "cheapest", 5000, True) == ("10cc", 210.0, "cheapest", 5000.0, 3, True)
    assert State._pay_key(s, "10cc", 210) != State._pay_key(s, "10cc", 211)


def test_pay_cache_is_only_keyed_through_pay_key():
    """静态判据：state.py 里不许再手写 pay_cache 的键（内联元组），ck 也必须由 _pay_key 产出。"""
    import ast
    path = Path(__file__).resolve().parents[1] / "swbrain" / "state.py"
    src = path.read_text(encoding="utf-8")
    inline = [ln.strip() for ln in src.splitlines() if "pay_cache.get((" in ln or "pay_cache[(" in ln]
    assert not inline, "pay_cache 的键是内联元组，应走 _pay_key：" + " | ".join(inline)
    # 每个碰 pay_cache 的方法里都必须出现 _pay_key（键只能从那里来）
    tree = ast.parse(src)
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(src, fn) or ""
        touches_one = "self.pay_cache.get(" in body or "self.pay_cache[" in body        # 整体重建（清理）不算
        if touches_one and "_pay_key" not in body:
            bad.append(fn.name)
    assert not bad, "这些方法动了 pay_cache 却没走 _pay_key：" + ", ".join(bad)


# ---- 代码指纹 ----
# 2026-09-23：改一行注释就让整个盘上缓存作废、重算一轮 10 分钟；
# 反过来 tools/hotspot_workbook.py（决定热点匹配半径、面积、汇流时间）却不在指纹里，改了不失效。

def test_code_fingerprint_ignores_comments(tmp_path, monkeypatch):
    root = tmp_path
    (root / "swbrain").mkdir()
    src = "def f(x):\n    return x * 2\n"
    for m in AC.MODEL_MODULES:
        f = root / "swbrain" / (m.replace(".", "/") + ".py")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    base = AC.code_fingerprint(root)

    target = root / "swbrain" / "dispatch.py"
    target.write_text("# 一行注释\ndef f(x):\n    return x * 2      # 行尾注释\n", encoding="utf-8")
    assert AC.code_fingerprint(root) == base, "改注释不该让缓存作废"

    target.write_text("\n\ndef f(x):\n\n    return x * 2\n\n", encoding="utf-8")
    assert AC.code_fingerprint(root) == base, "改空行不该让缓存作废"

    target.write_text("def f(x):\n    return x * 3\n", encoding="utf-8")
    assert AC.code_fingerprint(root) != base, "改了算式却没换指纹"


def test_hotspot_workbook_is_in_the_fingerprint():
    """客户热点工作簿的解析决定热点→管段的匹配半径、面积、汇流时间，是真影响缓存内容的模块。"""
    assert "tools.hotspot_workbook" in AC.MODEL_MODULES


def test_fingerprint_survives_unparseable_source(tmp_path):
    """词法过不去时退回按原样哈希，不能抛异常把服务带崩。"""
    root = tmp_path
    (root / "swbrain").mkdir()
    for m in AC.MODEL_MODULES:
        f = root / "swbrain" / (m.replace(".", "/") + ".py")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def f():\n    pass\n", encoding="utf-8")
    (root / "swbrain" / "dispatch.py").write_text("def f(:\n  ???\n", encoding="utf-8")
    assert len(AC.code_fingerprint(root)) == 12
