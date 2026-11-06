# 洪水推演成果展示（只读网页）。只用 Python 标准库，构建时不需要联网装任何包。
#   有外网：docker build -t flood-viewer:1.0 .
#   纯离线服务器：用服务器上已有的系统镜像当基础（里面有 python），例如
#     docker build --build-arg BASE_IMAGE=swbrain-app:<版本> -t flood-viewer:1.0 .       （docker images 看实际名字）
#   运行：见 docker-compose.yml（把 NAS 成果目录只读挂到 /data）
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
WORKDIR /app
COPY server/server.py /app/server/server.py
COPY web /app/web
ENV PYTHONUNBUFFERED=1 LANG=C.UTF-8 DATA_ROOT=/data WEB_ROOT=/app/web PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4).status==200 else 1)"
ENTRYPOINT []
CMD ["python", "-u", "/app/server/server.py"]
