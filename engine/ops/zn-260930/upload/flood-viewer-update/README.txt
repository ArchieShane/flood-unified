flood-viewer 更新（2026-10-12）：加三区域（Yas bridges / Al Bateen / Bani Yas，15 m，L1+L2）。只换 server.py 和 docker-compose.yml，镜像不用重建。
  cd /opt/flood-viewer            # 有 docker-compose.yml 和 .env 的那个目录
  cp docker-compose.yml docker-compose.yml.bak
  # 把本包的 server.py、docker-compose.yml 拷到这里覆盖
  docker compose up -d
  curl -s localhost:8002/api/rescan   # 端口按 .env 里的 PORT
验证：区域下拉框多出 Yas bridges interchange / Al Bateen / Bani Yas，格网只有 15 m，雨量 Yas 9 个、Al Bateen 9 个、Bani Yas 9 个，合计 27 个情景。
这三个区域没有设施清单 / 雨型 / 热点过程线，对应块为空；有 L1/L2 两态、统计、水量对账、L2−L1 差值图。
回退：cp docker-compose.yml.bak docker-compose.yml && docker compose up -d
