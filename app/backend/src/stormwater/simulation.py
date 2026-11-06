"""仿真任务的读写：Run history 列表 + New simulation 向导（表结构见 `sql/simulation_schema.sql`）。

这是本 API 里**唯一会写库**的地方，且只写 `syq-pipe` 的 `simulation` schema。
`db.py` 的只读连接专给管网分析用，不动它；这里另开可写连接，search_path 锁在
`simulation`，写不到管网那几个 schema 去。

口径：
- 状态流转：draft →（提交）queued → running → done / failed；draft 与 queued 可取消。
  草稿只能在 draft 状态下改、删；running / done 的由算力侧回写进度，不经向导。
- 三道防线是累加的：第 k 道的任务带上前 k 道勾选的资源（第二道规划 = 现状 + 规划工程
  视同建成）。「Queue all three」在提交时一次排出三个任务，共用 `batch_id`，见 `submit()`。
- 预计耗时取同范围、同网格的已完成任务的**中位**耗时；没有同口径历史就不出数（None），
  不拿别的网格顶替。就绪时间 = 此刻 + 耗时 × 任务数（同一对 H100 上串行）。
- 时间一律按阿布扎比本地时间（+04）返回，模型起算时刻按 UTC 入参。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from statistics import median
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import db_settings
from . import sim_l2
from .i18n import EXTENT_EN, en_label, tr

SCHEMA = "simulation"
LOCAL_TZ = "Asia/Dubai"
DEFAULT_COMPUTE_TARGET = "H100 pair"

#: 三道防线的先后；第 k 道包含前 k 道的资源
LINE_ORDER = ("line1", "line2_current", "line2_planned")
RAIN_SOURCES = ("design_storm", "observed_replay", "ncm_forecast")
#: 列表上方的筛选标签 → where 子句（`mine` 另需 user）
TABS = ("all", "running", "done", "queued", "replays", "mine", "drafts")

#: 可以改、删的状态
EDITABLE = ("draft",)
#: 可以从任务历史里删掉的：草稿 + 已结束的。排队 / 在算的要先取消，免得算力侧回写落空
DELETABLE = ("draft", "done", "failed", "cancelled")
CANCELLABLE = ("draft", "queued")
#: 这些操作人名下的任务是正式交付成果（城市级 run 与 Mussafah 四态），任何状态都不许删
PROTECTED_OPERATORS = ("AR",)
#: 算力侧回写允许的流转
TRANSITIONS = {
    "queued": ("running", "cancelled", "failed"),
    "running": ("done", "failed", "cancelled"),
}


class SimulationError(Exception):
    """带 HTTP 状态码的业务错误。`message` 是固定中文串，i18n.EN 里有对应英文。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


NOT_FOUND = "没有这个仿真任务"
NOT_EDITABLE = "只有草稿可以修改或删除"
NOT_DELETABLE = "排队中或正在计算的任务不能删除，请先取消"
PROTECTED_RUN = "AR 名下的交付成果不能删除"
NOT_CANCELLABLE = "只有草稿或排队中的任务可以取消"
BAD_TRANSITION = "任务状态不能这样流转"
NCM_ROADMAP = "NCM 预报降雨尚未接入（路线图）"
NEED_STORM = "设计暴雨需选一个预设，或同时给出自定义雨量与历时"
NEED_REPLAY = "实测回放需指明回放的降雨事件"
UNKNOWN_PRESET = "未知的雨型预设"
UNKNOWN_LINE = "未知的防线"
UNKNOWN_RESOURCE = "有资源编号不存在"
RESOURCE_BEYOND_LINE = "勾选了所选防线之后的资源（第一道不能带第二道的资源）"
UNKNOWN_USER = "未知的操作人"
ANALYSIS_NEEDS_DONE = "只有已完成的任务可以追加分析"
MESSAGES = (NOT_FOUND, NOT_EDITABLE, NOT_DELETABLE, PROTECTED_RUN, NOT_CANCELLABLE, BAD_TRANSITION, NCM_ROADMAP,
            NEED_STORM, NEED_REPLAY, UNKNOWN_PRESET, UNKNOWN_LINE, UNKNOWN_RESOURCE, RESOURCE_BEYOND_LINE,
            UNKNOWN_USER, ANALYSIS_NEEDS_DONE)


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """可写连接，正常退出提交、异常回滚。"""
    with psycopg.connect(db_settings().conninfo, row_factory=dict_row) as conn:
        conn.execute(f"set search_path to {SCHEMA}")
        conn.execute(f"set timezone to '{LOCAL_TZ}'")
        yield conn


# ---------------------------------------------------------------------------
# 目录（向导的选项）
# ---------------------------------------------------------------------------


def options(conn: psycopg.Connection, lang: str) -> dict[str, Any]:
    """向导各步的可选项与默认值。防线 / 资源名按语言取 label_zh / label_en。"""
    presets = conn.execute(
        "select key, depth_mm, duration_h, exact_depth_mm, label, is_design_basis "
        "from storm_preset order by sort_order"
    ).fetchall()
    lines = _lines(conn, lang)
    users = conn.execute("select id, initials, display_name from app_user order by id").fetchall()
    design = next((p for p in presets if p["is_design_basis"]), None)
    return {
        "rain_sources": [
            {"key": "design_storm", "available": True},
            {"key": "observed_replay", "available": True},
            {"key": "ncm_forecast", "available": False},   # 设计稿标 roadmap
        ],
        "storm_presets": presets,
        "storm_depths_mm": sorted({p["depth_mm"] for p in presets}),
        "storm_durations_h": sorted({p["duration_h"] for p in presets}, reverse=True),
        "defence_lines": lines,
        "users": users,
        # 设计稿下拉框里的取值；目前各只有一项，后续加选项只改这里
        "choices": {
            "temporal_pattern": ["Zone B official DDF alternating-block (2022)"],
            "spatial_distribution": ["Uniform citywide", "C1"],
            "extent": [
                {"extent": "City-wide", "grid_m": 250, "terrain": "5 m DTM"},
                {"extent": "Abu Dhabi", "grid_m": 250, "terrain": "5 m DTM"},
                {"extent": "Abu Dhabi Island", "grid_m": 250, "terrain": "5 m DTM"},
                {"extent": "Al Bateen", "grid_m": 10, "terrain": "5 m DTM"},
                {"extent": "C1", "grid_m": 15, "terrain": "5 m DTM"},
            ],
            "tide": [{"tide_label": "HAT 1.3 m NADD", "tide_level_m": 1.3, "tide_datum": "NADD",
                      "gate_close_above_m": 1.3}],
            "after_rain_mode": ["until_drained", "fixed"],
        },
        "defaults": {
            "rain_source": "design_storm",
            "storm_preset_key": design["key"] if design else None,
            "temporal_pattern": "Zone B official DDF alternating-block (2022)",
            "peak_position_pct": 40,
            "spatial_distribution": "Uniform citywide",
            "extent": "City-wide", "grid_m": 250, "terrain": "5 m DTM",
            "tide_label": "HAT 1.3 m NADD", "tide_level_m": 1.3, "tide_datum": "NADD",
            "gate_close_above_m": 1.3,
            "after_rain_mode": "until_drained", "after_rain_max_h": 36,
            "baseflow_pct": 20, "baseflow_min_dia_mm": 600, "baseflow_note": "Agreed with IDD 21 Sep",
            "defence_line_key": "line1",
            "queue_all_lines": False,
            "resource_ids": [r["id"] for ln in lines if ln["key"] == "line1" for r in ln["resources"]],
        },
    }


def _lines(conn: psycopg.Connection, lang: str) -> list[dict[str, Any]]:
    col = "label_zh" if lang == "zh" else "label_en"
    lines = conn.execute(
        f"select key, {col} as label, subtitle_en as subtitle, badge_en as badge "
        "from defence_line order by sort_order"
    ).fetchall()
    res = conn.execute(
        f"select id, defence_line_key, key, {col} as label, quantity "
        "from defence_resource order by defence_line_key, sort_order"
    ).fetchall()
    for ln in lines:
        ln["resources"] = [
            {k: r[k] for k in ("id", "key", "label", "quantity")}
            for r in res if r["defence_line_key"] == ln["key"]
        ]
    return lines


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def _tab_where(tab: str, user_id: int | None) -> tuple[str, list[Any]]:
    if tab == "drafts":
        return "r.status = 'draft'", []
    base = "r.status <> 'draft'"
    if tab == "all":
        return base, []
    if tab in ("running", "done", "queued"):
        return f"{base} and r.status = %s", [tab]
    if tab == "replays":
        return f"{base} and r.rain_source = 'observed_replay'", []
    # mine：没传操作人就一条都不给，不回落成全部
    return f"{base} and r.created_by = %s", [user_id]


def list_runs(conn: psycopg.Connection, *, tab: str, user: str | None, lang: str,
              limit: int, offset: int) -> dict[str, Any]:
    user_id = _user_id(conn, user) if user else None
    where, params = _tab_where(tab, user_id)
    col = "label_zh" if lang == "zh" else "label_en"
    rows = conn.execute(
        f"""
        select r.id, r.status, r.progress_pct, r.batch_id, r.rain_source, r.rain_label,
               r.storm_preset_key, r.depth_mm, r.duration_h,
               r.defence_line_key, d.{col} as defence_line_label, r.defence_basis, r.variant_label, r.output_dir,
               r.extent, r.grid_m, r.output_dir is not null as has_layers,
               r.started_at, r.ended_at, r.ended_is_estimate, r.opened_at,
               u.initials as created_by, r.created_at,
               coalesce((select json_agg(json_build_object('kind', a.kind, 'gap_hours', a.gap_hours)
                                         order by a.kind desc, a.gap_hours)
                         from run_analysis a where a.run_id = r.id), '[]'::json) as analyses
        from run r
        join defence_line d on d.key = r.defence_line_key
        left join app_user u on u.id = r.created_by
        where {where}
        order by coalesce(r.started_at, r.created_at) desc, r.id desc
        limit %s offset %s
        """,
        [*params, limit, offset],
    ).fetchall()
    from . import sim_results

    l2_label = None
    for r in rows:
        r["extent"] = EXTENT_EN.get(r.get("extent"), r.get("extent"))
        r["variant_label"] = en_label(r["variant_label"])   # 数据名恒英文；库里的旧中文行读出时归一
        output_dir = r.pop("output_dir")
        r["job_id"] = job_id_of(output_dir)
        # 防线口径取库里的值（write_defence_basis 写入）；老库还没回填的行现算兜底
        r["defence_basis"] = r["defence_basis"] or defence_basis(r["defence_line_key"], output_dir)
        # 全市任务（#100/101/102）已挂第二道现状（sim_l2），列表防线列按用户要求写第二道；
        # 库里 defence_line_key 不动（详情页的防线切换、_line_runs 仍按 line1 认它）
        if sim_l2.applies(r, output_dir):
            if l2_label is None:
                l2_label = conn.execute(f"select {col} as label from defence_line where key = 'line2_current'"
                                        ).fetchone()["label"]
            r["defence_line_key"], r["defence_line_label"] = "line2_current", l2_label
        # 多态任务（一个任务涵盖几种工况）列出态名，列表的防线列据此写「全部防线」；交付路径不外露
        m = sim_results.run_states(output_dir)
        r["states"] = [s["key"] for s in m["states"]] if m else None
        # 各态落在哪一列（line1 / line2_current / line2_planned / line2_suggested），前端据此写防线全名
        r["state_columns"] = [s.get("column") or s["defence_line_key"] for s in m["states"]] if m else None
    total = conn.execute(f"select count(*) as n from run r where {where}", params).fetchone()["n"]
    counts = conn.execute(
        """
        select count(*) filter (where status <> 'draft')                       as all,
               count(*) filter (where status = 'running')                      as running,
               count(*) filter (where status = 'done')                         as done,
               count(*) filter (where status = 'queued')                       as queued,
               count(*) filter (where status <> 'draft'
                                  and rain_source = 'observed_replay')         as replays,
               count(*) filter (where status <> 'draft' and created_by = %s)   as mine,
               count(*) filter (where status = 'draft')                        as drafts
        from run
        """,
        [user_id],
    ).fetchone()
    return {"tab": tab, "total": total, "counts": counts, "items": rows}


# ---------------------------------------------------------------------------
# 单个任务
# ---------------------------------------------------------------------------

#: 向导可写的字段（与 run 表同名）
FORM_FIELDS = (
    "rain_source", "storm_preset_key", "replay_event_ref", "depth_mm", "duration_h",
    "temporal_pattern", "peak_position_pct", "spatial_distribution",
    "extent", "grid_m", "terrain", "tide_level_m", "tide_datum", "tide_label", "gate_close_above_m",
    "model_start_utc", "after_rain_mode", "after_rain_max_h",
    "baseflow_pct", "baseflow_min_dia_mm", "baseflow_note",
    "defence_line_key", "queue_all_lines", "compute_target",
)


def job_id_of(output_dir: str | None) -> str | None:
    """任务 id = 交付目录名（engine 的任务单 id，shp_dat/、flood2d_* 的 manifest、两个页面都用它）。旧数据没有任务单，也取目录名。"""
    if not output_dir:
        return None
    return str(output_dir).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or None


def get_run(conn: psycopg.Connection, run_id: int, lang: str) -> dict[str, Any]:
    col = "label_zh" if lang == "zh" else "label_en"
    row = conn.execute(
        f"""
        select r.*, d.{col} as defence_line_label, u.initials as created_by_initials
        from run r join defence_line d on d.key = r.defence_line_key
        left join app_user u on u.id = r.created_by
        where r.id = %s
        """,
        [run_id],
    ).fetchone()
    if row is None:
        raise SimulationError(404, NOT_FOUND)
    row["created_by"] = row.pop("created_by_initials")
    row["extent"] = EXTENT_EN.get(row.get("extent"), row.get("extent"))
    row["variant_label"] = en_label(row.get("variant_label"))
    # 交付目录路径不外露，只告诉前端有没有结果图层（图层走 /layers 系列接口）
    output_dir = row.pop("output_dir", None)
    row["job_id"] = job_id_of(output_dir)
    row["defence_basis"] = row.get("defence_basis") or defence_basis(row["defence_line_key"], output_dir)
    row["has_layers"] = bool(output_dir)
    row["has_l2"] = sim_l2.has_l2(row, output_dir)
    from . import city_ponding   # 惰性：要 numpy / scipy，别拖慢只列表的调用
    row["has_ponding_points"] = city_ponding.has_points(output_dir)    # 详情页要不要出「模拟积水点」那一块
    row["states"], row["state_default"] = _states(conn, output_dir, col, lang)
    row["resources"] = conn.execute(
        f"""
        select dr.id, dr.defence_line_key, dr.key, dr.{col} as label, dr.quantity
        from run_resource rr join defence_resource dr on dr.id = rr.resource_id
        join defence_line d on d.key = dr.defence_line_key
        where rr.run_id = %s order by d.sort_order, dr.sort_order
        """,
        [run_id],
    ).fetchall()
    row["resource_ids"] = [r["id"] for r in row["resources"]]
    row["analyses"] = conn.execute(
        "select id, kind, gap_hours from run_analysis where run_id = %s order by kind desc, gap_hours",
        [run_id],
    ).fetchall()
    row["result"] = conn.execute(
        "select * from run_result where run_id = %s", [run_id]).fetchone()
    row["line_runs"] = _line_runs(conn, row, col)
    return row


def _states(conn: psycopg.Connection, output_dir: str | None, col: str,
            lang: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """多态任务的态（详情页右上角的切换与对比按态走，图层接口带 ?state=）；单一交付返回 (None, None)。"""
    from . import sim_results

    m = sim_results.run_states(output_dir)
    if m is None:
        return None, None
    labels = {r["key"]: r["label"]
              for r in conn.execute(f"select key, {col} as label from defence_line").fetchall()}
    return [{"key": s["key"], "label": en_label(s["label"]), "defence_line_key": s["defence_line_key"],
             "defence_line_label": labels.get(s["defence_line_key"]), "same_as": s.get("same_as"),
             "column": s.get("column") or s["defence_line_key"]}
            for s in m["states"]], m["default"]


def _line_runs(conn: psycopg.Connection, run: dict[str, Any], col: str) -> list[dict[str, Any]]:
    """详情页右上角的防线切换 / 对比：同一场雨在三道防线上各取一个任务。

    先认同批（`batch_id`，Queue all three 排出来的）；同批里没有的那道，再找同降雨、同范围、
    同网格的任务 —— 设计暴雨按预设键（自定义按雨深 + 历时）、回放按事件号。草稿 / 取消 / 失败的
    不算；有交付图层的优先，其次已完成，再按最近。本任务自己恒占本道。找不到的那道 `run_id` 为 None，
    不拿别的雨顶替。"""
    rows = conn.execute(
        f"""
        select distinct on (r.defence_line_key)
               r.defence_line_key, r.id, r.status, r.output_dir is not null as has_layers
        from run r
        where r.id = %(id)s
           or (r.status not in ('draft', 'cancelled', 'failed')
               and ((%(batch)s::uuid is not null and r.batch_id = %(batch)s::uuid)
                    or (r.rain_source = %(src)s
                        and r.extent is not distinct from %(extent)s
                        and r.grid_m is not distinct from %(grid)s
                        and case when %(src)s = 'observed_replay'
                                 then r.replay_event_ref is not distinct from %(replay)s
                                 when %(preset)s::text is not null then r.storm_preset_key = %(preset)s
                                 else r.storm_preset_key is null
                                      and r.depth_mm is not distinct from %(depth)s::numeric
                                      and r.duration_h is not distinct from %(dur)s::numeric end)))
        order by r.defence_line_key, (r.id = %(id)s) desc,
                 coalesce(r.batch_id = %(batch)s::uuid, false) desc,
                 r.output_dir is not null desc, r.status = 'done' desc,
                 coalesce(r.ended_at, r.created_at) desc, r.id desc
        """,
        {"id": run["id"], "batch": run["batch_id"], "src": run["rain_source"], "extent": run["extent"],
         "grid": run["grid_m"], "replay": run["replay_event_ref"], "preset": run["storm_preset_key"],
         "depth": run["depth_mm"], "dur": run["duration_h"]},
    ).fetchall()
    found = {r["defence_line_key"]: r for r in rows}
    labels = {r["key"]: r["label"]
              for r in conn.execute(f"select key, {col} as label from defence_line").fetchall()}
    out = []
    for key in LINE_ORDER:
        hit = found.get(key)
        out.append({"defence_line_key": key, "label": labels.get(key),
                    "run_id": hit["id"] if hit else None,
                    "status": hit["status"] if hit else None,
                    "has_layers": bool(hit and hit["has_layers"])})
    return out


def _normalise(conn: psycopg.Connection, form: dict[str, Any]) -> dict[str, Any]:
    """校验向导表单，补出雨量标签。返回可直接写库的字段。"""
    f = {k: form.get(k) for k in FORM_FIELDS}
    src = f["rain_source"] or "design_storm"
    f["rain_source"] = src
    if src == "ncm_forecast":
        raise SimulationError(422, NCM_ROADMAP)
    if src == "observed_replay":
        if not f["replay_event_ref"]:
            raise SimulationError(422, NEED_REPLAY)
        f["storm_preset_key"] = None
    else:
        f["replay_event_ref"] = None
        if f["storm_preset_key"]:
            p = conn.execute("select * from storm_preset where key = %s",
                             [f["storm_preset_key"]]).fetchone()
            if p is None:
                raise SimulationError(422, UNKNOWN_PRESET)
            # 预设格子上是名义雨量；设计基准那格有实际雨深 61.3 mm，以实际为准
            f["depth_mm"] = p["exact_depth_mm"] or p["depth_mm"]
            f["duration_h"] = p["duration_h"]
        elif f["depth_mm"] is None or f["duration_h"] is None:
            raise SimulationError(422, NEED_STORM)

    if f["defence_line_key"] not in LINE_ORDER:
        raise SimulationError(422, UNKNOWN_LINE)
    f["queue_all_lines"] = bool(f["queue_all_lines"])
    f["compute_target"] = f["compute_target"] or DEFAULT_COMPUTE_TARGET
    f["rain_label"] = _rain_label(f)
    return f


def _rain_label(f: dict[str, Any]) -> str:
    if f["rain_source"] == "observed_replay":
        return str(f["replay_event_ref"])
    if f["storm_preset_key"]:
        # 列表显示名义雨量（61 mm），不显示 61.3
        return f"{f['storm_preset_key'].split('mm_')[0]} mm in {_num(f['duration_h'])} h"
    return f"{_num(f['depth_mm'])} mm in {_num(f['duration_h'])} h"


def _num(x: Any) -> str:
    return f"{float(x):g}"


def _check_resources(conn: psycopg.Connection, ids: list[int], line_key: str,
                     queue_all: bool) -> list[int]:
    """资源只能来自所选防线及其之前的防线；勾了 Queue all three 时三张卡都可勾。"""
    ids = sorted(set(ids))
    if not ids:
        return []
    rows = conn.execute(
        "select id, defence_line_key from defence_resource where id = any(%s)", [ids]
    ).fetchall()
    if len(rows) != len(ids):
        raise SimulationError(422, UNKNOWN_RESOURCE)
    allowed = LINE_ORDER if queue_all else _upto(line_key)
    if any(r["defence_line_key"] not in allowed for r in rows):
        raise SimulationError(422, RESOURCE_BEYOND_LINE)
    return ids


def _upto(line_key: str) -> tuple[str, ...]:
    return LINE_ORDER[: LINE_ORDER.index(line_key) + 1]


def _user_id(conn: psycopg.Connection, initials: str) -> int:
    row = conn.execute("select id from app_user where initials = %s", [initials]).fetchone()
    if row is None:
        raise SimulationError(422, UNKNOWN_USER)
    return row["id"]


def estimate(conn: psycopg.Connection, form: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """确认页底部那条：任务数 / 预计耗时 / 就绪时间。"""
    count = len(LINE_ORDER) if form.get("queue_all_lines") else 1
    durations = [
        r["h"] for r in conn.execute(
            "select extract(epoch from ended_at - started_at) / 3600 as h from run "
            "where status = 'done' and extent = %s and grid_m = %s "
            "and started_at is not null and ended_at is not null",
            [form.get("extent"), form.get("grid_m")],
        ).fetchall()
    ]
    runtime_h = round(float(median(durations)), 1) if durations else None
    now = now or conn.execute("select now() as t").fetchone()["t"]
    ready = now + timedelta(hours=runtime_h * count) if runtime_h is not None else None
    return {
        "est_run_count": count,
        "est_runtime_h": runtime_h,
        "compute_target": form.get("compute_target") or DEFAULT_COMPUTE_TARGET,
        "est_ready_at": ready,
        "basis_runs": len(durations),   # 估算用了几条同口径历史任务；0 即不出数
    }


def _write_fields(conn: psycopg.Connection, f: dict[str, Any]) -> dict[str, Any]:
    est = estimate(conn, f)
    return {**f, "est_run_count": est["est_run_count"], "est_runtime_h": est["est_runtime_h"],
            "est_ready_at": est["est_ready_at"]}


def create_draft(conn: psycopg.Connection, form: dict[str, Any]) -> int:
    f = _normalise(conn, form)
    res = _check_resources(conn, form.get("resource_ids") or [], f["defence_line_key"], f["queue_all_lines"])
    created_by = _user_id(conn, form["created_by"]) if form.get("created_by") else None
    cols = {**_write_fields(conn, f), "created_by": created_by, "status": "draft"}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    run_id = conn.execute(f"insert into run ({names}) values ({marks}) returning id",
                          list(cols.values())).fetchone()["id"]
    _set_resources(conn, run_id, res)
    write_defence_basis(conn, run_id)
    return run_id


def update_draft(conn: psycopg.Connection, run_id: int, form: dict[str, Any]) -> None:
    """整表单覆盖（向导每步都带全量），不做字段级合并。"""
    _require_status(conn, run_id, EDITABLE, NOT_EDITABLE, lock=True)
    f = _normalise(conn, form)
    res = _check_resources(conn, form.get("resource_ids") or [], f["defence_line_key"], f["queue_all_lines"])
    cols = _write_fields(conn, f)
    sets = ", ".join(f"{k} = %s" for k in cols)
    conn.execute(f"update run set {sets}, updated_at = now() where id = %s", [*cols.values(), run_id])
    _set_resources(conn, run_id, res)
    write_defence_basis(conn, run_id)


def delete_run(conn: psycopg.Connection, run_id: int) -> None:
    """从任务历史里删掉一条：草稿或已结束的任务。资源勾选、分析、结果指标随外键级联删除；
    `output_dir` 指向的交付目录只读不写，这里不动（再登记一次即可找回）。
    `PROTECTED_OPERATORS` 名下的任务是交付成果，一律 403，先于状态判断。"""
    run = conn.execute(
        "select u.initials from run r left join app_user u on u.id = r.created_by where r.id = %s", [run_id]
    ).fetchone()
    if run is not None and run["initials"] in PROTECTED_OPERATORS:
        raise SimulationError(403, PROTECTED_RUN)
    _require_status(conn, run_id, DELETABLE, NOT_DELETABLE, lock=True)
    conn.execute("delete from run where id = %s", [run_id])


def submit(conn: psycopg.Connection, run_id: int) -> list[int]:
    """「Run」：草稿转排队。勾了 Queue all three 就按三道防线各排一个，共用 batch_id。

    同批其余任务复制本草稿的雨情与边界。草稿上存的是三张卡的勾选全集，每个任务取其中
    属于本防线及之前防线的那部分；**某道防线一项没勾的，按整道全选**（卡片没展开过
    就提交，不能让后两个任务退化成第一道）。返回排进队列的任务编号（本草稿在最前）。
    """
    run = _require_status(conn, run_id, EDITABLE, NOT_EDITABLE, lock=True)
    if not run["queue_all_lines"]:
        conn.execute("update run set status = 'queued', updated_at = now() where id = %s", [run_id])
        return [run_id]

    picked = {r["resource_id"] for r in conn.execute(
        "select resource_id from run_resource where run_id = %s", [run_id]).fetchall()}
    catalog = conn.execute("select id, defence_line_key from defence_resource").fetchall()
    touched = {r["defence_line_key"] for r in catalog if r["id"] in picked}
    pool = picked | {r["id"] for r in catalog if r["defence_line_key"] not in touched}

    def pick(key: str) -> list[int]:
        return [r["id"] for r in catalog if r["id"] in pool and r["defence_line_key"] in _upto(key)]

    batch = uuid.uuid4()
    copy_cols = ", ".join(c for c in (*FORM_FIELDS, "rain_label", "created_by", "est_run_count",
                                      "est_runtime_h", "est_ready_at") if c != "defence_line_key")
    ids = [run_id]
    for key in LINE_ORDER:
        if key == run["defence_line_key"]:
            continue
        new_id = conn.execute(
            f"insert into run ({copy_cols}, defence_line_key, status, batch_id) "
            f"select {copy_cols}, %s, 'queued', %s from run where id = %s returning id",
            [key, batch, run_id],
        ).fetchone()["id"]
        _set_resources(conn, new_id, pick(key))
        write_defence_basis(conn, new_id)
        ids.append(new_id)
    _set_resources(conn, run_id, pick(run["defence_line_key"]))
    conn.execute("update run set status = 'queued', batch_id = %s, updated_at = now() where id = %s",
                 [batch, run_id])
    return ids


def cancel(conn: psycopg.Connection, run_id: int) -> None:
    _require_status(conn, run_id, CANCELLABLE, NOT_CANCELLABLE, lock=True)
    conn.execute("update run set status = 'cancelled', updated_at = now() where id = %s", [run_id])


def update_status(conn: psycopg.Connection, run_id: int, patch: dict[str, Any]) -> None:
    """算力侧回写：开跑、进度、结束。只许按 TRANSITIONS 流转。"""
    run = _require_status(conn, run_id, tuple(TRANSITIONS), BAD_TRANSITION, lock=True)
    new = patch.get("status") or run["status"]
    if new != run["status"] and new not in TRANSITIONS[run["status"]]:
        raise SimulationError(409, BAD_TRANSITION)
    cols: dict[str, Any] = {"status": new}
    for k in ("progress_pct", "started_at", "ended_at", "ended_is_estimate"):
        if patch.get(k) is not None:
            cols[k] = patch[k]
    if new == "running" and run["started_at"] is None and "started_at" not in cols:
        cols["started_at"] = datetime.now().astimezone()
    if new == "done":
        cols.setdefault("progress_pct", 100)
        cols.setdefault("ended_at", datetime.now().astimezone())
        cols["ended_is_estimate"] = False
    sets = ", ".join(f"{k} = %s" for k in cols)
    conn.execute(f"update run set {sets}, updated_at = now() where id = %s", [*cols.values(), run_id])


def add_analysis(conn: psycopg.Connection, run_id: int, kind: str, gap_hours: float | None) -> None:
    _require_status(conn, run_id, ("done",), ANALYSIS_NEEDS_DONE)
    conn.execute(
        "insert into run_analysis (run_id, kind, gap_hours) values (%s, %s, %s) "
        "on conflict on constraint run_analysis_uniq do nothing",
        [run_id, kind, gap_hours if kind == "gap" else None],
    )


def _require_status(conn: psycopg.Connection, run_id: int, allowed: tuple[str, ...], message: str,
                    lock: bool = False) -> dict[str, Any]:
    row = conn.execute(
        "select * from run where id = %s" + (" for update" if lock else ""), [run_id]
    ).fetchone()
    if row is None:
        raise SimulationError(404, NOT_FOUND)
    if row["status"] not in allowed:
        raise SimulationError(409, message)
    return row


def _set_resources(conn: psycopg.Connection, run_id: int, ids: list[int]) -> None:
    conn.execute("delete from run_resource where run_id = %s", [run_id])
    if ids:
        with conn.cursor() as cur:
            cur.executemany("insert into run_resource (run_id, resource_id) values (%s, %s)",
                            [(run_id, i) for i in ids])


# ---------------------------------------------------------------------------
# 防线口径（列表「防线口径」列、详情页总览的那一格）
# ---------------------------------------------------------------------------

#: 防线代号（用户 2026-09-28 定：一律写代号 Line1 / Line2，不写「+」、不翻译）。
#: 键是 defence_line.key 与多态交付 states.json 的 column
LINE_CODE = {"line1": "Line1", "line2_current": "Line2", "line2_planned": "Line2 planned",
             "line2_suggested": "Line2 suggested"}
#: 单态任务按所选防线列出叠加到哪一道（第 k 道包含前 k 道）。
#: 规划 = 现状 + 规划工程视同建成，所以开了规划资源要把现状那道也列上（用户 2026-09-29 定）
_BASIS = {"line1": ("line1",), "line2_current": ("line1", "line2_current"),
          "line2_planned": ("line1", "line2_current", "line2_planned")}


def defence_basis(defence_line_key: str, output_dir: str | None) -> str:
    """防线口径的值，写进 `run.defence_basis`（用户 2026-09-28 要求落库）。

    - 多态任务（一个任务涵盖几种工况）按各态的 column 列出：「Line1 / Line2 / Line2 planned / Line2 suggested」；
    - 单态按所选防线：「Line1」「Line1 ｜ Line2」「Line1 ｜ Line2 ｜ Line2 planned」；
    - 全市交付的 L1 任务（#100–102）挂了第二道现状视图，按第二道写「Line1 ｜ Line2」（sim_l2.applies）。
    认不得的键原样写出，不猜。"""
    from . import sim_results

    m = sim_results.run_states(output_dir)
    if m is not None:
        cols = [st.get("column") or st["defence_line_key"] for st in m["states"]]
        return " / ".join(LINE_CODE.get(c, c) for c in cols)
    key = "line2_current" if sim_l2.applies({"defence_line_key": defence_line_key}, output_dir) \
        else defence_line_key
    return " ｜ ".join(LINE_CODE.get(k, k) for k in _BASIS.get(key, (key,)))


def write_defence_basis(conn: psycopg.Connection, run_id: int | None = None) -> int:
    """按任务当前的防线与交付目录重算防线口径并写库；不给 run_id 就全表重写。返回改动的行数。"""
    rows = conn.execute(
        "select id, defence_line_key, output_dir, defence_basis from run"
        + (" where id = %s" if run_id is not None else ""),
        [run_id] if run_id is not None else [],
    ).fetchall()
    n = 0
    for r in rows:
        v = defence_basis(r["defence_line_key"], r["output_dir"])
        if v != r["defence_basis"]:
            conn.execute("update run set defence_basis = %s where id = %s", [v, r["id"]])
            n += 1
    return n


# ---------------------------------------------------------------------------
# 首页（home v2）：KPI 四卡 + 同场雨对比 + 任务队列 + New results
# ---------------------------------------------------------------------------

#: 进队列的状态。KPI「Runs」只数这三种（草稿、取消的不算）
LIVE = ("queued", "running", "done")
RESULT_FIELDS = ("still_pond_count", "drained_volume_m3", "overflow_node_count", "ponding_duration_h",
                 "validation_event", "validation_hit_rate_pct", "validation_missed", "validation_false")


def summary(conn: psycopg.Connection, lang: str, new_limit: int = 5) -> dict[str, Any]:
    col = "label_zh" if lang == "zh" else "label_en"
    runs = conn.execute(
        "select count(*) filter (where status = any(%(live)s)) as total, "
        "count(*) filter (where status = 'done') as done, "
        "count(*) filter (where status = 'running') as running, "
        "count(*) filter (where status = 'queued') as queued, "
        "count(*) filter (where status = 'failed') as failed from run",
        {"live": list(LIVE)},
    ).fetchone()

    # 雨型覆盖：9 格里有几格已有完成的任务；在跑 / 排队的另算「还要几格、最晚何时」
    presets = conn.execute("select key, depth_mm, duration_h from storm_preset order by sort_order").fetchall()
    done_keys = {r["k"] for r in conn.execute(
        "select distinct storm_preset_key as k from run where status = 'done' and storm_preset_key is not null")}
    pend = conn.execute(
        "select storm_preset_key as k, max(coalesce(ended_at, est_ready_at)) as by_at from run "
        "where status in ('queued','running') and storm_preset_key is not null group by 1").fetchall()
    pending = [p for p in pend if p["k"] not in done_keys]
    storms = {
        "total": len(presets),
        "modelled": [p for p in presets if p["key"] in done_keys],
        "pending_count": len(pending),
        "pending_by": max((p["by_at"] for p in pending if p["by_at"]), default=None),
    }

    new_where = ("r.status = 'done' and r.opened_at is null "
                 "and r.ended_at >= date_trunc('week', now())")
    new_items = conn.execute(
        f"""
        select r.id, r.rain_label, r.storm_preset_key, r.depth_mm, r.duration_h, r.rain_source,
               r.defence_line_key, d.{col} as defence_line_label, r.started_at, r.ended_at,
               u.initials as created_by, to_jsonb(res) - 'run_id' - 'updated_at' as result,
               coalesce((select json_agg(json_build_object('kind', a.kind, 'gap_hours', a.gap_hours)
                                         order by a.kind desc, a.gap_hours)
                         from run_analysis a where a.run_id = r.id), '[]'::json) as analyses,
               (select b.still_pond_count from run p join run_result b on b.run_id = p.id
                 where p.status = 'done' and p.storm_preset_key = r.storm_preset_key
                   and p.defence_line_key = (%(prev)s::text[])[array_position(%(order)s::text[], r.defence_line_key)]
                 order by p.ended_at desc limit 1) as baseline_still_pond
        from run r join defence_line d on d.key = r.defence_line_key
        left join app_user u on u.id = r.created_by
        left join run_result res on res.run_id = r.id
        where {new_where}
        order by r.ended_at desc limit %(lim)s
        """,
        # 「from 141 with measures on the ground」：与同雨型、上一道防线的最近一次结果比
        {"prev": [None, "line1", "line2_current"], "order": list(LINE_ORDER), "lim": new_limit},
    ).fetchall()
    new_count = conn.execute(f"select count(*) as n from run r where {new_where}").fetchone()["n"]

    val = conn.execute(
        "select r.id, res.validation_event as event, res.validation_hit_rate_pct as hit_rate, "
        "res.validation_missed as missed, res.validation_false as false_alarms, r.ended_at "
        "from run r join run_result res on res.run_id = r.id "
        "where r.status = 'done' and res.validation_hit_rate_pct is not null "
        "order by r.ended_at desc limit 2").fetchall()
    validation = ({**val[0], "previous": val[1] if len(val) > 1 else None} if val else None)

    return {
        "runs": runs,
        "storms": storms,
        "new_results": {"count": new_count, "items": new_items},
        "validation": validation,
        "same_storm": _same_storm(conn),
        "queue": _queue(conn),
    }


def _same_storm(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """每个雨型、每道防线取最近一次完成任务的 still pond 数；一道都没有的雨型不列。"""
    rows = conn.execute(
        """
        select distinct on (r.storm_preset_key, r.defence_line_key)
               r.storm_preset_key, r.defence_line_key, b.still_pond_count, r.id as run_id
        from run r join run_result b on b.run_id = r.id
        where r.status = 'done' and r.storm_preset_key is not null and b.still_pond_count is not null
        order by r.storm_preset_key, r.defence_line_key, r.ended_at desc
        """
    ).fetchall()
    presets = conn.execute("select key, depth_mm, duration_h, sort_order from storm_preset").fetchall()
    out = []
    for p in sorted(presets, key=lambda p: (p["duration_h"], p["depth_mm"])):
        mine = {r["defence_line_key"]: r for r in rows if r["storm_preset_key"] == p["key"]}
        if mine:
            out.append({"storm_preset_key": p["key"], "depth_mm": p["depth_mm"], "duration_h": p["duration_h"],
                        "lines": [{"defence_line_key": k,
                                   "still_pond_count": mine[k]["still_pond_count"] if k in mine else None,
                                   "run_id": mine[k]["run_id"] if k in mine else None}
                                  for k in LINE_ORDER]})
    # 防线齐全的排前面，其次按短历时、小雨量
    out.sort(key=lambda s: -sum(ln["still_pond_count"] is not None for ln in s["lines"]))
    return out


def _queue(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """任务队列甘特：在跑的按实际开始 + 预计结束；排队的在同一对 H100 上接着串行排。

    排队任务没有预计耗时的，结束时间留空，前端只画起点，不编一个时长。
    """
    rows = conn.execute(
        "select id, status, rain_label, storm_preset_key, depth_mm, duration_h, rain_source, "
        "defence_line_key, progress_pct, started_at, ended_at, est_runtime_h, created_at "
        "from run where status in ('running','queued') "
        "order by (status = 'running') desc, created_at, id").fetchall()
    cursor = conn.execute("select now() as t").fetchone()["t"]
    out = []
    for r in rows:
        if r["status"] == "running":
            start = r["started_at"] or cursor
            end = r["ended_at"] or (start + timedelta(hours=float(r["est_runtime_h"]))
                                    if r["est_runtime_h"] else None)
        else:
            start = cursor
            end = start + timedelta(hours=float(r["est_runtime_h"])) if r["est_runtime_h"] else None
        if end is not None:
            cursor = max(cursor, end)
        out.append({**{k: r[k] for k in ("id", "status", "rain_label", "storm_preset_key", "depth_mm",
                                         "duration_h", "rain_source", "defence_line_key", "progress_pct")},
                    "start": start, "end": end})
    return out


def mark_opened(conn: psycopg.Connection, run_id: int) -> None:
    """点「Open ›」：只记第一次打开的时间。"""
    row = conn.execute("update run set opened_at = coalesce(opened_at, now()) where id = %s returning id",
                       [run_id]).fetchone()
    if row is None:
        raise SimulationError(404, NOT_FOUND)


def put_result(conn: psycopg.Connection, run_id: int, result: dict[str, Any]) -> None:
    """算力侧回写结果指标：整行覆盖。"""
    _require_status(conn, run_id, ("done",), ANALYSIS_NEEDS_DONE)
    vals = [result.get(k) for k in RESULT_FIELDS]
    conn.execute(
        f"insert into run_result (run_id, {', '.join(RESULT_FIELDS)}) "
        f"values (%s, {', '.join(['%s'] * len(RESULT_FIELDS))}) "
        f"on conflict (run_id) do update set "
        + ", ".join(f"{k} = excluded.{k}" for k in RESULT_FIELDS) + ", updated_at = now()",
        [run_id, *vals],
    )
