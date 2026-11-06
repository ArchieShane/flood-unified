# -*- coding: utf-8 -*-
"""新建泵的压力管水力计算（参数登记表第 9 / 10 项，2026-10-19）：泵曲线 × 系统曲线求工作点，给出实际出水量与压力管管径。

有明确规定的（WA-726 第一卷 7.3.2 节）：
  · 沿程损失用 Darcy–Weisbach，摩擦系数按 Colebrook-White；Ks 按表 7-3：流速 < 1.1 m/s 取 0.3 mm，1.1–1.8 m/s 取 0.15 mm
  · 局部损失 hL = K·V²/2g，K 取表 7-4 的配件值
Claude 自定（文档没给，记在参数登记表）：
  · 12 英寸泵曲线：只知道设计点 0.361 m³/s @ 23 m（Parsons / 客户泵曲线），取二次曲线 H = H0 − k·Q²，关死扬程 H0 = 1.25 × 23 m
  · 局部损失合计 K = 3.0（进口 0.5 + 出口 1.0 + 弯头 / 阀门约 1.5，按表 7-4 量级）
  · 静扬程 = 出水点水位 − 吸水井水位；吸水井水位 = 地面 − 1.0 m（湿井启泵水位，按 WA-726 第 6 章的常见湿井深度取整）
  · 管径从 DN300 起按档选，取能让工作点 ≥ 设计流量 95 % 的最小一档；DN1200 还不够就按 DN1200 的实际工作点出水
  · 运行水温 35 °C，运动黏度 0.727e-6 m²/s（WA-726 4.3.1 节给的值）
"""
from __future__ import annotations

import math

G = 9.81
NU = 0.727e-6                                   # WA-726 4.3.1：35 °C 水的运动黏度
Q12, H12 = 0.361, 23.0                           # 12 英寸泵设计点
H0 = 1.25 * H12                                  # Claude 自定：关死扬程
K12 = (H0 - H12) / Q12 ** 2
K_MINOR = 3.0                                    # Claude 自定：局部损失合计
WET_DEPTH = 1.0                                  # Claude 自定：吸水井水位在地面下 1.0 m
DN = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2)
FM_COST = {0.3: 300, 0.4: 400, 0.5: 500, 0.6: 650, 0.7: 800, 0.8: 950, 0.9: 1100, 1.0: 1300, 1.2: 1600}   # 占位单价 USD/m（量级）


def ks_of(v):
    """WA-726 表 7-3"""
    return 0.15e-3 if v >= 1.1 else 0.3e-3


def friction_f(v, d):
    re = max(v * d / NU, 4000.0)
    ks = ks_of(v)
    f = 0.02
    for _ in range(30):                          # Colebrook-White 迭代
        f = (-2.0 * math.log10(ks / (3.7 * d) + 2.51 / (re * math.sqrt(f)))) ** -2
    return f


def system_head(q, d, L, dz):
    a = math.pi * d * d / 4.0
    v = q / a
    return dz + (friction_f(v, d) * L / d + K_MINOR) * v * v / (2 * G), v


def pump_head(q, n):
    return H0 - K12 * (q / n) ** 2


def operating_point(n, d, L, dz):
    """n 台 12 英寸泵并联、DN d、长 L、静扬程 dz 的工作点（二分）：返回 (Q, H, V)；静扬程超过关死扬程返回 0"""
    if dz >= H0:
        return 0.0, dz, 0.0
    lo, hi = 0.0, n * math.sqrt(H0 / K12)
    for _ in range(60):
        q = 0.5 * (lo + hi)
        hs, _ = system_head(q, d, L, dz)
        if pump_head(q, n) > hs:
            lo = q
        else:
            hi = q
    hs, v = system_head(lo, d, L, dz)
    return lo, hs, v


V_MAX = 1.8          # WA-726 表 7-3 的 Ks 只定义到 1.8 m/s：设计流速不超过它


def size_main(n, L, z_src_ground, z_dst):
    """给 n 台泵配压力管（设计流速 ≤ 1.8 m/s，必要时几根并联、流量均分）：返回 dict（管径、根数、实际流量、扬程、流速、静扬程、压力管占位造价）"""
    dz = max(z_dst - (z_src_ground - WET_DEPTH), 0.0)
    q_design = n * Q12
    opts = []
    for m in (1, 2, 3, 4):
        for d in DN:
            if q_design / m / (math.pi * d * d / 4.0) <= V_MAX:
                opts.append((FM_COST[d] * L * m, m, d)); break
    cost, m, d = min(opts) if opts else (FM_COST[DN[-1]] * L * 4, 4, DN[-1])
    # 每根管带 n / m 台泵的流量（台数不整除时按比例）：工作点按单根管 + 等效泵数求
    q1, h, v = operating_point(n / m, d, L, dz)
    # 出水量封顶在设计流量：系统扬程低时泵会沿曲线往右跑，实际按设计工况选泵 / 节流（Claude 自定），压力管摩阻只会让出水变少
    q = min(q1 * m, q_design)
    h, v = system_head(q / m, d, L, dz)
    return {"n_pumps": n, "main_d_m": d, "n_mains": m, "q_m3s": round(q, 3), "q_design_m3s": round(q_design, 3),
            "q_ratio": round(q / q_design, 3), "head_m": round(h, 2), "v_ms": round(v, 2), "static_m": round(dz, 2), "main_cost_usd": round(cost)}


if __name__ == "__main__":
    for n, L, zs, zd in ((3, 2600, 4.5, 1.3), (6, 2600, 4.5, 1.3), (3, 500, 4.5, 4.2), (12, 2400, 4.0, 1.3)):
        print(n, L, size_main(n, L, zs, zd))
