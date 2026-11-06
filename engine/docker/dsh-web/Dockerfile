# DeepSeek Harness 原生 Web UI 镜像 swbrain-dsh：在应用镜像上加 node 与全局 dsh。
# 构建（仓库根目录为上下文，先有 swbrain-app 镜像）：
#   docker build -f docker/app/Dockerfile -t swbrain-app:<版本> .
#   docker build -f docker/dsh-web/Dockerfile --build-arg APP_IMAGE=swbrain-app:<版本> -t swbrain-dsh:<版本> .
#
# 为什么基于应用镜像：MCP 薄桥 `python -m swbrain.mcp_server` 要 import swbrain.ontology.schema，
# 那条链带 numpy / pandas / scenarios / lifecycle，单独拼一份依赖很容易漏；应用镜像里已经齐了。
#
# 它不装载任何管网状态：数字全部经 MCP 薄桥回调 swbrain 服务（同 compose 网络里是 http://swbrain:8766）。
# 只读：置 SWBRAIN_READONLY=1 —— dsh 的 MCP 客户端 headers 是静态配置，按会话传不了身份，所以这个入口只放只读动作。
ARG APP_IMAGE=swbrain-app:latest
FROM ${APP_IMAGE}

# node + npm：原生 Web UI 由 npm 包 @deepseek-ai/dsh 提供（Python SDK 里没有 Web 前端）
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# 全局 dsh；版本可用 --build-arg DSH_VERSION=… 钉住
ARG DSH_VERSION=latest
RUN npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" \
 && (dsh --version || echo "dsh 装好了但自报版本失败；看构建日志")

ENV DSH_HOME=/dsh_home \
    SWBRAIN_URL=http://swbrain:8766 \
    SWBRAIN_READONLY=1
EXPOSE 3080
# 健康检查：Web UI 起来后根路径回 401（要一次性 token），能连上端口即算好
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s \
  CMD node -e "require('net').connect(3080,'127.0.0.1').on('connect',()=>process.exit(0)).on('error',()=>process.exit(1))"

# 预设、补丁、工作区注册表在启动时渲染（dsh_bridge.render 的容器版，路径按容器里的来）；
# 渲染完再 exec dsh。见 docker/dsh-web/entrypoint.sh。
COPY docker/dsh-web/entrypoint.sh /usr/local/bin/swbrain-dsh-entry
RUN chmod +x /usr/local/bin/swbrain-dsh-entry
CMD ["swbrain-dsh-entry"]
