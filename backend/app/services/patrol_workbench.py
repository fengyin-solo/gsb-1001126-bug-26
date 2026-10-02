"""巡查工作台：核对责任组 → 确认人工桩号 → 提交回写 的三段式流转。

- 责任边界校验：核对的责任组必须落在路段责任边界内，越界一律拒绝；
- 桩号边界校验：人工桩号必须落在路段起止桩号范围内，越界一律拒绝；
- 位置回写：结论同时落到设施台账、路段清单和巡查待办，历史位置按原施工记录保留；
- 幂等回写：同一幂等键的并发重试只落一条记录，重复提交直接返回首次结果。
"""
from __future__ import annotations

import re
import threading
from datetime import date
from typing import Any

from app.store import store

PATROL_MODULE = "patrol"
FACILITY_MODULE = "traffic_facility"
SECTION_MODULE = "road_section"

STEPS = ["待核对责任组", "待确认桩号", "待提交回写", "已回写"]

_STAKE_RE = re.compile(r"^[Kk](\d+)\+(\d{1,3})$")


def parse_stake(text: object) -> int | None:
    """把 K12+345 形式的桩号换算成米数；无法解析时返回 None。"""
    match = _STAKE_RE.match(str(text or "").strip())
    if not match:
        return None
    km, meter = int(match.group(1)), int(match.group(2))
    if meter >= 1000:
        return None
    return km * 1000 + meter


def parse_range(text: object) -> tuple[int, int] | None:
    """把 K0+000~K8+500 形式的起止桩号解析为起止米数。"""
    parts = str(text or "").split("~")
    if len(parts) != 2:
        return None
    start, end = parse_stake(parts[0]), parse_stake(parts[1])
    if start is None or end is None or start > end:
        return None
    return start, end


def format_stake(meters: int) -> str:
    return f"K{meters // 1000}+{meters % 1000:03d}"


class PatrolWorkbenchService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idempotency: dict[str, dict[str, Any]] = {}

    # ---------- 待办清单 ----------
    def list_todos(self) -> list[dict[str, Any]]:
        return [self._join(row) for row in store.rows(PATROL_MODULE)]

    def _find_todo(self, todo_id: int) -> dict[str, Any] | None:
        for row in store.rows(PATROL_MODULE):
            if int(row.get("id", 0)) == todo_id:
                return row
        return None

    def _find_section(self, code: object) -> dict[str, Any] | None:
        for row in store.rows(SECTION_MODULE):
            if row.get("路段编号") == code:
                return row
        return None

    def _find_facility(self, code: object) -> dict[str, Any] | None:
        for row in store.rows(FACILITY_MODULE):
            if row.get("设施编号") == code:
                return row
        return None

    def _join(self, todo: dict[str, Any]) -> dict[str, Any]:
        section = self._find_section(todo.get("巡查路段"))
        facility = self._find_facility(todo.get("关联设施"))
        data = dict(todo)
        data["巡查状态"] = str(todo.get("status") or "")
        data["路段名称"] = section.get("路段名称") if section else None
        data["路段责任组"] = section.get("责任组") if section else None
        data["起止桩号"] = section.get("起止桩号") if section else None
        data["设施类型"] = facility.get("设施类型") if facility else None
        data["设施责任组"] = (facility.get("责任组") or "未分配") if facility else None
        data["设施桩号"] = facility.get("桩号位置") if facility else None
        data["设施迁移标注"] = facility.get("迁移标注") if facility else None
        return data

    # ---------- 第一步：核对责任组 ----------
    def verify_group(self, todo_id: int, group: object) -> tuple[dict[str, Any] | None, str]:
        todo = self._find_todo(todo_id)
        if todo is None:
            return None, f"巡查待办 {todo_id} 不存在或已归档"
        if todo.get("step") != STEPS[0]:
            return None, f"当前环节为「{todo.get('step')}」，不能核对责任组"
        group_text = str(group or "").strip()
        if not group_text:
            return None, "请先填写核对后的责任组"
        section = self._find_section(todo.get("巡查路段"))
        if section is None:
            return None, f"路段 {todo.get('巡查路段')} 不存在，无法核对责任边界"
        if group_text != str(section.get("责任组") or ""):
            return None, (
                f"责任组「{group_text}」超出路段 {section.get('路段编号')} 的责任边界"
                f"（应为「{section.get('责任组')}」），越界操作已拒绝"
            )
        facility = self._find_facility(todo.get("关联设施"))
        if facility is None:
            return None, f"关联设施 {todo.get('关联设施')} 不存在，无法核对责任组"
        todo["核对责任组"] = group_text
        # 存量无责任组设施：核对环节打上迁移标注，回写时补录进设施台账
        todo["迁移标注"] = "待迁移补录" if not facility.get("责任组") else None
        todo["step"] = STEPS[1]
        return self._join(todo), f"责任组已核对为「{group_text}」"

    # ---------- 第二步：确认人工桩号 ----------
    def confirm_stake(self, todo_id: int, stake: object) -> tuple[dict[str, Any] | None, str]:
        todo = self._find_todo(todo_id)
        if todo is None:
            return None, f"巡查待办 {todo_id} 不存在或已归档"
        if todo.get("step") != STEPS[1]:
            return None, f"当前环节为「{todo.get('step')}」，不能确认人工桩号"
        meters = parse_stake(stake)
        if meters is None:
            return None, f"人工桩号「{stake}」格式不正确，应为 K12+345 形式"
        section = self._find_section(todo.get("巡查路段"))
        bounds = parse_range(section.get("起止桩号")) if section else None
        if bounds is None:
            return None, f"路段 {todo.get('巡查路段')} 的起止桩号无法解析，无法校验桩号边界"
        if not bounds[0] <= meters <= bounds[1]:
            return None, (
                f"人工桩号 {format_stake(meters)} 超出路段 {section.get('路段编号')} "
                f"范围 {section.get('起止桩号')}，越界操作已拒绝"
            )
        todo["人工桩号"] = format_stake(meters)
        todo["step"] = STEPS[2]
        return self._join(todo), f"人工桩号已确认为 {format_stake(meters)}"

    # ---------- 第三步：提交回写（幂等） ----------
    def writeback(self, todo_id: int, idempotency_key: object) -> tuple[dict[str, Any] | None, str, bool]:
        key = str(idempotency_key or "").strip()
        if not key:
            return None, "缺少幂等键，回写已拒绝", False
        with self._lock:
            cached = self._idempotency.get(key)
            if cached is not None:
                return cached["entry"], "重复提交已忽略：同一幂等键只落一条记录", True
            todo = self._find_todo(todo_id)
            if todo is None:
                return None, f"巡查待办 {todo_id} 不存在或已归档", False
            if todo.get("step") != STEPS[2]:
                return None, f"当前环节为「{todo.get('step')}」，不能提交回写", False
            facility = self._find_facility(todo.get("关联设施"))
            section = self._find_section(todo.get("巡查路段"))
            if facility is None or section is None:
                return None, "关联设施或路段缺失，回写已拒绝", False
            today = date.today().isoformat()
            # 设施台账：历史位置按原施工记录保留，再写入新桩号与核对后的责任组
            history = facility.setdefault("历史位置", [])
            old_stake = str(facility.get("桩号位置") or "").strip()
            if old_stake and all(item.get("桩号位置") != old_stake for item in history):
                history.append({
                    "桩号位置": old_stake,
                    "来源": "原施工记录",
                    "日期": str(facility.get("设置日期") or today),
                })
            facility["桩号位置"] = todo["人工桩号"]
            facility["责任组"] = todo["核对责任组"]
            if todo.get("迁移标注"):
                facility["迁移标注"] = f"已迁移补录（{today}）"
            facility["最近回写"] = today
            # 路段清单：回写结论同步到路段
            section["最近回写"] = today
            section["交安设施数"] = sum(
                1 for row in store.rows(FACILITY_MODULE)
                if row.get("所属路段") == section.get("路段编号")
            )
            # 巡查待办：环节办结
            todo["step"] = STEPS[3]
            todo["status"] = "已完成"
            todo["pending"] = False
            todo["幂等键"] = key
            todo["回写时间"] = today
            todo["处置措施"] = f"桩号回写为 {todo['人工桩号']}，责任组 {todo['核对责任组']}"
            entry = {
                "巡查待办": self._join(todo),
                "设施台账": dict(facility),
                "路段清单": dict(section),
            }
            self._idempotency[key] = {"todo_id": todo_id, "entry": entry}
            return entry, "回写完成：结论已落到设施台账、路段清单和巡查待办", False
