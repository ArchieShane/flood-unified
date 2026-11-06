# 一二维耦合计算镜像 anuga-swmm：ANUGA 4.0.1（二维）+ SWMM 5.2（swmm-toolkit 0.17.0，一维）
# 从现有镜像 anuga-swmm:1.0（2026-09-25）反推：基础镜像、apt 包与 pip 版本都按它钉死（requirements.txt 由其 pip freeze 导出）。
# 构建：docker build -t anuga-swmm:1.0 docker/anuga-swmm
# 用法：见 .claude/skills/hotspot-1d2d-states/references/run-and-monitor.md
#   docker run --rm -e OMP_NUM_THREADS=6 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 -v <工作目录>:/work anuga-swmm:1.0 \
#     python -u /work/c2d/couple2way_v2.py <名> <态> 15 6 60
FROM python:3.11.16-slim-trixie

# 编译工具与 gfortran：ANUGA 的 C 扩展与部分依赖需要；libexpat1：rasterio 的 GDAL 要（slim 镜像没有）；与原镜像一致
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential gfortran libexpat1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && python -c "import anuga, swmm.toolkit, rasterio, scipy; print('anuga', anuga.__version__)"

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1
WORKDIR /work
CMD ["python3"]
