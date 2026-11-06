"""推演成果展示（原 flood-viewer）：扫描 `flood2d_*` 成果目录，提供「区域 → 格网精度 → 雨量」目录、
单个情景的展示数据和逐帧 PNG。前缀 `/api/viewer`；页面是 `/viewer/`（静态，见 `viewer/web`）。

数据目录由环境变量 `VIEWER_DATA_ROOT` 指定（缺省 /viewer-data），布局见 `viewer_catalog.py` 模块头。
与仿真任务模块同源：两者读的都是 thinking-drainage-system（engine/）推演后处理的产物。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import viewer_catalog as vc

router = APIRouter(prefix="/api/viewer", tags=["viewer"])


def _safe(root: Path, rel: str, suffixes: tuple[str, ...]) -> Path | None:
    base = os.path.abspath(root)                      # 不解析符号链接，只防 .. 越界
    p = Path(os.path.normpath(os.path.join(base, rel.lstrip("/\\"))))
    if not str(p).startswith(base + os.sep) or p.suffix.lower() not in suffixes or not p.is_file():
        return None
    return p


@router.get("/catalog")
def catalog() -> dict:
    s = vc.state()
    return {**s["catalog"], "scanned_at": s["scanned_at"], "n": s["n"]}


@router.get("/config")
def config() -> dict:
    local = vc.TILES_DIR.is_dir()
    return {"tiles": "/api/viewer/tiles/{z}/{x}/{y}.png" if local else vc.TILE_URL or None}


@router.get("/scenario")
def scenario(region: str, grid: int, rain: str):
    out, err = vc.scenario(parse_qs(f"region={region}&grid={grid}&rain={rain}"))
    return out if out else JSONResponse({"error": err}, status_code=404)


def _health(force: bool) -> dict:
    s = vc.state(force=force)
    return {"ok": True, "data_root": str(vc.DATA_ROOT), "data_root_exists": vc.DATA_ROOT.is_dir(), "scenarios": s["n"],
            "scanned_at": s["scanned_at"], "scan_took_s": s["took_s"], "skipped": s["skipped"], "conflicts": s["conflicts"]}


@router.get("/health")
def health() -> dict:
    return _health(False)


@router.get("/rescan")
def rescan() -> dict:
    return _health(True)


@router.get("/data/{path:path}")
def data(path: str):
    p = _safe(vc.DATA_ROOT, path, (".png", ".json", ".geojson"))
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/tiles/{path:path}")
def tiles(path: str):
    p = _safe(vc.TILES_DIR, path, (".png", ".jpg", ".jpeg", ".webp"))
    if p is None:
        return Response(status_code=404)              # 缺的瓦片安静地 404
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})
