"""本机用合成二维结果测 adm250_post.py（不需要 docker）：假 npz / twoway，其余用 build 真实生成的 pilot / inputs / 案例。"""
import json, os, pathlib, sys, tempfile
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
WK = pathlib.Path(r"C:\freedo\swbrain_work\swmm")
C2D = WK / "c2d"
tag = "CITY_M61H1"; name = f"AX_{tag}"
meta = json.loads((C2D / f"pilot_{name}_L2.json").read_text(encoding="utf-8"))
b = meta["box"]
rng = np.random.default_rng(1)
n = 91000
xy = np.column_stack([rng.uniform(b[0], b[2], n), rng.uniform(b[1], b[3], n)])
area = np.full(n, 250 * 250 / 3 * 0.65)
elev = rng.uniform(0, 30, n)
th = np.arange(0, 5.01, 5 / 60)
fr = np.zeros((len(th), n), np.float16)
hm = rng.exponential(0.05, n).astype(np.float32)
for i in range(len(th)):
    fr[i] = hm * min(1.0, i / 20)
np.savez_compressed(C2D / f"{name}_L2_2d.npz", xy=xy.astype(np.float32), area=area, elev=elev, hmax=hm, frames=fr, t_h=th)
tw = {"acc_m3": {"1to2": 1e6, "2to1": 5e5, "rain": 2e6, "pond": 3e5}, "final_vol_m3": 4e5, "boundary_out_m3": 1e5, "wall_s": 3600}
(C2D / f"twoway_{name}_L2.json").write_text(json.dumps(tw), encoding="utf-8")
out = pathlib.Path(tempfile.mkdtemp(prefix="adm250_post_test_", dir=r"C:\Users\admin\AppData\Local\Temp\claude\C--freedo-zn-260930\a789a4c4-e89a-4f5d-9217-6bc78cf4dbee\scratchpad"))
os.environ["AD_RESULT_ROOT"] = str(out); os.environ["AD_HOST"] = "testhost"
sys.path.insert(0, r"C:\freedo\zn-260930\thinking-drainage-system\tools")
import adm250_post as P
P.W = WK; P.C2D = C2D
sys.argv = ["adm250_post.py", "--tag", tag, "--res", "250"]
rc = P.main()
print("rc", rc, "out", out)
for p in sorted(out.rglob("*")):
    if p.is_file():
        print("  ", p.relative_to(out), p.stat().st_size)
