"""分区标识数据：登记数据表实际到达的分区，作为缺失分区重算的唯一事实来源。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "partitionmark"
REQUIRED_FIELDS = ["标识编号", "数据表名", "分区标识", "到达时间"]


class PartitionmarkService:
    def list_entries(
        self,
        *,
        table: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if table:
            rows = [row for row in rows if row.get("数据表名") == table]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("标识编号", "")) or keyword in str(row.get("分区标识", ""))
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        table = str(values["数据表名"]).strip()
        mark = str(values["分区标识"]).strip()
        for row in store.rows(MODULE):
            if row.get("数据表名") == table and row.get("分区标识") == mark:
                return None, [f"分区标识 {mark} 在表 {table} 已存在，请勿重复登记"]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: str(values.get(field)).strip() for field in REQUIRED_FIELDS})
        rows.append(entry)
        return entry, []

    def marks_of_table(self, table: str) -> list[dict[str, Any]]:
        """读取某张表当前全部已到达分区，检测流程只通过这里取数，保证口径唯一。"""
        return [dict(row) for row in store.rows(MODULE) if row.get("数据表名") == table]
