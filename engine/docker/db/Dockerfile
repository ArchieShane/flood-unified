# 离线数据库镜像 swbrain-db：把 pipe / makani / liveability 三个库里代码用到的表（tools/make_db_snapshot.py 导出的 dumps/）
# 在构建时恢复进镜像自带的 PGDATA。服务器纯离线，不连客户源库；换 DTM 后重跑 python -m swbrain.run 读的就是它。
# 构建（以 docker/db 为上下文，先跑 tools/make_db_snapshot.py）：docker build -t swbrain-db:<版本> docker/db
# 数据是只读参考快照，不挂卷：容器重建 = 回到打包时的状态。口令由运行时环境变量 POSTGRES_PASSWORD 设置，不在镜像里。
FROM pgrouting/pgrouting:17-3.5-4.0
ENV PGDATA=/pgdata-baked
COPY dumps /dumps
COPY build-db.sh /
RUN chmod +x /build-db.sh && /build-db.sh && rm -rf /dumps
COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh
EXPOSE 5432
ENTRYPOINT ["/entrypoint.sh"]
