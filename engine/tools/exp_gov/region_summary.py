"""一批区域的区域检查汇总成一张表（run_regions_check.sh 最后一步）。

  python tools/exp_gov/region_summary.py <配置目录>
读各区域 out/exp_gov/regions/<区域>/check.json，写 out/exp_gov/regions_rl/区域检查汇总.xlsx / .csv（配置目录名不同时用目录名）。
排序：可追踪比例高 → 网络错误少 → 影响水力模型的遗留问题密度（条 / km²）低。
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def main(cfg_dir):
    sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    for c in sorted(Path(cfg_dir).glob("*.json")):
        cfg = json.loads(c.read_text(encoding="utf-8"))
        p = ROOT / "out" / "exp_gov" / "regions" / cfg["name"] / "check.json"
        if not p.exists():
            rows.append({"区域": cfg["name"], "说明": cfg.get("note", "")[:40], "状态": "没有检查结果（失败或未跑）"}); continue
        k = json.loads(p.read_text(encoding="utf-8"))
        d, cn, rs = k.get("datum", {}), k.get("connectivity", {}), k.get("residual", {})
        v = {x["检查"]: x["结论"] for x in k.get("verdict", [])}
        km2 = k.get("domain_km2") or 0
        rows.append({
            "区域": cfg["name"], "说明": cfg.get("note", "").split("：")[0], "面积 km²": km2,
            "治理后管数": cn.get("gov_lines"), "可追踪到出水口的比例": cn.get("traced_share"), "子网数": cn.get("subnetworks"),
            "网络错误（处）": rs.get("network_errors"),
            "高程基准": v.get("高程基准"), "治理后井盖在 DTM ±1 m 内的比例": d.get("gov_share_within_1m"), "原样井盖比 DTM 高 95–105 m 的比例": d.get("raw_share_95_105"),
            "待客户处理（条）": rs.get("todo"), "其中影响水力模型（条）": rs.get("todo_affects_model"),
            "影响水力模型的问题密度（条 / km²）": round(rs.get("todo_affects_model", 0) / km2, 1) if km2 else None,
            "其中只影响台账（条）": rs.get("todo_ledger_only"),
            "顾问模型对账": (f"{k['consultant']['matched_share']:.0%} 对上，管底差中位 {k['consultant']['invert_diff_median_m']} m" if k.get("consultant") else "未做（没有顾问模型路径）"),
            "检查结论": "；".join(f"{a} {b}" for a, b in v.items()), "状态": "完成"})
    df = pd.DataFrame(rows)
    ok = df[df["状态"] == "完成"].sort_values(["可追踪到出水口的比例", "网络错误（处）", "影响水力模型的问题密度（条 / km²）"],
                                             ascending=[False, True, True])
    df = pd.concat([ok, df[df["状态"] != "完成"]], ignore_index=True)
    out = ROOT / "out" / "exp_gov" / ("regions_rl" if Path(cfg_dir).name == "rl" else f"regions_{Path(cfg_dir).name}")
    out.mkdir(parents=True, exist_ok=True)
    df.to_excel(out / "区域检查汇总.xlsx", index=False)
    df.to_csv(out / "区域检查汇总.csv", index=False, encoding="utf-8-sig")
    print(df[["区域", "面积 km²", "治理后管数", "可追踪到出水口的比例", "网络错误（处）", "高程基准", "待客户处理（条）", "其中影响水力模型（条）"]].to_string())
    print("写出", out / "区域检查汇总.xlsx")


if __name__ == "__main__":
    main(sys.argv[1])
