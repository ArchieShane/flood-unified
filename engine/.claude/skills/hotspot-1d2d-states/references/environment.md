# 环境与数据

## 软件
- **Docker 镜像 `anuga-swmm:1.0`**：ANUGA + SWMM 5.2（swmm-toolkit）。所有一二维计算、`.sww` 生成都在容器里跑，工作目录挂载到 `/work`。
  - **构建**：`docker build -t anuga-swmm:1.0 docker/anuga-swmm`（约 2 分钟，要联网下载 pip 包；镜像约 1.9 GB）。
    Dockerfile 从原镜像反推：`python:3.11.16-slim-trixie` + build-essential / gfortran / libexpat1 + `requirements.txt`（anuga 4.0.1、swmm-toolkit 0.17.0、rasterio 1.4.4 等全部钉死版本）。
  - **验证**：`docker run --rm -v <工作目录>:/work anuga-swmm:1.0 python /work/c2d/_chk_inp.py <某个模型名>` 能读入即可。
  - 不联网时：在有镜像的机器上 `docker save anuga-swmm:1.0 -o anuga-swmm.tar`，拷过去 `docker load -i anuga-swmm.tar`。
- 本机 Python 3.14：geopandas、rasterio、shapely 2、scipy、pyogrio、pyproj（后处理、建模都在本机）。
- 可选：ArcGIS Pro 的 `arcgispro-py3` 环境读 DWG（`extract_c1_design.py` 提图纸参数时用过）。
- Windows 上用 Git Bash 跑链脚本；PowerShell 看端口 / 进程。

## 工作目录
- `SWBRAIN_SWMM_WORK`（C1：`D:\swbrain_work\swmm`）——**SWMM 打不开中文路径**，模型文件、`c2d/` 结果、日志都放这里；仓库目录可以是中文。
- `c2d/couple2way_v2.py`、`c2d/npz_to_sww.py` 是 `tools/` 同名脚本的拷贝，**改了 `tools/` 要同步拷过去**（`cp tools/couple2way_v2.py $WORK/c2d/`）。正在跑的容器已加载旧代码，不受影响。
- `c2d/_chk_inp.py`：读入检查（打开 → 走 40 步 → 关），开跑前必做。

## 数据（C1 用到的）
下表是主要的几项；C1 用到的**全部**数据与资料（原始数据、参考资料、中间数据、各态模型文件、计算输出、交付物，每个文件的路径、用途、读取脚本）见 `docs/C1_资料清单.md`——给新热点群准备数据时照它逐项对。
| 数据 | 位置 | 用途 |
|---|---|---|
| 客户雨水管网 GDB | `D:\阿布扎比\DMT_StormWater_AbuDhabi_Processed.gdb` | 一维管网、出水口、泵站（PS_PUMP） |
| 5 m DTM | 配置 `terrain.dtm_path` | 二维地形 |
| 客户热点 | `console/data/hotspots.geojson` | 目标点、统计口径 |
| **客户 22 个水力模型** | `D:\世界模型\OneDrive_1_9-26-2026.zip` | **汇水区以此为准**；子汇水区 CN / Tc；Parsons 方案核对 |
| 顾问（Parsons）交付 | `D:\阿布扎比\Parsons_Model Data Export\` | 热点方案 PDF / DWG（泵站、塘、移动泵点），结果汇总表 |
| 客户 Mussafah 模型 | `$WORK/Mussafah_baseline.inp` | 设计雨型 |
| makani 库 | 配置 `source.makani` | 建筑、道路（对齐 / 选址核查） |

## 并行与线程
机器 20 线程：三态并行各 `OMP_NUM_THREADS=6`；单跑 8。5 个模型抢线程时每个都会明显变慢——同时跑前先告诉用户预计时间会拉长。
