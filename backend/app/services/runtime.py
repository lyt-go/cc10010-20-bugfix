"""运行实例业务规则：状态流转、字段流转，以及读取缺失分区记录后的补齐判定。

跨模块的补跑/补齐判断不在本模块重写规则，统一委托
partition_detection.PartitionDetectionService：运行实例先读取缺失分区数据记录，
再由同一流程依据有效规则版本给出结论，并写成一份可回查的补齐任务记录。
"""
from __future__ import annotations

from typing import Any

from app.services.partition_detection import detection_service
from app.store import store

MODULE = "runtime"
REQUIRED_FIELDS = ["记录编号", "作业名称", "开始时间"]
STATUS_ORDER = ["草稿", "运行中", "成功", "失败", "已终止"]
ACTION_RULES = {"启动作业": "运行中", "终止作业": "已终止", "标记成功": "成功"}
NEGATIVE_ACTIONS = []


class RuntimeService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"运行记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于运行实例可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"运行记录已{action}"

    # ----- 缺失分区补齐：先读记录，再由统一流程判定 -----

    def list_missing_partitions(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """运行实例侧读取缺失分区数据记录，口径与分区检测模块完全一致。"""
        return detection_service.list_missing_partitions(**kwargs)

    def review_fill(self, record_id: int) -> tuple[dict[str, Any] | None, str, bool]:
        """只判定、不落任务：供业务侧确认补跑结论依据的是哪条记录与哪版规则。"""
        return detection_service.evaluate_fill(record_id)

    def create_fill_task(
        self, record_id: int, *, runtime_id: int | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """读取缺失分区记录后才创建补齐任务；暂停规则/容忍窗口/失效版本一律不建。"""
        if runtime_id is not None and store.find(MODULE, runtime_id) is None:
            return None, f"运行实例 {runtime_id} 不存在或已归档，无法关联补齐任务"
        return detection_service.create_fill_task(record_id, runtime_id=runtime_id)

    def list_fill_tasks(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        return detection_service.list_fill_tasks(**kwargs)
