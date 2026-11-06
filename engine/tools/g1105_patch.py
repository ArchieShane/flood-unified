# -*- coding: utf-8 -*-
"""1105 批的引擎补丁（g1104_patch.py 复制）（不改 region_model.py）：① 建模前去掉重复管（补 M2）；② RD-E、RB2 跳过「非交换节点井盖不低于潮位」检查（用户 2026-11-05 方案 1）。
RD-E 地势很低（地形最低 -12.8 m）、没有出水口，潮位边界不起作用；7 个检修井井盖低于固定潮位 1.3 m。其余区域、以及「无重复管」检查照常执行。
被跳过的节点清单写进 reg["readiness"]["tide_check_skipped"]（参数报告可见）并打日志。run_region_g1105.py 启动时 install()。
"""
from __future__ import annotations

SKIP_TIDE_CHECK: set = set()      # 1105 批：先不跳过；若地势很低的区（E14-1 地形最低 -12 m）被「井盖低于潮位」检查拦住，先问用户再加（1104 批 RDE / RB2 是用户 2026-11-05 定的）


def install():
    import region_model as RM
    import region_net as RN
    orig = RM.readiness_checks

    def dedup(net, reg, log):
        """去掉重复管（起止节点、管径、长度都相同的只留第一根）：规则 model_rules.toml pipes dedup_duplicate_pipes = true（M2），但 g261101_net.py（261101 起复制的建网代码）没有做，
        readiness_checks 会因此拦住（Airport 4 对、RB2 10 对）。这里在检查前补做，被去掉的写进 reg["readiness"]["duplicate_pipes_dropped"]。"""
        dia = {x[0]: float(x[2]) for x in net["XS"]}
        seen, keep, dropped = {}, [], []
        for c in net["CO"]:
            key = (frozenset((c[1], c[2])), round(float(c[3]), 1), round(dia.get(c[0], 0.0), 3))
            if key in seen:
                dropped.append({"dropped": c[0], "kept": seen[key]})
            else:
                seen[key] = c[0]; keep.append(c)
        if dropped:
            ids = {d["dropped"] for d in dropped}
            net["CO"] = keep
            net["XS"] = [x for x in net["XS"] if x[0] not in ids]
            net["VX"] = [v for v in net["VX"] if v[0] not in ids]
            log(f"  去掉重复管 {len(dropped)} 根（起止节点、管径、长度都相同，只留第一根）")
        return dropped

    def wrapped(R, net, N, D, reg, sea, log=print):
        dropped = dedup(net, reg, log)
        if R.id not in SKIP_TIDE_CHECK:
            r = orig(R, net, N, D, reg, sea, log)
            reg["readiness"]["duplicate_pipes_dropped"] = dropped
            return r
        from shapely.geometry import Point
        pD = D if hasattr(D, "contains") else None
        low = []
        for k, j in net["J"].items():
            if j[3] >= RN.SEAL_SUR:
                continue
            exch = k in N and (pD is None or pD.contains(Point(net["coords"][k])))
            if not exch and j[1] > 0 and (j[0] + j[1]) < sea:
                low.append({"node": k, "rim_m": round(j[0] + j[1], 3), "sea_m": sea})
        orig(R, net, N, D, reg, -1e9, log)          # 只做「无重复管」检查（sea=-1e9 时井盖检查必过）
        reg["readiness"]["duplicate_pipes_dropped"] = dropped
        reg["readiness"]["tide_check_skipped"] = {"reason": "用户 2026-11-05 决定 RD-E 不做井盖低于潮位检查（地势低、无出水口）；RB2 同样处理（只算最大块后落到域外的非交换节点）", "nodes": low}
        reg["readiness"]["nonexchange_rim_below_tide"] = low
        log(f"  ⚠ {R.id}：跳过井盖低于潮位检查（{len(low)} 个非交换节点井盖低于潮位 {sea:g} m，已记入参数报告）")
    RM.readiness_checks = wrapped
