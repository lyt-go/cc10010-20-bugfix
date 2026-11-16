"""分区标识接口：维护数据表实际到达的分区标识，供缺失分区重算读取。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.partitionmark import PartitionmarkService

router = APIRouter(prefix="/api/partitionmark", tags=["分区标识"])

service = PartitionmarkService()


@router.get("", response_model=PageResult[dict])
def list_entries(
    table: str | None = Query(default=None, description="按数据表名过滤"),
    keyword: str | None = Query(default=None, description="按标识编号或分区标识检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """读取分区标识清单，可按数据表过滤；空结果返回空页。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(table=table, keyword=keyword, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分区标识 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条实际到达的分区标识，重复登记会被拦下。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message=f"分区标识校验未通过：{'、'.join(errors)}")
    return ActionResult(ok=True, message="分区标识已登记", entry=entry)


@router.get("/export/all")
def export_entries() -> dict[str, Any]:
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "partitionmark", "total": total, "items": items}
