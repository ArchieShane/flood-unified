# -*- coding: utf-8 -*-
"""读客户交付的入流过程线。

格式坑：[INFLOWS] 指向的 [TIMESERIES] 用的是**十进制小时**（0.0833 = 5 min），
不是 HH:MM:SS；而雨量序列用的是 HH:MM:SS。按后者去切前者会全取空。
"""
import pathlib
import re
from collections import defaultdict

import numpy as np


def read_inflow_ts(inp: pathlib.Path) -> dict:
    """→ {节点: (时间 h, 流量 LPS)}。"""
    sec, node_of, series = None, {}, defaultdict(lambda: ([], []))
    for ln in inp.open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            sec = m.group(1).upper(); continue
        if not s or s.startswith(";"):
            continue
        p = s.split()
        if sec == "INFLOWS" and len(p) >= 3:
            node_of[p[2]] = p[0]
        elif sec == "TIMESERIES" and len(p) == 3 and p[0] in node_of:
            try:
                series[p[0]][0].append(float(p[1])); series[p[0]][1].append(float(p[2]))
            except ValueError:
                pass
    return {node_of[k]: (np.array(t), np.array(v))
            for k, (t, v) in series.items() if len(t) > 1}
