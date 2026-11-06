# -*- coding: utf-8 -*-
"""四态对照表：L1 → L2 → L2+A → L2+B，以及**相邻两态之间的 gap**。

------------------------------------------------------------------ 四个态分别是什么
L1    既有灰色基础设施：管、井、泵站、出水口。客户台账里已建成在用的那一套。
L2    **已建成的调蓄设施**（23 座塘，194 万 m³）。注意这是「已经建好的」，
      不是规划的 —— 判据是客户自己的 informigration：管线/雨水口/出水口八九成
      标 EXISTING，而 23 个塘一个都没标，全挂在 2021–2023 的道路工程下。
L2+A  **Parsons 顾问设计的**新增调蓄（开放空间挖临时塘 + 12 寸移动泵）。
L2+B  **本系统设计的**新增调蓄（地下箱涵，选址按积水分布聚类）。

------------------------------------------------------------------ 为什么要给相邻 gap
只报「L1 vs L2+B」会把两件事混在一起：已建成设施的贡献、和新增设计的贡献。
分段给，才看得出每一步各自值多少。

------------------------------------------------------------------ 区域必须写全
本表只覆盖 **Mussafah_00**（汇水区几何 82.0 ha；客户台账 tbl_ha 记 148.76，差 1.81 倍，以几何为准）—— 穆萨法片区 8 个编号分区里的一个，
占该片区 5,041 ha 的 1.6%。**不是整个穆萨法。**
二维那几行的「淹没面积」分母是 14.5 km² 的二维计算域，不是 82.0 ha 的汇水区，
两者差近 10 倍，不能混着读。

用法：
    python tools/state_table.py Mussafah --scen _R61D2_72h --region Mussafah_00
产物：out/state_table_<区域>_<雨情>.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from workdir import WORK, OUT  # noqa: E402
import parsons_table as PT  # noqa: E402

# 态 → (显示名, 一维文件后缀, 二维 twoway 标签)。后缀里的历史命名很容易看错，
# 所以在这里一次性对死：_baseline=L1，无前缀=顾问的缓解态=L2+A，_L2box=我们的=L2+B。
STATES = [
    ("L1",   "L1　既有灰色设施",        "_baseline{s}", "L1"),
    ("L2",   "L2　已建成调蓄",          None,           None),   # 本区为空，见下
    ("L2+A", "L2+A　Parsons 设计",     "{s}",          "L2"),
    ("L2+B", "L2+B　本系统设计",        "_L2box{s}",    "L2B"),
    # L2+A+B：把 Parsons 当既成事实，在它之上再加本系统箱涵（tools/l2ab.py，按 L2+A 的残余积水选址）。
    # 它和 L2+B 不是一条链上的相邻态：L2+B 是从 L1 出发的并列方案，L2+A+B 是接在 L2+A 后面的叠加
    ("L2+A+B", "L2+A+B　Parsons 之上加本系统", "_L2AB{s}", "L2AB"),
]

ROWS = [
    ("洪水量",          "Flood Volume",          "flood_loss_m3",        "m³",  "{:,.0f}", "low"),
    ("洪水量(节点累计)", "Flood Vol (node sum)",  "flood_nodesum_m3",     "m³",  "{:,.0f}", "low"),
    ("退水(主网)",      "Recession (majority)",  "recession_majority_h", "h",   "{:,.2f}", "low"),
    ("退水(残余上游)",  "Recession (residual)",  "recession_residual_h", "h",   "{:,.2f}", "low"),
    ("系统排空(主网)",  "System Emptying",       "emptying_majority_h",  "h",   "{:,.2f}", "low"),
    ("蓄存能力",        "Storage Capacity",      "storage_total_m3",     "m³",  "{:,.0f}", "high"),
    ("临时泵",          "Temporary Pumps",       "n_pumps",              "台",  "{:,.0f}", "high"),
    ("峰值滞水",        "Peak Ponded",           "peak_ponded_m3",       "m³",  "{:,.0f}", "low"),
]
ROWS_2D = [
    ("二维·峰值淹没面积", "2D peak wet area",  "wet_ha",    "ha",  "{:,.2f}", "low"),
    ("二维·峰值水深",     "2D peak depth",     "hmax_m",    "m",   "{:,.3f}", "low"),
    ("二维·溢流节点数",   "Overflowing nodes", "n_over",    "个",  "{:,.0f}", "low"),
    ("二维·一维交出水量", "1D→2D volume",      "acc_1to2",  "m³",  "{:,.0f}", "low"),
]


# 有两行不能按「越低越好」读，必须随表给出，否则 ⚠ 会被当成劣化：
NOTES = {
    "系统排空(主网)":
        "调蓄设施**按设计就要把水存住再慢放**（箱涵按 24 h 排空定径）。"
        "这一项变长是设计意图，不是劣化 —— 要看的是它有没有把街面退水变短"
        "（L1 7.00 h → L2+B 2.00 h）。",
    "洪水量":
        "三个模型都是 ALLOW_PONDING YES，此时 SWMM 的 Flooding Loss 近似恒为 0，"
        "L2+A 的 340、L2+B 的 10 是井上蓄水能力被撑爆后的真实损失，"
        "量级对不上「洪水量(节点累计)」那一行，**以后者为准**。",
    "蓄存能力":
        "按几何算（管内容积 + 蓄水节点容积），**含超高**。L2+B 的箱涵超高 0.30 m / 设计水深 2.00 m，"
        "即这一行比可用库容高 15%：32,040 m³ 里可用约 27,861 m³。"
        "定容链条是 缺口 ×1.5（聚类系数，**假设，未标定**）×1.05（§4.2 沉沙）×1.15（超高），"
        "合计 1.81 倍 —— 六座箱涵服务缺口 11,450 m³，模型里 20,739 m³。",
    "临时泵":
        "台数按 .inp 逐个比对得出，不是照抄报告。多出来的泵是设施的配套，"
        "不是额外的应急调度需求。",
    "峰值滞水":
        "已扣除蓄水节点（塘/箱涵）内的水，只算街面 —— 否则一座正常工作的塘"
        "会让退水时间变成「永远退不完」（L2+B 末端 1,609 m³ 里有 1,577 m³ 在箱涵内）。",
}


def sww_peaks(log=print) -> dict:
    """二维峰值优先用**全 sww 时刻扫描**的结果，而不是耦合日志里的采样。

    两者差在采样密度：耦合日志每 10 分钟记一次（L2+A 270 个时刻里只记 30 条），
    而 flood2d 的抽帧工具扫了全部时刻。实测 L2+A 日志报 15.19 ha、全扫是 15.62 ha，
    差 2.8%。**同一个界面里两处数不一致，比数偏低更伤**，所以统一到更密的那个。
    """
    f = ROOT / "console" / "data" / "flood2d" / "manifest.json"
    if not f.exists():
        return {}
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"  读不了 flood2d manifest：{e}"); return {}
    return {x["state"]: x for x in m.get("states", [])}



def load_2d(c2d: pathlib.Path, model: str, tag: str, scen: str,
            cap_h: float | None = None) -> dict:
    """从耦合产物里读二维侧。缺哪个就空着，不猜。

    `cap_h`：**把各态的序列截到同一个时刻再取峰值**。各态的二维演算时长不一样
    （L1 跑到 10 h，L2+A / L2+B 只到 5 h），不截就是拿 10 h 的峰去比 5 h 的峰，
    对「峰值水深」这种会一直抬升或长期驻留的量尤其不公平。
    峰值淹没面积三态都出现在 t≈2.17 h，截不截都一样 —— 但判据要先写死。
    """
    f = c2d / f"twoway_{model}_{tag}{scen}.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    ser = d.get("series") or []
    if cap_h is not None:
        ser = [r for r in ser if (r.get("t_h") or 0) <= cap_h + 1e-9]
    hmax = max((r.get("hmax_m") or 0.0) for r in ser) if ser else None
    t_h = (max(ser, key=lambda r: r.get("hmax_m") or 0.0).get("t_h")
           if ser and hmax is not None else None)
    # 峰值面积同样从**截断后**的序列取，不能用 json 里那个全窗口的 max_wet_m2
    mw = max(ser, key=lambda r: r.get("wet_m2") or 0.0) if ser else None
    return {
        "wet_ha": ((mw.get("wet_m2") or 0.0) / 1e4) if mw else None,
        "wet_t_h": mw.get("t_h") if mw else None,
        "span_h": (ser[-1].get("t_h") if ser else None),
        "hmax_m": hmax, "hmax_t_h": t_h,
        "n_over": d.get("n_coupled"), "acc_1to2": d.get("acc_1to2_m3"),
        "acc_2to1": d.get("acc_2to1_m3"), "res_m": d.get("res_m"),
        "n_tri": d.get("n_tri"), "end_h": d.get("end_h"),
    }


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="四态对照 + 相邻 gap")
    ap.add_argument("model")
    ap.add_argument("--scen", default="_R61D2_72h", help="一维长演算后缀")
    ap.add_argument("--scen2d", default="_R61D2", help="二维耦合的场景标签")
    ap.add_argument("--region", default="Mussafah_00", help="**精确到分区**的区域名")
    ap.add_argument("--t2d", type=float, default=5.0,
                    help="二维序列统一截到这个时刻再取峰值（各态演算时长不同）")
    ap.add_argument("--region-ha", type=float, default=82.0,
                    help="汇水区**几何**面积。客户台账 tbl_ha 记 148.76，几何只有 81.98（0.55×），以几何为准")
    a = ap.parse_args(argv)

    c2d = WORK / "c2d"
    print(f"区域 **{a.region}**（{a.region_ha:,.1f} ha）　雨情 61 mm / 2 h"
          f"　二维统一截到 {a.t2d:g} h 取峰值")
    print(f"  注意：{a.region} 是穆萨法片区 8 个编号分区之一，"
          f"占该片区 8 个分区几何面积合计 5,041 ha 的 {a.region_ha / 5041 * 100:.1f}%，**不是整个穆萨法**。\n")

    PEAKS = sww_peaks()
    got, empty = {}, {}
    for code, label, suf, t2 in STATES:
        if suf is None:
            empty[code] = "本区内 0 座已建成塘（最近一座 7.3 km 外），该态 ≡ L1"
            got[code] = None
            continue
        d = PT.one(a.model, suf.format(s=a.scen), log=lambda *x: None)
        if d:
            d.update({("2d_" + k): v for k, v in
                      load_2d(c2d, a.model, t2, a.scen2d, a.t2d).items()})
            sp = PEAKS.get(code)
            if sp:                      # 全 sww 扫描的峰值更准，覆盖掉日志采样值
                d["2d_wet_ha"] = sp["peak_wet_ha"]
                d["2d_wet_t_h"] = sp["peak_wet_t_h"]
                d["2d_hmax_m"] = sp["peak_depth_m"]
                d["2d_hmax_t_h"] = sp["peak_depth_t_h"]
                d["2d_peak_src"] = "sww 全时刻扫描"
        got[code] = d or None
        if not d:
            empty[code] = "一维长演算的 inp/rpt/out 不全"

    codes = [c for c, *_ in STATES]
    head = f"  {'参数':<16}{'Parameter':<22}" + "".join(f"{c:>14}" for c in codes)
    print(head); print("  " + "-" * (len(head) - 2))

    table = {}
    def emit(zh, en, key, unit, fmt, better, pfx=""):
        vals = []
        for c in codes:
            d = got.get(c)
            v = None if not d else d.get(pfx + key)
            if v is None and c == "L2" and got.get("L1"):
                v = got["L1"].get(pfx + key)      # L2 ≡ L1，照抄并在下面标注
            vals.append(v)
        table[zh] = {"unit": unit, "values": dict(zip(codes, vals)), "better": better}
        cells = "".join((fmt.format(v) if v is not None else "—").rjust(14) for v in vals)
        print(f"  {zh:<16}{en:<22}{cells}")

    for zh, en, key, unit, fmt, better in ROWS:
        emit(zh, en, key, unit, fmt, better)
    print()
    for zh, en, key, unit, fmt, better in ROWS_2D:
        emit(zh, en, key, unit, fmt, better, pfx="2d_")

    # ---- 相邻 gap
    print(f"\n  相邻两态之间的 gap（后者相对前者）")
    pairs = [("L1", "L2"), ("L2", "L2+A"), ("L2+A", "L2+B"), ("L1", "L2+B"),
             ("L2+A", "L2+A+B"), ("L1", "L2+A+B")]     # Parsons 做完以后还要补多少、我们补上多少
    hd = f"  {'参数':<16}" + "".join(f"{a0 + '→' + b0:>16}" for a0, b0 in pairs)
    print(hd); print("  " + "-" * (len(hd) - 2))
    gaps = {}
    for zh, meta in table.items():
        row, cells = {}, ""
        for a0, b0 in pairs:
            x, y = meta["values"].get(a0), meta["values"].get(b0)
            if x is None or y is None:
                row[f"{a0}->{b0}"] = None; cells += "—".rjust(16); continue
            d = y - x
            pc = (d / x * 100) if x else None
            row[f"{a0}->{b0}"] = {"abs": d, "pct": pc}
            good = (d < 0) if meta["better"] == "low" else (d > 0)
            s = (f"{pc:+.1f}%" if pc is not None else f"{d:+,.0f}")
            cells += (s + ("" if good or d == 0 else " ⚠")).rjust(16)
        gaps[zh] = row
        print(f"  {zh:<16}{cells}")

    print(f"\n  口径说明（不看这几条会把设计意图读成劣化）")
    for k, v in NOTES.items():
        if k in table:
            print(f"    · **{k}**　{v}")

    for c, why in empty.items():
        print(f"\n  **{c}**：{why}")
    d2 = (got.get("L1") or {})
    if d2.get("2d_res_m"):
        print(f"\n  二维：{d2['2d_res_m']:g} m 网格、{d2.get('2d_n_tri', 0):,} 三角单元，"
              f"计算域 14.5 km²（**淹没面积的分母是它，不是 {a.region_ha:g} ha 的汇水区**）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    f = OUT.parent / f"state_table_{a.region}{a.scen2d}.json"
    f.write_text(json.dumps({"region": a.region, "region_ha": a.region_ha,
                             "scen": "61mm/2h", "states": got,
                             "table": table, "gaps": gaps, "empty": empty,
                             "notes": NOTES},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写出 {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
