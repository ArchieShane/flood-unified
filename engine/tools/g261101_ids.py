# -*- coding: utf-8 -*-
"""261101 新区域：SWMM 节点编号清洗（替换 region_model.sid，不改它）。
治理后数据里有 UNITID 含分号的节点（如 MIT_C10_Upper;_HS-3_MH-16），SWMM 的 inp 里 `;` 是注释起点，整行节点定义会被截断。
RD-A 的 sid 只把空白换成下划线；这里再把分号（以及引号 / 制表符）也换成下划线。只改写进 inp / pilot 的节点编号，数据本身不动。
"""
import re

sid = lambda x: re.sub(r"[\s;\"']+", "_", str(x))  # noqa: E731


def install():
    import region_model
    region_model.sid = sid
