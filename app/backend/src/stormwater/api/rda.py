"""RDA 全套成果接口（只读，直接读交付目录，读取逻辑在 `stormwater.rda`）。

    GET /api/rda/overview                          总览：雨、四个状态的指标、设施 / 积水点计数、积水点口径
    GET /api/rda/rain                              降雨过程（5 min 一档）
    GET /api/rda/states/{state}/criteria           四项判据 A–D：逐热点 + 域级
    GET /api/rda/states/{state}/ponding            积水点清单（?depth=0.15|0.30），带 L3 承接 / 剔除标记 / 200 m 内热点
    GET /api/rda/states/{state}/ponding/custom     积水点按自定义阈值重算（?depth=&min_area=&min_dur=）
    GET /api/rda/states/{state}/ponding/series     单个积水点逐帧的积水量 / 面积 / 最大水深（?lon=&lat=&depth=&min_area=&min_dur=）
    GET /api/rda/states/{state}/hotspot-buffer/{id}/series  单个客户热点 200 m 圆内逐帧的积水量 / 面积 / 最大水深（?depth=）
    GET /api/rda/states/{state}/hotspot-buffer     客户热点 200 m 圆内的地表峰值积水量 + 圆重叠（?depth=）
    GET /api/rda/states/{state}/ponding/areas      积水范围面 GeoJSON（?depth=）
    GET /api/rda/states/{state}/l3                 L3 承接汇总（后果等级、调度结果、两种调度范围）
    GET /api/rda/states/{state}/facilities         本状态设施图层 GeoJSON（逐级累加）+ 设施清单
    GET /api/rda/states/{state}/max-depth          逐单元全程最大水深（紧凑数组，?min_depth=）
    GET /api/rda/grid | states/{state}/frames[/{i}] | max-depth-diff | hotspots/series   逐帧时间轴 / 播放 / 与基准的差 / 热点 50 m 水深过程
    GET /api/rda/suggested                         L2+suggested 逐项措施、造价汇总、NPV、地面取水口
    GET /api/rda/suggested/{id}/compliance         一项措施的 WA-726 / 空间约束逐条检查
    GET /api/rda/layers/hotspots | domain | exclusion-zones   公共图层
"""

from fastapi import APIRouter, HTTPException, Query

from .. import rda

router = APIRouter(prefix="/api/rda", tags=["rda"])


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except rda.RdaError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/overview")
def get_overview():
    return _call(rda.overview)


@router.get("/rain")
def get_rain():
    return _call(rda.rain_series)


@router.get("/states/{state}/criteria")
def get_criteria(state: str):
    return _call(rda.criteria_for, state)


@router.get("/states/{state}/ponding")
def get_ponding(state: str, depth: str = Query("0.15")):
    return _call(rda.ponding, state, depth)


@router.get("/states/{state}/ponding/custom")
def get_ponding_custom(state: str, depth: float = Query(0.15), min_area: float = Query(100.0), min_dur: float = Query(0.5)):
    """按自定义阈值从二维结果重算积水点（三角网共边连片）；默认阈值下与成果自带清单一致。"""
    return _call(rda.ponding_custom, state, round(depth, 3), round(min_area, 1), round(min_dur, 2))


@router.get("/states/{state}/ponding/series")
def get_point_series(state: str, lon: float = Query(...), lat: float = Query(...), depth: float = Query(0.15),
                     min_area: float = Query(100.0), min_dur: float = Query(0.5)):
    """某个积水点（按经纬度指定）逐帧的积水量 / ≥depth 面积 / 最大水深；阈值要与清单一致。"""
    return _call(rda.point_series, state, lon, lat, round(depth, 3), round(min_area, 1), round(min_dur, 2))


@router.get("/states/{state}/hotspot-buffer/{hotspot_id}/series")
def get_hotspot_circle_series(state: str, hotspot_id: str, depth: float = Query(0.15)):
    """某个客户热点 200 m 圆内逐帧的积水量 / ≥depth 面积 / 圆内最大水深（扣评估剔除区）。"""
    return _call(rda.hotspot_circle_series, state, hotspot_id, round(depth, 3))


@router.get("/states/{state}/hotspot-buffer")
def get_hotspot_buffer(state: str, depth: float = Query(0.15)):
    """客户热点 200 m 圆内的地表峰值积水量（水深 ≥ depth 的单元，扣评估剔除区）+ 各热点圆的重叠。"""
    return _call(rda.hotspot_buffer, state, round(depth, 3))


@router.get("/states/{state}/ponding/areas")
def get_ponding_areas(state: str, depth: str = Query("0.15")):
    return _call(rda.ponding_areas, state, depth)


@router.get("/states/{state}/l3")
def get_l3(state: str):
    return _call(rda.l3, state)


@router.get("/states/{state}/facilities")
def get_facilities(state: str):
    return {"geojson": _call(rda.facilities_geojson, state), "list": _call(rda.facilities_list, state)}


@router.get("/states/{state}/max-depth")
def get_max_depth(state: str, min_depth: float = Query(0.05, ge=0.0, le=5.0)):
    return _call(rda.max_depth, state, round(min_depth, 2))


@router.get("/suggested")
def get_suggested():
    return _call(rda.suggested)


@router.get("/suggested/{fid}/compliance")
def get_compliance(fid: str):
    return _call(rda.compliance, fid)


@router.get("/layers/hotspots")
def get_hotspots():
    return _call(rda.hotspots)


@router.get("/layers/domain")
def get_domain():
    return _call(rda.domain)


@router.get("/layers/exclusion-zones")
def get_exclusion_zones():
    return _call(rda.exclusion_zones)


@router.get("/grid")
def get_grid():
    return _call(rda.grid)


@router.get("/states/{state}/frames")
def get_frame_index(state: str):
    return _call(rda.frame_index, state)


@router.get("/states/{state}/frames/{index}")
def get_frame(state: str, index: int, mode: str = Query("depth", pattern="^(depth|diff)$"),
              base: str | None = None, min_depth: float = Query(0.10, ge=0.0, le=5.0)):
    return _call(rda.frame, state, index, mode, base, round(min_depth, 2))


@router.get("/states/{state}/max-depth-diff")
def get_max_depth_diff(state: str, base: str):
    return _call(rda.max_depth_diff, state, base)


@router.get("/hotspots/series")
def get_hotspot_series():
    return _call(rda.hotspot_series)
