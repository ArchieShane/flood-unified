#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全市三级防线快速工作流（用已治理 90% 的数据直接出结果）

策略：
  1. 从全市治理后 DMT 按出水口自动划分汇水域（tools/citywide.py 的逻辑）
  2. 每个汇水域独立建一二维模型并行计算（L1）
  3. 对溢流 > 阈值的汇水域生成 L2 候选并优化
  4. L2 验证（再算一二维）
  5. L3 调度残余积水
  6. 拼接成全市结果

口径：
  - 用已治理的 90% 管网（234,149 根），剩余 10% 孤立系统会表现为"无出口"，水在地面积
  - 产流用全市统一 CN=82（客户参数）+ Makani 不透水率
  - 小汇水域（< 阈值）只算 L1，不做 L2 优化（成本效益低）
  - 可按汇水域并行（4-6 个），总机时约 2-4 天

用法：
    python tools/citywide_3lines_fast.py --storm 61mm1h --parallel 4 [--dry-run]

参数：
    --storm: 设计暴雨 ID（如 61mm1h, 96mm2h）
    --parallel: 并行数（默认 4）
    --min-area-ha: 汇水域面积阈值，小于此值只算 L1（默认 50 ha）
    --dry-run: 只生成计划，不执行
    --resume: 从中断处续跑

产物：
    out/citywide_3lines/<storm_id>/
      ├── plan.json                 # 汇水域清单与计划
      ├── <domain_id>/              # 每个汇水域的结果
      │   ├── L1/
      │   ├── L2/ (如果面积 >= 阈值)
      │   └── L3/
      ├── summary.json              # 全市汇总
      ├── ponding_all.geojson       # 合并积水点
      └── measures_all.geojson      # 合并 L2 措施
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pathlib
import sys
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# 复用已有模块
from citywide import load_network, scs_excess_mm  # noqa: E402

OUT = ROOT / "out" / "citywide_3lines"

# 默认参数
DEFAULTS = {
    "cn": 82.0,                     # 全市统一 CN（客户参数）
    "min_area_ha": 50.0,            # 小于此面积的汇水域不做 L2 优化
    "min_overflow_m3": 5000.0,      # 小于此溢流量的汇水域不做 L2
    "parallel": 4,                  # 并行数
    "l2_candidate_threshold_m3": 500.0,  # L2 候选井溢流阈值
}


def plan_domains(storm: dict, params: dict, log=print) -> list[dict]:
    """划分汇水域并生成计算计划。

    返回：[{domain_id, outlet_id, area_ha, nodes, pipes, do_L2, reason}, ...]
    """
    log("== 加载全市管网并划分汇水域")
    net = load_network(log=log)  # 返回 {nodes, edges, outlet_map, outlets}

    # 按出水口分组
    domains = []
    for outlet_id, node_ids in net["outlet_map"].items():
        if not node_ids:
            continue

        # 计算面积（服务半径法，citywide.py 的逻辑）
        n_nodes = len(node_ids)
        # 简化：面积 ≈ 节点数 × 平均服务面积 (100 m² per node 是粗估)
        area_ha = n_nodes * 0.01  # 100 m² = 0.01 ha

        # 判断是否做 L2
        do_L2 = area_ha >= params["min_area_ha"]
        reason = "area >= threshold" if do_L2 else f"area {area_ha:.1f} < {params['min_area_ha']} ha"

        domains.append({
            "domain_id": f"OUT{outlet_id}",
            "outlet_id": outlet_id,
            "area_ha": area_ha,
            "n_nodes": n_nodes,
            "n_pipes": len([e for e in net["edges"] if e[0] in node_ids or e[1] in node_ids]),
            "do_L2": do_L2,
            "reason": reason,
        })

    # 排序：大的优先
    domains.sort(key=lambda d: d["area_ha"], reverse=True)

    log(f"== 划分出 {len(domains)} 个汇水域")
    log(f"   其中 {sum(d['do_L2'] for d in domains)} 个达到 L2 优化阈值")
    log(f"   总面积约 {sum(d['area_ha'] for d in domains):.0f} ha")

    return domains


def compute_domain_L1(domain: dict, storm: dict, params: dict, out_dir: pathlib.Path, log=print) -> dict:
    """计算单个汇水域的 L1（一二维）。

    返回：{ponding_points, overflows, hotspots_metrics, swmm_rpt, anuga_tif}
    """
    log(f"== L1: {domain['domain_id']} ({domain['area_ha']:.1f} ha)")

    # TODO: 实现完整一二维计算
    # 1. 从全市 DMT 提取该汇水域的管网
    # 2. 按 Makani 不透水率 + CN=82 生成子汇水区
    # 3. 建 SWMM inp
    # 4. 准备 ANUGA 地形（DTM + 建筑 holes）
    # 5. 调用 tools/couple2way_v2.py
    # 6. 提取积水点（swbrain/ponding.py）
    # 7. 计算热点指标（如果这个域有热点）

    result = {
        "status": "placeholder",
        "ponding_points": [],
        "overflows": [],
        "total_overflow_m3": 0.0,
        "max_depth_m": 0.0,
    }

    # 写产物
    (out_dir / "L1").mkdir(parents=True, exist_ok=True)
    (out_dir / "L1" / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def compute_domain_L2(domain: dict, l1_result: dict, storm: dict, params: dict, out_dir: pathlib.Path, log=print) -> dict:
    """计算单个汇水域的 L2（候选 + 优化 + 验证）。"""
    log(f"== L2: {domain['domain_id']}")

    # TODO: 实现 L2 流程
    # 1. 生成候选（tools/l2opt_candidates.py 逻辑）
    # 2. 一维筛选（tools/l2opt_screen.py）
    # 3. 一维组合优化（tools/l2opt_greedy.py）
    # 4. 二维验证（再跑一二维，加入 L2 措施）

    result = {
        "status": "placeholder",
        "measures": [],
        "cost_usd": 0.0,
        "ponding_reduced_pct": 0.0,
    }

    (out_dir / "L2").mkdir(parents=True, exist_ok=True)
    (out_dir / "L2" / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def compute_domain_L3(domain: dict, l2_result: dict, storm: dict, params: dict, out_dir: pathlib.Path, log=print) -> dict:
    """计算单个汇水域的 L3（应急调度）。"""
    log(f"== L3: {domain['domain_id']}")

    # TODO: 实现 L3 调度
    # 读 L2 残余积水点 → swbrain/dispatch2.py

    result = {
        "status": "placeholder",
        "pump_trucks": 0,
        "tankers": 0,
        "max_recession_h": 0.0,
    }

    (out_dir / "L3").mkdir(parents=True, exist_ok=True)
    (out_dir / "L3" / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def worker_domain(task: dict) -> dict:
    """并行工作函数：计算单个汇水域的 L1/L2/L3。"""
    domain = task["domain"]
    storm = task["storm"]
    params = task["params"]
    out_root = pathlib.Path(task["out_root"])

    domain_id = domain["domain_id"]
    out_dir = out_root / domain_id
    out_dir.mkdir(parents=True, exist_ok=True)

    log = lambda msg: print(f"[{domain_id}] {msg}", flush=True)

    try:
        # L1
        l1_result = compute_domain_L1(domain, storm, params, out_dir, log=log)

        # L2（如果达到阈值）
        l2_result = None
        if domain["do_L2"] and l1_result.get("total_overflow_m3", 0) >= params["min_overflow_m3"]:
            l2_result = compute_domain_L2(domain, l1_result, storm, params, out_dir, log=log)
        else:
            log(f"   跳过 L2: {domain['reason']} 或溢流量 < {params['min_overflow_m3']} m³")

        # L3（如果有 L2 且有残余积水）
        l3_result = None
        if l2_result and l2_result.get("ponding_reduced_pct", 100) < 95:
            l3_result = compute_domain_L3(domain, l2_result, storm, params, out_dir, log=log)

        return {
            "domain_id": domain_id,
            "status": "success",
            "L1": l1_result,
            "L2": l2_result,
            "L3": l3_result,
        }

    except Exception as e:
        log(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            "domain_id": domain_id,
            "status": "failed",
            "error": str(e),
        }


def merge_results(domains: list[dict], results: list[dict], out_root: pathlib.Path, log=print) -> dict:
    """合并全市结果。"""
    log("== 合并全市结果")

    summary = {
        "n_domains": len(domains),
        "n_success": sum(1 for r in results if r["status"] == "success"),
        "n_failed": sum(1 for r in results if r["status"] == "failed"),
        "total_area_ha": sum(d["area_ha"] for d in domains),
        "L1": {
            "total_ponding_points": 0,
            "total_overflow_m3": 0.0,
        },
        "L2": {
            "n_domains_optimized": 0,
            "total_measures": 0,
            "total_cost_usd": 0.0,
        },
        "L3": {
            "n_domains_dispatched": 0,
            "total_pump_trucks": 0,
        },
    }

    for r in results:
        if r["status"] != "success":
            continue
        if r.get("L1"):
            summary["L1"]["total_ponding_points"] += len(r["L1"].get("ponding_points", []))
            summary["L1"]["total_overflow_m3"] += r["L1"].get("total_overflow_m3", 0)
        if r.get("L2"):
            summary["L2"]["n_domains_optimized"] += 1
            summary["L2"]["total_measures"] += len(r["L2"].get("measures", []))
            summary["L2"]["total_cost_usd"] += r["L2"].get("cost_usd", 0)
        if r.get("L3"):
            summary["L3"]["n_domains_dispatched"] += 1
            summary["L3"]["total_pump_trucks"] += r["L3"].get("pump_trucks", 0)

    (out_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    log(f"   成功: {summary['n_success']} / {summary['n_domains']}")
    log(f"   L1 积水点: {summary['L1']['total_ponding_points']}")
    log(f"   L2 优化域数: {summary['L2']['n_domains_optimized']}")

    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storm", required=True, help="设计暴雨 ID（如 61mm1h）")
    ap.add_argument("--parallel", type=int, default=DEFAULTS["parallel"], help=f"并行数（默认 {DEFAULTS['parallel']}）")
    ap.add_argument("--min-area-ha", type=float, default=DEFAULTS["min_area_ha"], help=f"L2 优化面积阈值（默认 {DEFAULTS['min_area_ha']} ha）")
    ap.add_argument("--dry-run", action="store_true", help="只生成计划，不执行")
    ap.add_argument("--resume", action="store_true", help="从中断处续跑")
    a = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    # 解析暴雨参数
    # TODO: 从 storm ID 解析雨量/历时，或从配置文件读
    storm = {
        "id": a.storm,
        "rainfall_mm": 61.0,  # placeholder
        "duration_h": 1.0,
    }

    params = {
        **DEFAULTS,
        "min_area_ha": a.min_area_ha,
    }

    out_root = OUT / a.storm
    out_root.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # 1. 生成计划
    domains = plan_domains(storm, params)
    plan = {
        "storm": storm,
        "params": params,
        "domains": domains,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_root / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n计划已写入: {out_root / 'plan.json'}")
    print(f"  汇水域数: {len(domains)}")
    print(f"  L2 优化域数: {sum(d['do_L2'] for d in domains)}")
    print(f"  总面积: {sum(d['area_ha'] for d in domains):.0f} ha")

    if a.dry_run:
        print("\n--dry-run: 只生成计划，不执行")
        return

    # 2. 并行计算
    print(f"\n== 开始并行计算（{a.parallel} 进程）")
    tasks = [
        {
            "domain": d,
            "storm": storm,
            "params": params,
            "out_root": str(out_root),
        }
        for d in domains
    ]

    # TODO: 实现续跑逻辑（检查已有结果，跳过已完成的）

    if a.parallel == 1:
        # 串行（调试用）
        results = [worker_domain(t) for t in tasks]
    else:
        # 并行
        with mp.Pool(a.parallel) as pool:
            results = pool.map(worker_domain, tasks)

    # 3. 合并结果
    summary = merge_results(domains, results, out_root)

    elapsed = time.time() - t0
    print(f"\n== 完成，耗时 {elapsed/60:.1f} 分钟")
    print(f"   结果: {out_root}")


if __name__ == "__main__":
    main()
