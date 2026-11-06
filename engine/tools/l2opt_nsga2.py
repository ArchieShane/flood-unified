# -*- coding: utf-8 -*-
"""L2+suggested 优化：NSGA-II 多目标进化算法验证（2026-10-22）

目的：验证贪心算法结果是否接近最优，或发现更优解。
方法：从贪心结果热启动，搜索「成本-效果」的 Pareto 前沿。

用法：
  python tools/l2opt_nsga2.py --run RDA_96B --prefix RDA --pop 100 --gen 100 --procs 18

输出：
  - out/l2opt/<run>/nsga2_result.json：Pareto 前沿的解
  - out/l2opt/<run>/nsga2_front.csv：每个解的 (J, cost)
  - out/l2opt/<run>/nsga2_comparison.png：Pareto 前沿 vs 贪心曲线对比图

依赖：
  pip install pymoo
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

# 延迟导入 pymoo（如果未安装会在运行时报错）
try:
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import Problem
    from pymoo.core.sampling import Sampling
    from pymoo.optimize import minimize
    from pymoo.operators.crossover.pntx import TwoPointCrossover
    from pymoo.operators.mutation.bitflip import BitflipMutation
except ImportError:
    print("错误：需要安装 pymoo")
    print("运行：pip install pymoo")
    sys.exit(1)

import l2opt_screen as SC  # noqa: E402
from l2opt_greedy import jscore  # noqa: E402
from workdir import WORK  # noqa: E402


class DrainageOptProblem(Problem):
    """排水设施优化问题（多目标）

    决策变量：X[i] ∈ {0, 1}，表示候选措施 i 是否被选
    目标函数：
      f1 = J（积水指标，最小化）
      f2 = cost（总成本，最小化）
    约束：
      g1 = 总成本 - 预算上限 ≤ 0
      g2 = 塘溢流增量 - 300 m³ ≤ 0
    """

    def __init__(self, run: str, prefix: str, budget_usd: float, max_spill: float, procs: int):
        self.run = run
        self.prefix = prefix
        self.budget = budget_usd
        self.max_spill = max_spill
        self.procs = procs

        # 加载数据
        o = ROOT / "out" / "l2opt" / run
        self.candidates = json.loads((o / "candidates.json").read_text(encoding="utf-8"))["candidates"]
        self.params = json.loads((o / "candidates.json").read_text(encoding="utf-8"))["params"]
        self.base = json.loads((o / "candidates.json").read_text(encoding="utf-8"))["base"]

        # 读取基线（L2P）的热点溢流
        storms = self.params["storms"]
        self.storms = storms
        self.base_overflow = {s: self.base[s]["zones"] for s in storms}

        # 候选措施映射
        self.cand_map = {c["id"]: i for i, c in enumerate(self.candidates)}

        n_var = len(self.candidates)
        super().__init__(
            n_var=n_var,
            n_obj=2,  # 目标：最小化 J 和 cost
            n_ieq_constr=2,  # 不等式约束：预算、溢流
            xl=0,
            xu=1,
            vtype=bool  # 布尔决策变量
        )

        print(f"问题规模：{n_var} 个候选措施")
        print(f"控制雨：{storms}")
        print(f"预算上限：{budget_usd/1e6:.1f} M USD")

    def _evaluate(self, X, out, *args, **kwargs):
        """评估种群中的每个个体

        X: (pop_size, n_var) 布尔矩阵
        out["F"]: (pop_size, 2) 目标函数值 [J, cost]
        out["G"]: (pop_size, 2) 约束违反量 [预算违反, 溢流违反]
        """
        pop_size = X.shape[0]
        F = np.zeros((pop_size, 2))  # 目标函数
        G = np.zeros((pop_size, 2))  # 约束

        print(f"\n评估 {pop_size} 个个体...")

        # 并行评估（使用 SC.Eval 的并行框架）
        tasks = []
        for i, individual in enumerate(X):
            selected_ids = [self.candidates[j]["id"] for j in range(len(individual)) if individual[j]]
            if len(selected_ids) == 0:
                # 空方案：J = 基线，cost = 0
                F[i, 0] = sum(len(self.base[s]["zones"]) for s in self.storms)
                F[i, 1] = 0
                G[i, 0] = 0
                G[i, 1] = 0
            else:
                tasks.append((i, selected_ids))

        # 批量评估（复用 l2opt_screen 的逻辑）
        results = self._batch_evaluate(tasks)

        for i, res in results:
            F[i, 0] = res["J"]
            F[i, 1] = res["cost"]
            G[i, 0] = max(0, res["cost"] - self.budget)
            G[i, 1] = max(0, res["spill"] - self.max_spill)

        out["F"] = F
        out["G"] = G

    def _batch_evaluate(self, tasks):
        """批量评估措施组合（并行）"""
        import multiprocessing as mp
        from functools import partial

        results = []

        if self.procs > 1 and len(tasks) > 1:
            with mp.Pool(self.procs) as pool:
                results = pool.map(partial(self._evaluate_single), tasks)
        else:
            results = [self._evaluate_single(t) for t in tasks]

        return results

    def _evaluate_single(self, task):
        """评估单个方案（一维 SWMM）"""
        idx, selected_ids = task

        # 构建方案（应用措施到基线）
        # 这里复用 l2opt_screen.apply 的逻辑
        result = {}

        # 计算成本
        cost = sum(
            next(c for c in self.candidates if c["id"] == cid)["cost_usd"]
            for cid in selected_ids
        )

        # 运行 SWMM 一维评估
        # TODO: 这里需要调用实际的 SWMM 评估逻辑
        # 暂时用占位逻辑（需要后续实现）
        J = self._run_swmm_1d(selected_ids)

        # 检查塘溢流
        spill = self._check_pond_spill(selected_ids)

        result["J"] = J
        result["cost"] = cost
        result["spill"] = spill

        return (idx, result)

    def _run_swmm_1d(self, selected_ids):
        """运行一维 SWMM 评估（占位，需实现）

        TODO: 集成 l2opt_screen.py 的评估逻辑
        这里需要：
        1. 从基线 inp 构建包含这些措施的 inp
        2. 跑 SWMM
        3. 计算 J 值
        """
        # 占位：返回随机值
        # 实际实现需要调用 SC.apply + SC.Eval
        return 10.0 + np.random.random() * 5.0

    def _check_pond_spill(self, selected_ids):
        """检查调蓄池溢流量（占位，需实现）"""
        # 占位：返回 0
        # 实际需要从 SWMM 结果读取
        return 0.0


class GreedySampling(Sampling):
    """从贪心结果初始化种群"""

    def __init__(self, greedy_path_file: pathlib.Path, n_candidates: int):
        super().__init__()
        self.greedy_path = json.loads(greedy_path_file.read_text(encoding="utf-8"))
        self.n_candidates = n_candidates

    def _do(self, problem, n_samples, **kwargs):
        """生成初始种群

        策略：
        - 20% 来自贪心路径（每隔几轮取一个解）
        - 80% 随机生成
        """
        X = np.zeros((n_samples, self.n_candidates), dtype=bool)

        # 从贪心路径取解
        path = self.greedy_path["path"]
        n_from_greedy = max(1, int(n_samples * 0.2))
        step = max(1, len(path) // n_from_greedy)

        greedy_count = 0
        for i in range(0, len(path), step):
            if greedy_count >= n_from_greedy:
                break
            chosen_ids = path[i]["chosen"]
            # 转为布尔向量
            for cid in chosen_ids:
                # 需要从 candidates 找到对应的索引
                # 这里简化处理，假设 id 就是索引
                pass  # TODO: 实现 id -> index 映射
            greedy_count += 1

        # 剩余随机生成
        for i in range(n_from_greedy, n_samples):
            # 随机选 10-80 项措施
            n_selected = np.random.randint(10, min(80, self.n_candidates))
            selected_idx = np.random.choice(self.n_candidates, n_selected, replace=False)
            X[i, selected_idx] = True

        return X


def plot_comparison(greedy_data, pareto_data, output_file):
    """绘制 Pareto 前沿 vs 贪心曲线对比图"""
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use('Agg')  # 非交互后端

        fig, ax = plt.subplots(figsize=(10, 6))

        # 贪心曲线
        greedy_J = [r["J"] for r in greedy_data]
        greedy_cost = [r["cost_usd"] / 1e6 for r in greedy_data]
        ax.plot(greedy_cost, greedy_J, 'o-', label='Greedy', linewidth=2, markersize=6, color='blue')

        # Pareto 前沿
        pareto_J = pareto_data[:, 0]
        pareto_cost = pareto_data[:, 1] / 1e6
        ax.scatter(pareto_cost, pareto_J, label='NSGA-II Pareto Front', s=30, color='red', alpha=0.6)

        ax.set_xlabel('Cost (M USD)', fontsize=12)
        ax.set_ylabel('J (Flooding Index)', fontsize=12)
        ax.set_title('Optimization Comparison: Greedy vs NSGA-II', fontsize=14)
        ax.legend()
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(output_file, dpi=150)
        print(f"对比图已保存：{output_file}")
    except ImportError:
        print("警告：matplotlib 未安装，跳过绘图")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    ap = argparse.ArgumentParser(description="NSGA-II 多目标优化验证贪心结果")
    ap.add_argument("--run", required=True, help="优化运行名称（如 RDA_96B）")
    ap.add_argument("--prefix", default="RDA", help="模型前缀")
    ap.add_argument("--pop", type=int, default=100, help="种群大小")
    ap.add_argument("--gen", type=int, default=100, help="迭代代数")
    ap.add_argument("--procs", type=int, default=16, help="并行进程数")
    ap.add_argument("--budget", type=float, default=150e6, help="预算上限（USD）")
    ap.add_argument("--max-spill", type=float, default=300.0, help="塘溢流增量上限（m³）")
    a = ap.parse_args(argv)

    print("=" * 60)
    print("NSGA-II 多目标优化 - 验证贪心结果")
    print("=" * 60)

    o = ROOT / "out" / "l2opt" / a.run
    if not o.exists():
        print(f"错误：运行目录不存在：{o}")
        return 1

    # 检查贪心结果
    greedy_file = o / "greedy_path.json"
    if not greedy_file.exists():
        print(f"错误：贪心结果不存在：{greedy_file}")
        return 1

    # 定义问题
    problem = DrainageOptProblem(
        run=a.run,
        prefix=a.prefix,
        budget_usd=a.budget,
        max_spill=a.max_spill,
        procs=a.procs
    )

    # 定义算法
    print(f"\n配置 NSGA-II：种群 {a.pop}，迭代 {a.gen} 代")

    algorithm = NSGA2(
        pop_size=a.pop,
        sampling=GreedySampling(greedy_file, len(problem.candidates)),
        crossover=TwoPointCrossover(prob=0.9),
        mutation=BitflipMutation(prob=1.0 / len(problem.candidates)),
        eliminate_duplicates=True
    )

    # 运行优化
    print("\n开始优化...")
    t0 = time.time()

    res = minimize(
        problem,
        algorithm,
        ('n_gen', a.gen),
        seed=42,
        verbose=True,
        save_history=False  # 节省内存
    )

    elapsed = time.time() - t0
    print(f"\n优化完成，耗时 {elapsed/3600:.2f} 小时")

    # 保存结果
    print("\n保存结果...")

    # Pareto 前沿
    pareto_F = res.F  # (n_solutions, 2): [J, cost]
    pareto_X = res.X  # (n_solutions, n_var): 布尔向量

    # 转为可读格式
    solutions = []
    for i in range(len(pareto_F)):
        selected_ids = [problem.candidates[j]["id"] for j in range(len(pareto_X[i])) if pareto_X[i][j]]
        solutions.append({
            "J": float(pareto_F[i, 0]),
            "cost_usd": float(pareto_F[i, 1]),
            "n_items": len(selected_ids),
            "items": selected_ids
        })

    result = {
        "run": a.run,
        "algorithm": "NSGA-II",
        "params": {
            "pop_size": a.pop,
            "n_gen": a.gen,
            "budget": a.budget,
            "max_spill": a.max_spill
        },
        "pareto_front": solutions,
        "n_solutions": len(solutions),
        "elapsed_seconds": elapsed
    }

    result_file = o / "nsga2_result.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {result_file}")

    # CSV 格式
    import csv
    csv_file = o / "nsga2_front.csv"
    with csv_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["J", "cost_usd", "n_items"])
        for sol in solutions:
            writer.writerow([sol["J"], sol["cost_usd"], sol["n_items"]])
    print(f"  {csv_file}")

    # 对比图
    greedy_data = json.loads(greedy_file.read_text(encoding="utf-8"))["path"]
    plot_file = o / "nsga2_comparison.png"
    plot_comparison(greedy_data, pareto_F, plot_file)

    # 分析结果
    print("\n" + "=" * 60)
    print("结果分析")
    print("=" * 60)

    greedy_final = greedy_data[-1]
    greedy_J = greedy_final["J"]
    greedy_cost = greedy_final["cost_usd"]

    print(f"\n贪心最终解：")
    print(f"  J = {greedy_J:.3f}")
    print(f"  成本 = {greedy_cost/1e6:.2f} M USD")
    print(f"  措施数 = {len(greedy_final['chosen'])}")

    print(f"\nNSGA-II Pareto 前沿：{len(solutions)} 个非支配解")

    # 找相似成本下的最优 J
    best_at_similar_cost = None
    for sol in solutions:
        if abs(sol["cost_usd"] - greedy_cost) / greedy_cost < 0.1:  # 成本相差 < 10%
            if best_at_similar_cost is None or sol["J"] < best_at_similar_cost["J"]:
                best_at_similar_cost = sol

    if best_at_similar_cost:
        improvement = (greedy_J - best_at_similar_cost["J"]) / greedy_J * 100
        print(f"\n相似成本下的最优解：")
        print(f"  J = {best_at_similar_cost['J']:.3f}")
        print(f"  成本 = {best_at_similar_cost['cost_usd']/1e6:.2f} M USD")
        print(f"  措施数 = {best_at_similar_cost['n_items']}")
        if improvement > 0:
            print(f"  ✅ 改进：J 降低 {improvement:.1f}%")
        elif improvement < -0.5:
            print(f"  ⚠️ 退化：J 升高 {-improvement:.1f}%")
        else:
            print(f"  ≈ 相当：差异 < 0.5%")

    # 找最小 J
    best_J_sol = min(solutions, key=lambda s: s["J"])
    print(f"\n全局最小 J：")
    print(f"  J = {best_J_sol['J']:.3f}")
    print(f"  成本 = {best_J_sol['cost_usd']/1e6:.2f} M USD")
    print(f"  措施数 = {best_J_sol['n_items']}")

    print("\n" + "=" * 60)
    print(f"结果已保存至：{o}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
