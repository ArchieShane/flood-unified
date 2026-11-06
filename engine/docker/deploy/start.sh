#!/usr/bin/env bash
# 部署包一键启动（Linux 服务器，在部署包目录里运行）：bash start.sh
# 没有 .env 先生成（自带随机数据库口令）并退出；有了就：导入三个镜像（没有时）→ 建目录 → 放配置 → docker compose up → 容器内自检
set -euo pipefail
cd "$(dirname "$0")"
command -v docker >/dev/null || { echo "没找到 docker"; exit 1; }
if [ ! -f .env ]; then
  pw=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  sed "s/__DB_PASSWORD__/$pw/" .env.example > .env
  echo "已生成 .env：先改 DATA_DIR / WORK_DIR / RUNTIME_DIR 再运行一次"; exit 0
fi
sed -i '1s/^\xEF\xBB\xBF//' .env 2>/dev/null || true      # 去掉可能的 BOM（否则第一个变量名带 BOM）
set -a; . ./.env; set +a
if printf '%s' "$WORK_DIR" | LC_ALL=C grep -q '[^!-~]'; then echo "WORK_DIR 不能有中文或空格"; exit 1; fi
for img in "swbrain-app:${VERSION}" "swbrain-db:${VERSION}" "anuga-swmm:1.0"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then echo "导入 images/${img/:/-}.tar"; docker load -i "images/${img/:/-}.tar"; else echo "$img 已有"; fi
done
mkdir -p "$DATA_DIR" "$WORK_DIR" "$RUNTIME_DIR/out" "$RUNTIME_DIR/console_data" "$RUNTIME_DIR/exchange"
if [ ! -f "$RUNTIME_DIR/config.toml" ]; then
  sed "s/__DB_PASSWORD__/$DB_PASSWORD/" config.package.toml > "$RUNTIME_DIR/config.toml"; echo "已放配置 $RUNTIME_DIR/config.toml"
fi
# 启动：优先 docker compose 插件，其次独立的 docker-compose，都没有（离线服务器常见）就用 docker run 做同样的事
if [ -z "${NO_COMPOSE:-}" ] && docker compose version >/dev/null 2>&1; then
  docker compose up -d
elif [ -z "${NO_COMPOSE:-}" ] && command -v docker-compose >/dev/null 2>&1; then
  docker-compose up -d
else
  echo "没有 compose：改用 docker run 启动（效果相同）"
  docker network inspect swbrain-net >/dev/null 2>&1 || docker network create swbrain-net >/dev/null
  docker rm -f swbrain swbrain-db >/dev/null 2>&1 || true
  docker run -d --name swbrain-db --restart unless-stopped --network swbrain-net --network-alias db --shm-size 1g     -e POSTGRES_PASSWORD="$DB_PASSWORD" --log-opt max-size=50m --log-opt max-file=5 "swbrain-db:${VERSION}" >/dev/null
  for i in $(seq 1 60); do docker exec swbrain-db pg_isready -U postgres -d pipe >/dev/null 2>&1 && break; sleep 2; done
  docker run -d --name swbrain --restart unless-stopped --network swbrain-net -p "${BIND:-0.0.0.0}:${PORT:-8766}:8766"     -v "$RUNTIME_DIR/config.toml:/app/config.toml" -v "$DATA_DIR:/data:ro" -v "$WORK_DIR:/work"     -v "$RUNTIME_DIR/out:/app/out" -v "$RUNTIME_DIR/console_data:/app/console/data" -v "$RUNTIME_DIR/exchange:/app/exchange"     -v /var/run/docker.sock:/var/run/docker.sock     -e SWBRAIN_SWMM_WORK=/work -e SWBRAIN_SWMM_WORK_HOST="$WORK_DIR" -e SWBRAIN_DEMO_WORK=/work/demo -e SWBRAIN_DEMO_WORK_HOST="$WORK_DIR/demo"     -e DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"     --log-opt max-size=50m --log-opt max-file=5 "swbrain-app:${VERSION}" >/dev/null
fi
# 之后的命令不依赖 compose：容器名固定为 swbrain / swbrain-db，直接 docker exec
for i in $(seq 1 120); do docker exec swbrain python -c "import socket; socket.create_connection(('127.0.0.1', 8766), 2)" >/dev/null 2>&1 && break; sleep 2; done   # 等容器里的服务真正在监听
docker exec swbrain python -m swbrain.doctor || true
echo "控制台 http://<本机内网IP>:${PORT:-8766}/   管理端 http://<本机内网IP>:${PORT:-8766}/admin.html"
echo "快速验证：docker exec swbrain python tools/c1_pipeline.py selftest"
echo "换了 DTM 之后重算数据快照：bash rebuild-state.sh"
