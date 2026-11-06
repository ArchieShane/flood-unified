set search_path to simulation;
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
