# 转换镜像 h2d-convert:1.0：anuga-swmm:1.0 + pyshp + hydro2d_convert.py（离线构建：docker build -t h2d-convert:1.0 h2d/）
FROM anuga-swmm:1.0
COPY pyshp-3.1.6-py3-none-any.whl /tmp/
RUN pip install --no-cache-dir --no-index /tmp/pyshp-3.1.6-py3-none-any.whl && rm /tmp/*.whl
COPY hydro2d_convert.py /opt/h2d/hydro2d_convert.py
WORKDIR /data
ENTRYPOINT ["python", "/opt/h2d/hydro2d_convert.py"]
