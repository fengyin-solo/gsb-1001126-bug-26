"""交安设施接口：列表/详情、巡查工作台与「核对责任组→确认桩号→提交回写」三步处置。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.traffic_facility import ServiceError, TrafficFacilityService

router = APIRouter(prefix="/api/traffic_facility", tags=["交安设施"])

service = TrafficFacilityService()

LIST_FIELDS = ["设施编号", "设施类型", "所属路段", "桩号位置", "设置日期", "反光等级", "完好程度", "设施状态"]
STATUSES = ["完好", "污损", "缺失", "已更换"]


def _fail(exc: ServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设施编号检索"),
    road: str | None = Query(default=None, description="按所属路段检索"),
    status: str | None = Query(default=None, description="完好、污损、缺失、已更换"),
    pending_migration: bool | None = Query(default=None, description="只看待迁移（无责任组）设施"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设施编号、路段、状态过滤交安设施列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, road=road, status=status,
        pending_migration=pending_migration, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出交安设施清单：返回当前台账全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "traffic_facility", "total": total, "items": items}


@router.get("/workbench/summary")
def workbench_summary(
    road: str | None = Query(default=None, description="按路段过滤巡查待办"),
) -> dict[str, Any]:
    """巡查工作台：设施待办、巡查待办与三步流程的数量分布。"""
    return service.workbench(road=road)


@router.get("/writebacks")
def list_writebacks(page: int = 1, size: int = 20) -> PageResult[dict]:
    """巡查处置回写记录：提交回写后落到这里，幂等键保证只落一条。"""
    items, total = service.list_writebacks(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条交安设施明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"交安设施 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条交安设施，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段或所属路段无效：{'、'.join(missing)}")
    return ActionResult(ok=True, message="交安设施已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条交安设施执行登记污损、登记缺失、更换设施；不允许的动作会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/verify-team", response_model=ActionResult)
def verify_team(entry_id: int, payload: EntryPayload) -> ActionResult:
    """第一步：核对责任组。责任组超出路段责任边界时拒绝。"""
    try:
        entry = service.verify_team(
            entry_id,
            str(payload.values.get("责任组") or ""),
            operator=payload.values.get("操作人"),
        )
    except ServiceError as exc:
        raise _fail(exc) from exc
    return ActionResult(ok=True, message=f"责任组已核对：{entry['责任组']}", entry=entry)


@router.post("/{entry_id}/confirm-stake", response_model=ActionResult)
def confirm_stake(entry_id: int, payload: EntryPayload) -> ActionResult:
    """第二步：确认人工桩号。桩号超出路段范围或未先核对责任组时拒绝。"""
    try:
        entry = service.confirm_stake(
            entry_id,
            str(payload.values.get("桩号位置") or ""),
            operator=payload.values.get("操作人"),
        )
    except ServiceError as exc:
        raise _fail(exc) from exc
    return ActionResult(ok=True, message=f"人工桩号已确认：{entry['桩号位置']}", entry=entry)


@router.post("/{entry_id}/writeback", response_model=ActionResult)
def submit_writeback(entry_id: int, payload: EntryPayload) -> ActionResult:
    """第三步：提交回写。幂等键命中时回放首次结果，并发重试只落一条记录。"""
    try:
        result = service.submit_writeback(
            entry_id,
            idempotency_key=payload.values.get("idempotency_key"),
            resolve_action=payload.values.get("处置措施"),
            operator=payload.values.get("操作人"),
            remark=payload.remark,
        )
    except ServiceError as exc:
        raise _fail(exc) from exc
    return ActionResult(
        ok=True,
        message=str(result["message"]),
        entry=result["entry"],
    )
