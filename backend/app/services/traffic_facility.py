"""交安设施业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "traffic_facility"
REQUIRED_FIELDS = ["设施编号", "设施类型", "所属路段"]
STATUS_ORDER = ["完好", "污损", "缺失", "已更换"]
ACTION_RULES = {"登记污损": "污损", "登记缺失": "缺失", "更换设施": "已更换"}
NEGATIVE_ACTIONS = []


def serialize(row: dict[str, Any]) -> dict[str, Any]:
    """列表/详情共用的输出口径：设施状态以 status 为准，缺省字段补齐，避免状态错位。"""
    data = dict(row)
    data["设施状态"] = str(row.get("status") or STATUS_ORDER[0])
    data["责任组"] = row.get("责任组") or "未分配"
    data.setdefault("迁移标注", None)
    data.setdefault("最近回写", None)
    data.setdefault("历史位置", [])
    return data


class TrafficFacilityService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        section: str | None = None,
        group: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设施编号", ""))]
        if section:
            rows = [row for row in rows if section in str(row.get("所属路段", ""))]
        if group:
            rows = [row for row in rows if str(row.get("责任组") or "") == group]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [serialize(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return serialize(entry) if entry is not None else None

    def get_detail(self, entry_id: int) -> dict[str, Any] | None:
        """单条设施详情：同路段相关设施按设施编号去重，重复登记的设施只显示一次。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = serialize(entry)
        seen = {str(entry.get("设施编号", ""))}
        related: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            code = str(row.get("设施编号", ""))
            if row.get("所属路段") != entry.get("所属路段") or code in seen:
                continue
            seen.add(code)
            related.append(serialize(row))
        detail["related"] = related
        return detail

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
        entry["责任组"] = values.get("责任组") or None
        entry["迁移标注"] = None
        entry["最近回写"] = None
        entry["历史位置"] = []
        rows.append(entry)
        return serialize(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"交安设施 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于交安设施可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return serialize(entry), f"交安设施已{action}"
