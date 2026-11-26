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
        # First tick creates an inbound supplier vehicle which is visible on the live map.
        self.assertGreaterEqual(len(database.simulation_vehicles_df()), 1)
        self.assertIn('Продавец → Hub', database.simulation_vehicles_df()['Маршрут'].tolist())
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

    def test_continuous_shift_financials_reconcile_to_hub_baseline(self):
        database.start_continuous_simulation()
        database.continuous_simulation_tick()
        finance = database.simulation_financials()
        self.assertEqual(finance["orders"], 1)
        self.assertGreaterEqual(finance["transport_total"], 0)
        self.assertGreaterEqual(finance["hub_baseline"], 0)
        self.assertAlmostEqual(
            finance["savings"],
            finance["hub_baseline"] - finance["transport_total"],
            places=2
        )
        database.stop_continuous_simulation()

    def test_continuous_tick_repairs_missing_hub_inventory_rows(self):
        points = database.points_df()
        hubs = points[points["Тип"] == "Hub"]
        products = database.products_df()
        self.assertFalse(hubs.empty)
        self.assertFalse(products.empty)
        with database.db() as conn:
            conn.execute("DELETE FROM inventory WHERE point_id=? AND product_id=?",
                         (int(hubs.iloc[0]["id"]), int(products.iloc[0]["id"])))
        database.start_continuous_simulation()
        result = database.continuous_simulation_tick()
        self.assertTrue(result["running"])
        with database.db() as conn:
            row = conn.execute("SELECT 1 FROM inventory WHERE point_id=? AND product_id=?",
                               (int(hubs.iloc[0]["id"]), int(products.iloc[0]["id"]))).fetchone()
        self.assertIsNotNone(row)
        database.stop_continuous_simulation()

    def test_financials_count_mesh_waiting_orders_as_mesh_and_include_projected_trip(self):
        code = database.start_continuous_simulation()
        result = database.create_order(f"{code}-O-00001", 1, 2, 1, self.settings)
        self.assertEqual(result["route"], "Mesh (ожидание)")
        finance = database.simulation_financials()
        self.assertEqual(finance["mesh_orders"], 1)
        self.assertEqual(finance["hub_orders"], 0)
        self.assertEqual(finance["mesh_waiting_batches"], 1)
        self.assertAlmostEqual(finance["mesh_trip_cost"], self.settings["local_trip_cost"])
        database.stop_continuous_simulation()

    def test_day_150_builds_6000_historical_orders_and_is_idempotent(self):
        history = database.prepare_mature_simulation()
        self.assertEqual(history["day_count"], 150)
        self.assertEqual(history["orders_count"], 6000)
        self.assertEqual(history["hub_orders"], 6000)
        self.assertEqual(history["mesh_orders"], 0)
        self.assertAlmostEqual(history["baseline_cost"], 6000 * 62)
        self.assertAlmostEqual(history["system_cost"], 50000)

        with database.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE order_code LIKE 'H150-D%'"
            ).fetchone()["n"]
            issued = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE order_code LIKE 'H150-D%' AND status='Выдан'"
            ).fetchone()["n"]
            days = conn.execute("SELECT COUNT(*) AS n FROM simulation_history_days").fetchone()["n"]
            movements = conn.execute(
                "SELECT COUNT(*) AS n FROM stock_movements WHERE reference LIKE 'H150-D%'"
            ).fetchone()["n"]
        self.assertEqual(count, 6000)
        self.assertEqual(issued, 6000)
        self.assertEqual(days, 150)
        self.assertEqual(movements, 18000)

        second = database.prepare_mature_simulation()
        self.assertEqual(second["orders_count"], 6000)
        with database.db() as conn:
            count_after = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE order_code LIKE 'H150-D%'"
            ).fetchone()["n"]
        self.assertEqual(count_after, 6000)

    def test_continuous_simulation_can_start_at_day_150(self):
        code = database.start_continuous_simulation(start_day=150)
        state = database.simulation_control_state()
        self.assertTrue(state["running"])
        self.assertEqual(state["shift_code"], code)
        self.assertEqual(state["start_day"], 150)
        self.assertEqual(state["tick_no"], 0)
        database.stop_continuous_simulation()

    def test_continuous_simulation_rejects_unsupported_start_day(self):
        with self.assertRaises(ValueError):
            database.start_continuous_simulation(start_day=42)

    def test_replenishment_does_not_duplicate_hub_to_anchor_transport(self):
        with database.db() as conn:
            conn.execute("UPDATE inventory SET quantity=0,reserved=0 WHERE point_id=1 AND product_id=1")
        database._auto_supplier_and_anchor_replenishment(1, "LIVE-TEST")
        database._auto_supplier_and_anchor_replenishment(4, "LIVE-TEST")
        with database.db() as conn:
            pending = conn.execute("""
                SELECT COUNT(*) AS n,COALESCE(SUM(quantity),0) AS q
                FROM simulation_supply_transports
                WHERE shift_code='LIVE-TEST' AND transport_type='Hub → Anchor'
                AND product_id=1 AND status='В пути'
            """).fetchone()
        self.assertEqual(pending["n"], 1)
        self.assertEqual(pending["q"], 15)

    def test_clear_reverses_full_mesh_lifecycle_and_usable_return(self):
        before = database.inventory_df().copy()
        for number in range(1, 81):
            database.create_order(f"FULL-CYCLE-{number:03d}", 1, 2, 1, self.settings)
        with database.db() as conn:
            order_id = conn.execute(
                "SELECT id FROM orders WHERE order_code='FULL-CYCLE-001'"
            ).fetchone()["id"]
        database.update_order_status(order_id, "Готов к выдаче")
        database.update_order_status(order_id, "Выдан")
        return_id = database.create_return(order_id, 1, "Проверка возврата")
        database.process_return(return_id, "Пригоден")
        with database.db() as conn:
            conn.execute("INSERT INTO operational_runs(run_code,notes) VALUES(?,?)",
                         ("FULL-CYCLE", "Тест полного цикла"))

        database.clear_temporary_simulation_data()
        after = database.inventory_df().copy()
        columns = ["ПВЗ", "SKU", "Остаток", "Резерв"]
        # Arrival can create a zero-balance inventory row at a regular PVZ;
        # compare stock balances while ignoring such harmless empty rows.
        after = after[(after["Остаток"] != 0) | (after["Резерв"] != 0) | (after["В пути"] != 0)]
        self.assertEqual(
            before[columns].sort_values(["ПВЗ", "SKU"]).reset_index(drop=True).to_dict("records"),
            after[columns].sort_values(["ПВЗ", "SKU"]).reset_index(drop=True).to_dict("records")
        )
        self.assertEqual(len(database.orders_df()), 0)
        self.assertEqual(len(database.returns_df()), 0)

    def test_operational_snapshot_counts_same_status_across_different_routes(self):
        code = database.start_continuous_simulation()
        first = database.create_order(f"{code}-A", 1, 2, 1, self.settings)
        second = database.create_order(f"{code}-B", 1, 2, 1, self.settings)
        with database.db() as conn:
            ids = conn.execute("SELECT id FROM orders WHERE order_code LIKE ? ORDER BY id",
                               (f"{code}-%",)).fetchall()
            conn.execute("UPDATE orders SET route='Hub' WHERE id=?", (ids[1]["id"],))
        snapshot = database.operational_snapshot()
        self.assertEqual(snapshot["status_counts"]["Ожидает партии"], 2)
        database.stop_continuous_simulation()

    def test_live_shift_snapshot_and_vehicle_map_use_only_current_shift(self):
        code = database.start_continuous_simulation()
        with database.db() as conn:
            anchor_id = conn.execute(
                "SELECT id FROM pickup_points WHERE point_type='Anchor' AND active=1 ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            conn.execute("UPDATE inventory SET quantity=0,reserved=0 WHERE point_id=? AND product_id=1",
                         (anchor_id,))
        result = database.create_order(f"{code}-O-00001", 1, 2, 1, self.settings)
        self.assertEqual(result["route"], "Hub")
        with database.db() as conn:
            order_id = conn.execute("SELECT id FROM orders WHERE order_code=?",
                                     (f"{code}-O-00001",)).fetchone()["id"]
        database.update_order_status(order_id, "В пути")

        snapshot = database.simulation_shift_snapshot()
        self.assertEqual(snapshot["total_orders"], 1)
        self.assertEqual(snapshot["status_counts"]["В пути"], 1)
        vehicles = database.simulation_vehicles_df()
        self.assertEqual(len(vehicles), 1)
        self.assertEqual(vehicles.iloc[0]["Заказ"], f"{code}-O-00001")
        self.assertGreaterEqual(len(database.simulation_timeline_df()), 1)
        database.stop_continuous_simulation()

    def test_simulation_destination_is_stable_for_a_full_target_batch(self):
        self.assertEqual(database._simulation_destination_index(1, 5, 80, 2), 0)
        self.assertEqual(database._simulation_destination_index(159, 5, 80, 2), 0)
        self.assertEqual(database._simulation_destination_index(161, 5, 80, 2), 1)

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
