import tempfile
import unittest
from pathlib import Path

import database


class ExtendedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "data" / "test_smart_local_stock.db"
        database.init_db()
        database.seed_demo()
        self.settings = {key: float(item["value"]) for key, item in database.settings_dict().items()}

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_can_add_anchor_and_receive_stock_to_selected_anchor(self):
        anchor_id = database.add_pickup_point("Anchor Восток", "Anchor", "Демо-город")
        database.receive_stock(anchor_id, 1, 7, "Тестовое размещение")
        with database.db() as conn:
            stock = conn.execute(
                "SELECT quantity FROM inventory WHERE point_id=? AND product_id=1",
                (anchor_id,)
            ).fetchone()
        self.assertEqual(stock["quantity"], 7)

    def test_seller_delivery_is_received_into_hub_once(self):
        with database.db() as conn:
            seller_id = conn.execute(
                "SELECT id FROM sellers WHERE name=?", ("ООО «ТехноПоставка»",)
            ).fetchone()["id"]
            hub_id = conn.execute(
                "SELECT id FROM pickup_points WHERE point_type='Hub' AND active=1 ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            before = conn.execute(
                "SELECT quantity FROM inventory WHERE point_id=? AND product_id=1",
                (hub_id,)
            ).fetchone()["quantity"]

        delivery_id = database.create_supplier_delivery(seller_id, 1, hub_id, 25, "SUP-TEST-001")
        self.assertEqual(database.receive_supplier_delivery(delivery_id), 25)
        with database.db() as conn:
            after = conn.execute(
                "SELECT quantity FROM inventory WHERE point_id=? AND product_id=1",
                (hub_id,)
            ).fetchone()["quantity"]
        self.assertEqual(after, before + 25)
        with self.assertRaises(ValueError):
            database.receive_supplier_delivery(delivery_id)

    def test_daily_operations_creates_persisted_shift_and_live_snapshot(self):
        result = database.run_daily_operations(5, False, 2)
        self.assertEqual(result["created_orders"], 5)
        self.assertEqual(result["failed_orders"], 0)
        runs = database.operational_runs_df()
        self.assertEqual(len(runs), 1)
        self.assertEqual(int(runs.iloc[0]["Создано заказов"]), 5)
        snapshot = database.operational_snapshot()
        self.assertGreaterEqual(snapshot["total_orders"], 5)
        self.assertEqual(snapshot["today_runs"]["runs_today"], 1)
        self.assertEqual(snapshot["today_runs"]["simulated_orders"], 5)

    def test_clear_temporary_simulation_data_reverses_stock_and_keeps_catalog(self):
        before = database.inventory_df().copy()
        database.run_daily_operations(5, False, 2)
        result = database.clear_temporary_simulation_data()
        self.assertEqual(result["runs"], 1)
        self.assertEqual(result["orders"], 5)
        self.assertEqual(len(database.orders_df()), 0)
        self.assertEqual(len(database.operational_runs_df()), 0)
        after = database.inventory_df().copy()
        self.assertEqual(
            before[["ПВЗ", "SKU", "Остаток", "Резерв"]].to_dict("records"),
            after[["ПВЗ", "SKU", "Остаток", "Резерв"]].to_dict("records")
        )
        self.assertGreater(len(database.products_df()), 0)
        self.assertGreater(len(database.points_df()), 0)

    def test_continuous_simulation_start_tick_stop_and_timeline(self):
        code = database.start_continuous_simulation()
        state, _ = database.simulation_state()
        self.assertTrue(state["running"])
        self.assertEqual(state["shift_code"], code)
        tick = database.continuous_simulation_tick()
        self.assertTrue(tick["running"])
        self.assertEqual(tick["tick"], 1)
        self.assertGreaterEqual(len(database.simulation_timeline_df()), 2)
        self.assertTrue(database.stop_continuous_simulation())
        state, _ = database.simulation_state()
        self.assertFalse(state["running"])

    def test_clear_temporary_data_also_removes_continuous_shift_supply_and_events(self):
        before = database.inventory_df().copy()
        database.start_continuous_simulation()
        database.continuous_simulation_tick()
        result = database.clear_temporary_simulation_data()
        self.assertFalse(database.simulation_state()[0]["running"])
        self.assertEqual(len(database.simulation_timeline_df()), 0)
        self.assertEqual(len(database.orders_df()), 0)
        self.assertEqual(len(database.supplier_deliveries_df()), 0)
        after = database.inventory_df().copy()
        self.assertEqual(
            before[["ПВЗ", "SKU", "Остаток", "Резерв"]].to_dict("records"),
            after[["ПВЗ", "SKU", "Остаток", "Резерв"]].to_dict("records")
        )

    def test_route_incident_creates_plan_and_changes_mesh_route_via_hub(self):
        for number in range(1, 81):
            database.create_order(
                f"INCIDENT-{number:03d}", 1, 2, 1, self.settings
            )
        with database.db() as conn:
            order_id = conn.execute(
                "SELECT id FROM orders WHERE order_code='INCIDENT-001'"
            ).fetchone()["id"]

        result = database.add_incident(
            order_id, "Перекрытие дороги",
            "Дорога на прямом участке перекрыта из-за аварии."
        )
        self.assertTrue(result["applied"])
        self.assertEqual(result["applied_route"], "Hub (обход)")
        with database.db() as conn:
            route = conn.execute(
                "SELECT route,status FROM orders WHERE id=?", (order_id,)
            ).fetchone()
        self.assertEqual(route["route"], "Hub (обход)")
        self.assertEqual(route["status"], "В пути")
        incidents = database.incidents_df()
        self.assertIn("через транзитный узел", incidents.iloc[0]["План действий"])


if __name__ == "__main__":
    unittest.main()
