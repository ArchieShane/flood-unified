# -*- coding: utf-8 -*-
"""261101 新区域的环境检查：就是 preflight.py，只把里面写死的 RD-A 设施提示（临时泵 13 个点、Geocell 23 个…）换成通用说法，其余检查不变。
用法同 preflight.py。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import preflight as pf  # noqa: E402

_add = pf.add


def add(level, name, detail=""):
    if name.startswith("L2 / L2planned 的设施数据"):
        detail = ("临时泵 / 移动泵 / 塘 / 渗井 / Geocell 和缺参数的泵不放进模型（不生效），照常跑；页面设备清单里标「未建模」，点 L2 / L2planned 能看到这一态新增的设备。"
                  "各区有哪些见 data/regions/<区域>/manifest_v2.json")
    _add(level, name, detail)


pf.add = add


def extra_checks():
    """这批区域的数据缺口（打包时没有治理）：子汇水区 Tc / CN 空值会让产流计算直接报错，提前拦住。"""
    if "--region" not in sys.argv:
        return
    rid = sys.argv[sys.argv.index("--region") + 1]
    gp = pf.ROOT / "data" / "regions" / rid / "catchments.gpkg"
    if not gp.is_file():
        return
    try:
        import geopandas as gpd
        c = gpd.read_file(gp)
        n_tc, n_cn = int(c.TC.isna().sum()), int(c.SCS_CN.isna().sum())
        miss = c[c.TC.isna() | c.SCS_CN.isna()]
        ha = float(miss.A.sum()) if len(miss) else 0.0
        if n_tc or n_cn:
            _add("FAIL", "子汇水区 Tc / CN 齐全", f"{len(c)} 块里 Tc 空 {n_tc}、CN 空 {n_cn}（占面积 {ha / float(c.A.sum()) * 100:.1f} %）。数据里标「待客户提供」，不编参数，补齐前不能推演")
        else:
            _add("PASS", "子汇水区 Tc / CN 齐全", f"{len(c)} 块")
        low = int((c.TC <= 0).sum())
        if low:
            _add("WARN", "子汇水区 Tc ≤ 0", f"{low} 块，按公共规则 runoff.min_tc_min 取下限")
    except Exception as e:  # noqa: BLE001
        _add("WARN", "子汇水区 Tc / CN 检查", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    extra_checks()
    raise SystemExit(pf.main())
