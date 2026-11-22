"""跳数预览接口：复查旧值、确认发布口径，不修改任何数据。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import hop_policy, hop_store
from app.store import store

router = APIRouter(prefix="/api/hop", tags=["二版跳数"])

MODULES = (hop_store.TEMPLATE_MODULE, hop_store.SUBGRAPH_MODULE)


class InterpretPayload(BaseModel):
    raw: Any = None
    boundary: Any = None
    adopted_template: str | None = None


@router.get("/preview/{module}/{entry_id}", response_model=dict)
def preview_entry(module: str, entry_id: int) -> dict[str, Any]:
    """按二版口径预览一条记录的可计算跳数（不落库）。"""
    if module not in MODULES:
        raise HTTPException(status_code=404, detail=f"模块 {module} 没有跳数口径")
    hop_store.ensure_migrated()
    row = store.find(module, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"记录 {entry_id} 不存在")
    hop = hop_store.hop_for_query(row, module)
    return {
        "module": module,
        "id": entry_id,
        "version": hop.get("version"),
        "value": hop.get("value"),
        "status": hop.get("status"),
        "executable": hop.get("executable"),
        "literal": hop.get("literal"),
        "boundary": hop.get("boundary"),
        "adopted_template": hop.get("adopted_template"),
        "adopted_config": hop.get("adopted_config"),
        "adopted_source": hop.get("adopted_source"),
        "conflicts": hop.get("conflicts") or [],
        "reason": hop.get("reason"),
    }


@router.post("/interpret", response_model=dict)
def interpret(payload: InterpretPayload) -> dict[str, Any]:
    """临时复查一个旧值：只看口径结果，不写回任何记录。"""
    return hop_policy.resolve_hop(
        payload.raw,
        boundary=payload.boundary,
        adopted_template=payload.adopted_template,
    )


@router.get("/templates", response_model=list[dict])
def template_hops(status: str | None = Query(default=None)) -> list[dict[str, Any]]:
    """按跳数状态复查全部搜索模板的取数结果。"""
    hop_store.ensure_migrated()
    items = []
    for row in store.rows(hop_store.TEMPLATE_MODULE):
        hop = hop_store.resolve_template_hop(row)
        if status and hop.get("status") != status:
            continue
        items.append({
            "模板编号": hop_store.template_code(row),
            "跳数上限": row.get("跳数上限"),
            "可计算跳数": hop.get("value"),
            "跳数状态": hop.get("status"),
            "跳数可执行": bool(hop.get("executable")),
            "采用模板": hop.get("adopted_template"),
            "跳数冲突": hop.get("conflicts") or [],
        })
    return items
