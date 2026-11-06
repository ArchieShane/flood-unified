-- simulation schema 的初始数据，取自高保真设计稿（Run history 列表 + New simulation 向导 4 步）。
-- 目录类数据按自然键 upsert；任务数据只在 run 表为空时灌入，可重复执行。
-- 设计稿上看不到的字段一律留 NULL（如历史任务的雨型时程、潮位、勾选资源），不补猜。
set search_path to simulation;

insert into app_user (initials) values ('ZN'), ('AL'), ('AR')
on conflict (initials) do nothing;

-- ① 雨型预设 3×3
insert into storm_preset (key, depth_mm, duration_h, exact_depth_mm, label, is_design_basis, sort_order) values
    ('33mm_24h', 33, 24, null, '33 mm in 24 h', false, 11),
    ('33mm_2h',  33,  2, null, '33 mm in 2 h',  false, 12),
    ('33mm_1h',  33,  1, null, '33 mm in 1 h',  false, 13),
    ('61mm_24h', 61, 24, null, '61 mm in 24 h', false, 21),
    ('61mm_2h',  61,  2, 61.3, '61 mm in 2 h',  true,  22),
    ('61mm_1h',  61,  1, null, '61 mm in 1 h',  false, 23),
    ('96mm_24h', 96, 24, null, '96 mm in 24 h', false, 31),
    ('96mm_2h',  96,  2, null, '96 mm in 2 h',  false, 32),
    ('96mm_1h',  96,  1, null, '96 mm in 1 h',  false, 33)
on conflict (key) do update set
    exact_depth_mm = excluded.exact_depth_mm, label = excluded.label,
    is_design_basis = excluded.is_design_basis, sort_order = excluded.sort_order;

-- ③ 防线
insert into defence_line (key, label_en, label_zh, subtitle_en, badge_en, sort_order) values
    ('line1',         'Defence line 1',         '第一道防线',        'Existing permanent network',     null,                1),
    ('line2_current', 'Defence line 2',         '第二道防线（现状）', 'Temporary measures in place today', 'Current resources', 2),
    ('line2_planned', 'Defence line 2 Planned', '第二道防线（规划）', 'Planned works counted as built', 'Planned resources', 3)
on conflict (key) do update set
    label_en = excluded.label_en, label_zh = excluded.label_zh,
    subtitle_en = excluded.subtitle_en, badge_en = excluded.badge_en, sort_order = excluded.sort_order;

insert into defence_resource (defence_line_key, key, label_en, label_zh, quantity, sort_order) values
    ('line1', 'pipelines',       'Pipelines',        '管道',       449581, 1),
    ('line1', 'pump_stations',   'Pump stations',    '泵站',           58, 2),
    ('line1', 'outfalls',        'Outfalls',         '排放口',        254, 3),
    ('line1', 'inlets',          'Inlets',           '雨水口',      96400, 4),
    ('line1', 'dams_wadis',      'Dams and wadis',   '坝与旱谷',       31, 5),
    ('line1', 'ditches',         'Ditches',          '明沟',          118, 6),
    ('line1', 'geocells',        'Geocells',         '土工格室',       44, 7),
    ('line1', 'permanent_ponds', 'Permanent ponds',  '永久调蓄池',     17, 8),
    ('line2_current', 'retention_ponds',       'Retention ponds',               '滞留池',             17, 1),
    ('line2_current', 'ditches_surface_pipes', 'Ditches and surface pipelines', '明沟与地表临时管',   41, 2),
    ('line2_current', 'spillways_geocells',    'Spillways and geocells',        '溢洪道与土工格室',   28, 3),
    ('line2_current', 'bunds_barriers',        'Bunds, Jersey and sand barriers', '土堤、防撞墩与沙袋', 96, 4),
    ('line2_current', 'stationed_pumps',       'Stationed pumps',               '驻点泵',             30, 5),
    ('line2_planned', 'temporary_ponds',       'Temporary ponds',               '临时调蓄池',         24, 1),
    ('line2_planned', 'geocells',              'Geocells',                      '土工格室',           61, 2),
    ('line2_planned', 'spillways_diversions',  'Spillways and diversions',      '溢洪道与分流',       21, 3),
    ('line2_planned', 'inlet_pipe_upgrades',   'Inlet and pipe upgrades',       '雨水口与管道改造',  173, 4)
on conflict (defence_line_key, key) do update set
    label_en = excluded.label_en, label_zh = excluded.label_zh,
    quantity = excluded.quantity, sort_order = excluded.sort_order;

-- 任务：列表 7 条 + 向导里的 1 条草稿
do $$
declare
    zn int := (select id from app_user where initials = 'ZN');
    al int := (select id from app_user where initials = 'AL');
    draft_id bigint;
    r bigint;
begin
    if exists (select 1 from run) then
        raise notice 'run 表已有数据，跳过任务种子';
        return;
    end if;

    -- 列表（Run history），时间为阿布扎比本地时间
    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, ended_is_estimate, created_by)
    values ('running', 62, 'design_storm', '61mm_24h', '61 mm in 24 h', 61, 24, 'City-wide', 250, 'line1',
            '2026-09-25 22:00+04', '2026-09-26 09:00+04', true, zn)
    returning id into r;

    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, created_by)
    values ('done', 100, 'design_storm', '61mm_1h', '61 mm in 1 h', 61, 1, 'Abu Dhabi', 250, 'line1',
            '2026-09-24 07:30+04', '2026-09-24 11:20+04', al)
    returning id into r;
    insert into run_analysis (run_id, kind) values (r, 'validation');

    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, created_by)
    values ('done', 100, 'design_storm', '61mm_2h', '61 mm in 2 h', 61, 2, 'City-wide', 250, 'line2_current',
            '2026-09-23 21:52+04', '2026-09-24 01:42+04', zn)
    returning id into r;
    insert into run_analysis (run_id, kind, gap_hours) values (r, 'gap', 4), (r, 'gap', 8);

    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, created_by)
    values ('done', 100, 'design_storm', '61mm_24h', '61 mm in 24 h', 61, 24, 'City-wide', 250, 'line2_current',
            '2026-09-23 18:10+04', '2026-09-23 22:00+04', zn)
    returning id into r;
    insert into run_analysis (run_id, kind, gap_hours) values (r, 'gap', 4);

    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, created_by)
    values ('done', 100, 'design_storm', '33mm_1h', '33 mm in 1 h', 33, 1, 'City-wide', 250, 'line2_planned',
            '2026-09-22 21:40+04', '2026-09-23 01:30+04', zn);

    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, created_by)
    values ('done', 100, 'design_storm', '33mm_2h', '33 mm in 2 h', 33, 2, 'Abu Dhabi Island', 250, 'line1',
            '2026-09-22 18:05+04', '2026-09-22 21:55+04', al)
    returning id into r;
    insert into run_analysis (run_id, kind) values (r, 'validation');

    insert into run (status, progress_pct, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     extent, grid_m, defence_line_key, started_at, ended_at, created_by)
    values ('done', 100, 'design_storm', '33mm_24h', '33 mm in 24 h', 33, 24, 'Al Bateen', 10, 'line1',
            '2026-09-20 11:20+04', '2026-09-20 15:10+04', zn);

    -- 向导 4 步填完、尚未提交的草稿（Draft 徽标）
    insert into run (status, rain_source, storm_preset_key, rain_label, depth_mm, duration_h,
                     temporal_pattern, peak_position_pct, spatial_distribution,
                     extent, grid_m, terrain, tide_level_m, tide_datum, tide_label, gate_close_above_m,
                     model_start_utc, after_rain_mode, after_rain_max_h,
                     baseflow_pct, baseflow_min_dia_mm, baseflow_note,
                     defence_line_key, queue_all_lines,
                     est_run_count, est_runtime_h, compute_target, est_ready_at)
    values ('draft', 'design_storm', '61mm_2h', '61 mm in 2 h', 61.3, 2,
            'Zone B official DDF alternating-block (2022)', 40, 'Uniform citywide',
            'City-wide', 250, '5 m DTM', 1.3, 'NADD', 'HAT 1.3 m NADD', 1.3,
            '2026-09-28 00:00+00', 'until_drained', 36,
            20, 600, 'Agreed with IDD 21 Sep',
            'line1', false,
            1, 4, 'H100 pair', '2026-09-26 04:00+04')
    returning id into draft_id;

    -- 草稿只勾选了第一道防线的 8 类资源
    insert into run_resource (run_id, resource_id)
    select draft_id, id from defence_resource where defence_line_key = 'line1';
end $$;
