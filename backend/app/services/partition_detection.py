"""分区检测统一流程：规则校验、缺失分区重算与补齐任务判定都收敛到这里。

设计要点（对应缺陷修复口径）：

1. 检测只认有效规则版本。记录会固化规则版本与规则快照，缺失分区清单和完整性
   结论都能回查到产生它们的那版规则；规则后续修改不改变历史记录。
2. 规则暂停后保留旧缺失记录，但新检测不再写入缺失分区、不再产生补齐任务；
   迟到的检测批次遇到已暂停规则时返回旧结论而不是重新生成缺失分区。
3. 分区延迟容忍窗口内才到达的分区不算缺失（容忍中），窗口外仍未到达才算缺失，
   避免容忍窗口被忽略。
4. 同一条规则并发检测时用非阻塞互斥锁保证只有一个检测结果生效，重复批次直接
   返回原记录，不重复落库、不重复建任务。
5. 补齐任务只在运行实例侧读取缺失分区记录后创建；检测本身只产记录、不建任务。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from app.services.partitioncheck import ACTIVE_STATUS, PAUSED_STATUS
from app.services.partitionmark import PartitionmarkService
from app.store import store

RULE_MODULE = "partitioncheck"
RECORD_MODULE = "missingpartition"
FILL_TASK_MODULE = "partitionfilltask"

SWITCH_ON = "开启"

_detection_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
mark_service = PartitionmarkService()


def _now() -> datetime:
    return datetime.now()


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fallback_expected_partitions(rule: dict[str, Any], detect_time: datetime) -> list[str]:
    """调用方未显式给出期望分区时，按检测时刻向前取期望分区数个自然日分区。"""
    expected_count = int(rule["期望分区数"])
    return [
        f"dt={(detect_time - timedelta(days=offset)).strftime('%Y-%m-%d')}"
        for offset in range(expected_count - 1, -1, -1)
    ]


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


def _rule_snapshot(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "规则编号": rule.get("规则编号"),
        "数据表名": rule.get("数据表名"),
        "期望分区数": rule.get("期望分区数"),
        "分区延迟容忍": rule.get("分区延迟容忍", 0),
        "检测间隔分钟": rule.get("检测间隔分钟", 0),
        "自动补齐开关": rule.get("自动补齐开关", "关闭"),
        "补齐优先级": rule.get("补齐优先级", ""),
        "规则版本": rule.get("规则版本", 1),
    }


def _latest_record(rule_id: int) -> dict[str, Any] | None:
    records = [
        row for row in store.rows(RECORD_MODULE) if int(row.get("规则id", 0)) == rule_id
    ]
    if not records:
        return None
    return max(records, key=lambda row: int(row.get("id", 0)))


def get_detection_lock(rule_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _detection_locks.get(rule_id)
        if lock is None:
            lock = threading.Lock()
            _detection_locks[rule_id] = lock
        return lock


class PartitionDetectionService:
    """规则检测、缺失分区清单查询与补齐判定的唯一入口。"""

    # ----- 缺失分区记录的查询口径 -----

    def list_missing_partitions(
        self,
        *,
        rule_id: int | None = None,
        batch_no: str | None = None,
        only_effective: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(RECORD_MODULE))
        if rule_id is not None:
            rows = [row for row in rows if int(row.get("规则id", 0)) == rule_id]
        if batch_no:
            rows = [row for row in rows if row.get("检测批次号") == batch_no]
        if only_effective:
            rows = [row for row in rows if row.get("是否生效")]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        return store.find(RECORD_MODULE, record_id)

    # ----- 统一检测流程 -----

    def detect(
        self,
        rule_id: int,
        *,
        expected_partitions: list[str] | None = None,
        batch_no: str | None = None,
        detect_time: datetime | None = None,
    ) -> tuple[dict[str, Any] | None, list[str], bool]:
        """对一条规则执行检测，返回 (记录或None, 错误/说明, 是否生效)。

        路由层、运行实例侧等所有入口都调用本方法，不再各自实现规则判断。
        """
        rule = store.find(RULE_MODULE, rule_id)
        if rule is None:
            return None, [f"检测规则 {rule_id} 不存在或已归档"], False

        detect_time = detect_time or _now()
        tolerance = int(rule.get("分区延迟容忍") or 0)

        # 幂等：同一检测批次重放，直接回查原记录，不重复重算。
        if batch_no:
            for row in store.rows(RECORD_MODULE):
                if row.get("检测批次号") == batch_no and int(row.get("规则id", 0)) == rule_id:
                    return row, [f"检测批次 {batch_no} 已存在，回查原检测记录"], bool(row.get("是否生效"))

        lock = get_detection_lock(rule_id)
        if not lock.acquire(blocking=False):
            # 并发检测同一规则：只允许先拿到锁的那个结果生效。
            return None, ["该规则正在检测中，并发请求已忽略，请稍后读取生效结果"], False

        try:
            # 暂停规则：保留旧缺失记录，但不新增记录、不新增补齐任务。
            if rule.get("status") == PAUSED_STATUS:
                latest = _latest_record(rule_id)
                messages = ["规则已暂停，本次检测不产生新的缺失分区记录，保留暂停前结论"]
                if latest is not None:
                    messages.append(f"回查检测记录 {latest.get('检测批次号')}")
                return latest, messages, False

            if rule.get("status") != ACTIVE_STATUS:
                return None, [f"规则状态为「{rule.get('status')}」，仅「{ACTIVE_STATUS}」规则可以执行检测"], False

            expected = [str(item).strip() for item in (expected_partitions or []) if str(item).strip()]
            if not expected:
                expected = _fallback_expected_partitions(rule, detect_time)
            expected_count = int(rule["期望分区数"])
            if len(expected) != expected_count:
                return (
                    None,
                    [f"期望分区清单 {len(expected)} 个与规则期望分区数 {expected_count} 不一致，已拒绝重算"],
                    False,
                )

            marks = mark_service.marks_of_table(str(rule["数据表名"]))
            arrival_by_mark: dict[str, datetime] = {}
            for mark in marks:
                arrived_at = _parse_time(mark.get("到达时间"))
                if arrived_at is not None:
                    arrival_by_mark[str(mark["分区标识"])] = arrived_at

            missing: list[dict[str, Any]] = []
            tolerated: list[dict[str, Any]] = []
            arrived: list[str] = []
            # 每个期望分区按其在清单中的次序确定应到时间：越旧的分区应到时间越早。
            # 分区到期后再给一个延迟容忍窗口，窗口内仍未到达记「容忍中」，窗口外
            # 仍未到达才是真正的缺失分区；容忍为 0 时未到达一律算缺失。
            interval = int(rule.get("检测间隔分钟") or 0)
            total_expected = len(expected)
            for index, partition in enumerate(expected, start=1):
                arrived_at = arrival_by_mark.get(partition)
                if arrived_at is not None:
                    arrived.append(partition)
                    continue
                # 最新分区 age_slots=0、此刻到期，越旧的分区应到时间每档前移一个间隔。
                age_slots = total_expected - index
                due_time = detect_time - timedelta(minutes=interval * age_slots)
                tolerate_until = due_time + timedelta(minutes=tolerance)
                item = {
                    "分区标识": partition,
                    "序号": index,
                    "应到时间": due_time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "容忍截止时间": tolerate_until.strftime("%Y-%m-%dT%H:%M:%S"),
                    "延迟容忍分钟": tolerance,
                }
                if tolerance > 0 and detect_time <= tolerate_until:
                    tolerated.append(item)
                else:
                    missing.append(item)

            integrity = "完整" if not missing else "不完整"
            record_id = _next_id(RECORD_MODULE)
            record = {
                "id": record_id,
                "记录编号": f"MP-{detect_time.strftime('%Y%m%d%H%M%S')}-{rule_id}-{record_id}",
                "检测批次号": batch_no or f"BATCH-{detect_time.strftime('%Y%m%d%H%M%S')}-{rule_id}",
                "规则id": rule_id,
                "规则编号": rule.get("规则编号"),
                "规则版本": rule.get("规则版本", 1),
                "规则快照": _rule_snapshot(rule),
                "数据表名": rule.get("数据表名"),
                "检测时间": detect_time.isoformat(timespec="seconds"),
                "期望分区数": expected_count,
                "期望分区清单": expected,
                "已到达分区": arrived,
                "容忍中分区": tolerated,
                "缺失分区清单": missing,
                "缺失分区数": len(missing),
                "分区完整性结论": integrity,
                "延迟容忍分钟": tolerance,
                "自动补齐开关": rule.get("自动补齐开关", "关闭"),
                "补齐优先级": rule.get("补齐优先级", ""),
                "是否生效": True,
                "暂停后保留": False,
                "status": "草稿",
                "pending": bool(missing),
                "abnormal": bool(missing),
            }
            store.rows(RECORD_MODULE).append(record)
            return record, [], True
        finally:
            lock.release()

    # ----- 运行实例侧：读取缺失分区记录后再决定补齐 -----

    def evaluate_fill(self, record_id: int) -> tuple[dict[str, Any] | None, str, bool]:
        """跨模块补齐判定的唯一口径：先读缺失分区记录，再核对有效规则版本。

        返回 (记录, 说明, 是否允许补齐)。判定只依据当前有效规则版本，暂停规则、
        关闭自动补齐、记录已不完整或已存在任务时都不允许新建补齐任务。
        """
        record = store.find(RECORD_MODULE, record_id)
        if record is None:
            return None, f"缺失分区记录 {record_id} 不存在或已归档", False
        if not record.get("是否生效"):
            return record, "该检测记录未生效，不能作为补齐依据", False
        if int(record.get("缺失分区数", 0)) <= 0:
            return record, "分区完整性结论为完整，无需补齐", False

        rule = store.find(RULE_MODULE, int(record["规则id"]))
        if rule is None:
            return record, "产生记录的检测规则已不存在，拒绝补齐", False
        if rule.get("status") == PAUSED_STATUS:
            return record, "规则已暂停，保留历史缺失记录但不新建补齐任务", False
        if rule.get("status") != ACTIVE_STATUS:
            return record, f"规则当前状态为「{rule.get('status')}」，不是有效规则版本，拒绝补齐", False
        if record.get("规则版本") != rule.get("规则版本"):
            return record, "检测记录对应的规则版本已过期，请按当前规则版本重新检测", False
        if rule.get("自动补齐开关") != SWITCH_ON:
            return record, "规则未开启自动补齐，不创建补齐任务", False
        return record, "规则版本有效且存在缺失分区，允许创建补齐任务", True

    def create_fill_task(
        self, record_id: int, *, runtime_id: int | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """运行实例读取缺失分区记录后调用：判定通过才写一条补齐任务。"""
        record, message, allowed = self.evaluate_fill(record_id)
        if not allowed:
            return None, message

        for task in store.rows(FILL_TASK_MODULE):
            if int(task.get("缺失记录id", 0)) == record_id and task.get("任务状态") != "已取消":
                return task, f"缺失分区记录 {record_id} 已存在补齐任务 {task.get('任务编号')}，不重复创建"

        now = _now()
        task = {
            "id": _next_id(FILL_TASK_MODULE),
            "任务编号": f"PFT-{now.strftime('%Y%m%d%H%M%S')}-{record_id}",
            "缺失记录id": record_id,
            "检测批次号": record.get("检测批次号"),
            "规则id": record.get("规则id"),
            "规则编号": record.get("规则编号"),
            "规则版本": record.get("规则版本"),
            "数据表名": record.get("数据表名"),
            "待补齐分区": [item["分区标识"] for item in record.get("缺失分区清单", [])],
            "补齐优先级": record.get("补齐优先级", ""),
            "来源运行实例id": runtime_id,
            "创建时间": now.isoformat(timespec="seconds"),
            "任务状态": "待执行",
            "status": "草稿",
            "pending": True,
            "abnormal": False,
        }
        store.rows(FILL_TASK_MODULE).append(task)
        return task, "已读取缺失分区记录并创建分区补齐任务"

    def list_fill_tasks(
        self, *, record_id: int | None = None, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(FILL_TASK_MODULE))
        if record_id is not None:
            rows = [row for row in rows if int(row.get("缺失记录id", 0)) == record_id]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total


detection_service = PartitionDetectionService()
