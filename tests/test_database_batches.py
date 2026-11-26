import sqlite3
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
        # Keep legacy lifecycle tests focused on stock/status transitions.
        # Full-cost routing is covered explicitly by financial-model tests.
        for key in ("anchor_fee_per_order", "replenishment_per_order", "storage_per_order", "monthly_system_cost"):
            database.save_setting(key, 0)
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

    def test_dispatched_batch_becomes_completed_after_all_orders_arrive(self):
        self.create_orders(80, "ARRIVAL")
        with database.db() as conn:
            order_ids = [row["id"] for row in conn.execute(
                "SELECT id FROM orders WHERE order_code LIKE 'ARRIVAL-%' ORDER BY id"
            ).fetchall()]

        for order_id in order_ids:
            database.update_order_status(order_id, "Готов к выдаче")

        self.assertEqual(database.mesh_batches_df().iloc[0]["Статус"], "completed")

    def test_legacy_database_migration_preserves_existing_records(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        current_path = database.DB_PATH
        database.DB_PATH = legacy_path
        try:
            conn = sqlite3.connect(legacy_path)
            conn.executescript("""
                CREATE TABLE pickup_points(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    point_type TEXT NOT NULL, city TEXT NOT NULL
                );
                CREATE TABLE products(
                    id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, category TEXT, volume_l REAL NOT NULL DEFAULT 0,
                    demand_class TEXT
                );
                CREATE TABLE inventory(
                    id INTEGER PRIMARY KEY, point_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
                    reserved INTEGER NOT NULL DEFAULT 0, incoming INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(point_id,product_id)
                );
                CREATE TABLE orders(
                    id INTEGER PRIMARY KEY, order_code TEXT UNIQUE NOT NULL,
                    product_id INTEGER NOT NULL, destination_point_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
                    route TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO pickup_points VALUES(1,'Legacy Anchor','Anchor','Legacy City');
                INSERT INTO products VALUES(1,'OLD-001','Старый товар','Тест',2.5,'BX');
                INSERT INTO inventory VALUES(1,1,1,37,4,0);
                INSERT INTO orders VALUES(1,'OLD-ORDER',1,1,2,'Зарезервирован','Hub','2026-01-01 10:00:00');
            """)
            conn.commit()
            conn.close()

            database.init_db()
            with database.db() as migrated:
                point = migrated.execute("SELECT active FROM pickup_points WHERE id=1").fetchone()
                stock = migrated.execute("SELECT quantity,reserved FROM inventory WHERE point_id=1 AND product_id=1").fetchone()
                order = migrated.execute(
                    "SELECT order_code,quantity,status,source_point_id,estimated_cost,batch_id FROM orders WHERE id=1"
                ).fetchone()
                tables = {row["name"] for row in migrated.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}

            self.assertEqual(point["active"], 1)
            self.assertEqual(stock["quantity"], 37)
            self.assertEqual(stock["reserved"], 4)
            self.assertEqual(order["order_code"], "OLD-ORDER")
            self.assertEqual(order["quantity"], 2)
            self.assertEqual(order["status"], "Зарезервирован")
            self.assertIn("mesh_batches", tables)
        finally:
            database.DB_PATH = current_path

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
