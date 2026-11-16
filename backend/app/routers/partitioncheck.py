"""分区检测接口：维护检测规则、触发统一检测流程、回查缺失分区记录。

规则判断不在路由层实现：登记/修改的字段校验在 service，检测重算与补齐判定在
partition_detection 统一流程里，路由只负责参数透传与结果包装。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.partition_detection import detection_service
from app.services.partitioncheck import PartitioncheckService

router = APIRouter(prefix="/api/partitioncheck", tags=["分区检测"])

service = PartitioncheckService()

LIST_FIELDS = ["规则编号", "数据表名", "期望分区数", "分区延迟容忍", "缺失分区告警", "检测间隔分钟", "自动补齐开关", "补齐优先级"]
STATUSES = ["草稿", "规则评审中", "已启用", "已暂停"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、规则评审中、已启用、已暂停"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤分区检测列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/missing-partitions", response_model=PageResult[dict])
def list_missing_partitions(
    rule_id: int | None = Query(default=None, description="按检测规则过滤"),
    batch_no: str | None = Query(default=None, description="按检测批次号回查"),
    only_effective: bool = Query(default=False, description="只看生效中的检测记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """缺失分区数据记录清单：所有模块都通过这里回查同一份记录。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = detection_service.list_missing_partitions(
        rule_id=rule_id, batch_no=batch_no, only_effective=only_effective, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/missing-partitions/{record_id}", response_model=dict)
def get_missing_partition(record_id: int) -> dict:
    """读取单条缺失分区记录，含规则版本与规则快照，可直接回查结论来源。"""
    record = detection_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"缺失分区记录 {record_id} 不存在或已归档")
    return record


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分区检测清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "partitioncheck", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条检测规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检测规则，缺字段或数值非法（如负数期望分区数）时说明原因。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message=f"规则校验未通过：{'、'.join(errors)}")
    return ActionResult(ok=True, message="检测规则已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改规则定义：与登记共用校验口径，定义字段变化时递增规则版本并保留历史。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=f"规则校验未通过：{'、'.join(errors)}")
    if errors:
        return ActionResult(ok=False, message=f"规则校验未通过：{'、'.join(errors)}", entry=entry)
    return ActionResult(ok=True, message="检测规则已更新并生成新版本", entry=entry)


@router.post("/{entry_id}/detect", response_model=ActionResult)
def detect_partitions(entry_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """触发一次分区检测，走统一重算流程并写入可回查的缺失分区记录。

    可在 values 里传 expected_partitions（期望分区清单）与 batch_no（检测批次号）。
    暂停规则、并发检测、容忍窗口等口径全部由统一流程处理。
    """
    values = payload.values if payload is not None else {}
    expected = values.get("expected_partitions") or values.get("期望分区清单")
    if isinstance(expected, str):
        expected = [item.strip() for item in expected.split(",") if item.strip()]
    batch_no = values.get("batch_no") or values.get("检测批次号")
    record, messages, effective = detection_service.detect(
        entry_id, expected_partitions=expected, batch_no=str(batch_no) if batch_no else None
    )
    if record is None:
        return ActionResult(ok=False, message="；".join(messages))
    message = "检测完成，缺失分区记录已生成" if effective else "；".join(messages)
    return ActionResult(ok=effective, message=message, entry=record)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检测规则执行提交规则评审、启用规则、暂停规则；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
