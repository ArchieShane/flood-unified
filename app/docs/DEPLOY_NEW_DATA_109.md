# 新增一份仿真数据 → 在 109 服务器上导入 flood-aio 服务

> 场景：拿到一份新的仿真交付（与此前导入过的同一类形状），**直接拷到 10.255.254.109 上，在 109 上导入**，页面即可看到。
> 不经过 NAS、不需要重建镜像、不需要停服务。不涉及 RDA 数据展示逻辑整合，也不涉及积水点台账 / 逐点批算（`data/processed` 那一套）。
>
> 依据：`backend/docker-aio/*.sh`、`backend/src/stormwater/sim_import.py / sim_c1.py / sim_sww.py / sim_city.py / sim_native.py / sim_results.py`、`docs/SIMULATION_API.md`。
> 我没连过 109，下面凡是仓库里查不到的（部署目录、SSH 账号、磁盘余量）标了「待核对」。

---

## 1. 先理解：导入到底做了什么

109 上跑的是一体化容器 `flood-aio`（nginx + API + PostgreSQL 同一个容器，对外 `8002`）。

| 东西 | 位置 | 说明 |
|---|---|---|
| 部署目录 | `<部署目录>/docker-aio`（待核对） | `run.sh`、`data/`、`pgdata/` 都在这里 |
| 数据库 | 容器内 `syq-pipe` 的 `simulation` schema，库文件在宿主机 `pgdata/`（**别删**） | 导入 = 往 `simulation.run` 写一行 |
| 抽取结果 | 宿主机 `data/processed/sim_deliveries/<键>/` = 容器 `/app/data/processed/sim_deliveries/<键>/`（可写挂载） | **页面读的是这里** |
| 溢水点缓存 | 宿主机 `cache/sim_ponding` | 首次打开溢水点现算，之后读缓存 |

**导入一份交付 = 两步，都在容器里完成**：
1. **抽取**：读你拷来的交付原件，把二维结果聚成落图格网，写成页面认的格式放进 `sim_deliveries/<键>/`；
2. **登记**：在 `simulation.run` 里新建一行（按 `output_dir` 去重，重复执行 = 更新同一行，不会出现重复任务）。

交付原件**只读不改**。导入完成后页面只依赖抽取结果；原件留着只为将来重跑 / 更新。

---

## 2. 先判断是哪一种交付

| 形状 | 目录名例 | 怎么导入 |
|---|---|---|
| **一二维交付**（绝大多数：C1、Mussafah 模型版、Yas / Al Bateen / Bani Yas、旧 .sww、全市） | `C1_模型版_61mm2h_5m`、`Mussafah_00_模型版_61mm2h`、`全市_61mm2h` | §4，`sim_import` 自动选抽取口径 |
| **原生计算结果**（ANUGA `.sww` + SWMM `.rpt`，如 AAM） | `原生计算结果_可拷贝` | §7 |

形状由程序判（`sim_import.detect`），不用凭名字猜：有 `请先阅读_原生结果说明.txt` + `配套参数与标识/` + `L1/*_2D.sww` → 原生；名字以「全市」开头 → 全市；态目录下 `二维/` 有 `*_二维水深时序.sww` → 旧 .sww；其余 → 一二维 npz。

---

## 3. 导入前：核对交付 & 把它放到 109 上

### 3.1 交付必须满足

1. **目录名**是 `<片区>_<雨深>mm<历时>h[_<网格>m | _G<网格>]`，如 `C1_模型版_61mm2h_5m`、`YAS_61mm24h_G5`。
   名字会被解析成片区 / 雨量 / 历时 / 网格，决定任务的范围、雨型显示名、网格。带 `.bak-…` 后缀的备份目录不识别。
2. **各态都算完**：态目录前缀 `L1_`、`L2_`、`L2+planned`、`L2+suggested`；每个态目录里要有 `*耦合运行记录.json`。缺的态不报错（只有 L1、L2 的照常导入）；**同一态不能有两个目录**（带「中间步」字样的过程态自动忽略）。
3. **npz 在哪**（`sim_c1.state_files`）：
   - C1 样式：态目录下 `一二维/` 里自带 `*_二维水深帧与最大水深.npz`、运行记录、`耦合的一维/*.rpt|inp`，**交付自包含**。
   - 模型版 / 区域样式（`一维/` + `二维/`）：交付里**没有 npz**，npz 在算力侧的 `work/c2d/<名>_<态记号>_2d.npz`（L1 / L2 / L2P / L2S3）。
     **这类交付必须连同 `work/c2d` 里对应的 npz 一起拷到 109**，导入时用 `--c2d` 指过去（§4.2）。
4. 不要带 macOS 的 `._*` 伴生文件（代码会忽略，但白占空间）：拷之前 `find <交付> -name '._*' -delete`。

### 3.2 放到 109 的哪里

最省事的位置：**`<部署目录>/docker-aio/data/incoming/`**。理由：`data/` 已经以只读方式挂进容器 `/app/data`，宿主机上新建的子目录容器里**立刻可见**，不用重建容器；抽取只读原件，只读挂载正好。

```bash
# 在 109 上
mkdir -p <部署目录>/docker-aio/data/incoming
# 在你的机器上拷过去（账号、路径待核对；大文件建议 rsync，可断点续传）
rsync -avP --exclude '._*' ./C1_模型版_61mm2h_5m  <账号>@10.255.254.109:<部署目录>/docker-aio/data/incoming/
rsync -avP --exclude '._*' ./c2d  <账号>@10.255.254.109:<部署目录>/docker-aio/data/incoming/c2d_<批次>/     # 仅模型版需要
```

拷完在 109 上自检：

```bash
cd <部署目录>/docker-aio
ls data/incoming/C1_模型版_61mm2h_5m                          # 看到 L1_… L2_… 等态目录
docker exec flood-aio ls /app/data/incoming/C1_模型版_61mm2h_5m   # 容器里同样可见
chmod -R a+rX data/incoming                                    # 保证容器里读得到
df -h data                                                     # 磁盘余量
```

### 3.3 容量与耗时（量级，来自 `SIMULATION_API.md`；109 的余量待核对）

- 抽取结果：C1 15 m 落图格，四态约 **4 GB**（336 帧 ≈ 1 GB/态）；AAM 抽取结果约 1.3 GB。**原件 + 抽取结果同在 109 上，磁盘按两份算。**
- 5 m 模型 npz 约 800 万三角单元，分块读峰值内存 ≈ 1.3 GB；耗时几十分钟量级 → **放后台跑**（§4.3）。

---

## 4. 一二维交付的导入

### 4.1 备份数据库（建议每次都做，几十秒）

```bash
cd <部署目录>/docker-aio && ./backup-db.sh        # pg_dump -Fc 在线备份，保留最近 7 份在 ./backups/
```

### 4.2 导入（在容器里直接跑，不需要 docker-import.sh）

`docker-import.sh` 是为 NAS 写的（要求 `/nas` 存在），**这里不用它**，直接 `docker exec` 调同一个入口：

```bash
# 先只看判定的形状（不抽取不登记），确认是预期的那种
docker exec -u root -e PYTHONPATH=/app/src -e PYTHONUTF8=1 flood-aio \
  python -m stormwater.sim_import /app/data/incoming/C1_模型版_61mm2h_5m --kind

# 正式导入
docker exec -u root -e PYTHONPATH=/app/src -e PYTHONUTF8=1 flood-aio \
  python -m stormwater.sim_import /app/data/incoming/C1_模型版_61mm2h_5m --by ZN

# 模型版交付：npz 在另一处，用 --c2d 指到存放 *_2d.npz 的目录
docker exec -u root -e PYTHONPATH=/app/src -e PYTHONUTF8=1 flood-aio \
  python -m stormwater.sim_import /app/data/incoming/Mussafah_00_模型版_61mm2h \
  --c2d /app/data/incoming/c2d_<批次> --by ZN
```

参数：
- `--by`：操作人缩写，**必须是库里已有的**（目前 ZN / AL / AR，否则 422「未知的操作人」）。`AR` 名下的任务受保护（页面 / 接口都删不掉），除非特意要保护，别用 AR。
- `--c2d`：npz 目录。不给时程序从交付往上找 `<主机>/work/c2d`，在 109 的 `incoming` 下找不到，所以模型版交付**必须给**。目录里同一态不能有多个候选 `*_<L1|L2|L2P|L2S3>_2d.npz`，有多个就让 `--c2d` 指到只含这一场的目录。
- `--state L1 --state L2`：只处理指定态（可重复）。

用 `-u root` 是为了与旧流程一致、规避权限差异；抽取结果权限见 §4.4。

成功的末行是 `已登记为仿真 #<id>（<形状>）`，**记下 id**。

### 4.3 长任务放后台

```bash
cd <部署目录>/docker-aio
nohup docker exec -u root -e PYTHONPATH=/app/src -e PYTHONUTF8=1 flood-aio \
  python -m stormwater.sim_import /app/data/incoming/C1_模型版_61mm2h_5m --by ZN \
  > /tmp/import_C1.log 2>&1 &
tail -f /tmp/import_C1.log
```

**抽取期间不要 `docker restart flood-aio`**，会打断（半成品留在 `sim_deliveries/<键>/`，重跑同一条命令即可覆盖）。
多份交付**串行**跑，别并发（内存和磁盘都吃紧）。

### 4.4 修权限、清缓存

```bash
cd <部署目录>/docker-aio
chmod -R a+rX data/processed/sim_deliveries          # API 在容器里以 uid 10001 读，要全局可读
docker restart flood-aio                              # 清进程内缓存；新登记的任务不重启也能看到，更新已有任务必须重启
```

### 4.5 抽取结果的名字

抽取结果目录 = `sim_deliveries/<片区>_<雨深>mm<历时>h`，网格不是 15 m 才加 `_<n>m`（如 `C1_61mm2h` 与 `C1_61mm2h_5m` 并存、互不覆盖）。去重靠这个目录名（= 库里的 `output_dir`）。

---

## 5. 验证（每次导入后都做）

```bash
ID=<导入返回的任务号>

# 库里这一行
docker exec flood-aio psql -U postgres -d syq-pipe -c \
 "select id,status,extent,grid_m,rain_label,output_dir,variant_label,started_at,ended_at
  from simulation.run order by id desc limit 5"

# 接口
curl -s "http://127.0.0.1:8002/api/simulations/$ID" | head -c 800            # has_layers=true、states 非空
curl -s "http://127.0.0.1:8002/api/simulations/$ID/layers" | head -c 1200    # 帧数、bounds、claim_boundary
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8002/api/simulations/$ID/layers/max-depth"
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8002/api/simulations/$ID/layers/frames/0"
```

核对：`status=done`、`output_dir` 为 `processed/sim_deliveries/<键>`、`extent / grid_m / rain_label` 与交付目录名一致、`variant_label` 的态数 = 实有态目录数。多态任务各态再带 `?state=<态 key>` 查一遍。

页面：`http://10.255.254.109:8002/` → 仿真列表出现该任务 → 打开详情看最大水深、时间轴、防线 / 态切换、溢水点、（多态）方案效果、热点表；切英文界面看片区名与态名是否残留中文。
页面须**原样保留** `claim_boundary` 与「退水未验证」标识（诊断性、未标定成果）。

溢水点首次现算要十几秒到更久，可预热（用 uid 10001，缓存文件才不属 root）：

```bash
docker exec -u 10001 -e PYTHONPATH=/app/src -e PYTHONUTF8=1 flood-aio python -m stormwater.sim_ponding <id>
```

---

## 6. 更新已导入的交付（对方重算后给了新版）

1. 新版拷到 `data/incoming/` 下，**目录名保持一致**（覆盖同一个键）；
2. 重跑 §4.2 同一条命令（按 `output_dir` 去重，更新同一行，不新增任务）；
3. `docker restart flood-aio`；
4. 清溢水点缓存：`ls cache/sim_ponding` 找到该任务相关的文件删掉（只是缓存，不确定就整目录清空，会按需重算）。

覆盖是原地的，期间这份交付的页面可能读到新旧混合，选没人用的时段做。

---

## 7. 原生计算结果（AAM 类）

原件 ~9.7 GB，流程分两段：**抽取**读原件、**登记**只要抽取结果。

**方式一（推荐，原件不用拷到 109）**：在开发机 / 算力机上抽取，只把抽取结果（约 1.3 GB）带上 109：

```bash
# 开发机
cd backend
$env:PYTHONUTF8="1"; $env:PYTHONPATH="src"
.venv\Scripts\python.exe -m stormwater.sim_native extract data\<日期>\原生计算结果_可拷贝
tar -C backend/data/processed/sim_deliveries -czf AAM_60mm1h_100m.tar.gz AAM_60mm1h_100m
scp AAM_60mm1h_100m.tar.gz <账号>@10.255.254.109:<部署目录>/docker-aio/

# 109
cd <部署目录>/docker-aio
./backup-db.sh
./import-native.sh AAM_60mm1h_100m.tar.gz --by ZN     # 解到 data/processed/sim_deliveries → 修权限 → 容器内 register → 重启
```

**方式二**：原件拷到 109 的 `data/incoming/`，直接 `docker exec … python -m stormwater.sim_import /app/data/incoming/原生计算结果_可拷贝 --by ZN`（抽取慢、吃内存）。

**限制（读代码得出）**：`sim_native.py` 里 `OUT_KEY = "AAM_60mm1h_100m"`、`REGION = "AAM"`、`STATES`（只有 L1 / L2），以及 `import-native.sh` 里的 `KEY` 都是**写死的**。
同一份 AAM 重抽重登记没问题；**换雨量 / 网格 / 片区的原生结果要先改这几处代码**，走 §9。
交付自带 `both_quality_passed = false`，页面 `claim_boundary` 会写明，不得当「物理精度已验证」引用。

---

## 8. 撤销一次错误导入

导入只动两处：库里 `simulation.run` 一行（资源 / 结果 / 分析级联删除）、`sim_deliveries/<键>/` 目录。原件从不被修改。

```bash
cd <部署目录>/docker-aio
# 方案一：整库回退（导入前做过备份）
docker exec -i flood-aio pg_restore -U postgres -d syq-pipe --clean --if-exists < backups/syq-pipe_<时间>.dump

# 方案二：只删这个任务
docker exec flood-aio psql -U postgres -d syq-pipe -c "delete from simulation.run where id = <ID>"
rm -rf data/processed/sim_deliveries/<键>        # 先确认没有别的任务的 output_dir 指向它
docker restart flood-aio
```

接口 `DELETE /api/simulations/{id}` 只对 `draft / done / failed / cancelled` 有效，AR 名下的 403；它只删库行，不删抽取目录。

---

## 9. 什么时候要改代码、换镜像

| 现象 | 原因 | 处理 |
|---|---|---|
| 英文界面片区名仍是中文 | `i18n.EXTENT_EN` 没有这个片区（读不到则原样返回，不报错） | `backend/src/stormwater/i18n.py` 加映射 |
| 英文界面态名 / 措施名残留中文 | `i18n.LABEL_EN`、`sim_c1.STATES` 没覆盖。改中文文案必须同步 i18n 键，否则 `test_i18n.py` 红 | 改 `i18n.py`，跑 `pytest tests/test_i18n.py` |
| 新片区第一次接入报「不可抽取 / 找不到 npz」 | 文件命名与 `sim_c1.state_files` 假设不符（模型版 / 区域交付的命名是照服务器脚本推的，没见过真实交付） | 先跑 `sim_probe`（下）把摘要发回开发侧 |
| 态与防线对应不对 | `STATES`：L1→第一道，L2→第二道（现状），L2+planned / L2+suggested→第二道（规划）。L2 实际措施要对交付的《计算设置.md》核一遍 | 改 `STATES` |
| 有新的态种类 | 抽取代码不认 | 改代码 |
| `No module named stormwater.sim_xxx` | 109 的镜像比代码旧 | 升级镜像，或临时 `docker cp` 新文件进容器（重建容器后丢，已导入数据不丢） |

新片区第一次接入前建议先自检（只读，不输出数据本体）：

```bash
docker exec -u root -e PYTHONPATH=/app/src -e PYTHONUTF8=1 flood-aio \
  python -m stormwater.sim_probe /app/data/incoming/<交付目录> --depth 6 --out /tmp/摘要.txt
docker cp flood-aio:/tmp/摘要.txt .
```
重点看「work/c2d（按 … 找到）」那行和「可抽取 / 不可抽取」结论。

**改了代码的升级流程**（构建在开发机，Linux/Git Bash 环境）：

```bash
cd backend/docker-aio
./build.sh v<N>
docker save flood-aio:v<N> | gzip > flood-aio_v<N>.tar.gz
sha256sum flood-aio_v<N>.tar.gz > flood-aio_v<N>.tar.gz.sha256
scp flood-aio_v<N>.tar.gz* <账号>@10.255.254.109:<部署目录>/docker-aio/

# 109
cd <部署目录>/docker-aio && ./upgrade.sh v<N>     # 校验 → 先备份库 → load → 补挂载点 → run.sh → 等 healthy
```
`pgdata / data / cache / backups / .env` 都沿用，已导入任务不丢；回滚 `./run.sh <旧标签>`。**先升级镜像，再导入新数据。**

---

## 10. 常见报错速查

| 报错 / 现象 | 原因 | 处理 |
|---|---|---|
| `交付目录名 … 不是「片区_雨深mm历时h[_网格m]」的形状` | 目录名不规范 / 备份目录 | 改回规范名或用原目录 |
| `… 下 L2+suggested 有多个目录，只留一个` | 同一态两个目录 | 删掉多余的，或把过程态改名带「中间步」 |
| `交付里没有 npz，且找不到 work/c2d` | 模型版交付没给 `--c2d` | 把 npz 拷到 109，加 `--c2d` |
| `找不到 <态> 的 npz（… 匹配到 N 个）` | 命名对不上 / 多个候选 | `--c2d` 指到只含这一场的目录；或回传 `probe` 摘要 |
| `… 下 *.rpt 找到 2 个，需恰好一个` | 有 `._*` 伴生文件 | `find <交付> -name '._*' -delete` |
| 容器里 `ls /app/data/incoming` 看不到 | 目录不在 `data/` 下，或容器不是用 `./data` 挂载起的 | 放到 `<部署目录>/docker-aio/data/incoming`；确认 `docker inspect flood-aio` 的挂载 |
| 抽取中途失败 / OOM | 5 m 模型内存不够，或被重启打断 | `free -g`；错开时段，重跑同一条命令即覆盖 |
| `未知的操作人`（422） | `--by` 不在 `app_user` | 用 ZN / AL / AR |
| 页面有任务但图层 404「这个任务没有结果图层」 | 抽取目录缺失 / 权限不可读 | 检查 `data/processed/sim_deliveries/<键>`，`chmod -R a+rX` |
| 页面读到旧结果 | 进程缓存 / 溢水点缓存 | `docker restart flood-aio`；清 `cache/sim_ponding` |
| `/layers/ponding` 422 | 成果缺算溢水点要的输入 | 属交付内容问题，回传 `probe` 摘要 |
| 热点表只有「全域 / 离热点 200 m」口径 | 交付没带汇水区面（只有 C1 带） | 正常 |
| 热点没坐标 | 容器里缺 `/app/data/processed/hotspots_845.csv`，或编号对不上（按 `ADM-<补零编号>`） | 确认文件在 |

---

## 11. 一页检查清单

**导入前**
- [ ] 目录名规范，无 `._*`，各态有 `*耦合运行记录.json`，同态无重复目录
- [ ] 模型版交付：对应 `*_2d.npz` 已一并拷到 109
- [ ] 已拷到 `<部署目录>/docker-aio/data/incoming/`，容器里 `ls /app/data/incoming/…` 看得到
- [ ] 磁盘、内存够；`./backup-db.sh` 已做
- [ ] 新片区第一次接入：`probe` 结论为「可抽取」

**导入**
- [ ] `--kind` 判形状 → `sim_import … --by ZN [--c2d …]`（长任务 nohup）
- [ ] 记下 `已登记为仿真 #<id>`
- [ ] `chmod -R a+rX data/processed/sim_deliveries`；`docker restart flood-aio`

**导入后**
- [ ] 库里 `status=done`，`output_dir / extent / grid_m / rain_label` 正确
- [ ] `/api/simulations/<id>`、`/layers`、`/layers/max-depth`、`/layers/frames/0` 均 200
- [ ] 页面列表与详情（图层、时间轴、溢水点、方案效果）正常；英文界面无残留中文
- [ ] 若改过代码：先 `upgrade.sh` 再导入，旧镜像保留以便回滚

---

## 附：相关文件

| 文件 | 用途 |
|---|---|
| `backend/src/stormwater/sim_import.py` | 统一入口，按形状分流（`--kind` 只判形状） |
| `sim_c1.py` / `sim_sww.py` / `sim_city.py` / `sim_native.py` | 各形状抽取与登记（`STATES`、落图格网、热点口径见模块头） |
| `sim_probe.py` | 只读自检 |
| `sim_results.py` | `import_delivery`：写 `simulation.run`，按 `output_dir` 去重 |
| `i18n.py` | `EXTENT_EN` / `LABEL_EN`：英文界面片区名与态名 |
| `backend/docker-aio/backup-db.sh` `run.sh` `upgrade.sh` `build.sh` `import-native.sh` | 备份 / 起容器 / 升级 / 构建 / 原生结果登记 |
| `docs/SIMULATION_API.md`、`backend/docker-aio/README.md` | 接口与交付口径、一体化镜像总说明 |
