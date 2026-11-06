# 洪水推演成果展示（flood-viewer）

只读网页，展示三台服务器算完、同步到 NAS 的 1D/2D 推演成果。**和原控制台（thinking-drainage-system/console）完全独立**，不读数据库、不起计算，只读 NAS 目录。

页面只有三个下拉框：**区域 → 格网精度 → 模拟雨量**。选完后展示（内容同原 C1 页）：四个态页签（L1 / L2 / L2+planned / L2+suggested）、关键指标和对比基准、逐时播放 / 最大水深 / 差值图、积水面积曲线、雨型、三个热点水深过程、各热点表、设施清单与地图符号。

## 部署

```bash
cd flood-viewer
cp .env.example .env          # RESULT_DIR 缺省 /media/pop-fs/flood_data/result，PORT 缺省 8002
docker compose up -d --build
```

访问 `http://<服务器IP>:8002/`；`/api/health` 看扫描结果（扫到几个情景、哪些目录被跳过、哪些情景在多台机器上重复）。

**服务器没有外网**（本项目的实际情况）：构建和运行都不需要联网，两条路选一条：
1. 在服务器上用已有镜像当基础构建：`docker images` 找一个带 python 的（如 `swbrain-app:<版本>`），在 `.env` 里写 `BASE_IMAGE=swbrain-app:<版本>`，再 `docker compose up -d --build`。
2. 在能联网的机器上构建后搬过去：`docker build -t flood-viewer:1.0 .` → `docker save flood-viewer:1.0 | gzip > flood-viewer.tar.gz` → 拷到服务器 `docker load < flood-viewer.tar.gz` → `docker compose up -d`（把 compose 里 `build:` 一段删掉，免得再构建）。

Leaflet 已内置在 `web/vendor/`。**底图由客户的浏览器直接取在线瓦片**（缺省 OpenStreetMap，服务器本身不需要外网），所以只要客户端能上网就有电子地图。要换底图源，设环境变量 `TILE_URL`（compose 的 environment 里加一行，如高德 / 天地图的瓦片地址模板 `…/{z}/{x}/{y}…`）；客户端也没网时，把离线瓦片（`{z}/{x}/{y}.png`）放到一个目录、挂成 `/tiles`（compose 里有注释示例），会优先用它；`TILE_URL` 设为空则是纯色背景。

## 数据目录约定

容器内 `/data` = NAS 的 `result/`，即 `sync_to_nas.sh` 的输出：

```
result/<主机名>/runtime/console_data/flood2d_<区域>_<情景>/{manifest.json, stats.json, c1_stats.json|ms_stats.json, L1/f000.png …}
result/<主机名>/runtime/console_data/{c1_hyeto.json, c1_hs_series.json}
```

- 目录名 `flood2d_<区域>_m<雨量>[h<历时>][g<网格>]`：`c1_m61` = C1、61 mm / 2 h、15 m；`c1_m61h1g5` = C1、61 mm / 1 h、5 m。区域代码 `c1` = C1，`ms` = Mussafah_00，其他代码直接显示大写（要改名用 compose 的 `REGION_LABELS`）。
- 只扫 `<主机名>/runtime/console_data`（也兼容 `<主机名>/console_data`、`/data` 直接放 `flood2d_*`），不进 `work/`，所以 NAS 上几十 GB 的 sww / npz 不受影响。
- 同一情景（区域 + 网格 + 雨量）在多台机器上都有时，取 `manifest.json` 最新的一份（服务器 2 重算的 M61 / M96H1 会盖过服务器 1 的旧结果）；冲突清单见 `/api/health`。
- 不展示：雨量在 `EXCLUDE_MM`（缺省 32）且网格在 `EXCLUDE_GRID`（缺省 15 m）里的目录——客户把 32 mm 更正为 33 mm，15 m 的 32 mm 旧结果已下架（5 m 的 32 mm 保留展示）（列在 `/api/health` 的 `skipped`）；旧口径目录（`flood2d_c1_r61`、`flood2d_c1_u61`、`flood2d_c1_d24` 等，名字不符合上面的规则）、缺 `manifest.json` 或统计文件的目录（同样列在 `/api/health` 的 `skipped`）。
- `c1_hyeto.json` / `c1_hs_series.json` 每台机器只含本机跑过的情景，服务按键合并各台的文件；5 m 情景（`…G5`）沿用同雨量 15 m 版的雨型与热点过程线键。缺了就在页面上显示「没有数据」，不影响其他内容。
- 新成果同步到 NAS 后，最迟 `SCAN_TTL`（缺省 120 s）出现在下拉框里；要立刻看就开 `/api/rescan`。

## 本机试跑（不用 docker）

```powershell
$env:DATA_ROOT = "D:\某目录\result"; $env:PORT = "8081"
python server\server.py        # 然后开 http://127.0.0.1:8081/
```

## 文件

| 文件 | 作用 |
|---|---|
| `server/server.py` | 标准库 HTTP 服务：目录扫描、`/api/catalog`、`/api/scenario`、`/data/…`（逐帧 PNG，带 ETag 缓存）、`/api/health`、`/api/rescan` |
| `web/index.html` `app.js` `style.css` | 单页前端（原生 JS + Leaflet） |
| `Dockerfile` `docker-compose.yml` | 基础镜像可换（`BASE_IMAGE`），NAS 只读挂载，可选离线瓦片 |

## 已知局限

- Mussafah_00 的 `ms_stats.json` 没有设施清单、雨型、热点过程线（计算流程里还没生成），对应区块显示为空或「没有数据」；它的态名称由统计文件给出。Mussafah 15 m 推演 10-10 才开跑，算完同步后会自动出现。
- 本机（Windows）没有跑起 Docker daemon，镜像尚未在本机构建验证；服务本身已用 C1 的 15 m 数据在本机验证过。
