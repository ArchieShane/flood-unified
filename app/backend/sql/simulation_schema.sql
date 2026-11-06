-- 仿真任务（New simulation 向导 + Run history 列表）的落库结构。
-- 库：syq-pipe；schema：simulation。可重复执行（只建不存在的对象）。
--
-- 对应 UI：
--   列表  Run history —— simulation.run（一行一个任务）+ run_analysis（Analyses 列的标签）
--   向导① Rain scenario        —— run 的 rain_* / storm_* 列，预设雨型见 storm_preset
--   向导② Extent and boundary  —— run 的 extent / tide / baseflow 等列
--   向导③ Lines of defence     —— run.defence_line_key + run_resource（勾选了哪些资源）
--   向导④ Confirmation         —— run 的 est_* 列（预计耗时、算力、就绪时间）
-- 时间一律 timestamptz；列表上的时间按阿布扎比本地时间（+04）录入，模型起算时刻按 UTC。

create schema if not exists simulation;
set search_path to simulation;

-- 操作人（列表 By 列的头像缩写）
create table if not exists app_user (
    id           serial primary key,
    initials     text not null unique,          -- ZN / AL
    display_name text,
    created_at   timestamptz not null default now()
);

-- 雨型预设：向导①的 3×3 格子（总雨量 × 历时）
create table if not exists storm_preset (
    key             text primary key,           -- 如 61mm_2h
    depth_mm        numeric(6,1) not null,      -- 名义雨量（格子上的整数）
    duration_h      numeric(5,2) not null,
    exact_depth_mm  numeric(6,1),               -- 实际雨深（设计基准 61.3 mm）
    label           text not null,              -- 61 mm in 2 h
    is_design_basis boolean not null default false,
    sort_order      int not null default 0,
    unique (depth_mm, duration_h)
);

-- 防线：Defence line 1 / line 2 现状 / line 2 规划
create table if not exists defence_line (
    key         text primary key,               -- line1 / line2_current / line2_planned
    label_en    text not null,
    label_zh    text not null,
    subtitle_en text,
    badge_en    text,                           -- Current resources / Planned resources
    sort_order  int not null default 0
);

-- 各防线纳入仿真的资源目录及数量
create table if not exists defence_resource (
    id               serial primary key,
    defence_line_key text not null references defence_line(key),
    key              text not null,
    label_en         text not null,
    label_zh         text not null,
    quantity         int not null,
    sort_order       int not null default 0,
    unique (defence_line_key, key)
);

-- 仿真任务主表
create table if not exists run (
    id                   bigserial primary key,
    status               text not null default 'draft'
                         check (status in ('draft','queued','running','done','failed','cancelled')),
    progress_pct         numeric(5,2) check (progress_pct between 0 and 100),
    batch_id             uuid,                  -- 「Queue all three」一次排出的几个任务共用

    -- ① 降雨情景
    rain_source          text not null default 'design_storm'
                         check (rain_source in ('design_storm','observed_replay','ncm_forecast')),
    storm_preset_key     text references storm_preset(key),
    rain_label           text not null,         -- 列表 Rain Scenario 列
    depth_mm             numeric(6,1),
    duration_h           numeric(5,2),
    temporal_pattern     text,
    peak_position_pct    numeric(5,2) check (peak_position_pct between 0 and 100),
    spatial_distribution text,

    -- ② 范围与边界
    extent               text not null,         -- City-wide / Abu Dhabi / Abu Dhabi Island / Al Bateen
    grid_m               numeric(6,1) not null,
    terrain              text,                  -- 5 m DTM
    tide_level_m         numeric(5,2),          -- HAT 1.3
    tide_datum           text,                  -- NADD
    tide_label           text,                  -- HAT 1.3 m NADD
    gate_close_above_m   numeric(5,2),
    model_start_utc      timestamptz,
    after_rain_mode      text check (after_rain_mode in ('until_drained','fixed')),
    after_rain_max_h     numeric(5,1),
    baseflow_pct         numeric(5,2),
    baseflow_min_dia_mm  int,                   -- 基流只加在 ≥ 600 mm 的干管上
    baseflow_note        text,                  -- AGREED WITH IDD 21 SEP

    -- ③ 防线口径
    defence_line_key     text not null references defence_line(key),
    queue_all_lines      boolean not null default false,

    -- ④ 确认页预估
    est_run_count        int,
    est_runtime_h        numeric(5,1),
    compute_target       text,                  -- H100 pair
    est_ready_at         timestamptz,

    -- 运行时间
    started_at           timestamptz,
    ended_at             timestamptz,
    ended_is_estimate    boolean not null default false,   -- 运行中任务的 End Time 带 est.

    created_by           int references app_user(id),
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);
create index if not exists run_status_idx     on run (status);
create index if not exists run_created_by_idx on run (created_by);
create index if not exists run_started_at_idx on run (started_at desc);

-- 任务勾选的防线资源（向导③的复选框）
create table if not exists run_resource (
    run_id      bigint not null references run(id) on delete cascade,
    resource_id int    not null references defence_resource(id),
    primary key (run_id, resource_id)
);

-- 任务的后续分析（列表 Analyses 列：Validation / Gap 4 h / Gap 8 h）
create table if not exists run_analysis (
    id        bigserial primary key,
    run_id    bigint not null references run(id) on delete cascade,
    kind      text not null check (kind in ('validation','gap')),
    gap_hours numeric(5,1),
    check ((kind = 'gap') = (gap_hours is not null)),
    constraint run_analysis_uniq unique nulls not distinct (run_id, kind, gap_hours)
);

-- 2026-09-26 补：实测降雨回放要记下回放的是哪场事件（向导① Observed event replay）
alter table run add column if not exists replay_event_ref text;

-- 2026-09-26 补：Validation 的 gap_hours 为空，普通 unique 挡不住重复，改成 nulls not distinct
alter table run_analysis drop constraint if exists run_analysis_run_id_kind_gap_hours_key;
do $$ begin
    if not exists (select 1 from pg_constraint where conname = 'run_analysis_uniq') then
        alter table run_analysis add constraint run_analysis_uniq unique nulls not distinct (run_id, kind, gap_hours);
    end if;
end $$;

-- 2026-09-26 补（首页 home v2）：
-- ① 「New results」= 本周完成、还没人点开过的任务，要记首次打开时间
alter table run add column if not exists opened_at timestamptz;

-- ② Analyses 标签多一种 ERP（行动方案）
alter table run_analysis drop constraint if exists run_analysis_kind_check;
alter table run_analysis add constraint run_analysis_kind_check check (kind in ('validation','gap','erp'));

-- ③ 任务结果的汇总指标（一任务一行，由算力侧回写；没算出来的指标留 NULL）
--    still_pond_count 喂「Same storm」柱状图与 New results 卡片；validation_* 喂 Validation KPI。
--    产品评论 #8：L1 / L2 对比口径是排水量、管井点溢出数、积水时长，三列一并预留。
create table if not exists run_result (
    run_id                  bigint primary key references run(id) on delete cascade,
    still_pond_count        int check (still_pond_count >= 0),
    drained_volume_m3       numeric(14,1),
    overflow_node_count     int check (overflow_node_count >= 0),
    ponding_duration_h      numeric(6,2),
    validation_event        text,               -- 回放的实测事件，如 March 2026
    validation_hit_rate_pct numeric(5,2) check (validation_hit_rate_pct between 0 and 100),
    validation_missed       int,
    validation_false        int,
    updated_at              timestamptz not null default now()
);

-- 2026-09-26 补：任务挂结果图层。记算力侧交付目录相对 backend/data/ 的路径（只读，见 sim_results.py）；
--   详情页据此落最大水深与逐 5 min 时序快照。同一份交付只登记一次。
alter table run add column if not exists output_dir text;
create unique index if not exists run_output_dir_uniq on run (output_dir) where output_dir is not null;

-- 2026-09-27 补：同一场雨、同一道防线下有几个方案时的区分名（中文原串，英文走 i18n.EN）。
--   第一份是 Mussafah_00 四态交付：第二道（规划）同时有 L2+A Parsons 方案与 L2+B 本系统箱涵。见 sim_sww.py。
alter table run add column if not exists variant_label text;

-- 2026-09-28 补：防线口径（列表「防线口径」列的值）落库。单态「Line1」「Line1 ｜ Line2」「Line1 ｜ Line2 planned」，
--   多态按各态列出「Line1 / Line2 / Line2 planned / Line2 suggested」；全市 #100–102 挂了第二道视图写「Line1 ｜ Line2」。
--   由 simulation.write_defence_basis() 在建草稿、改草稿、提交、登记交付时写入；存量行跑一次它回填。
alter table run add column if not exists defence_basis text;

-- 2026-10-18 补：库里存的数据名一律英文（run.extent 取自交付目录名、variant_label 取自态标签，早先写进了中文）。
--   幂等：已是英文的行不受影响；新写入的行由 sim_c1.EXTENT_EN / 各 sim_*.STATES 在源头给英文。
update run set extent = case extent
        when '艾因' then 'Al Ain' when '阿尔艾因' then 'Al Ain' when '阿布扎比' then 'Abu Dhabi'
        when '阿布扎比岛' then 'Abu Dhabi Island' when '全市' then 'City-wide' when '扎夫拉' then 'Al Dhafra'
        else extent end
 where extent in ('艾因', '阿尔艾因', '阿布扎比', '阿布扎比岛', '全市', '扎夫拉');
update run set variant_label = regexp_replace(
        replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(variant_label,
          '已确认雨水塘（1 座）', 'Confirmed rain pond (1)'),
          '已建成调蓄（≡ L1）', 'Built storage (≡ L1)'),
          '已建成塘（22 座）', 'Built ponds (22)'),
          '规划方案 + 本系统箱涵', 'Planned scheme + our box culverts'),
          '规划方案', 'Planned scheme'),
          '现状管网 + 现状泵站', 'Existing network + existing pump stations'),
          '加 OP-02 移动泵', 'Plus OP-02 mobile pumps'),
          '转输与地面取水泵', 'Transfer and surface-intake pumps'),
          'Parsons 方案', 'Parsons scheme'),
          '既有管网', 'Existing network'),
          '已建成调蓄', 'Built storage'),
        '^(\d+) 态：', '\1 states: ')
 where variant_label ~ '[一-鿿]';
