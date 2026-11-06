#!/bin/sh
# Harness 容器入口：先把预设 / 补丁 / 工作区渲染进 $DSH_HOME（渲染要的是容器里的路径，
# 不能用宿主机上 dsh_bridge.render 烤好的 Windows 绝对路径），再 exec dsh。
#
# 渲染用的 Python 片段复用 swbrain.dsh_bridge.render（它按传入的 home 生成全部路径），
# 只额外把 node_modules 联接跳过（容器里 npm 全局装好了，Node 直接解析得到）。
set -e

python - <<'PY'
import os, pathlib, sys
sys.path.insert(0, "/app")
from swbrain import db, dsh_bridge
cfg = db.load_config(None)
home = pathlib.Path(os.environ.get("DSH_HOME", "/dsh_home"))
home.mkdir(parents=True, exist_ok=True)
# 容器里回调用的是 compose 网络里的服务名（或 host.docker.internal），不是 config.toml 里的 127.0.0.1:端口：
# render() 会把 SWBRAIN_URL 烤进预设，所以渲染前先把它钉成容器能到的地址（环境变量为准）
url = os.environ.get("SWBRAIN_URL")
if url:
    cfg.setdefault("agent", {}).setdefault("dsh", {})["swbrain_url"] = url
p = dsh_bridge.render(cfg, home=home, link_modules=False)
st = dsh_bridge.settings(cfg)
print(f"[swbrain-dsh] 渲染到 {home}：预设 {p['preset_dir']}、补丁 {p['sdk.patch.yml'].parent}；模型 {st['provider']}/{st['model']}", flush=True)
PY
# dsh 的凭据文件（.credentials.yaml）权限检查：Windows 绑定挂载一律报 mode 777，直接把凭据放挂载目录会被拒绝启动。
# 所以凭据（模型密钥）走环境变量，这里把凭据文件收紧到 600；文件不存在时 dsh 自会从环境变量读。
[ -f "$DSH_HOME/.credentials.yaml" ] && chmod 600 "$DSH_HOME/.credentials.yaml" 2>/dev/null || true
# 只读凭据：服务（控制台）在托管启动时写 <home_web>/.delegation，两边挂的是同一个目录；
# 这里只报一句，方便排查（容器里 MCP 桥启动时读它；文件不在 = 共享口令模式，走 ?key=）
[ -f "$DSH_HOME/.delegation" ] && echo "[swbrain-dsh] 只读凭据已就位" || echo "[swbrain-dsh] 没有只读凭据（共享口令模式，或服务还没写过）"
exec dsh --profile web --patch "$DSH_HOME/patches/web.patch.yml" --port "${DSH_PORT:-3080}" --no-open
