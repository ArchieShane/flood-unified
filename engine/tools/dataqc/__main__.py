# -*- coding: utf-8 -*-
"""雨水管网与 DTM 数据质量检查：命令行入口。

在仓库里跑是 ``python -m tools.dataqc``；拿到独立包（dist/dataqc-<版本>）后是 ``python -m dataqc``，
或直接用包里的启动器 ``dataqc.bat`` / ``dataqc.sh``。下面按独立包写：

  # 管网 + DTM（报告原口径：DTM 在管网外框外扩 100 m 内统计）
  python -m dataqc --pipes D:/data/DMT_StormWater_AbuDhabi_Processed.gdb --dtm D:/data/AUH_DTM_5m_Z40.TIF --out ./out/全市 --gpkg

  # 只查 DTM，范围为任意面（计算域、行政区、汇水区）
  python -m dataqc --dtm D:/data/AUH_DTM_5m_Z40.TIF --domain 计算域.geojson --domain-name "C1 模型版计算域" --out ./out/C1

  # 管网也只取计算域内的（与域相交的管段、落在域内的节点）
  python -m dataqc --pipes ... --dtm ... --domain ... --clip-pipes --out ...

  # 参数多、要反复跑：写成配置文件
  python -m dataqc --config 示例配置.json

  # 有 ArcGIS Pro：用它的 Python 跑，加 --arcgis 用拓扑规则与追踪网络逐条复核（输出目录别带中文）
  "D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" -m dataqc --pipes ... --arcgis --out ...

产物：<out>/report.html（报告）、result.json（全部结果，含全量编号）、issues.gpkg（每类问题一层，--gpkg）。
装完先跑 python -m dataqc.selftest 自检（合成数据，不碰真实数据）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m dataqc", description="雨水管网与 DTM 数据质量检查（只读）")
    ap.add_argument("--config", help="配置文件（JSON，键与下面这些参数同名；命令行给的值覆盖它）")
    ap.add_argument("--pipes", help="管网：.gdb 目录、.gdb.zip、.gpkg 或 shapefile 目录")
    ap.add_argument("--dtm", help="DTM：GeoTIFF 等 GDAL 可读的栅格")
    ap.add_argument("--domain", help="统计范围面（GeoJSON／GPKG／shp），DTM 只统计面内的格")
    ap.add_argument("--domain-name", help="报告里怎么称呼这个范围（缺省用文件名）")
    ap.add_argument("--clip-pipes", action="store_true", help="管网也只取 --domain 内的")
    ap.add_argument("--dtm-scope", choices=["network", "domain", "full"], help="DTM 统计范围：network 管网外框外扩 100 m（有 --pipes 时缺省）、"
                                                                            "domain 计算域（有 --domain 时缺省）、full 全图")
    ap.add_argument("--tolerance", type=float, default=0.5, help="连通判定容差（米），缺省 0.5")
    ap.add_argument("--datum-offset", type=float, help="台账高程 − DTM 的基准差（米）；缺省从井盖与 DTM 自动估")
    ap.add_argument("--thresholds", help="阈值覆盖 JSON，如 {\"覆土下限\": 0.5}")
    ap.add_argument("--extra", help="报告附加内容 JSON：notes／advice／dq／bugs／footer")
    ap.add_argument("--title", help="报告标题")
    ap.add_argument("--arcgis", action="store_true", help="用 arcpy 复核拓扑与连通（要在 ArcGIS Pro 的 Python 里跑）")
    ap.add_argument("--gpkg", action="store_true", help="导出问题图层 GeoPackage")
    ap.add_argument("--out", help="输出目录（没给 --config 时必填）")
    a = ap.parse_args(argv)
    if a.config:
        _读配置(a, ap)
    if not a.pipes and not a.dtm:
        ap.error("至少给 --pipes 或 --dtm")
    if not a.out:
        ap.error("要给 --out，或在 --config 里写 out")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.time()

    def log(*x):
        print("[%5.0fs]" % (time.time() - t0), *x, flush=True)

    import geopandas as gpd
    import numpy as np
    import shapely

    from . import network, raster, report
    from .thresholds import Thresholds

    thr = Thresholds.read_json(a.thresholds)
    extra = json.load(open(a.extra, encoding="utf-8")) if a.extra else {}
    os.makedirs(a.out, exist_ok=True)

    domain = domain_name = None
    if a.domain:
        dg = gpd.read_file(a.domain)
        domain_name = a.domain_name or os.path.splitext(os.path.basename(a.domain))[0]
    Recognized = {"inputs": {k: getattr(a, k) for k in ("pipes", "dtm", "domain", "tolerance", "datum_offset", "thresholds")}}

    net = net_meta = net_result = None
    if a.pipes:
        log("读管网", a.pipes)
        net = network.Network(a.pipes, log=log)
        if a.clip_pipes and a.domain:
            net.clip(dg.to_crs(net.crs).union_all(), log=log)
        net_meta = net.meta()

    dtm = None
    scope_note = ""
    if a.dtm:
        import rasterio
        with rasterio.open(a.dtm) as ds:
            dcrs = ds.crs
            if net is not None:
                if net.crs is not None and dcrs is not None and not net.crs.equals(dcrs):
                    log("⚠️ 管网坐标系 %s 与 DTM %s 不同：节点按 DTM 坐标系重投影后采样" % (net.crs.to_string(), dcrs.to_string()))
                net.sample_dtm(ds, dcrs)
        scope = a.dtm_scope or ("domain" if a.domain else "network" if net is not None else "full")
        bounds = area = lines = None
        if scope == "domain":
            if not a.domain:
                ap.error("--dtm-scope domain 要给 --domain")
            area = list(dg.to_crs(dcrs).geometry.values)
            a_ = sum(shapely.area(np.asarray(area, dtype=object))) / 1e6
            scope_note = "%s（%.2f km²）" % (domain_name, a_)
        elif scope == "network":
            if net is None:
                ap.error("--dtm-scope network 要给 --pipes")
            bounds = net.bounds(100, dcrs)
            scope_note = "管网外框外扩 100 m"
        else:
            scope_note = "全图"
        if net is not None:
            lines = list(net.lines(dcrs))
        log("DTM 检查：", scope_note)
        dtm = raster.scan(a.dtm, thr, bounds=bounds, domain=area, lines=lines, log=log)

    if net is not None:
        log("管网体检")
        net_result = network.check_network(net, thr, tolerance=a.tolerance, has_dtm=dtm is not None, datum_offset=a.datum_offset, log=log)
        Recognized["network"] = net_result
        Recognized["networkMeta"] = net_meta
    if dtm is not None:
        Recognized["dtm"] = {k: v for k, v in dtm.items() if k not in ("spikePts", "pitPts")}

    verify = None
    if a.arcgis:
        if not a.pipes:
            ap.error("--arcgis 只复核管网，要给 --pipes")
        from . import arcgis_verify
        verify_dir = os.path.join(a.out, "arcgis")
        try:
            import arcpy  # noqa: F401
            in_process = True
        except ImportError:
            in_process = False
        log("ArcGIS 复核（拓扑规则 + 追踪网络）" + ("" if in_process else "：另起 ArcGIS Pro 的 Python"))
        if in_process:
            if net_result is None:
                ap.error("--arcgis 要先能算出管网结果：管网读失败或没有 --pipes")
            verify = arcgis_verify.verify(a.pipes, net, net_result, thr, a.tolerance, verify_dir, log=log)
        else:
            payload = os.path.join(a.out, "arcgis_payload.json")
            json.dump({"net_result": _js(net_result) if net_result else None, "tolerance": a.tolerance},
                      open(payload, "w", encoding="utf-8"), ensure_ascii=False, default=_js)
            verify = arcgis_verify.verify_subprocess(a.pipes, verify_dir, payload, log=log)
        Recognized["arcgis"] = verify

    json.dump(Recognized, open(os.path.join(a.out, "result.json"), "w", encoding="utf-8"), ensure_ascii=False, default=_js)
    report.build_report(os.path.join(a.out, "report.html"), net_result=net_result, net_meta=net_meta, dtm=dtm, dtm_scope_note=scope_note, verify=verify,
              thr=thr, extra=extra, title=a.title, pipe_name=os.path.basename(a.pipes.rstrip("/\\")) if a.pipes else None)
    if a.gpkg:
        from . import issues
        n = issues.export_gpkg(os.path.join(a.out, "issues.gpkg"), net, net_result, dtm, thr, log=log)
        log("问题图层 %d 层" % n)
    log("完成：", os.path.join(a.out, "report.html"))


def _js(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _读配置(a, ap):
    """把配置文件里的键填进 argparse 的结果：命令行给过的值优先（用 default 判断）。"""
    try:
        cfg = json.load(open(a.config, encoding="utf-8"))
    except Exception as e:
        ap.error("读不了配置文件 %s：%s" % (a.config, e))
    默认 = ap.parse_args([])
    键 = {"pipes": "pipes", "dtm": "dtm", "domain": "domain", "domain_name": "domain_name", "clip_pipes": "clip_pipes",
         "dtm_scope": "dtm_scope", "tolerance": "tolerance", "datum_offset": "datum_offset",
         "thresholds": "thresholds", "extra": "extra", "title": "title", "arcgis": "arcgis", "gpkg": "gpkg", "out": "out"}
    for k, 参 in 键.items():
        v = cfg.get(k)
        if v is None:
            continue
        if getattr(a, 参) == getattr(默认, 参):      # 命令行没给就用配置文件的
            setattr(a, 参, v)
    # 阈值也可以直接写在配置里（dict），落成临时文件给后面读
    if cfg.get("thresholds") and isinstance(cfg["thresholds"], dict):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(cfg["thresholds"], f, ensure_ascii=False)
        f.close()
        a.thresholds = f.name
    if cfg.get("extra") and isinstance(cfg["extra"], dict):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(cfg["extra"], f, ensure_ascii=False)
        f.close()
        a.extra = f.name


if __name__ == "__main__":
    main()
