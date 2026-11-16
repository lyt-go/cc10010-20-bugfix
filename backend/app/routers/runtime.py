"""运行实例接口：维护运行记录，并在读取缺失分区记录后决定是否补跑/补齐。

补齐相关接口只做参数透传，规则判断与任务落库都走 partition_detection 统一流程，
保证运行实例与分区检测两个入口指向同一份缺失分区数据记录。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.runtime import RuntimeService

router = APIRouter(prefix="/api/runtime", tags=["运行实例"])

service = RuntimeService()

LIST_FIELDS = ["记录编号", "作业名称", "开始时间", "结束时间", "执行状态", "读取行数", "写入行数", "错误信息"]
STATUSES = ["草稿", "运行中", "成功", "失败", "已终止"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="草稿、运行中、成功、失败、已终止"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤运行实例列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/missing-partitions", response_model=PageResult[dict])
def list_missing_partitions(
    rule_id: int | None = Query(default=None, description="按检测规则过滤"),
    batch_no: str | None = Query(default=None, description="按检测批次号回查"),
    only_effective: bool = Query(default=True, description="默认只看生效中的检测记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """运行实例读取缺失分区数据记录：与分区检测模块返回同一份记录。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_missing_partitions(
        rule_id=rule_id, batch_no=batch_no, only_effective=only_effective, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/fill-tasks/review", response_model=ActionResult)
def review_fill_task(payload: EntryPayload) -> ActionResult:
    """补跑前预判定：只给出结论与原因，不创建任务，便于业务侧核对规则版本。"""
    record_id = payload.values.get("missing_record_id") or payload.values.get("缺失记录id")
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="missing_record_id 必须是缺失分区记录的整数 id")
    record, message, allowed = service.review_fill(record_id)
    return ActionResult(ok=allowed, message=message, entry=record)


@router.post("/fill-tasks", response_model=ActionResult)
def create_fill_task(payload: EntryPayload) -> ActionResult:
    """读取缺失分区数据记录后创建补齐任务；判定不通过时不建任务并说明原因。"""
    record_id = payload.values.get("missing_record_id") or payload.values.get("缺失记录id")
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="missing_record_id 必须是缺失分区记录的整数 id")
    runtime_id = payload.values.get("runtime_id") or payload.values.get("运行实例id")
    if runtime_id is not None:
        try:
            runtime_id = int(runtime_id)
        except (TypeError, ValueError):
            runtime_id = None
    task, message = service.create_fill_task(record_id, runtime_id=runtime_id)
    if task is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=task)


@router.get("/fill-tasks", response_model=PageResult[dict])
def list_fill_tasks(
    record_id: int | None = Query(default=None, description="按缺失分区记录过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """回查已经生成的分区补齐任务。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_fill_tasks(record_id=record_id, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运行实例清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "runtime", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条运行记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"运行记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条运行记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="运行记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条运行记录执行启动作业、终止作业、标记成功；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
