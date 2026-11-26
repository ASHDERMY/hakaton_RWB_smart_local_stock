import tempfile
import unittest
from pathlib import Path

import database


class MeshBatchDatabaseTests(unittest.TestCase):
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

    def create_orders(self, count, prefix="TEST"):
        results = []
        for number in range(1, count + 1):
            results.append(database.create_order(
                f"{prefix}-{number:03d}", 1, 2, 1, self.settings
            ))
        return results

    def test_target_batch_is_dispatched_using_actual_order_count(self):
        self.create_orders(80)
        orders = database.orders_df()
        batch = database.mesh_batches_df().iloc[0]

        self.assertEqual(batch["Статус"], "dispatched")
        self.assertEqual(batch["Заказов в ожидании"], 0)
        self.assertEqual(int((orders["Статус"] == "В пути").sum()), 80)
        self.assertTrue((orders["Маршрут"] == "Mesh").all())
        self.assertAlmostEqual(float(orders["Оценка затрат, ₽"].sum()), 80 * 44.5)

        with database.db() as conn:
            anchor = conn.execute(
                "SELECT quantity,reserved FROM inventory WHERE point_id=1 AND product_id=1"
            ).fetchone()
        self.assertEqual(anchor["quantity"], 10)
        self.assertEqual(anchor["reserved"], 0)

    def test_expired_uneconomic_batch_moves_reservations_to_hub(self):
        self.create_orders(50, "TIMEOUT")
        with database.db() as conn:
            conn.execute(
                "UPDATE mesh_batches SET created_at=datetime('now','-6 hours') WHERE status='waiting'"
            )

        results = database.process_mesh_batches()
        self.assertEqual(results[0]["decision"], "hub")

        orders = database.orders_df()
        self.assertEqual(int((orders["Маршрут"] == "Hub").sum()), 50)
        self.assertTrue((orders["Статус"] == "Зарезервирован").all())
        self.assertTrue(orders["Партия"].isna().all())

        with database.db() as conn:
            anchor = conn.execute(
                "SELECT quantity,reserved FROM inventory WHERE point_id=1 AND product_id=1"
            ).fetchone()
            hub = conn.execute(
                "SELECT quantity,reserved FROM inventory WHERE point_id=4 AND product_id=1"
            ).fetchone()
        self.assertEqual(anchor["reserved"], 0)
        self.assertEqual(hub["reserved"], 50)

    def test_hub_stock_shortage_blocks_fallback_without_losing_anchor_reserve(self):
        with database.db() as conn:
            conn.execute(
                "UPDATE inventory SET quantity=10 WHERE point_id=4 AND product_id=1"
            )
        self.create_orders(50, "BLOCKED")
        with database.db() as conn:
            conn.execute(
                "UPDATE mesh_batches SET created_at=datetime('now','-6 hours') WHERE status='waiting'"
            )

        results = database.process_mesh_batches()
        self.assertEqual(results[0]["decision"], "blocked_hub_stock")

        batch = database.mesh_batches_df().iloc[0]
        self.assertEqual(batch["Статус"], "blocked_hub_stock")
        with database.db() as conn:
            anchor = conn.execute(
                "SELECT quantity,reserved FROM inventory WHERE point_id=1 AND product_id=1"
            ).fetchone()
            hub = conn.execute(
                "SELECT quantity,reserved FROM inventory WHERE point_id=4 AND product_id=1"
            ).fetchone()
        self.assertEqual(anchor["reserved"], 50)
        self.assertEqual(hub["reserved"], 0)

    def test_cancelling_waiting_order_releases_reserve(self):
        database.create_order("CANCEL-001", 1, 2, 2, self.settings)
        with database.db() as conn:
            order_id = conn.execute(
                "SELECT id FROM orders WHERE order_code='CANCEL-001'"
            ).fetchone()["id"]

        database.update_order_status(order_id, "Отменён")
        with database.db() as conn:
            anchor = conn.execute(
                "SELECT quantity,reserved FROM inventory WHERE point_id=1 AND product_id=1"
            ).fetchone()
        self.assertEqual(anchor["quantity"], 90)
        self.assertEqual(anchor["reserved"], 0)
        self.assertEqual(database.mesh_batches_df().iloc[0]["Статус"], "cancelled")


if __name__ == "__main__":
    unittest.main()
