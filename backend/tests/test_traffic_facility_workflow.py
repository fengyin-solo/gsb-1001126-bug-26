"""交安设施处置流程回归测试：覆盖列表页、详情页、巡查工作台三个复现入口。

直接运行：python -m tests.test_traffic_facility_workflow
不依赖 pytest，只用标准库 unittest + FastAPI TestClient，每次用全新的 app 装载种子数据。
"""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app
from app.store import store


class TrafficFacilityWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        # 重置内存仓库，保证用例间互不污染
        store._tables = {name: [dict(row) for row in rows] for name, rows in __import__("app.seed", fromlist=["SEED_ROWS"]).SEED_ROWS.items()}
        store._idempotency_keys = {}
        store._migrate_traffic_facilities()
        store._sync_road_section_counts()
        self.client = TestClient(app)

    # ---------- 列表页 ----------

    def test_empty_filter_returns_empty_page_without_stale_rows(self) -> None:
        """无数据时返回空页，且前端口径（items 直接可用）不残留上一路段内容。"""
        response = self.client.get("/api/traffic_facility", params={"road": "不存在的路段"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)

    def test_list_status_column_matches_internal_status(self) -> None:
        """列表「设施状态」列必须和内部 status 同口径，避免状态错位。"""
        items = self.client.get("/api/traffic_facility").json()["items"]
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item["设施状态"], item["status"])

    def test_list_filters_dedup_and_migration_flags(self) -> None:
        """重复编号归并、存量无责任组设施迁移标注都在列表中体现。"""
        payload = self.client.get("/api/traffic_facility").json()
        self.assertEqual(payload["total"], 5)  # 6 条种子中 TRAF-0003 重复，归并为 5 条
        by_id = {item["id"]: item for item in payload["items"]}
        self.assertTrue(by_id[5]["待迁移"])
        self.assertTrue(by_id[6]["待迁移"])
        self.assertFalse(by_id[1]["待迁移"])

    # ---------- 详情页 ----------

    def test_detail_merges_duplicate_facilities(self) -> None:
        """详情不重复显示重复设施：重复记录归并到主记录下展示。"""
        detail = self.client.get("/api/traffic_facility/3").json()
        self.assertEqual(detail["设施编号"], "TRAF-0003")
        self.assertTrue(detail["重复标记"])
        self.assertEqual(len(detail["重复记录"]), 1)
        self.assertEqual(detail["重复记录"][0]["桩号位置"], "K13+520")

    def test_detail_unknown_returns_404(self) -> None:
        response = self.client.get("/api/traffic_facility/999")
        self.assertEqual(response.status_code, 404)

    # ---------- 责任边界 ----------

    def test_cross_boundary_team_rejected(self) -> None:
        """越界责任组指派必须拒绝。"""
        response = self.client.post(
            "/api/traffic_facility/2/verify-team",
            json={"values": {"责任组": "巡查一组"}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("越界指派已拒绝", response.json()["detail"])

    def test_team_verification_migrates_legacy_facility(self) -> None:
        """存量无责任组设施核对责任组后完成迁移标注。"""
        response = self.client.post(
            "/api/traffic_facility/6/verify-team",
            json={"values": {"责任组": "巡查三组"}},
        )
        self.assertEqual(response.status_code, 200)
        entry = response.json()["entry"]
        self.assertFalse(entry["待迁移"])
        self.assertEqual(entry["迁移状态"], "已归属")

    # ---------- 桩号确认 ----------

    def test_stake_requires_team_first(self) -> None:
        response = self.client.post(
            "/api/traffic_facility/5/confirm-stake",
            json={"values": {"桩号位置": "K3+200"}},
        )
        self.assertEqual(response.status_code, 400)

    def test_cross_boundary_stake_rejected(self) -> None:
        """人工桩号越出路段范围必须拒绝。"""
        self.client.post("/api/traffic_facility/5/verify-team", json={"values": {"责任组": "巡查一组"}})
        response = self.client.post(
            "/api/traffic_facility/5/confirm-stake",
            json={"values": {"桩号位置": "K6+000"}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("越界定位已拒绝", response.json()["detail"])

    def test_stake_history_keeps_construction_record(self) -> None:
        """历史位置按原施工记录保留，人工确认只改当前桩号。"""
        self.client.post("/api/traffic_facility/5/verify-team", json={"values": {"责任组": "巡查一组"}})
        response = self.client.post(
            "/api/traffic_facility/5/confirm-stake",
            json={"values": {"桩号位置": "K3+200"}},
        )
        self.assertEqual(response.status_code, 200)
        entry = response.json()["entry"]
        self.assertEqual(entry["桩号位置"], "K3+200")
        self.assertEqual(entry["历史桩号"], "K3+100")
        self.assertEqual(entry["原始桩号"], "K3+100")

    # ---------- 提交回写 / 幂等 ----------

    def _finish_first_two_steps(self, facility_id: int, team: str, stake: str) -> None:
        rs = self.client.post(f"/api/traffic_facility/{facility_id}/verify-team", json={"values": {"责任组": team}})
        self.assertEqual(rs.status_code, 200)
        rs = self.client.post(f"/api/traffic_facility/{facility_id}/confirm-stake", json={"values": {"桩号位置": stake}})
        self.assertEqual(rs.status_code, 200)

    def test_writeback_requires_prerequisites(self) -> None:
        response = self.client.post("/api/traffic_facility/2/writeback", json={"values": {}})
        self.assertEqual(response.status_code, 400)

    def test_concurrent_writeback_with_same_idempotency_key_lands_once(self) -> None:
        """并发重试通过幂等键只落一条记录，结论同步台账、路段清单、巡查待办。"""
        self._finish_first_two_steps(5, "巡查一组", "K3+200")
        key = "idem-test-key-5"

        def submit() -> int:
            return self.client.post(
                "/api/traffic_facility/5/writeback",
                json={"values": {"idempotency_key": key, "处置措施": "更换设施"}},
            ).status_code

        with ThreadPoolExecutor(max_workers=5) as pool:
            codes = list(pool.map(lambda _: submit(), range(5)))
        self.assertEqual(codes, [200] * 5)

        writebacks = self.client.get("/api/traffic_facility/writebacks").json()
        self.assertEqual(writebacks["total"], 1)
        self.assertEqual(writebacks["items"][0]["幂等键"], key)

        # 设施台账
        entry = self.client.get("/api/traffic_facility/5").json()
        self.assertEqual(entry["status"], "已更换")
        self.assertTrue(entry["已回写"])
        self.assertFalse(entry["pending"])

        # 路段清单
        roads = {r["路段名称"]: r for r in self.client.get("/api/road_section").json()["items"]}
        self.assertEqual(roads["北一环快速路"]["关联设施数"], 2)
        self.assertIn("TRAF-0009 K3+200", roads["北一环快速路"]["最近设施回写"])

        # 巡查待办
        summary = self.client.get("/api/traffic_facility/workbench/summary").json()
        todo_ids = {todo["id"] for todo in summary["facility_todos"]}
        self.assertNotIn(5, todo_ids)

    def test_retry_with_same_key_does_not_duplicate(self) -> None:
        self._finish_first_two_steps(6, "巡查三组", "K9+950")
        key = "idem-test-key-6"
        for _ in range(3):
            response = self.client.post(
                "/api/traffic_facility/6/writeback",
                json={"values": {"idempotency_key": key, "处置措施": "现场修复"}},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/traffic_facility/writebacks").json()["total"], 1)
        entry = self.client.get("/api/traffic_facility/6").json()
        self.assertEqual(entry["status"], "完好")

    # ---------- 巡查工作台 ----------

    def test_workbench_lists_pending_facilities_and_patrols(self) -> None:
        summary = self.client.get("/api/traffic_facility/workbench/summary").json()
        facility_ids = {todo["id"] for todo in summary["facility_todos"]}
        patrol_ids = {todo["id"] for todo in summary["patrol_todos"]}
        self.assertEqual(facility_ids, {2, 3, 5, 6})
        self.assertEqual(patrol_ids, {1, 2})
        self.assertEqual(summary["待迁移"], 2)

    def test_workbench_road_filter(self) -> None:
        summary = self.client.get(
            "/api/traffic_facility/workbench/summary", params={"road": "滨河西路"}
        ).json()
        self.assertEqual({todo["id"] for todo in summary["facility_todos"]}, {3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
