"""HTTP API：仿真模块（仿真任务 + RD-A 全套成果）。

前后端分离：本进程**只出 JSON**，页面由 `frontend/` 那个 Vue 3 + Vite 工程单独部署。
指挥中心（845 点批算）、单点快算、Qcap 反算等非仿真接口已移除。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import sim_mock
from .hyetograph import router as hyetograph_router
from .rda import router as rda_router
from .simulations import router as simulations_router
from .viewer import router as viewer_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 算力侧未接入前，后台模拟仿真任务的进度（见 `sim_mock.py`；SIM_MOCK_PROGRESS=0 关闭）
    stop = sim_mock.start()
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="仿真模块 API", version="0.3.0", lifespan=lifespan)

#: 允许跨源访问本 API 的前端地址；部署到别的域名时在 .env 里用逗号分隔覆盖 `CORS_ORIGINS`。
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if o.strip()
]

app.include_router(simulations_router)
app.include_router(rda_router)
app.include_router(hyetograph_router)
app.include_router(viewer_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # 本 API 不认 Cookie 也不发 Session，故不开 credentials。
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def root() -> dict:
    """探活（容器健康检查用）。"""
    return {"service": app.title, "version": app.version, "docs": "/docs"}
