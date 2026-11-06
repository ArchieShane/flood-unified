flood-viewer 更新（2026-10-17）：加艾因（Al Ain）250 m 推演。只换 server.py，镜像不用重建。
  cd /opt/flood-viewer            # 有 docker-compose.yml 和 .env 的那个目录
  cp server.py server.py.bak 2>/dev/null; # 把本包的 server.py 拷到这里覆盖
  docker compose up -d
  curl -s localhost:8002/api/rescan   # 端口按 .env 里的 PORT
注意：本 server.py 是仓库里最新的一份，同时带 ADM 30 m（ad_）。如果你线上的 server.py 比这份新，请只合并这三处：
  1) REGION_LABELS["aa_alain"] = "Al Ain 艾因"   2) STATS_FILES 加 "aa_stats.json"   3) side_lookup 里 startswith(("ad_", "aa_")) 直接 return None
数据：把容器里的逐帧图目录拷到 NAS（目录名必须带 g250）：
  docker cp swbrain:/app/console/data/flood2d_aa_alain_m61h1 /media/pop-fs/flood_data/result/<主机名>/runtime/console_data/flood2d_aa_alain_m61h1g250
验证：区域下拉框多出 "Al Ain 艾因"，格网 250 m，雨量 61 mm / 1 h；有 L1/L2 两态、统计、水量对账、L2−L1 差值图；没有设施清单 / 雨型 / 热点过程线。

--- 追加（艾因指标卡只有一张时）---
app.js（页面前端，镜像里是旧版）：没有热点分区的区域，指标卡多列 ≥0.10 m 面积、最大水深、最大积水量。C1 / 三区域 / ADM 的显示不变。
把 app.js 放到 docker-compose.yml 同目录，在 volumes: 里 server.py 那行下面加一行（缩进一致）：
      - ./app.js:/app/web/app.js:ro
然后 docker compose up -d --force-recreate，浏览器 Ctrl+F5。
回退：删掉这一行再 up -d。
