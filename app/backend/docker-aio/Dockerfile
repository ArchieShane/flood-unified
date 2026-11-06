# 城市洪涝应对指挥中心 —— 一体化镜像：前端（nginx）+ 后端 API + PostGIS 数据库软件（库内容不在镜像里）
#
# 只对外一个端口 8080：nginx 提供前端静态页并把 /api 反代到容器内 uvicorn(127.0.0.1:8101)；
# 数据库只监听容器内回环，不对外（要对外调试设 PG_LISTEN，见 entrypoint.sh）。
#
# 构建上下文由 build.sh 拼出（docker-aio/.ctx/）：src/ sql/ requirements.txt web/(前端 dist)
# nginx.conf entrypoint.sh。数据目录（DEM、批算 CSV、仿真成果）不进镜像，运行时目录映射到 /app/data。
# 库内容：首次启动时 ./pgdata 为空 → 建 syq-pipe：有 /seed/syq-pipe.dump 就恢复它，没有就建空库（simulation 表结构 + 目录数据）。
# 数据库文件必须映射到宿主机目录（见 run.sh / docker-compose.yml），入口脚本不允许数据库跑在容器可写层里。
#
# 基础：local/postgis:16.11-postgis3.6-pgrouting（Debian 12，与线上 postgis16 同版本，库里用到 pgrouting/vector 等扩展）。
# Python 3.14 从官方 python:3.14-slim-bookworm 整目录搬过来（同为 bookworm，glibc 一致）。

ARG PG_BASE=local/postgis:16.11-postgis3.6-pgrouting

# ---------- ① Python 依赖 ----------
FROM python:3.14-slim-bookworm AS pybuild
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install \
      --only-binary=rasterio,pandas,numpy,scipy,psycopg-binary,uvloop,httptools,watchfiles,websockets,pyyaml \
      -r requirements.txt

# ---------- ② 最终镜像（不含数据库内容：首次启动由 entrypoint.sh 建库 / 恢复快照） ----------
FROM ${PG_BASE}
COPY --from=pybuild /usr/local /usr/local
COPY --from=pybuild /opt/venv /opt/venv
# python 官方镜像里的运行时依赖；rasterio 的 GDAL 需要 libexpat.so.1，缺了会让 registry.py 静默退回 Zone B
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 libffi8 libsqlite3-0 libbz2-1.0 liblzma5 zlib1g libuuid1 tini nginx \
 && rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf \
 && rm -rf /var/lib/apt/lists/* \
 && ldconfig \
 && /opt/venv/bin/python -c "import rasterio, scipy, pandas, psycopg, ssl, sqlite3; print('py ok')"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PGDATA=/var/lib/postgresql/data \
    PIPE_DB_HOST=127.0.0.1 PIPE_DB_PORT=5432 PIPE_DB_NAME=syq-pipe \
    PIPE_DB_SCHEMA=dmt_irrigationstormwater_abudhabi PIPE_DB_USER=postgres \
    PIPE_DB_PASSWORD="freedo"

WORKDIR /app
COPY src/ ./src/
COPY sql/ ./sql/
COPY web/ /usr/share/flood-web/
COPY viewer-web/ /usr/share/flood-web-viewer/
COPY nginx.conf /etc/nginx/conf.d/flood.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && useradd --system --create-home --uid 10001 app \
 && mkdir -p /app/data/processed/sim_ponding /viewer-data \
 && chown -R app /app/data/processed/sim_ponding

EXPOSE 8080
VOLUME ["/var/lib/postgresql/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
