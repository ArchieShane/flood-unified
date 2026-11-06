# 推演 worker 镜像 = 运行环境，不含任何脚本和数据。
#   里面只有：Python 3.11 + ANUGA（二维）+ SWMM（一维）+ numpy / scipy / pandas / rasterio / pyproj + geopandas / pyogrio / shapely / psycopg。
#   脚本、规则、数据全部在宿主机目录，整个 engine/ 目录映射成 /app（见 docker-compose.worker.yml）：
#     tools/ swbrain/        脚本（公共脚本；某个区域要专用脚本，放它自己的 data/regions/<区域>/ 里，同样靠映射执行）
#     config/                规则、数据源登记
#     data/                  公共小数据 + 各区域目录
#   所以推演下一个区域 = 在宿主机上建一个目录（region.toml + 数据 + 需要的脚本），docker run 时指向它，不换镜像、不重建。
#   只有运行环境本身要变（升级 ANUGA / SWMM、加 Python 依赖）才重建镜像。
# 构建（要能 pip 到 wheel；离线就先在有网的机器 pip download 到 wheels/ 再用 PIP_ARGS）：
#   docker build -t flood-worker:v3 -f engine/docker/worker/Dockerfile engine/docker/worker
#   docker build --build-arg PIP_ARGS="--no-index --find-links /tmp/wheels" ...
ARG BASE_IMAGE=anuga-swmm:1.0
FROM ${BASE_IMAGE}
ARG PIP_ARGS=""
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir ${PIP_ARGS} -r /tmp/requirements.txt && python -c "import geopandas, pyogrio, shapely, psycopg; print('geo ok')"
ENV SWBRAIN_ENV=container PYTHONPATH=/app/tools:/app LANG=C.UTF-8 PYTHONUNBUFFERED=1
WORKDIR /app
CMD ["python3"]
