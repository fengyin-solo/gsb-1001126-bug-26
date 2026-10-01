"""交安设施业务规则：责任组核对、人工桩号确认与回写三步流程都收在这里。

处置流程固定为「核对责任组 → 确认人工桩号 → 提交回写」：
- 责任组必须落在所属路段的责任组边界内，越界指派一律拒绝；
- 人工桩号必须落在所属路段起止桩号范围内，越界桩号一律拒绝；
- 历史桩号按原施工记录保留，人工确认只更新当前桩号并追加变更记录；
- 提交回写通过幂等键串行落账，并发重试只生成一条处置记录。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "traffic_facility"
ROAD_MODULE = "road_section"
PATROL_MODULE = "patrol"
WRITEBACK_MODULE = "facility_writeback"

REQUIRED_FIELDS = ["设施编号", "设施类型", "所属路段"]
STATUS_ORDER = ["完好", "污损", "缺失", "已更换"]
ACTION_RULES = {"登记污损": "污损", "登记缺失": "缺失", "更换设施": "已更换"}
NEGATIVE_ACTIONS = ["登记污损", "登记缺失"]
# 提交回写时可选的处置结论
RESOLVE_RULES = {"更换设施": "已更换", "现场修复": "完好"}

STAKE_PATTERN = re.compile(r"^K(\d+)\+(\d{3})$")


class ServiceError(Exception):
    """业务规则不满足时抛出，路由层据此返回对应的可读错误。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def parse_stake(value: str) -> int:
    """把 K12+300 形式的桩号解析成米数；格式不对直接拒绝。"""
    text = str(value or "").strip().upper().replace("－", "-")
    match = STAKE_PATTERN.match(text)
    if not match:
        raise ServiceError(f"桩号「{value}」格式不正确，应为 K 公里数+三位米数，例如 K12+300")
    kilometers, meters = (int(match.group(1)), int(match.group(2)))
    return kilometers * 1000 + meters


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_road(road_name: str) -> dict[str, Any]:
    for road in store.rows(ROAD_MODULE):
        if road.get("路段名称") == road_name:
            return road
    raise ServiceError(f"所属路段「{road_name}」不在路段清单中，无法核对责任边界", status_code=404)


def _sync_display_fields(entry: dict[str, Any]) -> None:
    """设施状态列与内部 status 保持同一口径，避免列表状态错位。"""
    entry["设施状态"] = entry.get("status")


def _workflow_state(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "责任组已核对": bool(entry.get("责任组已核对")),
        "桩号已确认": bool(entry.get("桩号已确认")),
        "已回写": bool(entry.get("已回写")),
        "待迁移": bool(entry.get("待迁移")),
        "迁移状态": entry.get("迁移状态", "已归属"),
    }


class TrafficFacilityService:
    # ---------- 列表与详情 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        road: str | None = None,
        status: str | None = None,
        pending_migration: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设施编号", ""))]
        if road:
            rows = [row for row in rows if road in str(row.get("所属路段", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if pending_migration is not None:
            rows = [row for row in rows if bool(row.get("待迁移")) == pending_migration]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [dict(row) for row in rows[start:start + size]]
        for row in page_rows:
            _sync_display_fields(row)
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        _sync_display_fields(detail)
        road = None
        for candidate in store.rows(ROAD_MODULE):
            if candidate.get("路段名称") == entry.get("所属路段"):
                road = candidate
                break
        detail["可选责任组"] = list(road.get("责任组选项", [])) if road else []
        detail["路段桩号范围"] = road.get("起止桩号") if road else None
        detail.update(_workflow_state(entry))
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        road_name = str(values.get("所属路段") or "").strip()
        try:
            _find_road(road_name)
        except ServiceError:
            return None, ["所属路段"]
        with store.lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = False
            entry["abnormal"] = False
            entry["桩号位置"] = str(values.get("桩号位置") or "").strip()
            entry["历史桩号"] = entry["桩号位置"]
            entry["原始桩号"] = entry["桩号位置"]
            entry["责任组"] = str(values.get("责任组") or "").strip()
            entry["待迁移"] = not bool(entry["责任组"])
            entry["迁移状态"] = "待核对责任组" if entry["待迁移"] else "已归属"
            entry["责任组已核对"] = bool(entry["责任组"])
            entry["桩号已确认"] = False
            entry["已回写"] = False
            entry["重复标记"] = False
            entry["重复记录"] = []
            _sync_display_fields(entry)
            rows.append(entry)
            self._refresh_road_counts()
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"交安设施 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于交安设施可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target in {"污损", "缺失"}
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if target == "已更换":
            entry["完好程度"] = "已更换"
        _sync_display_fields(entry)
        return entry, f"交安设施已{action}"

    # ---------- 巡查工作台 ----------

    def workbench(self, *, road: str | None = None) -> dict[str, Any]:
        """巡查待办：设施台账里待处置的设施 + 巡查记录里待办的巡查单。"""
        facility_todos: list[dict[str, Any]] = []
        for entry in store.rows(MODULE):
            if not (entry.get("pending") or entry.get("abnormal") or entry.get("待迁移")):
                continue
            if road and road not in str(entry.get("所属路段", "")):
                continue
            todo = {
                "id": entry["id"],
                "来源": "设施台账",
                "设施编号": entry.get("设施编号"),
                "设施类型": entry.get("设施类型"),
                "所属路段": entry.get("所属路段"),
                "桩号位置": entry.get("桩号位置"),
                "责任组": entry.get("责任组") or "待核对",
                "状态": entry.get("status"),
                **_workflow_state(entry),
            }
            facility_todos.append(todo)

        patrol_todos: list[dict[str, Any]] = []
        for record in store.rows(PATROL_MODULE):
            if record.get("status") not in {"待巡查", "巡查中"}:
                continue
            if road and road not in str(record.get("巡查路段", "")):
                continue
            patrol_todos.append({
                "id": record["id"],
                "来源": "巡查记录",
                "巡查编号": record.get("巡查编号"),
                "所属路段": record.get("巡查路段"),
                "状态": record.get("status"),
                "发现问题": record.get("发现问题"),
            })

        writebacks = store.rows(WRITEBACK_MODULE)
        return {
            "facility_todos": facility_todos,
            "patrol_todos": patrol_todos,
            "total": len(facility_todos) + len(patrol_todos),
            "待迁移": sum(1 for item in facility_todos if item["待迁移"]),
            "待核对责任组": sum(1 for item in facility_todos if not item["责任组已核对"]),
            "待确认桩号": sum(
                1 for item in facility_todos
                if item["责任组已核对"] and not item["桩号已确认"]
            ),
            "待回写": sum(
                1 for item in facility_todos
                if item["桩号已确认"] and not item["已回写"]
            ),
            "已处置": len(writebacks),
        }

    # ---------- 三步处置流程 ----------

    def verify_team(self, entry_id: int, team: str, operator: str | None = None) -> dict[str, Any]:
        """第一步：核对责任组。责任组必须在所属路段的责任组边界内。"""
        team = str(team or "").strip()
        if not team:
            raise ServiceError("责任组不能为空，请先核对该设施归属的责任组")
        with store.lock:
            entry = self._require_entry(entry_id)
            if entry.get("已回写"):
                raise ServiceError("该设施已提交回写，责任组不可再变更")
            road = _find_road(str(entry.get("所属路段") or ""))
            allowed_teams = [str(item) for item in road.get("责任组选项", [])]
            if team not in allowed_teams:
                # 越界操作必须拒绝，不能把设施挂到别的路段责任组上
                raise ServiceError(
                    f"责任组「{team}」不属于路段「{road.get('路段名称')}」的责任边界"
                    f"（可选：{'、'.join(allowed_teams) or '未配置'}），越界指派已拒绝"
                )
            entry["责任组"] = team
            entry["责任组已核对"] = True
            # 责任组一旦人工核对完成，存量迁移即落地
            entry["待迁移"] = False
            entry["迁移状态"] = "已归属"
            entry.setdefault("流程记录", []).append(
                {"步骤": "核对责任组", "责任组": team, "操作人": operator or "值班管理员", "时间": _now()}
            )
            _sync_display_fields(entry)
            self._refresh_road_counts()
            return dict(entry)

    def confirm_stake(self, entry_id: int, stake: str, operator: str | None = None) -> dict[str, Any]:
        """第二步：确认人工桩号。桩号必须落在所属路段起止范围内。"""
        stake = str(stake or "").strip()
        target_meter = parse_stake(stake)
        with store.lock:
            entry = self._require_entry(entry_id)
            if not entry.get("责任组已核对"):
                raise ServiceError("请先核对责任组，再确认人工桩号")
            if entry.get("已回写"):
                raise ServiceError("该设施已提交回写，桩号不可再变更")
            road = _find_road(str(entry.get("所属路段") or ""))
            start_meter = int(road.get("起点米数", 0))
            end_meter = int(road.get("终点米数", 0))
            if not start_meter <= target_meter <= end_meter:
                # 越界桩号必须拒绝，防止设施被写到别的路段
                raise ServiceError(
                    f"人工桩号 {stake} 超出路段「{road.get('路段名称')}」范围"
                    f"（{road.get('起止桩号')}），越界定位已拒绝"
                )
            previous = str(entry.get("桩号位置") or "").strip()
            # 历史位置按原施工记录保留，只更新当前桩号
            entry.setdefault("历史桩号", entry.get("原始桩号") or previous)
            entry["原始桩号"] = entry.get("原始桩号") or entry["历史桩号"]
            entry["桩号位置"] = stake
            entry["桩号已确认"] = True
            entry.setdefault("桩号变更记录", []).append({
                "原桩号": previous,
                "人工桩号": stake,
                "操作人": operator or "值班管理员",
                "时间": _now(),
            })
            entry.setdefault("流程记录", []).append(
                {"步骤": "确认人工桩号", "原桩号": previous, "人工桩号": stake,
                 "操作人": operator or "值班管理员", "时间": _now()}
            )
            _sync_display_fields(entry)
            return dict(entry)

    def submit_writeback(
        self,
        entry_id: int,
        *,
        idempotency_key: str | None = None,
        resolve_action: str | None = None,
        operator: str | None = None,
        remark: str | None = None,
    ) -> dict[str, Any]:
        """第三步：提交回写。结论落到设施台账、路段清单和巡查待办。"""
        resolve_action = str(resolve_action or "更换设施").strip()
        if resolve_action not in RESOLVE_RULES:
            raise ServiceError(
                f"处置措施「{resolve_action}」不支持，可选：{'、'.join(RESOLVE_RULES)}"
            )
        key = str(idempotency_key or f"facility:{entry_id}:writeback").strip() or f"facility:{entry_id}:writeback"
        with store.lock:
            # 并发/重试：幂等键已命中时直接回放首次结果，只落一条记录
            existed = store.idempotency_seen(key)
            if existed is not None:
                return dict(existed)

            entry = self._require_entry(entry_id)
            if not entry.get("责任组已核对"):
                raise ServiceError("责任组尚未核对，不能提交回写")
            if not entry.get("桩号已确认"):
                raise ServiceError("人工桩号尚未确认，不能提交回写")
            if entry.get("已回写"):
                # 同一设施走另一个键重复提交也不允许再落账
                previous = self._find_writeback(entry_id)
                if previous is not None:
                    return dict(previous)
                raise ServiceError("该设施已提交回写，不能重复处置")

            target_status = RESOLVE_RULES[resolve_action]
            timestamp = _now()
            # 1) 设施台账：状态流转、待办解除
            entry["status"] = target_status
            entry["pending"] = False
            entry["abnormal"] = False
            entry["已回写"] = True
            entry["回写时间"] = timestamp
            entry["完好程度"] = "已更换" if target_status == "已更换" else "已修复"
            entry.setdefault("流程记录", []).append(
                {"步骤": "提交回写", "处置措施": resolve_action,
                 "操作人": operator or "值班管理员", "时间": timestamp}
            )
            _sync_display_fields(entry)

            # 2) 巡查待办结论落账（幂等键保护，重试只落一条）
            writeback_rows = store.rows(WRITEBACK_MODULE)
            record_id = max((int(row.get("id", 0)) for row in writeback_rows), default=0) + 1
            record = {
                "id": record_id,
                "回写编号": f"CL-{datetime.now().strftime('%Y%m%d')}-{record_id:03d}",
                "设施编号": entry.get("设施编号"),
                "所属路段": entry.get("所属路段"),
                "桩号位置": entry.get("桩号位置"),
                "历史桩号": entry.get("历史桩号"),
                "责任组": entry.get("责任组"),
                "处置措施": resolve_action,
                "处置结论": target_status,
                "操作人": operator or "值班管理员",
                "备注": remark or "",
                "回写时间": timestamp,
                "来源": "巡查工作台",
                "设施台账ID": entry_id,
                "幂等键": key,
            }
            writeback_rows.append(record)

            # 3) 路段清单：刷新关联设施数并记录最近回写
            self._refresh_road_counts()
            road = _find_road(str(entry.get("所属路段") or ""))
            road["最近设施回写"] = f"{entry.get('设施编号')} {entry.get('桩号位置')} {timestamp}"

            result = {
                "ok": True,
                "message": f"{entry.get('设施编号')} 已完成回写：设施台账、路段清单、巡查待办同步更新",
                "entry": dict(entry),
                "writeback": dict(record),
                "idempotent": False,
            }
            store.remember_idempotency(key, result)
            return dict(result)

    def list_writebacks(self, *, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(WRITEBACK_MODULE)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    # ---------- 内部辅助 ----------

    def _require_entry(self, entry_id: int) -> dict[str, Any]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise ServiceError(f"交安设施 {entry_id} 不存在或已归档", status_code=404)
        return entry

    def _find_writeback(self, entry_id: int) -> dict[str, Any] | None:
        for record in store.rows(WRITEBACK_MODULE):
            if record.get("设施台账ID") == entry_id:
                return record
        return None

    def _refresh_road_counts(self) -> None:
        for road in store.rows(ROAD_MODULE):
            name = road.get("路段名称")
            road["关联设施数"] = sum(
                1 for row in store.rows(MODULE) if row.get("所属路段") == name
            )
