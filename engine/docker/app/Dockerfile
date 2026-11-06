# 系统服务镜像 swbrain-app：控制台 + 接口 + 多智能体 + 管理端 + 一二维流程脚本（部署包用，2026-09-30）。
# 构建（仓库根目录为上下文；种子用具名上下文，避免被 COPY . 再拷一份）：
#   docker build --build-context seed=docker/app/seed -f docker/app/Dockerfile -t swbrain-app:<版本> .
# 不含口令（.dockerignore 排除 config.toml）。数据库快照 = swbrain.run 的运行产物，由 tools/make_release.py 暂存到 docker/app/seed/ 再打进镜像（/app/seed）；
# 运行时由 docker-compose 把这些目录映射到宿主机：
#   /app/config.toml（配置）、/data（客户数据，只读）、/work（一二维工作目录）、/app/out、/app/console/data、/app/exchange（运行产物；首次启动从 /app/seed 补齐）
# 一二维计算仍在 anuga-swmm:1.0 里跑：本镜像带 docker 命令行，经挂载的 /var/run/docker.sock 在宿主机上起计算容器。
FROM docker:27-cli AS dockercli

FROM python:3.14-slim-trixie
# libexpat1：rasterio / pyogrio 自带的 GDAL 要（slim 镜像没有）
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY requirements.lock.txt requirements.txt /app/
# 锁定版本装业务依赖；DeepSeek Harness SDK 可选（没有 Linux 包时退回规则规划器，不影响其余功能）
RUN grep -v -i '^deepseek-harness-sdk' requirements.lock.txt > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt \
 && (pip install --no-cache-dir deepseek-harness-sdk || echo "deepseek-harness-sdk 装不上：智能体用规则规划器") \
 && (pip install --no-cache-dir "PyJWT[crypto]==2.10.1" || echo "PyJWT 装不上：只能用共享口令（[identity].mode = shared_key）")
# ↑ 统一身份（可选）的验签库也预装进镜像：部署包用户要切到 sdip 模式时不用重建镜像；缺省 shared_key 不用它
COPY . /app
# exchange/ 在运行时被挂载覆盖：把自带的样例与契约模式留一份，启动时补进去（cp -n 不覆盖已有文件）
RUN cp -r /app/exchange /app/exchange_seed
# 数据快照种子（不覆盖宿主机已有文件，见 entrypoint.sh）
COPY --from=seed / /app/seed
RUN chmod +x /app/docker/app/entrypoint.sh
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 LANG=C.UTF-8 \
    SWBRAIN_SWMM_WORK=/work SWBRAIN_DEMO_WORK=/work/demo
EXPOSE 8766
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8766), 5)" || exit 1
ENTRYPOINT ["/app/docker/app/entrypoint.sh"]
