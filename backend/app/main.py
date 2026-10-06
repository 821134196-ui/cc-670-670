"""FastAPI 入口：业务错误统一转 JSON、CORS、托管前端构建产物。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.database import init_db
from app.errors import BusinessError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIST = os.path.join(BASE_DIR, "..", "..", "frontend", "dist")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("AUTO_INIT_DB", "1") == "1":
        init_db()
        if os.environ.get("AUTO_SEED", "1") == "1":
            from app.seed import seed_if_empty
            seed_if_empty()
    yield


app = FastAPI(
    title="工厂危险作业监护人替换与现场复核系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, **exc.details}},
    )


app.include_router(router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 演示/联调用时钟推进：本地模拟环境里可以“快进时间”验证许可与资质过期
@app.post("/api/dev/clock")
async def dev_clock(request: Request):
    from app.services.clock import clock
    body = await request.json()
    if "advance_seconds" in body:
        clock.advance(float(body["advance_seconds"]))
    return {"now": clock.now().isoformat()}


if os.path.isdir(FRONTEND_DIST):
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        index = os.path.join(FRONTEND_DIST, "index.html")
        return FileResponse(index)
