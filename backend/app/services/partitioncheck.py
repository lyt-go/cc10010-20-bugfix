"""分区检测业务规则：状态流转、字段校验、规则版本与筛选口径都收在这里。

规则上的数值字段（期望分区数、分区延迟容忍、检测间隔分钟）在登记与修改两个入口
走同一套校验，负数或非整数一律拒绝；规则定义字段发生变化时递增规则版本，并把旧
版本快照留在规则历史里，供缺失分区记录回查。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "partitioncheck"
REQUIRED_FIELDS = ["规则编号", "数据表名", "期望分区数"]
OPTIONAL_INT_FIELDS = ["分区延迟容忍", "检测间隔分钟"]
RULE_DEFINITION_FIELDS = [
    "数据表名",
    "期望分区数",
    "分区延迟容忍",
    "缺失分区告警",
    "检测间隔分钟",
    "自动补齐开关",
    "补齐优先级",
]
SWITCH_VALUES = {"开启", "关闭"}
STATUS_ORDER = ["草稿", "规则评审中", "已启用", "已暂停"]
ACTION_RULES = {"提交规则评审": "规则评审中", "启用规则": "已启用", "暂停规则": "已暂停"}
NEGATIVE_ACTIONS = []
PAUSED_STATUS = "已暂停"
ACTIVE_STATUS = "已启用"


def _parse_int(field: str, value: Any, *, allow_zero: bool = True) -> tuple[int | None, str | None]:
    """把前端透传的数值统一解析成整数；负数、小数、非数字都给出可读错误。"""
    if value is None or str(value).strip() == "":
        return None, f"{field}必须填写整数"
    text = str(value).strip()
    try:
        number = int(text)
    except (TypeError, ValueError):
        return None, f"{field}必须是整数，收到的是「{text}」"
    if number < 0 or (not allow_zero and number == 0):
        return None, f"{field}不允许为负数或零，收到的是 {number}"
    return number, None


def validate_rule_values(values: dict[str, Any], *, partial: bool = False) -> tuple[dict[str, Any], list[str]]:
    """登记/修改规则共用的字段校验，返回清洗后的字段与错误清单。

    partial=True 时只校验提交上来的字段（修改入口），否则必填字段缺失也报错。
    """
    cleaned: dict[str, Any] = {}
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field == "期望分区数":
            continue
        raw = values.get(field)
        if not partial and not str(raw or "").strip():
            errors.append(field)
        elif raw is not None and str(raw).strip():
            cleaned[field] = str(raw).strip()

    if not partial or "期望分区数" in values:
        expected, error = _parse_int("期望分区数", values.get("期望分区数"), allow_zero=False)
        if error:
            errors.append(error)
        elif expected is not None:
            cleaned["期望分区数"] = expected

    for field in OPTIONAL_INT_FIELDS:
        if not partial and field not in values:
            cleaned[field] = 0
            continue
        if field not in values or values.get(field) is None or str(values.get(field)).strip() == "":
            if not partial:
                cleaned[field] = 0
            continue
        number, error = _parse_int(field, values.get(field), allow_zero=True)
        if error:
            errors.append(error)
        else:
            cleaned[field] = number

    switch = values.get("自动补齐开关")
    if switch is not None and str(switch).strip():
        switch = str(switch).strip()
        if switch not in SWITCH_VALUES:
            errors.append("自动补齐开关只允许填写「开启」或「关闭」")
        else:
            cleaned["自动补齐开关"] = switch
    elif not partial:
        cleaned["自动补齐开关"] = "关闭"

    for field in ["缺失分区告警", "补齐优先级"]:
        raw = values.get(field)
        if raw is not None and str(raw).strip():
            cleaned[field] = str(raw).strip()

    return cleaned, errors


class PartitioncheckService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        cleaned, errors = validate_rule_values(values)
        missing = [field for field in errors if field in REQUIRED_FIELDS]
        if missing:
            return None, missing
        if errors:
            return None, errors
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(cleaned)
        for field in ["缺失分区告警", "补齐优先级"]:
            entry.setdefault(field, "")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["规则版本"] = 1
        entry["规则历史"] = [
            {
                "规则版本": 1,
                "快照时间": datetime.now().isoformat(timespec="seconds"),
                "变更动作": "登记规则",
                "规则快照": dict(cleaned),
            }
        ]
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """修改规则定义：校验通过后落字段；定义字段真的变化才递增版本并留快照。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [f"检测规则 {entry_id} 不存在或已归档"]
        cleaned, errors = validate_rule_values(values, partial=True)
        if errors:
            return None, errors
        changed = {
            field: value
            for field, value in cleaned.items()
            if field in RULE_DEFINITION_FIELDS and entry.get(field) != value
        }
        if "规则编号" in cleaned and cleaned["规则编号"] != entry.get("规则编号"):
            entry["规则编号"] = cleaned["规则编号"]
        if not changed:
            return entry, []
        entry.update(changed)
        next_version = int(entry.get("规则版本", 1)) + 1
        entry["规则版本"] = next_version
        snapshot = {field: entry.get(field) for field in RULE_DEFINITION_FIELDS}
        entry.setdefault("规则历史", []).append(
            {
                "规则版本": next_version,
                "快照时间": datetime.now().isoformat(timespec="seconds"),
                "变更动作": "修改规则定义",
                "规则快照": snapshot,
            }
        )
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分区检测可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检测规则已{action}"
