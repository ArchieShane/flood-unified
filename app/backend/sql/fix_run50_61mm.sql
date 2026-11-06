-- 把 simulation.run 中编号 50 的任务从「60 mm in 1 h」改为「61 mm in 1 h」。
-- #50 是 AAM_60mm1h_100m 整份交付（L1 / L2 是它的两个态，同属一条任务），库里只有这一行。
-- 只改记录的展示值，不会重新推演；结果数字仍是按 60 mm/h 算出的。
-- storm_preset_key 保持空：这是均匀雨，不是 Zone B 交替块雨型，挂 61mm_1h 预设会让雨型图画错。
-- 可重复执行：已经是 61 时 UPDATE 匹配 0 行，不会误改。

\set ON_ERROR_STOP on
\echo '=== 1. 改之前（应是 1 行：60.0 / 1.00 / R60D1，storm_preset_key 为空）'
select id, status, storm_preset_key, rain_label, depth_mm, duration_h, temporal_pattern, output_dir
from simulation.run where id = 50;

begin;

update simulation.run
   set depth_mm         = 61.0,
       rain_label       = '61 mm in 1 h',
       temporal_pattern = 'uniform 61 mm/h for 1 h (R61D1)',
       updated_at       = now()
 where id = 50
   and depth_mm = 60.0
   and duration_h = 1.00
   and temporal_pattern = 'uniform 60 mm/h for 1 h (R60D1)';
-- 上面应显示 UPDATE 1；若是 UPDATE 0，说明 #50 的现值与预期不符，请 rollback 并把第 1 步的输出发我。

\echo '=== 2. 改之后（提交前预览）'
select id, status, storm_preset_key, rain_label, depth_mm, duration_h, temporal_pattern, updated_at
from simulation.run where id = 50;

-- 确认无误就保留 commit；想放弃（或只预览）就把 commit 改成 rollback。
commit;

\echo '=== 3. 提交后复查'
select id, rain_label, depth_mm, duration_h, temporal_pattern from simulation.run where id = 50;

-- 用法（容器名 flood-aio，库 syq-pipe）：
--   docker cp fix_run50_61mm.sql flood-aio:/tmp/
--   docker exec -i flood-aio psql -U postgres -d syq-pipe -f /tmp/fix_run50_61mm.sql
-- 非容器部署：
--   psql -h <主机> -p <端口> -U postgres -d syq-pipe -f fix_run50_61mm.sql
-- 改完后后端若有缓存，重启一次 flood-aio 容器即可（docker restart flood-aio）。
