艾因 250 m · 2026-10-18 更新包：塘的口径改为「排进塘的那根管在 L1 里不是出水口（水憋在管网里），L2 接进塘」+ 自动收尾

口径（用户 10-18 定）：
  · Sayh Al Saad 塘（POND 图层）的进水管（客户出水口 SW_ASA_SAS_OFL_400，落在塘上）：L1 里不是出水口（末端封闭），L2 接进塘
  · 另 13 个点塘（gpkg）不接一维（出水口去向不明），只靠占地内二维地表水进塘；不再有溢流堰
  · L1 口径变了，所以 L1、L2 都要重算

用法（服务器上）：
  1. 解压本目录；把 6 个 .py/.sh 文件放到 /mnt/AI/flood/swbrain/aa_250m/aa_250m/ ，把 cases/alain/ 下 3 个 json 放到 /mnt/AI/flood/swbrain/aa_250m/aa_250m/cases/alain/ ，覆盖同名旧文件
  2. cd /mnt/AI/flood/swbrain/aa_250m/aa_250m
  3. TAGS=ALAIN_M61H1 bash aa_250m_server.sh rerun-l2     # 命令名沿用，现在重算 L1 + L2；后台约 2 小时（脚本开头自带自检，不用单独跑 selftest）
     自动：自检 → 备份旧结果 → 重建网络缓存 → build → L1/L2 一二维 → 后处理 → 补热点/积水点 → 验收(L2 溢水量 < L1) → 转不过滤 shp+dat → 进 viewer → 交付拷 NAS → 核对
  4. 进度：bash aa_250m_server.sh status ；tail -f aa_250m_server.driver.log
  5. 算完：TAGS=ALAIN_M61H1 bash aa_250m_server.sh verify

本机验证（anuga-swmm:1.0 真 SWMM，与服务器同镜像）：
  · 最小网络自检：L1（末端封闭）冒水 0.337，L2（接塘）0.187，塘蓄水 0.15 → L2 < L1，通过
  · 真实艾因数据 prep + build：L1 的 .inp 里 OUT_349592_2677416 与 of_349592_2677416 不存在；L2 里 PI_ST_349606_2677365_349592_2677416（DN1400）接进塘；无溢流堰；13 个点塘无一维连接；SWMM 读入 L1、L2 均 OK
  · 真实艾因数据：切出排进这根管的连通管网块（428 个节点）单独跑真 SWMM：L1 地面冒水 5,519 m³，L2 54 m³
  · 未在本机验证：整网耦合后的完整结果（rerun-l2 的验收把关：L2 溢水量必须 < L1，否则停在验收，不转不发布）
注意：塘的体量相对全域溢水量（约 2,370 万 m³）很小，L2 比 L1 少的量约在万 m³ 量级，页面上的百分比会很小。
