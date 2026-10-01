"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

交安设施在首次装载时做一次存量迁移：
- 历史桩号按原施工记录保留（没有历史桩号就以当前桩号为原始值）；
- 没有责任组的存量设施标注为「待迁移」，必须先在巡查工作台核对责任组；
- 相同设施编号的重复记录归并为一条，避免详情里重复显示重复设施。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_ROWS

TRAFFIC_FACILITY_MODULE = "traffic_facility"


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 幂等键账本：同一业务键的并发/重试只允许落一条记录
        self._idempotency_keys: dict[str, dict[str, Any]] = {}
        # 写回台账、待办等跨表操作串行化，避免并发下重复落账
        self._lock = threading.RLock()
        self._migrate_traffic_facilities()
        self._sync_road_section_counts()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def add(self, module: str, entry: dict[str, Any]) -> dict[str, Any]:
        rows = self.rows(module)
        entry["id"] = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        rows.append(entry)
        return entry

    def idempotency_seen(self, key: str) -> dict[str, Any] | None:
        """幂等键命中时返回首次落账结果，未命中返回 None。"""
        return self._idempotency_keys.get(key)

    def remember_idempotency(self, key: str, result: dict[str, Any]) -> None:
        self._idempotency_keys.setdefault(key, result)

    def _migrate_traffic_facilities(self) -> None:
        """存量交安设施迁移：保留施工桩号、标注无责任组设施、归并重复编号。"""
        rows = self.rows(TRAFFIC_FACILITY_MODULE)
        merged: dict[str, dict[str, Any]] = {}
        next_duplicate_id = 9000
        for row in rows:
            # 历史位置按原施工记录保留，只在首次迁移时固化
            row.setdefault("历史桩号", row.get("桩号位置"))
            row.setdefault("原始桩号", row.get("桩号位置"))
            code = str(row.get("设施编号") or "").strip()
            if code in merged:
                primary = merged[code]
                primary.setdefault("重复记录", [])
                duplicate = dict(row)
                duplicate["重复ID"] = duplicate.pop("id", next_duplicate_id)
                next_duplicate_id += 1
                primary["重复记录"].append(duplicate)
                primary["重复标记"] = True
                continue
            row["重复标记"] = False
            row.setdefault("重复记录", [])
            if not str(row.get("责任组") or "").strip():
                # 存量无责任组设施：迁移标注，责任组边界未核对前不允许提交回写
                row["待迁移"] = True
                row["迁移状态"] = "待核对责任组"
            else:
                row.setdefault("待迁移", False)
                row.setdefault("迁移状态", "已归属")
            # 三步处置流程的状态位
            row.setdefault("责任组已核对", bool(row.get("责任组")))
            row.setdefault("桩号已确认", False)
            row.setdefault("已回写", False)
            merged[code] = row
        self._tables[TRAFFIC_FACILITY_MODULE] = list(merged.values())

    def _sync_road_section_counts(self) -> None:
        """路段清单上的关联设施数与设施台账保持一致。"""
        facility_rows = self.rows(TRAFFIC_FACILITY_MODULE)
        for road in self.rows("road_section"):
            name = road.get("路段名称")
            road["关联设施数"] = sum(
                1 for row in facility_rows if row.get("所属路段") == name
            )
            road.setdefault("最近设施回写", None)

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
