"""知谱工坊 · 元数据知识图谱与智能探查平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services import hop_store
from app.store import store

app = FastAPI(title="知谱工坊 · 元数据知识图谱与智能探查平台", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()


@app.get("/api/hop-migration")
def hop_migration() -> dict[str, object]:
    """跳数数据迁移报告：存量模板与子图的二版可计算跳数。"""
    return hop_store.migrate_all()


@app.post("/api/hop-migration")
def rerun_hop_migration() -> dict[str, object]:
    """强制重跑迁移（幂等，不覆盖已登记的子图证据和旧字段字面）。"""
    return hop_store.migrate_all(force=True)


# 服务启动即完成存量迁移，保证第一次读取看到的就是二版跳数数据。
hop_store.migrate_all()


# 资源路由含 /api/{module}，必须放在 health / overview 之后注册，
# 否则固定路径会被动态路径抢先匹配。
for module in ROUTERS:
    app.include_router(module.router)
