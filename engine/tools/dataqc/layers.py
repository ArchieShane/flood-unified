# -*- coding: utf-8 -*-
"""认图层：哪些是管段、哪些是节点、各是什么节点，建模要的每个量落在哪一列。

移植自 m4-data 管网图层识别.java，规则逐条相同。列名一律按小写比较。
认出来的结果原样进报告 —— 认错了看得见；认不到的角色照实报「缺」，不猜。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 节点种类：决定「度为 1 算不算问题」「这个子网有没有出口」
OUTFALL, CAPPED_END, INLET, CHAMBER, SOAKAWAY, PUMP, VALVE, STORAGE, OTHER = (
    "出水口", "封堵头", "雨水口", "检查井", "渗井", "泵站", "阀门", "蓄水", "其他")
OUTLET_KINDS = {OUTFALL, SOAKAWAY, PUMP, STORAGE}


@dataclass
class LinkLayer:
    table: str
    pressure: bool
    id: str | None
    us_id: str | None
    ds_id: str | None
    diameter: str | None
    us_invert: str | None
    ds_invert: str | None
    attr_length: str | None
    material: str | None
    status: str | None
    pipe_type: str | None
    wall: str | None


@dataclass
class NodeLayer:
    table: str
    kind_of: str
    id: str | None
    invert: str | None
    cover: str | None
    elev_source: str | None


@dataclass
class Recognized:
    links: list[LinkLayer] = field(default_factory=list)
    nodes: list[NodeLayer] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


_MERGE_COPY = re.compile(r"^merge", re.I)


def find_col(rows: list[str], *candidates: str) -> str | None:
    """列名里找第一个命中的（不区分大小写）。"""
    low = {counter.lower(): counter for counter in rows}
    for counter in candidates:
        if counter.lower() in low:
            return low[counter.lower()]
    return None


def kind_of(table: str) -> str | None:
    """按表名认节点种类。认不出回 None（那一层不进网络）。"""
    t = table.lower()
    if "outfall" in t:
        return OUTFALL
    if "cappedend" in t or "capped_end" in t or "endcap" in t:
        return CAPPED_END
    if any(k in t for k in ("inlet", "catchbasin", "gully", "catchpit")):
        return INLET
    if "soakaway" in t:
        return SOAKAWAY
    if "pump" in t:
        return PUMP
    if "valve" in t:
        return VALVE
    if any(k in t for k in ("reservoir", "tank", "pond")):
        return STORAGE
    if any(k in t for k in ("chamber", "manhole", "junction", "interceptor")) or t == "sw_node" or t.endswith("_node"):
        return CHAMBER
    return None


def wall_col(rows: list[str]) -> str | None:
    """管壁（实心／穿孔）：pipe_type／unittype／wall_type 会记 Perforated、PefrtedPip 一类。"""
    low = {counter.lower(): counter for counter in rows}
    for counter in ("pipe_type", "unittype", "wall_type", "pipe_wall"):
        if counter in low:
            return low[counter]
    return None


def recognize(list_layers: list[tuple[str, str, list[str]]]) -> Recognized:
    """图层们：(表名, 几何类型, 列名)。"""
    dump = Recognized()
    for table, geom, rows in list_layers:
        g = (geom or "").upper()
        if _MERGE_COPY.search(table):
            dump.skipped[table] = "合并副本（各类图层合成的一份），算进来会把每条管、每个问题算两遍"
            continue
        if "LINE" in g or "CURVE" in g:
            # 带 _10 的是 shapefile 截成 10 字符的列名（2026-10-19 加：全市 Stormwater shp 用的是截断名；平台原规则只认全名）
            id = find_col(rows, "unitid", "pipe_id", "pipeid", "id", "objectid")
            us = find_col(rows, "asset_before", "asset_befo", "us_node", "from_node", "upstream_node", "fromnode", "us_node_id")
            ds = find_col(rows, "asset_after", "asset_afte", "ds_node", "to_node", "downstream_node", "tonode", "ds_node_id")
            diam = find_col(rows, "pipe_diameter", "pipe_diame", "diameter", "dia", "pipe_size", "diameter_mm")
            if diam is None and us is None:
                dump.skipped[table] = "线图层，但既没有管径也没有上下游编号，不像管段"
                continue
            t = table.lower()
            # INVERT_L_1：ArcGIS 截断重名时第二个加 _1，GDB 里 INVERT_LEVEL_UPSTREAM 在 DOWNSTREAM 之前，所以 _1 是下游
            dump.links.append(LinkLayer(
                table, "pressure" in t or t.startswith("irr"), id, us, ds, diam,
                find_col(rows, "invert_level_upstream", "invert_lev", "us_invert", "invert_up", "upstream_invert", "start_invert", "inv_up"),
                find_col(rows, "invert_level_downstream", "invert_l_1", "ds_invert", "invert_dn", "downstream_invert", "end_invert", "inv_dn"),
                find_col(rows, "pipe_length", "pipe_lengt", "linelength", "length", "length_m", "channel_length"),
                find_col(rows, "pipe_material", "pipe_mater", "material", "channel_material"),
                find_col(rows, "status"),
                find_col(rows, "pipe_subtype", "pipe_subty", "pipe_type", "type"),
                wall_col(rows)))
        elif "POINT" in g:
            kind = kind_of(table)
            if kind is None:
                dump.skipped[table] = "点图层，但表名认不出是哪类管网节点"
                continue
            # ⚠️ 不认 wellbottomelev／groundelev：DMT 雨水 GDB 每个节点层都另加了这一组（elevsource 标「DEM提取」），
            #    与原始 invert_level／cover_level 差约 99 m（不同基准）。混进来会把加工值当原始标高用
            dump.nodes.append(NodeLayer(
                table, kind, find_col(rows, "unitid", "pointcode", "node_id", "id"),
                find_col(rows, "invert_level", "invert_lev", "invertlevel", "bottom_level", "base_level", "invert"),
                find_col(rows, "cover_level", "cover_leve", "coverlevel", "ground_level", "groundlevel", "rim_level", "cover"),
                find_col(rows, "elevsource", "elev_source")))
        else:
            dump.skipped[table] = "几何是 %s，不是管段也不是节点" % geom
    return dump
