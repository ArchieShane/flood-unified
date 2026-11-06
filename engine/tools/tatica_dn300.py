"""result-017 的 .inp 里 DN300 有多少是「GDB 缺管径补的 0.3 m」（ad_run_30m.py 第 169 行：dia > 0 否则 0.3）。

  python tools/tatica_dn300.py <result-017 目录> <输出 json>

管段 c_<i> = build_city1d.read_pipes() 结果的第 i 行（ad_run_30m.network 按位置编号）。先用管长核对对应关系，再分三类：
GDB 原值 300 mm / GDB 缺管径（空或 ≤0）补的 / 其他管径。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_city1d as B  # noqa: E402


def conduits(inp):
    rows, on = {}, None
    with open(inp, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                on = s == "[CONDUITS]"
                continue
            if on and s and not s.startswith(";"):
                p = s.split()
                if p[0].startswith("c_"):
                    rows[int(p[0][2:])] = float(p[3])
    return rows


def main(root, out):
    df = B.read_pipes(None, log=print)
    dia = df.PIPE_DIAMETER.astype("float64").values
    ln = df.LineLength.astype("float64").values
    res = {}
    for inp in sorted(Path(root).glob("*/L1_existing_network/1d/AD_*_L1.inp")):
        c = conduits(inp)
        idx = np.array(sorted(c))
        L_inp = np.array([c[i] for i in idx])
        ok_len = np.isclose(L_inp, np.where(ln[idx] > 0, ln[idx], L_inp), rtol=1e-3, atol=0.01)
        d = dia[idx]
        miss = ~(d > 0)
        r = {"pipes": int(len(idx)), "length_match_share": float(ok_len.mean()),
             "gdb_300": int(np.sum(np.isclose(d, 300))), "filled_300": int(miss.sum()),
             "filled_300_km": float(L_inp[miss].sum() / 1000), "length_km": float(L_inp.sum() / 1000)}
        r["share_dn300_total"] = (r["gdb_300"] + r["filled_300"]) / r["pipes"]
        r["share_filled"] = r["filled_300"] / r["pipes"]
        res[inp.parent.parent.parent.name] = r
        print(f"{inp.name}: 管 {r['pipes']:,}，管长对上 {r['length_match_share']:.1%}；DN300 共 {r['share_dn300_total']:.1%}"
              f"（GDB 原值 {r['gdb_300']:,}，缺管径补的 {r['filled_300']:,} = {r['share_filled']:.1%}）", flush=True)
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:3])
