# -*- coding: utf-8 -*-
"""全部阈值、出处，以及每项的严重度、整改途径。与 m4-data 管网体检.java / 栅格体检.java 逐一相同。

每个阈值回答两个问题之一：
  数据错误 —— 值落到这里多半是录错了，出处是通用工程经验，待客户确认；
  标准符合 —— 不满足 DMT 2022 雨水设计手册（WA-726-1），现有资产不一定按现行版建造，只作提示。
客户要调整，用 --thresholds 给一个 JSON（{"覆土下限": 0.5, ...}），报告会注明「已按客户要求调整」。
"""
from __future__ import annotations

import json

# 键: (缺省值, 单位, 种, 出处)
defaults = {
    "min_diameter": (50, "mm", "数据错误", "通用工程经验：小于 50 mm 的不是排水管（待客户确认）"),
    "max_diameter": (5000, "mm", "数据错误", "通用工程经验：大于 5000 mm 的要么是箱涵要么是单位错（待客户确认）"),
    "length_deviation": (0.10, "比例", "数据错误", "通用工程经验：属性管长与几何长度偏差超过 10%（待客户确认）"),
    "zero_length": (0.01, "m", "数据错误", "通用工程经验：1 cm 以内视为零长度（待客户确认）"),
    "very_short": (0.5, "m", "数据错误", "通用工程经验：短于 0.5 m 会压缩显式求解的时间步长（待客户确认）"),
    "flat_slope_drop": (0.001, "m", "数据错误", "通用工程经验：上下游管底差小于 1 mm 视为零坡（待客户确认）"),
    "steep_slope": (0.10, "比例", "数据错误", "通用工程经验：重力排水管坡度超过 10% 罕见，多半是标高错（待客户确认）"),
    "max_well_depth": (15, "m", "数据错误", "通用工程经验：井深超过 15 m 多半是标高基准或单位错（待客户确认；DMT 2022 手册没有规定井深上限）"),
    "min_cover": (0.3, "m", "数据错误", "通用工程经验：覆土小于 0.3 m 多半是标高错（待客户确认）"),
    "cover_dtm_tol": (1.0, "m", "数据错误", "通用工程经验：对齐基准后井盖与 DTM 差 1 m 以上（待客户确认）"),
    "max_buried": (10, "m", "数据错误", "通用工程经验：地面到井底超过 10 m 罕见（待客户确认）"),
    "id_offset_tol": (1.0, "m", "数据错误", "通用工程经验：编号指向的节点离端点 1 m 以上（待客户确认）"),
    "dup_node_dist": (0.05, "m", "数据错误", "通用工程经验：不同编号的两个节点相距小于 5 cm（待客户确认）"),
    "manual_min_cover": (1.2, "m", "标准符合", "DMT 2022 雨水与地下排水设计手册 WA-726-1 §4.3.5（手册第 38 页）：无额外保护的管道最小覆土 1200 mm"),
    "manual_min_diameter": (300, "mm", "标准符合", "DMT 2022 雨水与地下排水设计手册 WA-726-1（手册第 30 页）：地表水管道推荐最小管径 300 mm"),
    # DTM（栅格分块统计 / 栅格体检）
    "spike_diff": (2.0, "m", "数据错误", "通用经验：一个像元比 8 邻格都高（低）出 2 m 以上，多半是噪声或未滤净的建筑、车辆（待客户确认）"),
    "flat_diff": (0.01, "m", "数据错误", "通用经验：与 8 邻格的高差都小于 1 cm 视为平地（提示，不是错误）"),
    "low_threshold": (-5.0, "m", "数据错误", "阿布扎比沿海地面极少低于 −5 m：连片低于此值的需核实是下穿通道、基坑还是数据错误（待客户确认）"),
    "elev_min": (-30, "m", "数据错误", "阿布扎比地面不可能低于 −30 m（待客户确认）"),
    "elev_max": (3000, "m", "数据错误", "阿布扎比地面不可能高于 3000 m（待客户确认）"),
    "footprint_buffer": (50, "m", "数据错误", "管线两侧 50 m 以内必须有地面高程，二维模型才能与节点耦合"),
    # 模型地形（模型实际用的单元高程 对 原始 DTM 在单元范围内的平均）
    "terrain_diff_warn": (1.0, "m", "数据错误", "通用经验：粗网格取块平均后，单元高程与原始 DTM 块平均差 1 m 以上要查（待客户确认）"),
    "terrain_diff_crit": (5.0, "m", "数据错误", "通用经验：差 5 m 以上基本是处理错（NoData 混入平均、基准错、取错格），会造成假墙或假坑"),
    "terrain_bias": (0.5, "m", "数据错误", "通用经验：全部单元偏差的中位数超过 0.5 m，多半是高程基准不一致"),
}

# 中文名 ↔ 键：客户与报告都按中文名说事，代码里用英文名
_PAIRS = [
    ("min_diameter", "管径下限"), ("max_diameter", "管径上限"), ("length_deviation", "管长偏差"), ("zero_length", "零长"),
    ("very_short", "极短"), ("flat_slope_drop", "零坡高差"), ("steep_slope", "陡坡"), ("max_well_depth", "井深上限"),
    ("min_cover", "覆土下限"), ("cover_dtm_tol", "井盖DTM偏差"), ("max_buried", "埋深上限"), ("id_offset_tol", "编号几何偏差"),
    ("dup_node_dist", "重复节点距离"), ("manual_min_cover", "手册最小覆土"), ("manual_min_diameter", "手册推荐最小管径"),
    ("spike_diff", "尖峰高差"), ("flat_diff", "平地高差"), ("low_threshold", "洼地阈值"), ("elev_min", "高程下限"),
    ("elev_max", "高程上限"), ("footprint_buffer", "足迹外扩"),
    ("terrain_diff_warn", "模型地形偏差提示"), ("terrain_diff_crit", "模型地形偏差严重"), ("terrain_bias", "模型地形系统偏差"),
]
LABELS = dict(_PAIRS)            # 英文键 → 中文名（报告里显示用）
BY_LABEL = {zh: en for en, zh in _PAIRS}   # 中文名 → 英文键（客户按中文名改阈值用）

SAMPLE_LIMIT = 20
PLACEHOLDER_MATERIALS = {"", "undefined", "unknown", "n/a", "na", "none", "other", "-"}


class Thresholds:
    """缺省值 + 覆盖。按属性读：阈.覆土下限。"""

    def __init__(self, coverage: dict | None = None):
        self._vals = {k: v[0] for k, v in defaults.items()}
        self._overridden = set()
        for k, v in (coverage or {}).items():
            k = BY_LABEL.get(k, k)
            if k not in defaults:
                raise KeyError("不认得的阈值（英文键或中文名）：%s（可用：%s）" % (k, "、".join(BY_LABEL)))
            self._vals[k] = float(v)
            self._overridden.add(k)

    @classmethod
    def read_json(cls, path: str | None) -> "Thresholds":
        return cls(json.load(open(path, encoding="utf-8")) if path else None)

    def __getattr__(self, key):
        try:
            return self.__dict__["_vals"][key]
        except KeyError:
            raise AttributeError(key) from None

    def as_rows(self, keys=None) -> list[dict]:
        """报告「阈值与出处」表用。key 用中文名——客户按这个名改。"""
        return [{"key": LABELS.get(k, k), "value": self._vals[k], "unit": defaults[k][1], "meaning": defaults[k][2],
                 "source": defaults[k][3], "overridden": k in self._overridden} for k in (keys or defaults)]


PIPE_KEYS = list(defaults)[:15]
DTM_KEYS = list(defaults)[15:]

ALL_CODES = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13", "A14", "A15", "A16", "A17", "A18",
        "A19", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9",
        "C10", "C11", "C12", "C13", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "E1", "E2", "E3", "E4", "E5", "F1", "F2", "F3"]


def severity(counter: str) -> str:
    """crit：模型会跑不起来或结果错；warn：要处理；info：知道一下。"""
    if counter in {"A1", "A4", "A13", "A14", "B1", "B2", "C1", "C4", "C8", "D1", "D6", "E3"}:
        return "crit"
    if counter in {"C2", "A8", "A12", "A17", "A19", "C13", "D8", "E1"}:
        return "info"
    return "warn"


def route_of(counter: str) -> str:
    """desk 桌面修正／supply 补交资料／survey 外业复测／std 对照设计标准／none 不需整改。我们不改客户数据。"""
    if counter in {"A19", "D8"}:
        return "std"
    if counter in {"A3", "A5", "A6", "A13", "A14", "A15", "A16", "B1", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "C9", "C10",
             "C11", "F3", "R7"}:
        return "desk"
    if counter in {"A1", "A7", "A8", "A17", "A18", "B2", "C1", "C3", "C4", "C5", "C6", "C7", "C8", "C13", "D4", "E1", "E5", "F1", "F2",
             "R1", "R2", "R4", "R8", "R9", "R10"}:
        return "supply"
    if counter in {"A2", "A4", "A9", "A10", "A11", "A12", "C12", "D1", "D2", "D3", "D5", "D6", "D7", "E2", "E3", "E4", "R6", "R12"}:
        return "survey"
    return "none"


def shape_of(counter: str) -> str:
    """问题落在哪种要素上：管段／节点／管端／交点／无。导出问题图层时据此取几何。"""
    if counter in {"A9", "A10", "A11", "A12", "A14", "B8", "B9", "C5", "C6", "C13", "E2", "E3", "E4", "E5", "F1", "F2", "F3"}:
        return "节点"
    if counter in {"C1", "C11"}:
        return "管端"
    if counter == "C7":
        return "交点"
    if counter in {"C2", "E1"}:
        return "无"
    return "管段"
