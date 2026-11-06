# -*- coding: utf-8 -*-
"""雨水口进水能力（2026-10-28，RD-A 治理数据版一二维用；couple2way_v2.py 在容器里 import，要和它一起拷到 /work/c2d）。

WA-726 第一卷附录 A3.5.2「Grated Gutter Inlets · Sump Locations」，单位 m、m³/s：
  堰流  式 A3-22  Qi = 0.085 (3.28 P) (3.28 d)^1.5        P = 格栅周长（不计条栅和路缘侧）
  孔口  式 A3-23  Qi = 0.204 A (21.52 g d)^0.5             A = 格栅净开孔面积，g = 9.806
  两式之间有过渡段（手册：约 120–430 mm，随周长与开孔面积变），这里取两式的较小值作包络。
  防堵：手册要求格栅进水口只按计算能力的 50 %–60 % 取（A3.5.1 / A3.5.2），clog 取 0.5。
"""
from __future__ import annotations

import numpy as np

G = 9.806


def grate_sump_q(d, P, A, clog=0.5):
    """格栅以上水深 d（m，可为数组）→ 进水量（m³/s）"""
    d = np.maximum(np.asarray(d, float), 0.0)
    qw = 0.085 * (3.28 * P) * (3.28 * d) ** 1.5
    qo = 0.204 * A * np.sqrt(21.52 * G * d)
    return clog * np.minimum(qw, qo)
