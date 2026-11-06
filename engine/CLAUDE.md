# 本仓库所有 Claude 窗口必须遵守

## 1. 共用文件夹，禁止清理（2026-10-29 事故后，用户定）

本文件夹 `D:\阿布扎比\排水智能体` 同时被多个 Claude 窗口使用。2026-10-29，一个窗口提交后清理了工作区，把所有没纳入 git 的文件一并删掉，删掉的东西不进回收站。被删的包括：
- 客户原始数据 `20261019/`、`Stormwater.zip`、`test.zip`、`RB-01/`；
- 设计手册 `WA-726` 的 PDF、`arcgis/` 工具箱；
- 各窗口还没提交的改动。

所以：

- **在本文件夹里禁止执行以下命令**：
  - `git clean`
  - `git reset --hard`
  - `git checkout -- .` / `git restore .`
  - `git stash -u` / `git stash --include-untracked`
  - `git switch` / `git checkout <分支>`（切分支会改动别的窗口正在用的文件）
- **提交一律在单独的临时目录里做**：
  1. `git fetch origin`
  2. `git worktree add -b <新分支> ../wt_<名字> origin/main`
  3. 只把本窗口改过的文件复制过去，在那里提交、推送、开 PR
  4. 完成后 `git worktree remove ../wt_<名字>`

  本文件夹当前所在的分支不要动。
- **要删文件之前，先确认它是不是别的窗口的**。不确定就不删，先问用户。

## 2. 客户数据放在代码文件夹外面

- 客户原始数据、压缩包、手册 PDF 一律放在 `D:\阿布扎比\客户数据\`（或其他不在本文件夹里的位置），不要放进本文件夹。
- 工具里读客户数据的路径，指向文件夹外的位置。
- 计算结果放在 `D:\swbrain_work\` 或 `out/`（`out/` 已被 git 忽略，但同样会被清理命令删掉，重要结果要另存）。

## 3. 客户数据不入库

客户原始数据、计算结果不提交到 git。

## 公共规则文件（所有推演脚本必须读它）

一二维推演的模型规则（SWMM 选项、管材糙率、25 % 初始水量、出海口 1.3 m、WA-726 雨水口公式、二维糙率与建筑挖洞、产流方法等）统一放在 `config/model_rules.toml`（F 系列，每条带 source / ref），脚本用 `tools/model_rules.py` 的 `load()` / `get("a.b.c")` 读取，**不要在脚本里写死这些数字**。改规则要改 `meta.version` 并记 `meta.changelog`。区域专属数据（井、管、泵、出水口、子汇水区、深坑、建筑洞）不放规则文件。写推演脚本前必须先让用户确认 L1 / L2 / L2planned 各含哪些数据（`[states]`）。校验：`python tools/model_rules.py --inp <某个 .inp>` 核对 `[OPTIONS]`。

每次推演的交付根目录必须有 `params.json`（management 页面「推演参数」模块读它，客户要看的就是这份）：`python tools/run_params.py --job-id <id> --state L1 --inputs <inputs_<tag>_<态>.json> --out <交付目录>`，内容 = 公共规则（带来源 / 出处）+ 本次运行输入登记 + 规则与实际取值的一致性核对。`config/model_rules.toml` 新增取值时，必须同时在 `config/model_rules_labels.toml` 补中英文名称（`python tools/run_params.py --check-labels` 检查）。


## 缺数据先查 Makani 库

推演缺数据（管网属性、雨水口、泵、出水口、井、下垫面、地块…）时，**先去 Makani 库找**（`config/data_sources.toml` 的 `[makani]`，109:5446，库 `makani_sync_full`，只读，密码走环境变量 `MAKANI_PASSWORD`），找不到或字段是 N/A 再向用户要。例：雨水口在 `inlet` / `catchbasin` 表（`unitid` 与 GDB 设备一致）；泵站在 `ps_pump`（只有扬程 / 功率 / 备注，没有泵曲线）。汇报时说明查了哪些表、结果如何。取到的数据按区域固化到 `data/regions/<区域>/`（`tools/region_pack.py`），不要让推演运行时依赖在线的库。

## 容器只是运行环境（脚本在宿主机）

推演 worker 镜像 `flood-worker`（`docker/worker/Dockerfile`）里只有运行环境：Python + ANUGA + SWMM + 地理依赖，**没有脚本也没有数据**。整个 `engine/` 目录用 `-v` 映射成容器内的 `/app`（`docker/docker-compose.worker.yml`），脚本、规则、数据都在宿主机。推演下一个区域：在宿主机建 `data/regions/<区域>/`（`region.toml` + 数据 + 需要的脚本），用同一个镜像跑，不换镜像。只有运行环境要变（升级 ANUGA / SWMM、加 Python 依赖）才重建镜像。

## 推演的启动与新增区域（scripts/）

每个区域有自己的一个服务脚本 `data/regions/<区域>/<区域>-server.sh`（`new_region.sh` 自动生成，实现在 `scripts/region-server.sh`），一个脚本管整个推演：
`RDA-server.sh check | start | stop | status | log | resume | report`
- `check`：启动前检查环境（docker / 镜像 / 映射目录 / 规则 / 区域数据与 manifest 的 sha256 / 雨型 / 工作目录与 NAS 空间 / CPU / 内存 / 并行度），有 FAIL 返回非 0。
- `start [--scenarios …]`：**先 check，不通过不启动**；通过后在后台启动容器 `flood-job-<区域>`，执行 `tools/run_region.py`（已在运行会拦住）。不带 `--scenarios` 用 `region.toml` 的 `[run] default_scenarios`。
- `status`：容器状态 + CPU / 内存 + 各场景进度（阶段、百分比、模拟时间、用时）+ 最近日志。进度来自 `<工作目录>/run/<区域>/status.json`，由推演主脚本用 `tools/run_status.py` 的 `Status` 写（阶段：prepare / build / compute / post / export）。
- `log [-f] [-n N] [--scenario S]`：日志（`<工作目录>/run/<区域>/run.log`）。
- `stop [--pause]` / `resume`：停止（可续跑）/ 暂停（docker pause）与恢复。
- `report`：各场景成果输出目录（`deliveries/<job_id>`、`result/<主机>/runtime/console_data/flood2d_*`、`shp_dat/<job_id>/<态>`）和关键文件是否齐全。job_id = `<区域>_<雨量>mm<历时>h`。
新增区域：`scripts/new_region.sh <区域ID> --domain … --outfalls "A,B" --catchments … --manholes … --aecom-inp …`（复制 `data/regions/_template`、**自动改好 region.toml**、生成 `<区域>-server.sh`）→ `tools/region_pack.py --region <区域>` → `<区域>-server.sh check` → `start`。
目录约定、检查阈值在 `config/servers.toml`（`[nas]`、`[check]`）。推演主脚本是 `tools/run_region.py`（只做 L1；已写，下面「推演主流程」）。

## 推演主流程（tools/run_region.py，只做 L1）

`<区域>-server.sh start` 在容器里执行 `tools/run_region.py --region … --scenarios …`，每个场景（`S<雨量>D<历时>`，job_id = `<区域>_<雨量>mm<历时>h`）一个子进程，按 `servers.toml` 的并行槽位同时跑：
1. **建模** `tools/region_model.py`（读 `data/regions/<区域>` + `config/model_rules.toml` + `data/common/rain_mussafah.json`）：计算域（子汇水区并集闭运算）→ 一维管网（`tools/region_net.py`：GDB 管线 / 设备 / 泵 / 湿井 → SWMM；出水口固定水位 1.3 m + 拍门；接了多根管的出水口自动拆成「井 + 连接管 + 出水口」）→ 复合 CN 产流（Makani 下垫面算不透水比例）→ 入流挂到 AECOM 出口最近的结构节点 → 管网 25 % 初始水量 → 二维输入（DTM 裁剪、深坑填平、Makani 下垫面糙率栅格、建筑挖洞）→ `<名>_L1.inp` + `c2d/pilot_<名>_L1.json` + `inputs_<tag>_L1.json`（逐项输入登记）。
2. **计算** `tools/couple2way_v2.py`（一二维耦合，ANUGA + SWMM）。进度从它的输出解析写进 `status.json`。已有 npz + 运行记录的场景跳过计算（续跑）。
3. **后处理与导出** `tools/region_post.py`：统计 → `params.json`（`tools/run_params.py`）→ 逐帧图 `<NAS>/result/<主机>/runtime/console_data/flood2d_ad_<区域>_m<雨量>h<历时>g<网格>/`（flood-viewer 读）→ 交付目录 `<NAS>/deliveries/<job_id>/`（英文布局同 ADM 全市改造后数据；management 的 `sim_import` 入库）+ `ponding_ge.json` → npz → sww → `shp_dat/<job_id>/L1/{shp,dat}`。后几步单独 try，失败只记进进度说明，不丢前面的成果。
RD-A 实测（109，5 线程）：二维网格 89,551 个三角单元、建筑挖洞 327 个 / 65.1 ha，与 F 系列交付包一致；约 70 s 算 1 个模拟小时。
每个交付根还有 `outputs.json`（`RP.write_outputs`）：成果位置（相对 NAS 根目录：`deliveries/<job_id>`、`result/<主机>/…/flood2d_*`、`shp_dat/<job_id>`、工作目录）、关键文件、规则版本、地形 / 雨型 / 出水口水位等描述，以及 `continue_from`（以后在这次 L1 的基础上加 L2 sugg / L3：用交付里的 1D inp 和 `common/model_inputs/` 里的 pilot / dem / surf 继续）。management 入库时带到页面：详情页「成果数据位置」模块，并按 `region` 列出同一区域之前所有推演，页面上的区域 / 网格 / 雨型 / 地形 / 潮位标签也由它决定（没有 outputs.json 的 ADM 全市交付仍用原来的写死标签）。交付里没带 `.sww`，详情页的「模拟积水点」那一块不出。
