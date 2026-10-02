"""交安设施接口：维护交安设施，覆盖登记污损、登记缺失、更换设施等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.traffic_facility import TrafficFacilityService

router = APIRouter(prefix="/api/traffic_facility", tags=["交安设施"])

service = TrafficFacilityService()

LIST_FIELDS = ["设施编号", "设施类型", "所属路段", "桩号位置", "责任组", "迁移标注", "设施状态"]
STATUSES = ["完好", "污损", "缺失", "已更换"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设施编号检索"),
    section: str | None = Query(default=None, description="按所属路段检索，如 ROAD-0001"),
    group: str | None = Query(default=None, description="按责任组检索，如 交安一组"),
    status: str | None = Query(default=None, description="完好、污损、缺失、已更换"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设施编号、所属路段、责任组与状态过滤交安设施列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, section=section, group=group, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条交安设施明细：含历史位置（原施工记录）与按设施编号去重后的同路段相关设施。"""
    detail = service.get_detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"交安设施 {entry_id} 不存在或已归档")
    return detail


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条交安设施，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="交安设施已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条交安设施执行登记污损、登记缺失、更换设施；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出交安设施清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "traffic_facility", "total": total, "items": items}
