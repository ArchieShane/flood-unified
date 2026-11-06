# 推演程序部署（离线内网服务器）

## 一、在有网的机器上打包（如 109）
```bash
cd <engine 目录>
scripts/package_release.sh --out /path/to/release [--with-app]      # --with-app：再带 flood-new 页面镜像
```
产物：`flood-worker_<TAG>.tar.gz`（镜像，运行环境）、`engine_<日期>.tar.gz`（脚本 / 规则 / 数据）、`SHA256SUMS`、`install_server.sh`。拷到服务器（U 盘 / 内网共享）。

## 二、在每台推演服务器上安装
```bash
cd <放部署包的目录>
./install_server.sh --engine-dir /opt/flood/engine --nas <NAS 在这台上的挂载路径> --work <本机工作目录，不能有中文>
```
前提：docker 可用；NAS 已挂载且可写；工作目录所在磁盘 ≥ 200 GB。脚本会校验、`docker load` 镜像、解压 engine、写 `engine/docker/.env`，最后自动跑一遍环境检查。

## 三、分配场景并启动（三台共用同一个 NAS 目录）
每台只启动自己负责的场景（不重复）：
```bash
/opt/flood/engine/data/regions/RDA/RDA-server.sh start --scenarios S33D24,S61D24,S96D24,S33D2,S61D2,S96D2   # 服务器 1（48 核）
/opt/flood/engine/data/regions/RDA/RDA-server.sh start --scenarios S33D1,S61D1                               # 服务器 2（32 核）
/opt/flood/engine/data/regions/RDA/RDA-server.sh start --scenarios S96D1                                      # 服务器 3（32 核）
```
`status` 看进度，`log -f` 看日志，`stop` 停止，`report` 看各场景成果目录。结果都写到 NAS：`deliveries/<job_id>`、`result/<主机>/runtime/console_data/flood2d_*`、`shp_dat/<job_id>`。

## 四、安装页面（management + flood-viewer）并设置映射出来的端口
页面是一个容器，只对外**一个端口**（容器内 8080）；成果展示 `/viewer/` 是同一端口下的路径。在放页面的那台机器上，和 `flood-new_*.tar.gz`、`install_app.sh`、`app.sh` 放同一个目录：
```bash
./install_app.sh --dir /opt/flood/app --port 8006 --nas <NAS 挂载路径> --data /data/flood-app
cd /opt/flood/app && ./app.sh start
```
浏览器打开 `http://<服务器IP>:<端口>/`，成果展示是 `http://<服务器IP>:<端口>/viewer/`。
- **改端口**：`./app.sh set-port <端口>`（先检查是否被占用，没占用才重建容器；会写进 `app.env`）。
- `./app.sh status | logs | stop | restart`。
- **让页面看到成果**：推演完后 `./app.sh import <job_id>`（如 `./app.sh import RDA_33mm1h`），或 `./app.sh import-all`（登记 NAS 的 `deliveries/` 下还没登记过的）。入库后页面上的「推演参数」「成果数据位置」自动出现；flood-viewer 读 NAS 的 `result/`，最迟 2 分钟出现。
- 页面容器的数据（数据库、入库后的成果、缓存）放 `--data` 指定的目录，NAS 只读映射；镜像里没有数据。

## 升级
脚本、规则、数据在 engine 目录里：换新的 `engine_*.tar.gz` 重装即可，**不用换镜像**。只有升级 ANUGA / SWMM、加 Python 依赖才重打镜像。
