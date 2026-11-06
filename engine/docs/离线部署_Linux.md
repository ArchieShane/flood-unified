# 离线部署（Linux 服务器 + Docker）

2026-10-03 · 适用：服务器完全离线、只在内网访问、要在服务器上跑一二维计算、之后会换更高精度的地形。

## 一、上传包里有什么

开发机上 `python tools/make_release.py --with-data` 生成**一个目录** `swbrain-<版本>/`，整个传到服务器即可（`data/ work/ runtime/` 里的文件是硬链接，不额外占开发机磁盘）：

```
swbrain-<版本>/
  images/                     三个镜像（docker save 出来的 tar）
    swbrain-app-<版本>.tar      服务（代码 + 依赖 + 数据快照种子）
    swbrain-db-<版本>.tar       离线数据库（pipe / makani / liveability 用到的表已烤进镜像）
    anuga-swmm-1.0.tar          一二维计算
  docker-compose.yml  .env.example  config.package.toml  start.sh  rebuild-state.sh  manifest.json  部署说明.md
  data/    → 服务器 DATA_DIR，只读挂进容器 /data
    gdb/        客户雨水管网 GDB（zip 与解压版）
    dtm/        5 m DTM（换更高精度地形就是替换这里）
    hotspots/   热点工作簿 workbooks/、506 Hot Spots AUH.kmz 等
    client_models/  客户 22 个水力模型（zip 与解压版）
    hydraulic_model_export/SWMM_Export/Mussafah_00.inp
    parsons/    Parsons 交付
  work/    → 服务器 WORK_DIR，读写挂进容器 /work：一二维工作目录（已有的模型与计算结果，流程可续算）
  runtime/ → 服务器 RUNTIME_DIR：out/交付/（已有的一二维交付成果）；config.toml、out/state 等在首次启动时生成
```

**数据库怎么处理**：不连客户库，也不用服务器上装 PostgreSQL。
- 服务运行时本来就不连库，它读 `out/state/*.parquet`（`swbrain.run` 的运行产物），这份快照已打进 app 镜像（`/app/seed`），首次启动补进 `RUNTIME_DIR/out`、`console_data`、`exchange`，**只补没有的，不覆盖**。
- 重算（换 DTM 后）要读源表：`swbrain-db` 镜像里已有 pipe / makani / liveability 三个库中代码用到的表（`tools/make_db_snapshot.py` 导出，共约 3.5 GB），只在 compose 内网里，不发布端口。
- flood 库只用于生成控制台图层，结果已在快照里，没有带。

## 二、服务器安装（离线）

前提：Docker Engine ≥ 24 与 compose 插件已装；服务器上有 `bash`。

```bash
cd swbrain-<版本>
bash start.sh          # 第一次：生成 .env（含随机数据库口令）后退出
vi .env                # 改 DATA_DIR / WORK_DIR / RUNTIME_DIR；WORK_DIR 必须纯英文、无空格
bash start.sh          # 导入三个镜像 → 建目录 → 放配置 → 启动 → 自检
```

目录规划（示例）：把上传包里的 `data/ work/ runtime/` 移到（或直接就是）`/opt/swbrain/` 下，`.env` 里三个路径指向它们。

启动后：
- 控制台 `http://<服务器内网IP>:8766/`，管理端 `/admin.html`；
- 先在 `RUNTIME_DIR/config.toml` 的 `[serve]` 里设 `access_key`、`admin_key`，再 `docker compose restart swbrain`（服务是明文 HTTP，只适合可信内网）；
- 快速验证（约 2 分钟，不需要任何数据）：`docker compose exec swbrain python tools/c1_pipeline.py selftest`；
- 日志 `docker compose logs -f swbrain`，停止 `docker compose down`。

## 三、宿主机映射一览

| 宿主机 | 容器内 | 读写 | 内容 |
|---|---|---|---|
| `RUNTIME_DIR/config.toml` | `/app/config.toml` | 读 | 配置与口令 |
| `DATA_DIR` | `/data` | **只读** | 客户原始数据、DTM |
| `WORK_DIR` | `/work`（**同路径**也挂给计算容器） | 读写 | 一二维模型、计算结果、日志 |
| `RUNTIME_DIR/out` | `/app/out` | 读写 | 数据快照 `out/state`、各类产物、`out/交付` |
| `RUNTIME_DIR/console_data` | `/app/console/data` | 读写 | 控制台数据 |
| `RUNTIME_DIR/exchange` | `/app/exchange` | 读写 | 与预测侧交换的 JSON、热点清单 |
| `/var/run/docker.sock` | 同 | — | 在宿主机上起 `anuga-swmm` 计算容器 |

容器里没有任何需要保留的数据：删掉容器重建，宿主机上的东西都在。

## 四、一二维计算在服务器上跑

计算由服务容器通过 `docker.sock` 在宿主机上另起 `anuga-swmm:1.0` 容器，挂载的是 `WORK_DIR`（宿主机真实路径，已由 compose 的 `SWBRAIN_SWMM_WORK_HOST` 传入）。

```bash
docker compose exec swbrain python tools/c1_pipeline.py check --tag M61     # 数据与每一步检查
docker compose exec swbrain python tools/c1_pipeline.py run all --tag M61   # 续算（已有产物的步骤跳过）
```

也可以在管理端批准计算申请后发起。
**安全提示**：挂 `docker.sock` 等于给了服务容器宿主机的管理权限，所以务必设 `access_key` / `admin_key`、只对内网开放。

## 五、换更高精度的地形（DTM）

1. 把新 DTM（GeoTIFF，EPSG:32640）放进 `DATA_DIR/dtm/`；如果文件名变了，改 `RUNTIME_DIR/config.toml` 的 `[terrain].dtm_path`（容器内路径 `/data/dtm/<新文件名>`）。
2. 分辨率变了，同时看 `[terrain.catchment]` 的 `res_m`、`tile_m`、`buffer_m` 等参数是否要跟着调（格网太细、瓦片太大会很慢、很占内存）。
3. 重算数据快照：`bash rebuild-state.sh`。它先备份旧快照到 `RUNTIME_DIR/out/state.bak-<时间>`，删掉 DTM 相关缓存（`vertex_dtm.parquet`、`inlet_catchments.*`——这两个缓存只看管网与参数是否变化，**不看 DTM 文件换没换**，不删就不会按新地形重采），再离线跑 `python -m swbrain.run --no-db`，最后重启服务。
4. 一二维计算用的 DTM：`c1_pipeline.py` 的各步骤会从 `[terrain].dtm_path` 重新读，已有的模型文件（`WORK_DIR` 里的 `.inp`、`dem.tif`）不会自动更新，要用 `--force` 重跑对应步骤。

## 六、升级与回滚

- 升级：把新版 `images/*.tar` 与 `docker-compose.yml`、`.env` 的 `VERSION` 换掉，`bash start.sh`。`RUNTIME_DIR` 里已有的 `out/state` 不会被新镜像里的种子覆盖；要用新种子，先把旧的移走。
- 回滚：`.env` 的 `VERSION` 改回旧版本，`docker compose up -d`。
- 备份：`RUNTIME_DIR` 与 `WORK_DIR`。
