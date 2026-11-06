"""仿真任务接口：Run history 列表 + New simulation 向导。读写逻辑在 `stormwater.simulation`。

    GET    /api/simulations/options          向导各步的选项与默认值
    GET    /api/simulations                  列表（tab = all/running/done/queued/replays/mine/drafts）
    POST   /api/simulations/estimate         确认页的预计耗时 / 就绪时间（不落库）
    POST   /api/simulations                  存草稿；body.submit = true 则存完直接提交（「Run」）
    GET    /api/simulations/{id}             单个任务（确认页 / 回到向导续填）
    PUT    /api/simulations/{id}             改草稿（整表单覆盖）
    DELETE /api/simulations/{id}             删草稿或已结束的任务（排队 / 在算的先取消）
    POST   /api/simulations/{id}/submit      草稿 → 排队（Queue all three 时一次排三个）
    POST   /api/simulations/{id}/cancel      取消草稿或排队中的任务
    PATCH  /api/simulations/{id}/status      算力侧回写状态 / 进度 / 起止时间
    POST   /api/simulations/{id}/analyses    完成后追加分析（Validation / Gap N h / ERP）
    GET    /api/simulations/summary          首页：KPI 四卡、同场雨对比、任务队列、New results
    POST   /api/simulations/{id}/open        点「Open ›」，记首次打开时间（New results 据此消掉）
    PUT    /api/simulations/{id}/result      算力侧回写结果指标（still pond、命中率等）
    GET    /api/simulations/{id}/layers                 结果图层元数据（范围、结果摘要、时间轴）
    GET    /api/simulations/{id}/layers/max-depth       逐格最大水深 GeoJSON（几何只在这里给一次）
    GET    /api/simulations/{id}/layers/frames/{index}  第 index 帧的「格子号 → 水深」紧凑数组
    GET    /api/simulations/{id}/layers/timeline        播放用的逐帧当前值（同刻积水面积、最深、累计溢水量、已出现溢水点）
    GET    /api/simulations/{id}/ponding-points/{编号}/series  单个模拟积水点逐帧的积水量 / 面积 / 最大水深
    GET    /api/simulations/{id}/ponding-points[/meta]  模拟积水点（按阈值现算，口径同 RD-A；?depth=&min_area=&min_dur=），`city_ponding.py`
    GET    /api/simulations/{id}/hotspot-buffer/{热点}/series  单个热点 200 m 圆内逐帧的积水量 / 面积 / 最大水深
    GET    /api/simulations/{id}/hotspot-buffer热点 200 m 圆内的地表峰值积水量 + 圆重叠（?depth=），`hotspot_buffer.py`
    GET    /api/simulations/{id}/hotspots               交付自带的积水点（热点）：坐标 / 汇水区 / 逐态统计，没有 404
    GET    /api/simulations/{id}/layers/ponding         按 docs/analysis_ponding 算法重算的易积水片区
    GET    /api/simulations/{id}/layers/inflow          模型实际用的总入流过程线（逐井 [INFLOWS] 累加）
    GET    /api/simulations/{id}/layers/residual        演算末残留积水按洼地分块、逐块归因（四态交付才有）
    GET    /api/simulations/{id}/l2                     全市任务的第二道防线信息（报告数 + 已建成塘，`sim_l2.py`）
    GET    /api/simulations/layers/proposed-ponds       拟建调蓄池多边形（与任务无关，WGS84；属性原样透传）
    GET    /api/simulations/{id}/states/effect          多态任务逐时刻的方案效果（少上地表多少水、少几个溢水点）
    GET    /api/simulations/{id}/states/perf            效果分析页：一对「既有 vs 方案」（?vs=态、?h=时刻档）的对照数字
                                                  以上除 states/* 外都认 ?state=（多态任务取哪一态，如 L2+B；不给取默认态）

协议值（status、rain_source、defence_line_key、tab、kind）不翻译；只有防线 / 资源名随语言。
本 API 没有登录态，「Mine」靠 `?user=` 传操作人缩写。
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .. import city_ponding, sim_l2, sim_results
from .. import simulation as sim
from ..i18n import resolve_lang, tr

NO_POINTS = "这个任务没有模拟积水点"

router = APIRouter(prefix="/api/simulations", tags=["simulations"])


def _lang(
    lang: str | None = None,
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> str:
    return resolve_lang(lang, accept_language)


Lang = Depends(_lang)


class SimulationForm(BaseModel):
    """向导四步的全量表单。"""

    # ① 降雨情景
    rain_source: Literal["design_storm", "observed_replay", "ncm_forecast"] = "design_storm"
    storm_preset_key: str | None = Field(None, description="预设格子，如 61mm_2h；自定义暴雨留空")
    replay_event_ref: str | None = Field(None, description="实测回放的事件标识")
    depth_mm: float | None = Field(None, gt=0, le=1000, description="自定义暴雨的总雨量")
    duration_h: float | None = Field(None, gt=0, le=72, description="自定义暴雨的历时")
    temporal_pattern: str | None = None
    peak_position_pct: float | None = Field(None, ge=0, le=100)
    spatial_distribution: str | None = None
    # ② 范围与边界
    extent: str = Field(..., min_length=1)
    grid_m: float = Field(..., gt=0)
    terrain: str | None = None
    tide_level_m: float | None = None
    tide_datum: str | None = None
    tide_label: str | None = None
    gate_close_above_m: float | None = None
    model_start_utc: datetime | None = Field(None, description="不带时区按 UTC 理解")
    after_rain_mode: Literal["until_drained", "fixed"] | None = None
    after_rain_max_h: float | None = Field(None, ge=0, le=240)
    baseflow_pct: float | None = Field(None, ge=0, le=100)
    baseflow_min_dia_mm: int | None = Field(None, ge=0)
    baseflow_note: str | None = None
    # ③ 防线
    defence_line_key: Literal["line1", "line2_current", "line2_planned"] = "line1"
    queue_all_lines: bool = False
    resource_ids: list[int] = Field(default_factory=list)
    # ④ 确认
    compute_target: str | None = None
    created_by: str | None = Field(None, description="操作人缩写，如 ZN")

    @field_validator("model_start_utc")
    @classmethod
    def _as_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class CreateBody(SimulationForm):
    submit: bool = Field(False, description="true = 存完直接提交（确认页的 Run）")


class StatusPatch(BaseModel):
    status: Literal["running", "done", "failed", "cancelled"] | None = None
    progress_pct: float | None = Field(None, ge=0, le=100)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    ended_is_estimate: bool | None = None


class AnalysisBody(BaseModel):
    kind: Literal["validation", "gap", "erp"]
    gap_hours: float | None = Field(None, gt=0, le=240)


def _fail(exc: sim.SimulationError, lang: str) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=tr(exc.message, lang))


@router.get("/options")
def get_options(lang: str = Lang):
    with sim.connect() as conn:
        return sim.options(conn, lang)


@router.get("/summary")
def get_summary(lang: str = Lang):
    with sim.connect() as conn:
        return sim.summary(conn, lang)


@router.get("")
def list_simulations(
    tab: Literal["all", "running", "done", "queued", "replays", "mine", "drafts"] = "all",
    user: str | None = Query(None, description="操作人缩写；tab=mine 时必填，同时决定 counts.mine"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    lang: str = Lang,
):
    try:
        with sim.connect() as conn:
            return sim.list_runs(conn, tab=tab, user=user, lang=lang, limit=limit, offset=offset)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.post("/estimate")
def estimate(form: SimulationForm, lang: str = Lang):
    with sim.connect() as conn:
        return sim.estimate(conn, form.model_dump())


@router.post("", status_code=201)
def create_simulation(body: CreateBody, lang: str = Lang):
    try:
        with sim.connect() as conn:
            run_id = sim.create_draft(conn, body.model_dump(exclude={"submit"}))
            queued = sim.submit(conn, run_id) if body.submit else []
            return {**sim.get_run(conn, run_id, lang), "queued_ids": queued}
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/layers/proposed-ponds")
def get_proposed_ponds(lang: str = Lang):
    """拟建调蓄池（`data/temp/proposed_ponds.geojson`，EPSG:32640 转 WGS84）。与任务无关，详情页按需叠加。"""
    try:
        return sim_results.proposed_ponds()
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}")
def get_simulation(run_id: int, lang: str = Lang):
    try:
        with sim.connect() as conn:
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.put("/{run_id}")
def update_simulation(run_id: int, form: SimulationForm, lang: str = Lang):
    try:
        with sim.connect() as conn:
            sim.update_draft(conn, run_id, form.model_dump())
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.delete("/{run_id}", status_code=204)
def delete_simulation(run_id: int, lang: str = Lang):
    try:
        with sim.connect() as conn:
            sim.delete_run(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    return Response(status_code=204)


@router.post("/{run_id}/submit")
def submit_simulation(run_id: int, lang: str = Lang):
    try:
        with sim.connect() as conn:
            queued = sim.submit(conn, run_id)
            return {**sim.get_run(conn, run_id, lang), "queued_ids": queued}
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.post("/{run_id}/cancel")
def cancel_simulation(run_id: int, lang: str = Lang):
    try:
        with sim.connect() as conn:
            sim.cancel(conn, run_id)
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.patch("/{run_id}/status")
def patch_status(run_id: int, patch: StatusPatch, lang: str = Lang):
    try:
        with sim.connect() as conn:
            sim.update_status(conn, run_id, patch.model_dump())
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.post("/{run_id}/analyses", status_code=201)
def add_analysis(run_id: int, body: AnalysisBody, lang: str = Lang):
    if (body.kind == "gap") != (body.gap_hours is not None):
        raise HTTPException(422, tr(GAP_NEEDS_HOURS, lang))
    try:
        with sim.connect() as conn:
            sim.add_analysis(conn, run_id, body.kind, body.gap_hours)
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


class ResultBody(BaseModel):
    still_pond_count: int | None = Field(None, ge=0)
    drained_volume_m3: float | None = Field(None, ge=0)
    overflow_node_count: int | None = Field(None, ge=0)
    ponding_duration_h: float | None = Field(None, ge=0)
    validation_event: str | None = None
    validation_hit_rate_pct: float | None = Field(None, ge=0, le=100)
    validation_missed: int | None = Field(None, ge=0)
    validation_false: int | None = Field(None, ge=0)


@router.post("/{run_id}/open")
def open_simulation(run_id: int, lang: str = Lang):
    try:
        with sim.connect() as conn:
            sim.mark_opened(conn, run_id)
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.put("/{run_id}/result")
def put_result(run_id: int, body: ResultBody, lang: str = Lang):
    try:
        with sim.connect() as conn:
            sim.put_result(conn, run_id, body.model_dump())
            return sim.get_run(conn, run_id, lang)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


State = Query(None, description="多态任务（一个任务涵盖几种工况）取哪一态，如 L1 / L2+B；不给取默认态，单一交付不要给")


@router.get("/{run_id}/layers")
def get_layers(run_id: int, state: str | None = State, lang: str = Lang):
    try:
        with sim.connect() as conn:
            return sim_results.layers_meta(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/layers/max-depth")
def get_max_depth(run_id: int, state: str | None = State, lang: str = Lang):
    try:
        with sim.connect() as conn:
            path = sim_results.max_depth_geojson(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    return FileResponse(path, media_type="application/geo+json")


@router.get("/{run_id}/layers/inflow")
def get_inflow(run_id: int, state: str | None = State, lang: str = Lang):
    """模型实际用的总入流过程线：5 min 平均流量 + 累计入流量（`sim_sww._inflow_hydrograph`）。"""
    try:
        with sim.connect() as conn:
            return sim_results.inflow(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/layers/frames/{index}")
def get_frame(run_id: int, index: int, response: Response, state: str | None = State, lang: str = Lang):
    try:
        with sim.connect() as conn:
            body = sim_results.frame(conn, run_id, index, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    # 成果只读、帧不会变：让浏览器缓存，拖回已看过的时刻不再发请求
    response.headers["Cache-Control"] = "private, max-age=3600"
    return body


@router.get("/{run_id}/layers/timeline")
def get_timeline(run_id: int, response: Response, state: str | None = State, lang: str = Lang):
    """逐帧当前值（`sim_results.timeline`），与 /layers 的 frames 一一对应；拿不出的项给 null。"""
    try:
        with sim.connect() as conn:
            body = sim_results.timeline(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    response.headers["Cache-Control"] = "private, max-age=3600"
    return body


@router.get("/{run_id}/layers/ponding")
def get_ponding(run_id: int, state: str | None = State, lang: str = Lang):
    """易积水片区（`sim_ponding.py`）。成因 key 与偏离 key 是协议值不翻，只翻成因的展示文案。

    片区的 `cells` 是格子号，前端拿最大水深图层里同号的方格画范围，不另传几何。
    """
    try:
        with sim.connect() as conn:
            body = sim_results.ponding(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    for c in body["clusters"]:
        c["mechanism"] = tr(c["mechanism"], lang)
        c["name"] = tr(c.get("name"), lang)
    return body


@router.get("/{run_id}/ponding-points/meta")
def get_ponding_points_meta(run_id: int, lang: str = Lang):
    """模拟积水点的元信息：可选的态、默认阈值、热点清单与不在计算域内的热点（`city_ponding.py`；没有 404）。"""
    pdir = _points_dir(run_id, lang)
    return city_ponding.meta(pdir)


@router.get("/{run_id}/ponding-points")
def get_ponding_points(
    run_id: int,
    state: str | None = State,
    depth: float = Query(0.15),
    min_area: float = Query(100.0),
    min_dur: float = Query(0.5),
    lang: str = Lang,
):
    """按阈值从二维结果现算的模拟积水点（口径同 RD-A：三角网共边连片）：清单带最大水深 / 面积 / 积水量及其时刻、
    200 m 内客户热点。默认态取态清单的 default。没有 `points/` 的任务 404。"""
    pdir = _points_dir(run_id, lang)
    key = state or city_ponding.meta(pdir)["states"][0]
    try:
        body = city_ponding.ponding_points(str(pdir), key, round(depth, 3), round(min_area, 1), round(min_dur, 2))
    except city_ponding.PointsError as exc:
        raise HTTPException(status_code=404, detail=tr(str(exc), lang)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return dict(body)


@router.get("/{run_id}/ponding-points/{point_id}/series")
def get_ponding_point_series(
    run_id: int,
    point_id: str,
    state: str | None = State,
    depth: float = Query(0.15),
    min_area: float = Query(100.0),
    min_dur: float = Query(0.5),
    lang: str = Lang,
):
    """某个模拟积水点逐帧的积水量 / ≥depth 面积 / 最大水深；阈值要与清单一致（编号只在同一态、同一阈值内有效）。"""
    pdir = _points_dir(run_id, lang)
    key = state or city_ponding.meta(pdir)["states"][0]
    try:
        return city_ponding.point_series(str(pdir), key, point_id, round(depth, 3), round(min_area, 1), round(min_dur, 2))
    except city_ponding.PointsError as exc:
        raise HTTPException(status_code=404, detail=tr(str(exc), lang)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{run_id}/hotspot-buffer/{hotspot_id}/series")
def get_hotspot_circle_series(run_id: int, hotspot_id: str, state: str | None = State, depth: float = Query(0.15), lang: str = Lang):
    """某个交付热点 200 m 圆内逐帧的积水量 / ≥depth 面积 / 圆内最大水深；没有 `points/` 的任务 404。"""
    pdir = _points_dir(run_id, lang)
    key = state or city_ponding.meta(pdir)["states"][0]
    try:
        return city_ponding.hotspot_circle_series(str(pdir), key, hotspot_id, round(depth, 3))
    except city_ponding.PointsError as exc:
        raise HTTPException(status_code=404, detail=tr(str(exc), lang)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{run_id}/hotspot-buffer")
def get_hotspot_buffer(run_id: int, state: str | None = State, depth: float = Query(0.15), lang: str = Lang):
    """交付的客户热点 200 m 圆内的地表峰值积水量（水深 ≥ depth 的单元）+ 各热点圆的重叠；没有 `points/` 的任务 404。"""
    pdir = _points_dir(run_id, lang)
    key = state or city_ponding.meta(pdir)["states"][0]
    try:
        return dict(city_ponding.hotspot_buffer(str(pdir), key, round(depth, 3)))
    except city_ponding.PointsError as exc:
        raise HTTPException(status_code=404, detail=tr(str(exc), lang)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _points_dir(run_id: int, lang: str):
    with sim.connect() as conn:
        row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=tr(sim.NOT_FOUND, lang))
    pdir = city_ponding.points_dir(row["output_dir"])
    if pdir is None:
        raise HTTPException(status_code=404, detail=tr(NO_POINTS, lang))
    return pdir


@router.get("/{run_id}/hotspots")
def get_hotspots(run_id: int, lang: str = Lang):
    """交付自带的积水点（如 C1 的 8 个热点）：坐标、汇水区、逐态统计；不带 ?state=，一次给全部态。"""
    try:
        with sim.connect() as conn:
            return sim_results.hotspots(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/outputs")
def get_outputs(run_id: int, lang: str = Lang):
    """成果数据位置（相对 NAS 根目录）+ 同一区域之前的推演；没有 outputs.json 的任务 404。"""
    try:
        with sim.connect() as conn:
            return sim_results.run_outputs(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/params")
def get_params(run_id: int, lang: str = Lang):
    """本次推演的全部参数（规则 + 输入登记 + 一致性核对）；没有 params.json 的任务 404。"""
    try:
        with sim.connect() as conn:
            return sim_results.run_params(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/ponding-ge")
def get_ponding_ge(run_id: int, lang: str = Lang):
    """各态「水深 ≥ 阈值」的积水量（页面的「需要排 → 可以排 → 排完剩余」模块）；没有 ponding_ge.json 的任务 404。"""
    try:
        with sim.connect() as conn:
            return sim_results.ponding_ge(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/pipes")
def get_pipes(run_id: int, lang: str = Lang):
    """这次推演进模型的管线（GeoJSON，WGS84）；地图「雨水管网」开关用。没有 pipes.geojson 的任务 404。"""
    try:
        with sim.connect() as conn:
            return sim_results.run_pipes(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/equipment")
def get_equipment(run_id: int, lang: str = Lang):
    """全市应对设备配置：第一档固定设施（L2 sugg，45%）与第二档移动泵 + 罐车（L3，20%）的各场景数量；没有这两个文件的任务 404。"""
    try:
        with sim.connect() as conn:
            return sim_results.equipment(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/l2")
def get_l2(run_id: int, lang: str = Lang):
    """全市 L1 交付任务的第二道防线（Line 2 Current）：一维报告的两态对照 + 已建成塘。

    数字与 key 都是协议值，文案全在前端；只有 61 mm / 2 h 有报告数，别的雨 `available=false`。
    """
    try:
        with sim.connect() as conn:
            return sim_l2.load(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/layers/storage")
def get_storage_layer(run_id: int, state: str | None = None, lang: str = Lang):
    """多态任务某一态模型里的储水设施（蓄水单元）与接它们的管 / 泵（`sim_l2.storage_layer`）；没有就 404。"""
    try:
        with sim.connect() as conn:
            return sim_l2.storage_layer(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/layers/facilities")
def get_facilities_layer(run_id: int, state: str | None = None, lang: str = Lang):
    """某一态交付的设施 GIS（塘 / 泵点 / 排水去向 / 地面取水片）；没有就 404。"""
    try:
        with sim.connect() as conn:
            return sim_l2.facilities_layer(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/layers/residual")
def get_residual(run_id: int, state: str | None = State, lang: str = Lang):
    """演算末残留积水按洼地分块，逐块归因（类别 key 是协议值不翻；POI 名是原名不翻）。

    块的 `cells` 是格子号，前端拿最大水深图层里同号的方格画范围，不另传几何。
    """
    try:
        with sim.connect() as conn:
            return sim_results.residual(conn, run_id, state)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc


@router.get("/{run_id}/states/effect")
def get_state_effect(run_id: int, lang: str = Lang):
    """多态任务的方案效果（`sim_effect.py`）：同一时刻各态对基线（第一态）做减法。点名随语言，态 key 是协议值不翻。"""
    try:
        with sim.connect() as conn:
            body = sim_results.effect(conn, run_id)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    for p in body["points"]:
        p["name"] = tr(p.get("name"), lang)
    for st in body["states"]:
        st["label"] = tr(st["label"], lang)
        for p in st["outside_points"]:
            p["name"] = tr(p.get("name"), lang)
    return body


@router.get("/{run_id}/states/perf")
def get_state_perf(run_id: int, vs: str | None = Query(None, description="方案态 key，如 L2+B；不给取最后一态"),
                   h: float | None = Query(None, description="时刻档（h），不给取默认 2 h"), lang: str = Lang):
    """效果分析页（`sim_perf.py`）：既有（第一态）对方案态。态 key、类别 key、残留原因 key 是协议值不翻。"""
    try:
        with sim.connect() as conn:
            body = sim_results.perf(conn, run_id, vs, h)
    except sim.SimulationError as exc:
        raise _fail(exc, lang) from exc
    for k in ("base", "vs"):
        if k in body:        # 单态任务（sim_perf.single）没有 vs
            body[k]["label"] = tr(body[k]["label"], lang)
    for o in body["options"]:
        o["label"] = tr(o["label"], lang)
    for c in body["columns"]:
        c["label"] = tr(c["label"], lang)
    for p in body["points"]:
        p["name"] = tr(p.get("name"), lang)
    return body


GAP_NEEDS_HOURS = "Gap 分析要给小时数，Validation 不给"
