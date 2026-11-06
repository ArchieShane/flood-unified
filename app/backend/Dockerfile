# 城市洪涝应对指挥中心 —— 后端 API 镜像（FastAPI / uvicorn）
#
# 只打后端：页面在同级的 frontend/（Vue 3 + Vite）里单独部署，本镜像只出 JSON。
# 构建上下文就是 backend/，容器内目录布局必须保持 /app/src + /app/data ——
# registry.py 的 BACKEND_DIR 是 `__file__.parents[2]`，src 与 data 得是同一层的兄弟。
#
# 目标平台 linux/amd64。本机若是 Apple 芯片，构建命令必须带 --platform（见 docker-build.sh），
# 否则装进去的是 arm64 轮子，推到 x86 服务器起不来。
#
# 基础镜像用 bookworm（glibc 2.36）而非 alpine：rasterio 1.5.1 / pandas 3.0.6 的
# cp314 轮子要求 manylinux_2_28，musl 上没有对应轮子，alpine 会退化成现场编译 GDAL。

FROM python:3.14-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
# 带 C/Rust 扩展的那几个包在 cp314 / linux x86_64 上都有预编译轮子
# （rasterio、pandas 要 manylinux_2_28，bookworm 的 glibc 2.36 够；
#  watchfiles 走 cp310-abi3 通用轮子），所以不装 build-essential / GDAL 头文件。
# --only-binary 点名这几个而不是 :all:，是因为纯 Python 的间接依赖里可能有
# 只发 sdist 的（装它们不需要编译器）；点名之后，一旦哪个编译型包退化成源码
# 构建，这里当场失败，而不是默默产出一个缺 C 扩展、跑起来才炸的镜像。
#
# 直接装全量 requirements.txt（含 pytest / httpx2 两个测试依赖）：
# 多出来的体积只有几 MB，换的是版本号只有一处来源、不会和本机环境漂移。
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install \
      --only-binary=rasterio,pandas,numpy,scipy,psycopg-binary,uvloop,httptools,watchfiles,websockets,pyyaml \
      -r requirements.txt


FROM python:3.14-slim-bookworm

COPY --from=builder /opt/venv /opt/venv

# rasterio 轮子自带的 GDAL 依赖系统的 libexpat.so.1，slim 镜像里没有 ——
# 缺了它 `import rasterio` 报 ImportError，registry.py 会**静默**全体退回 Zone B、高程全为空。
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 只拷代码。data/ 整个不进镜像，运行时从宿主机只读挂载到 /app/data（见 docker-run.sh）——
# 数据（DEM 1.1G、仿真成果 7G+）改动与代码节奏不同，换数据只需同步宿主机目录再重启容器，不必重建镜像。
# 容器内目录布局仍是 /app/src + /app/data：registry.py 的 BACKEND_DIR 是 `__file__.parents[2]`。
COPY src/ ./src/

# 非 root 运行。挂进来的 data/ 必须对 uid 10001 可读（宿主机上 chmod -R a+rX，见 docker-run.sh）——
# 读不到 DEM 时 registry.py 会**静默**全体退回 Zone B。
#
# 运行期唯一要写的是溢水点缓存 data/processed/sim_ponding/：sim_ponding.load() 缓存未命中时现算并落盘，
# 且缓存键含成果目录的绝对路径（本机算的缓存在容器里 /app/data/... 下一律不命中）。
# data/ 只读挂载，这一层另挂具名卷盖在上面；这里预建目录并归 app，具名卷首次创建时沿用这个属主。
RUN useradd --system --create-home --uid 10001 app \
 && mkdir -p /app/data/processed/sim_ponding \
 && chown -R app /app/data/processed/sim_ponding
USER app

EXPOSE 8101

# `/` 是探活端点，不连数据库，所以数据库不可达时健康检查仍然是绿的 ——
# 这里查的是「进程活着、能应答」，不是「后端依赖都通」。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8101/', timeout=4).status==200 else 1)"

# 数据库凭据与 CORS 白名单一律运行时注入（--env-file / -e），不进镜像。
# 端口写死 8101：改对外端口用 -p 宿主端口:8101 映射，不必动镜像。
CMD ["uvicorn", "stormwater.api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8101"]
