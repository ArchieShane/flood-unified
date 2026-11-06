# 一体化镜像 flood-aio（前端 + 后端 API + PostGIS 数据库软件）

一个容器里跑三样，**只对外一个端口（宿主机默认 8002，映射到容器内 nginx 的 8080）**：

| 进程 | 监听 | 说明 |
|---|---|---|
| nginx | `0.0.0.0:8080`（对外） | 前端静态页（Vue 构建产物）；`/api/*`、`/docs`、`/openapi.json` 反代到 API；其余路径回落 `index.html`（前端是 history 路由） |
| uvicorn（FastAPI） | `127.0.0.1:8101`（仅容器内） | 后端 API，连容器内 `127.0.0.1:5432` |
| PostgreSQL 16 / PostGIS 3.6 / pgrouting | `127.0.0.1:5432`（仅容器内） | `syq-pipe` 库，**库文件在宿主机目录 `./pgdata`** |

前后端同源，不需要 CORS。浏览器打开 `http://<服务器>:8002/`，接口文档 `http://<服务器>:8002/docs`。

## 数据库为什么重启不丢

- 库文件放在宿主机映射的 `./pgdata`（`/var/lib/postgresql/data`）。服务器重启、`docker restart`、删容器重建、升级镜像，`./pgdata` 都不动（已在演练里验证：新建任务 → restart → 删容器重建，任务仍在）。
- 入口脚本**拒绝**在 `PGDATA` 没有映射到宿主机目录 / 具名卷时启动。基础镜像声明了 `VOLUME`，不映射时 docker 会悄悄建匿名卷，脚本专门识别并拒绝它；仅调试可设 `ALLOW_EPHEMERAL=1`。
- `--restart unless-stopped` 让 docker 起来后自动拉起；`--stop-timeout 120`（compose 是 `stop_grace_period: 2m`）让数据库做完检查点再退出（日志里能看到 `checkpoint complete` → `database system is shut down`）；`fsync / synchronous_commit` 保持开启，异常断电也由 WAL 恢复。
- 在线备份：`./backup-db.sh`（`pg_dump -Fc`，不停服务，保留最近 7 份，放 `./backups/`）。建议 cron 每天一次，并把 `backups/` 同步到别的磁盘 / 机器——同一块盘上的备份防不了坏盘。
- **别删 `./pgdata`**。

## 镜像里没有库内容：首次启动怎么建库

镜像只带 PostgreSQL / PostGIS 软件。`./pgdata` 为空（首次启动）时入口脚本 `initdb`，然后建 `syq-pipe`：

| 情况 | 结果 |
|---|---|
| 挂了 `/seed/syq-pipe.dump`（`run.sh` 发现 `./db/syq-pipe.dump` 就自动挂） | `pg_restore` 恢复它（跳过 vector 扩展），再幂等补齐 simulation 表结构 |
| 没有快照（**服务器上还没有库时就是这种**） | 建空库：`simulation` schema 全部表 + 目录数据（操作人 ZN/AL/AR、9 个雨型预设、防线与资源）。`INIT_DEMO_RUNS=1` 才灌设计稿里的演示任务，默认不灌 |

之后每次启动只幂等地执行 `simulation_schema.sql`（只建不存在的对象），所以升级镜像带来的新表 / 新列会自动补上，已有数据不动。

后端运行时只读写 `simulation` schema；管网库（`dmt_irrigationstormwater_abudhabi`）只有离线批算脚本用，页面不需要。

## 构建

```bash
cd backend/docker-aio
./build.sh v1                                          # 构建前端 + 拼上下文 + docker build
docker save flood-aio:v1 | gzip > flood-aio_v1.tar.gz  # 要搬到别的机器时
```

- 基础库镜像默认 `local/postgis:16.11-postgis3.6-pgrouting`（Debian 12，服务器上本地构建的那个），可用 `PG_BASE=` 换；必须是 **Debian 12 (bookworm)** 系，否则和 Python 运行时对不上。
- 构建会 `npm ci && npm run build` 前端（`SKIP_WEB=1` 且 `frontend/dist` 已存在则跳过），构建机要能装 npm / pip / apt 依赖。
- Windows 上签出的脚本可能是 CRLF，`build.sh` 会在进镜像前转成 LF；仓库的 `.gitattributes` 也固定了 `*.sh`、`Dockerfile` 等为 LF。传到 Linux 的脚本别用 CRLF。
- 要把某个现有库做成快照：`PGHOST=... PGPORT=... PGPASSWORD=... ./dump-db.sh` → `db/syq-pipe.dump`。

## 部署与启动（服务器）

```bash
gunzip -c flood-aio_v1.tar.gz | docker load
rsync -a <源>:/.../data/ ./data/          # 数据目录：DEM、processed 批算 CSV、sim_deliveries 仿真成果
./run.sh v1                                 # 或 docker compose up -d（TAG=v1）
docker logs -f flood-aio                    # 看首次建库
curl http://127.0.0.1:8002/healthz          # 探活；页面 http://<服务器>:8002/
```

| 宿主机 | 容器 | 权限 | 说明 |
|---|---|---|---|
| `./data` | `/app/data` | 只读 | 数据目录（约 19 GB，不含 `temp*/` 原始交付） |
| `./data/processed/sim_deliveries` | 同路径 | 可写 | 抽取出的仿真成果；登记时写，页面读 |
| `./cache/sim_ponding` | `/app/data/processed/sim_ponding` | 可写 | 溢水点缓存 |
| `./pgdata` | `/var/lib/postgresql/data` | 可写 | **数据库文件** |
| `./db/syq-pipe.dump` | `/seed/syq-pipe.dump` | 只读 | 可选，仅首次启动恢复用 |
| `/media/pop-fs/flood_data/result` | `/nas` | 只读 | 服务器成果（目录不存在就不挂），用 `../docker-import.sh` 登记 |

环境变量（`.env` 或 `-e`）：`PIPE_DB_PASSWORD`（默认写在镜像里，覆盖即可）、`APP_PORT`（宿主机端口，默认 8002）、`SIM_MOCK_PROGRESS`（接真实算力侧后设 0）、`PG_LISTEN`（要从外面连库才设 `*`，并自己加 `-p 35433:5432`；默认不对外）。

## 登记服务器成果

容器在跑时，`../docker-import.sh` 会直接 `docker exec` 进 flood-aio 做（库就在容器里，写入落进 `./pgdata`）：

```bash
cd backend
./docker-import.sh probe  <主机名> --depth 6
./docker-import.sh import <主机名>/runtime/out/交付/C1_模型版_61mm2h_5m --by ZN
```

### 登记「原生计算结果」（AAM，ANUGA .sww + SWMM）

这类交付（`原生计算结果_可拷贝`，9.7 GB）不走 NAS 登记：在开发机上抽取（`sim_native extract`，结果约 1.3 GB），把抽取结果带到服务器，容器里只做登记。
细节与口径见 `src/stormwater/sim_native.py` 模块头；脚本用法见 `import-native.sh` 头部：

```bash
./import-native.sh AAM_60mm1h_100m.tar.gz --by ZN     # 解到 ./data/processed/sim_deliveries/ → 容器内登记 → 重启清缓存
```

## 升级

```bash
docker load < 新镜像 && ./run.sh <新标签>
```
`run.sh` 删旧容器、起新容器，`./pgdata` 沿用，数据不丢。要重建一个空库：停容器 → **先 `./backup-db.sh`** → 清空 `./pgdata` → 再起。

## 其他

- 数据库用户 `postgres`，口令写在镜像 ENV `PIPE_DB_PASSWORD` 里，别把镜像推到公共仓库。
- 单独只要后端（外部库）用 `../Dockerfile` + `../docker-run.sh`。
