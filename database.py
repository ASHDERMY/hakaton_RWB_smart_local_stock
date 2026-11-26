import sqlite3
from pathlib import Path
import pandas as pd
from contextlib import contextmanager

from engine import evaluate_batch

DB_PATH = Path("data/smart_local_stock.db")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column(conn, table, definition):
    name = definition.split()[0]
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS pickup_points(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, point_type TEXT NOT NULL,
            city TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            category TEXT, volume_l REAL NOT NULL DEFAULT 0, demand_class TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory(
            id INTEGER PRIMARY KEY, point_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
            incoming INTEGER NOT NULL DEFAULT 0 CHECK(incoming >= 0),
            UNIQUE(point_id,product_id),
            FOREIGN KEY(point_id) REFERENCES pickup_points(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY, order_code TEXT UNIQUE NOT NULL, product_id INTEGER NOT NULL,
            destination_point_id INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL, route TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(destination_point_id) REFERENCES pickup_points(id)
        );
        CREATE TABLE IF NOT EXISTS assumptions(
            key TEXT PRIMARY KEY, value REAL NOT NULL, unit TEXT NOT NULL,
            classification TEXT NOT NULL, note TEXT
        );
        CREATE TABLE IF NOT EXISTS stock_movements(
            id INTEGER PRIMARY KEY, point_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
            movement_type TEXT NOT NULL, quantity INTEGER NOT NULL, reference TEXT,
            note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS returns(
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            reason TEXT NOT NULL, condition_status TEXT NOT NULL DEFAULT 'pending',
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS incidents(
            id INTEGER PRIMARY KEY, order_id INTEGER, category TEXT NOT NULL,
            description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value REAL NOT NULL, unit TEXT NOT NULL,
            classification TEXT NOT NULL, note TEXT
        );
        CREATE TABLE IF NOT EXISTS mesh_batches(
            id INTEGER PRIMARY KEY,
            anchor_point_id INTEGER NOT NULL,
            destination_point_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            dispatched_at TEXT,
            closed_at TEXT,
            decision_reason TEXT,
            trip_cost REAL NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(anchor_point_id) REFERENCES pickup_points(id),
            FOREIGN KEY(destination_point_id) REFERENCES pickup_points(id)
        );
        CREATE TABLE IF NOT EXISTS sellers(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT '',
            contact TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS supplier_deliveries(
            id INTEGER PRIMARY KEY,
            seller_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            hub_point_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            status TEXT NOT NULL DEFAULT 'Ожидает приёмки',
            reference TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            received_at TEXT,
            FOREIGN KEY(seller_id) REFERENCES sellers(id),
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(hub_point_id) REFERENCES pickup_points(id)
        );
        CREATE TABLE IF NOT EXISTS simulation_supply_transports(
            id INTEGER PRIMARY KEY, shift_code TEXT NOT NULL, tick_no INTEGER NOT NULL,
            transport_type TEXT NOT NULL, reference TEXT NOT NULL, delivery_id INTEGER,
            product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            from_name TEXT NOT NULL, to_name TEXT NOT NULL,
            from_lat REAL NOT NULL, from_lon REAL NOT NULL, to_lat REAL NOT NULL, to_lon REAL NOT NULL,
            progress REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'В пути',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS simulation_control(
            id INTEGER PRIMARY KEY CHECK(id=1), running INTEGER NOT NULL DEFAULT 0,
            shift_code TEXT, tick_no INTEGER NOT NULL DEFAULT 0,
            started_at TEXT, stopped_at TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO simulation_control(id,running,tick_no) VALUES(1,0,0);
        CREATE TABLE IF NOT EXISTS simulation_events(
            id INTEGER PRIMARY KEY, shift_code TEXT NOT NULL, tick_no INTEGER NOT NULL,
            event_type TEXT NOT NULL, message TEXT NOT NULL, order_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS simulation_vehicle_progress(
            order_id INTEGER PRIMARY KEY, progress REAL NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS operational_runs(
            id INTEGER PRIMARY KEY,
            run_code TEXT NOT NULL UNIQUE,
            created_orders INTEGER NOT NULL DEFAULT 0,
            failed_orders INTEGER NOT NULL DEFAULT 0,
            dispatched_orders INTEGER NOT NULL DEFAULT 0,
            ready_orders INTEGER NOT NULL DEFAULT 0,
            issued_orders INTEGER NOT NULL DEFAULT 0,
            incidents_created INTEGER NOT NULL DEFAULT 0,
            estimated_cost REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # Миграции для SQLite-баз, созданных предыдущими версиями MVP.
        _add_column(c, "pickup_points", "active INTEGER NOT NULL DEFAULT 1")
        _add_column(c, "orders", "source_point_id INTEGER")
        _add_column(c, "orders", "estimated_cost REAL NOT NULL DEFAULT 0")
        _add_column(c, "orders", "updated_at TEXT")
        _add_column(c, "orders", "batch_id INTEGER")
        _add_column(c, "incidents", "action_plan TEXT")
        _add_column(c, "incidents", "applied_route TEXT")
        _add_column(c, "incidents", "applied INTEGER NOT NULL DEFAULT 0")
        c.execute("CREATE INDEX IF NOT EXISTS idx_orders_batch_status ON orders(batch_id,status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_batches_status_created ON mesh_batches(status,created_at)")

        defaults = [
            ("local_trip_cost", 2400, "RUB/рейс", "Гипотеза", "Стоимость локального рейса"),
            ("hub_cost_per_order", 62, "RUB/заказ", "Неподтверждённая гипотеза", "Тестовый ориентир, не себестоимость RWB"),
            ("mesh_variable_cost", 14.5, "RUB/заказ", "Гипотеза", "Прочие переменные расходы на один заказ"),
            ("batch_target_orders", 80, "заказов", "Проектное допущение", "Целевая загрузка партии; не является фактической вместимостью"),
            ("max_orders", 130, "заказов/рейс", "Проектное ограничение", "Лимит количества заказов в одном рейсе"),
            ("max_volume_l", 1500, "л/рейс", "Проектное ограничение", "Объёмная вместимость одного рейса"),
            ("max_wait_hours", 5, "часов", "Проектное ограничение", "Максимальное ожидание накопления; не является SLA"),
        ]
        c.executemany("INSERT OR IGNORE INTO settings VALUES(?,?,?,?,?)", defaults)
        c.execute(
            """INSERT OR IGNORE INTO sellers(name,category,contact)
            VALUES(?,?,?)""",
            ("ООО «ТехноПоставка»", "Электроника и аксессуары", "demo@example.invalid"),
        )


def seed_demo():
    # Seed by stable business keys, never by assumed integer IDs.
    # This remains safe when city demo points or user data already occupy low IDs.
    with db() as c:
        point_specs = [
            ("Anchor Центр", "Anchor", "Демо-город"),
            ("ПВЗ Север", "Обычный", "Демо-город"),
            ("ПВЗ Юг", "Обычный", "Демо-город"),
            ("Центральный склад Hub", "Hub", "Демо-город"),
        ]
        for name, point_type, city in point_specs:
            c.execute(
                """INSERT INTO pickup_points(name,point_type,city)
                SELECT ?,?,? WHERE NOT EXISTS (
                    SELECT 1 FROM pickup_points WHERE name=? OR
                    (point_type=? AND city=? AND name=?)
                )""",
                (name, point_type, city, name, point_type, city, name),
            )

        point_ids = {
            row["name"]: row["id"]
            for row in c.execute(
                "SELECT id,name FROM pickup_points WHERE name IN (?,?,?,?)",
                tuple(spec[0] for spec in point_specs),
            ).fetchall()
        }

        product_specs = [
            ("SKU-001", "Беспроводная мышь", "Электроника", 1.2, "AX"),
            ("SKU-002", "Кабель USB-C", "Аксессуары", 0.3, "AX"),
            ("SKU-003", "Настольная лампа", "Дом", 4.0, "BY"),
        ]
        c.executemany(
            """INSERT OR IGNORE INTO products(sku,name,category,volume_l,demand_class)
            VALUES(?,?,?,?,?)""",
            product_specs,
        )
        product_ids = {
            row["sku"]: row["id"]
            for row in c.execute(
                "SELECT id,sku FROM products WHERE sku IN (?,?,?)",
                tuple(spec[0] for spec in product_specs),
            ).fetchall()
        }

        stock_specs = [
            ("Anchor Центр", "SKU-001", 90),
            ("Anchor Центр", "SKU-002", 120),
            ("Anchor Центр", "SKU-003", 15),
            ("Центральный склад Hub", "SKU-001", 500),
            ("Центральный склад Hub", "SKU-002", 800),
            ("Центральный склад Hub", "SKU-003", 200),
        ]
        c.executemany(
            """INSERT OR IGNORE INTO inventory(point_id,product_id,quantity,reserved,incoming)
            VALUES(?,?,?,0,0)""",
            [
                (point_ids[point_name], product_ids[sku], quantity)
                for point_name, sku, quantity in stock_specs
            ],
        )

        # Add map-only city demo points after core demo points so fresh test DBs
        # keep the expected core records; existing IDs are never overwritten.
        city_points = [
            ("WB ПВЗ · Воинов-Интернационалистов, 34", "Обычный", "Йошкар-Ола"),
            ("WB ПВЗ · Советская, 97", "Обычный", "Йошкар-Ола"),
            ("WB ПВЗ · Ленинский проспект, 16А", "Обычный", "Йошкар-Ола"),
            ("WB ПВЗ · Красноармейская, 115", "Обычный", "Йошкар-Ола"),
            ("WB ПВЗ · Кремлёвская, 20", "Обычный", "Йошкар-Ола"),
        ]
        for point_name, point_type, city in city_points:
            c.execute(
                """INSERT INTO pickup_points(name,point_type,city)
                SELECT ?,?,? WHERE NOT EXISTS (
                    SELECT 1 FROM pickup_points WHERE name=?
                )""",
                (point_name, point_type, city, point_name),
            )

        # Демонстрационный продавец — вымышленный участник модели, не контрагент RWB.
        c.execute(
            """INSERT OR IGNORE INTO sellers(name,category,contact)
            VALUES(?,?,?)""",
            ("ООО «ТехноПоставка»", "Электроника и аксессуары", "demo@example.invalid"),
        )


def products_df():
    with db() as c:
        return pd.read_sql_query(
            "SELECT id,sku AS SKU,name AS Товар,category AS Категория,volume_l AS 'Объём, л',demand_class AS 'ABC/XYZ' FROM products ORDER BY sku", c
        )


def inventory_df():
    with db() as c:
        return pd.read_sql_query("""SELECT p.name AS ПВЗ,p.point_type AS Тип,pr.sku AS SKU,pr.name AS Товар,
        i.quantity AS Остаток,i.reserved AS Резерв,i.incoming AS 'В пути',
        (i.quantity+i.incoming-i.reserved) AS Доступно
        FROM inventory i JOIN pickup_points p ON p.id=i.point_id
        JOIN products pr ON pr.id=i.product_id ORDER BY p.name,pr.sku""", c)


def points_df():
    with db() as c:
        return pd.read_sql_query(
            "SELECT id,name AS Название,point_type AS Тип,city AS Город,active AS Активен FROM pickup_points ORDER BY id", c
        )


def add_pickup_point(name, point_type, city):
    name = (name or "").strip()
    city = (city or "").strip()
    if not name or not city:
        raise ValueError("Укажите название точки и город.")
    if point_type not in ("Anchor", "Обычный", "Hub"):
        raise ValueError("Недопустимый тип точки.")
    with db() as c:
        cur = c.execute(
            "INSERT INTO pickup_points(name,point_type,city,active) VALUES(?,?,?,1)",
            (name, point_type, city)
        )
        point_id = cur.lastrowid
        if point_type in ("Anchor", "Hub"):
            c.execute(
                """INSERT INTO inventory(point_id,product_id,quantity)
                SELECT ?,id,0 FROM products""",
                (point_id,)
            )
        return point_id


def orders_df():
    with db() as c:
        return pd.read_sql_query("""SELECT o.id,o.order_code AS Заказ,pr.sku AS SKU,pr.name AS Товар,o.quantity AS Количество,
        d.name AS 'ПВЗ назначения',s.name AS Источник,o.route AS Маршрут,o.batch_id AS Партия,o.status AS Статус,
        ROUND(o.estimated_cost,2) AS 'Оценка затрат, ₽',o.created_at AS Создан
        FROM orders o JOIN products pr ON pr.id=o.product_id
        JOIN pickup_points d ON d.id=o.destination_point_id
        LEFT JOIN pickup_points s ON s.id=o.source_point_id ORDER BY o.id DESC""", c)


def mesh_batches_df():
    with db() as c:
        return pd.read_sql_query("""SELECT b.id AS Партия,a.name AS Anchor,d.name AS 'ПВЗ назначения',
        b.status AS Статус,COUNT(CASE WHEN o.status='Ожидает партии' THEN 1 END) AS 'Заказов в ожидании',
        ROUND(COALESCE(SUM(CASE WHEN o.status='Ожидает партии' THEN p.volume_l*o.quantity ELSE 0 END),0),2) AS 'Объём ожидания, л',
        ROUND(MAX(0,(julianday('now')-julianday(b.created_at))*24),2) AS 'Ожидание, ч',
        ROUND(b.trip_cost,2) AS 'Стоимость рейса, ₽',ROUND(b.total_cost,2) AS 'Стоимость партии, ₽',
        b.decision_reason AS Решение,b.created_at AS Создана,b.dispatched_at AS Отправлена
        FROM mesh_batches b
        JOIN pickup_points a ON a.id=b.anchor_point_id
        JOIN pickup_points d ON d.id=b.destination_point_id
        LEFT JOIN orders o ON o.batch_id=b.id
        LEFT JOIN products p ON p.id=o.product_id
        GROUP BY b.id ORDER BY b.id DESC""", c)


def settings_dict():
    with db() as c:
        return {r["key"]: dict(r) for r in c.execute("SELECT * FROM settings")}


def save_setting(key, value):
    value = float(value)
    positive_keys = {"batch_target_orders", "max_orders", "max_volume_l", "max_wait_hours", "hub_cost_per_order"}
    if key in positive_keys and value <= 0:
        raise ValueError("Для этого параметра значение должно быть больше нуля.")
    if key in {"local_trip_cost", "mesh_variable_cost"} and value < 0:
        raise ValueError("Стоимость не может быть отрицательной.")
    with db() as c:
        cur = c.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
        if cur.rowcount != 1:
            raise ValueError("Неизвестный параметр настройки.")


def _settings_from_db(c):
    return {r["key"]: float(r["value"]) for r in c.execute("SELECT key,value FROM settings")}


def add_product(sku, name, category, volume_l, demand_class):
    if not sku.strip() or not name.strip():
        raise ValueError("Укажите SKU и название товара.")
    if float(volume_l) <= 0:
        raise ValueError("Объём единицы товара должен быть больше нуля.")
    with db() as c:
        cur = c.execute(
            "INSERT INTO products(sku,name,category,volume_l,demand_class) VALUES(?,?,?,?,?)",
            (sku.strip(), name.strip(), category.strip(), float(volume_l), demand_class)
        )
        pid = cur.lastrowid
        for point in c.execute("SELECT id FROM pickup_points WHERE point_type IN ('Anchor','Hub')").fetchall():
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)", (point["id"], pid))
        return pid


def receive_stock(point_id, product_id, quantity, note="Приёмка"):
    if int(quantity) <= 0:
        raise ValueError("Количество должно быть больше нуля.")
    with db() as c:
        point = c.execute("SELECT id FROM pickup_points WHERE id=? AND active=1", (point_id,)).fetchone()
        product = c.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
        if not point or not product:
            raise ValueError("Точка или товар не найдены либо точка неактивна.")
        c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)", (point_id, product_id))
        c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?", (int(quantity), point_id, product_id))
        c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                  (point_id, product_id, "receipt", int(quantity), None, note))


def _batch_stats(c, batch_id):
    return c.execute("""SELECT COUNT(*) AS order_count,
        COALESCE(SUM(p.volume_l*o.quantity),0) AS volume_l,
        COALESCE((julianday('now')-julianday(b.created_at))*24,0) AS age_hours
        FROM mesh_batches b
        LEFT JOIN orders o ON o.batch_id=b.id AND o.status='Ожидает партии'
        LEFT JOIN products p ON p.id=o.product_id
        WHERE b.id=? GROUP BY b.id""", (batch_id,)).fetchone()


def _close_batch(c, batch_id, settings, force=False):
    batch = c.execute("SELECT * FROM mesh_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch or batch["status"] not in ("waiting", "blocked_hub_stock"):
        return {"decision": "skipped", "reason": "Партия уже закрыта или не ожидает обработки."}

    stats = _batch_stats(c, batch_id)
    if not stats or stats["order_count"] == 0:
        c.execute("UPDATE mesh_batches SET status='cancelled',closed_at=CURRENT_TIMESTAMP,decision_reason=? WHERE id=?",
                  ("Все заказы партии отменены.", batch_id))
        return {"decision": "empty", "reason": "Партия пуста."}

    age_hours = max(float(stats["age_hours"] or 0), float(settings["max_wait_hours"]) if force else 0)
    decision = evaluate_batch(
        int(stats["order_count"]), float(stats["volume_l"]), age_hours,
        float(settings["local_trip_cost"]), float(settings["mesh_variable_cost"]),
        float(settings["hub_cost_per_order"]), float(settings["batch_target_orders"]),
        int(settings["max_orders"]), float(settings["max_volume_l"]), float(settings["max_wait_hours"])
    )

    if decision["decision"] == "wait":
        return decision

    orders = c.execute("""SELECT o.*,p.volume_l FROM orders o
        JOIN products p ON p.id=o.product_id
        WHERE o.batch_id=? AND o.status='Ожидает партии' ORDER BY o.id""", (batch_id,)).fetchall()

    if decision["decision"] == "mesh":
        per_order_cost = float(decision["mesh_cost_per_order"])
        for order in orders:
            cur = c.execute("""UPDATE inventory
                SET quantity=quantity-?,reserved=reserved-?
                WHERE point_id=? AND product_id=? AND quantity>=? AND reserved>=?""",
                (order["quantity"], order["quantity"], order["source_point_id"], order["product_id"],
                 order["quantity"], order["quantity"]))
            if cur.rowcount != 1:
                raise ValueError(f"Нарушена целостность остатка для заказа {order['order_code']}.")
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (order["source_point_id"], order["product_id"], "dispatch", order["quantity"],
                 order["order_code"], f"Отправка в составе Mesh-партии {batch_id}"))
            c.execute("""UPDATE orders SET route='Mesh',status='В пути',estimated_cost=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""", (per_order_cost, order["id"]))

        total_cost = per_order_cost * len(orders)
        c.execute("""UPDATE mesh_batches SET status='dispatched',dispatched_at=CURRENT_TIMESTAMP,
            closed_at=CURRENT_TIMESTAMP,trip_cost=?,total_cost=?,decision_reason=? WHERE id=?""",
            (float(settings["local_trip_cost"]), total_cost, decision["reason"], batch_id))
        return {**decision, "orders": len(orders), "total_cost": total_cost}

    if decision["decision"] == "hub":
        hub = c.execute("SELECT * FROM pickup_points WHERE point_type='Hub' AND active=1 ORDER BY id LIMIT 1").fetchone()
        if not hub:
            reason = "Нет активной точки Hub для резервного маршрута."
            if batch["status"] != "blocked_hub_stock":
                first_order_id = orders[0]["id"] if orders else None
                c.execute("INSERT INTO incidents(order_id,category,description) VALUES(?,?,?)",
                          (first_order_id, "Невозможен перевод на Hub", reason))
            c.execute("UPDATE mesh_batches SET status='blocked_hub_stock',decision_reason=? WHERE id=?", (reason, batch_id))
            return {"decision": "blocked_hub_stock", "reason": reason}

        required = {}
        for order in orders:
            required[order["product_id"]] = required.get(order["product_id"], 0) + int(order["quantity"])

        for product_id, quantity in required.items():
            row = c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",
                            (hub["id"], product_id)).fetchone()
            if not row or row["available"] < quantity:
                reason = (
                    "Партия достигла условия закрытия, но в Hub недостаточно запаса "
                    "для переноса всех заказов. Пополните Hub и повторите обработку."
                )
                if batch["status"] != "blocked_hub_stock":
                    first_order_id = orders[0]["id"] if orders else None
                    c.execute("INSERT INTO incidents(order_id,category,description) VALUES(?,?,?)",
                              (first_order_id, "Недостаток запаса Hub", reason))
                c.execute("UPDATE mesh_batches SET status='blocked_hub_stock',decision_reason=? WHERE id=?", (reason, batch_id))
                return {"decision": "blocked_hub_stock", "reason": reason}

        for order in orders:
            released = c.execute("UPDATE inventory SET reserved=reserved-? WHERE point_id=? AND product_id=? AND reserved>=?",
                                 (order["quantity"], order["source_point_id"], order["product_id"], order["quantity"]))
            if released.rowcount != 1:
                raise ValueError(f"Не удалось снять резерв Anchor для заказа {order['order_code']}.")
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",
                      (hub["id"], order["product_id"]))
            c.execute("UPDATE inventory SET reserved=reserved+? WHERE point_id=? AND product_id=?",
                      (order["quantity"], hub["id"], order["product_id"]))
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (order["source_point_id"], order["product_id"], "release_reserve", order["quantity"],
                 order["order_code"], f"Снятие резерва при переводе партии {batch_id} на Hub"))
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (hub["id"], order["product_id"], "reserve", order["quantity"],
                 order["order_code"], f"Резерв на Hub после перевода партии {batch_id}"))
            c.execute("""UPDATE orders SET route='Hub',status='Зарезервирован',source_point_id=?,
                estimated_cost=?,batch_id=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (hub["id"], float(settings["hub_cost_per_order"]), order["id"]))

        c.execute("""UPDATE mesh_batches SET status='fallback_hub',closed_at=CURRENT_TIMESTAMP,
            decision_reason=? WHERE id=?""", (decision["reason"], batch_id))
        return {**decision, "orders": len(orders), "total_cost": float(settings["hub_cost_per_order"]) * len(orders)}

    if decision["decision"] == "capacity_error":
        c.execute("UPDATE mesh_batches SET status='blocked_capacity',decision_reason=? WHERE id=?",
                  (decision["reason"], batch_id))
    return decision


def process_mesh_batches():
    """Evaluate target/capacity/timeout conditions for all open Mesh batches."""
    with db() as c:
        settings = _settings_from_db(c)
        batch_ids = [r["id"] for r in c.execute(
            "SELECT id FROM mesh_batches WHERE status IN ('waiting','blocked_hub_stock') ORDER BY created_at,id"
        ).fetchall()]
        results = []
        for batch_id in batch_ids:
            results.append({"batch_id": batch_id, **_close_batch(c, batch_id, settings)})
        return results


def create_order(order_code, product_id, destination_point_id, quantity, settings):
    order_code = (order_code or "").strip()
    quantity = int(quantity)
    if not order_code:
        raise ValueError("Укажите номер заказа.")
    if quantity <= 0:
        raise ValueError("Количество заказа должно быть больше нуля.")

    with db() as c:
        dest = c.execute("SELECT * FROM pickup_points WHERE id=? AND active=1", (destination_point_id,)).fetchone()
        if not dest or dest["point_type"] != "Обычный":
            raise ValueError("Назначением должен быть активный обычный ПВЗ.")
        product = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            raise ValueError("Товар не найден.")

        anchor = c.execute("""SELECT * FROM pickup_points
            WHERE point_type='Anchor' AND active=1 AND city=?
            ORDER BY id LIMIT 1""", (dest["city"],)).fetchone()
        if not anchor:
            anchor = c.execute("SELECT * FROM pickup_points WHERE point_type='Anchor' AND active=1 ORDER BY id LIMIT 1").fetchone()
        hub = c.execute("SELECT * FROM pickup_points WHERE point_type='Hub' AND active=1 ORDER BY id LIMIT 1").fetchone()
        hub_cost = float(settings["hub_cost_per_order"])

        anchor_available = 0
        if anchor:
            row = c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",
                            (anchor["id"], product_id)).fetchone()
            anchor_available = int(row["available"]) if row else 0

        order_volume = float(product["volume_l"]) * quantity
        mesh_single_order_fits = (
            anchor is not None
            and quantity <= anchor_available
            and order_volume <= float(settings["max_volume_l"])
            and int(settings["max_orders"]) >= 1
        )

        if mesh_single_order_fits:
            batch = c.execute("""SELECT * FROM mesh_batches
                WHERE anchor_point_id=? AND destination_point_id=? AND status='waiting'
                ORDER BY id DESC LIMIT 1""", (anchor["id"], destination_point_id)).fetchone()
            if batch:
                stats = _batch_stats(c, batch["id"])
                fits_existing = (
                    int(stats["order_count"]) + 1 <= int(settings["max_orders"])
                    and float(stats["volume_l"]) + order_volume <= float(settings["max_volume_l"])
                )
                if not fits_existing:
                    _close_batch(c, batch["id"], settings, force=True)
                    batch = None
            if not batch:
                cur = c.execute("INSERT INTO mesh_batches(anchor_point_id,destination_point_id) VALUES(?,?)",
                                (anchor["id"], destination_point_id))
                batch_id = cur.lastrowid
            else:
                batch_id = batch["id"]

            c.execute("""INSERT INTO orders(order_code,product_id,destination_point_id,quantity,status,route,
                source_point_id,estimated_cost,batch_id,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (order_code, product_id, destination_point_id, quantity, "Ожидает партии",
                 "Mesh (ожидание)", anchor["id"], 0, batch_id))
            c.execute("UPDATE inventory SET reserved=reserved+? WHERE point_id=? AND product_id=?",
                      (quantity, anchor["id"], product_id))
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (anchor["id"], product_id, "reserve", quantity, order_code, f"Резерв под Mesh-партию {batch_id}"))

            _close_batch(c, batch_id, settings)
            created = c.execute("SELECT route,status,estimated_cost,source_point_id,batch_id FROM orders WHERE order_code=?",
                                (order_code,)).fetchone()
            source = c.execute("SELECT name FROM pickup_points WHERE id=?", (created["source_point_id"],)).fetchone()
            return {
                "route": created["route"],
                "status": created["status"],
                "source": source["name"] if source else "—",
                "estimated_cost": float(created["estimated_cost"]),
                "batch_id": created["batch_id"],
            }

        if not hub:
            raise ValueError("В Anchor недостаточно товара или заказ не помещается в Mesh; активная точка Hub не найдена.")
        c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)", (hub["id"], product_id))
        row = c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",
                        (hub["id"], product_id)).fetchone()
        if not row or row["available"] < quantity:
            raise ValueError("Недостаточно доступного остатка в Anchor и Hub. Пополните запас.")
        c.execute("""INSERT INTO orders(order_code,product_id,destination_point_id,quantity,status,route,
            source_point_id,estimated_cost,updated_at)
            VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (order_code, product_id, destination_point_id, quantity, "Зарезервирован", "Hub",
             hub["id"], hub_cost))
        c.execute("UPDATE inventory SET reserved=reserved+? WHERE point_id=? AND product_id=?",
                  (quantity, hub["id"], product_id))
        c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
            VALUES(?,?,?,?,?,?)""",
            (hub["id"], product_id, "reserve", quantity, order_code, "Резерв под заказ Hub"))
        return {"route": "Hub", "status": "Зарезервирован", "source": hub["name"],
                "estimated_cost": hub_cost, "batch_id": None}


def update_order_status(order_id, new_status):
    transitions = {
        "Ожидает партии": {"Отменён"},
        "Зарезервирован": {"В пути", "Отменён"},
        "В пути": {"Готов к выдаче", "Ошибка"},
        "Ошибка": {"В пути"},
        "Готов к выдаче": {"Выдан", "Возврат оформлен"},
    }
    with db() as c:
        order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise ValueError("Заказ не найден.")
        if new_status not in transitions.get(order["status"], set()):
            raise ValueError(f"Недопустимый переход: {order['status']} → {new_status}.")

        if new_status == "Отменён":
            cur = c.execute("""UPDATE inventory SET reserved=reserved-?
                WHERE point_id=? AND product_id=? AND reserved>=?""",
                (order["quantity"], order["source_point_id"], order["product_id"], order["quantity"]))
            if cur.rowcount != 1:
                raise ValueError("Не удалось корректно снять резерв: проверьте остатки.")
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (order["source_point_id"], order["product_id"], "release_reserve", order["quantity"],
                 order["order_code"], "Снятие резерва при отмене заказа"))

        if new_status == "В пути" and order["status"] == "Зарезервирован":
            cur = c.execute("""UPDATE inventory SET quantity=quantity-?,reserved=reserved-?
                WHERE point_id=? AND product_id=? AND quantity>=? AND reserved>=?""",
                (order["quantity"], order["quantity"], order["source_point_id"], order["product_id"],
                 order["quantity"], order["quantity"]))
            if cur.rowcount != 1:
                raise ValueError("Недостаточно зарезервированного товара для отправки.")
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (order["source_point_id"], order["product_id"], "dispatch", order["quantity"],
                 order["order_code"], "Отправка по заказу"))

        if new_status == "Готов к выдаче":
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",
                      (order["destination_point_id"], order["product_id"]))
            c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",
                      (order["quantity"], order["destination_point_id"], order["product_id"]))
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (order["destination_point_id"], order["product_id"], "arrival", order["quantity"],
                 order["order_code"], "Поступление в ПВЗ назначения"))

        if new_status == "Выдан":
            cur = c.execute("""UPDATE inventory SET quantity=quantity-?
                WHERE point_id=? AND product_id=? AND quantity>=?""",
                (order["quantity"], order["destination_point_id"], order["product_id"], order["quantity"]))
            if cur.rowcount != 1:
                raise ValueError("На ПВЗ назначения недостаточно товара для выдачи.")
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (order["destination_point_id"], order["product_id"], "issue", order["quantity"],
                 order["order_code"], "Выдача покупателю"))

        c.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, order_id))
        if new_status == "Готов к выдаче" and order["batch_id"]:
            unfinished = c.execute("""SELECT COUNT(*) AS n FROM orders
                WHERE batch_id=? AND status NOT IN ('Готов к выдаче','Выдан','Возврат оформлен','Отменён')""",
                (order["batch_id"],)).fetchone()["n"]
            if unfinished == 0:
                c.execute("UPDATE mesh_batches SET status='completed',closed_at=CURRENT_TIMESTAMP WHERE id=?",
                          (order["batch_id"],))
        if new_status == "Отменён" and order["batch_id"]:
            remaining = c.execute("SELECT COUNT(*) AS n FROM orders WHERE batch_id=? AND status='Ожидает партии'",
                                  (order["batch_id"],)).fetchone()["n"]
            if remaining == 0:
                c.execute("UPDATE mesh_batches SET status='cancelled',closed_at=CURRENT_TIMESTAMP,decision_reason=? WHERE id=?",
                          ("Все заказы партии отменены.", order["batch_id"]))


def create_return(order_id, quantity, reason):
    with db() as c:
        order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order or order["status"] != "Выдан":
            raise ValueError("Возврат можно оформить только по выданному заказу.")
        if quantity < 1:
            raise ValueError("Количество возврата должно быть больше нуля.")
        already_returned = c.execute("SELECT COALESCE(SUM(quantity),0) AS q FROM returns WHERE order_id=?",
                                     (order_id,)).fetchone()["q"]
        if already_returned + quantity > order["quantity"]:
            raise ValueError("Суммарное количество возвратов превышает количество в заказе.")
        cur = c.execute("INSERT INTO returns(order_id,quantity,reason) VALUES(?,?,?)",
                        (order_id, quantity, reason.strip()))
        c.execute("UPDATE orders SET status='Возврат оформлен',updated_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
        return cur.lastrowid


def process_return(return_id, condition_status):
    if condition_status not in ("Пригоден", "Повреждён"):
        raise ValueError("Выберите состояние товара.")
    with db() as c:
        ret = c.execute("SELECT * FROM returns WHERE id=?", (return_id,)).fetchone()
        if not ret or ret["status"] != "pending":
            raise ValueError("Возврат уже обработан или не найден.")
        order = c.execute("SELECT * FROM orders WHERE id=?", (ret["order_id"],)).fetchone()
        if condition_status == "Пригоден":
            point_id = order["destination_point_id"]
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",
                      (point_id, order["product_id"]))
            c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",
                      (ret["quantity"], point_id, order["product_id"]))
            c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                VALUES(?,?,?,?,?,?)""",
                (point_id, order["product_id"], "return", ret["quantity"], str(return_id),
                 "Возврат пригодного товара на ПВЗ назначения"))
        c.execute("UPDATE returns SET condition_status=?,status='processed' WHERE id=?",
                  (condition_status, return_id))


def returns_df():
    with db() as c:
        return pd.read_sql_query("""SELECT r.id,r.order_id AS 'ID заказа',o.order_code AS Заказ,r.quantity AS Количество,
        r.reason AS Причина,r.condition_status AS Состояние,r.status AS Статус,r.created_at AS Создан
        FROM returns r JOIN orders o ON o.id=r.order_id ORDER BY r.id DESC""", c)


def movements_df():
    with db() as c:
        return pd.read_sql_query("""SELECT m.id,p.name AS ПВЗ,pr.sku AS SKU,m.movement_type AS Операция,
        m.quantity AS Количество,m.reference AS Документ,m.note AS Комментарий,m.created_at AS Дата
        FROM stock_movements m JOIN pickup_points p ON p.id=m.point_id
        JOIN products pr ON pr.id=m.product_id ORDER BY m.id DESC""", c)


def incidents_df():
    with db() as c:
        return pd.read_sql_query(
            """SELECT id,order_id AS 'ID заказа',category AS Категория,
            description AS Описание,action_plan AS 'План действий',
            applied_route AS 'Новый маршрут',status AS Статус,
            created_at AS Дата FROM incidents ORDER BY id DESC""", c
        )


def add_incident(order_id, category, description):
    description = (description or "").strip()
    if not description:
        raise ValueError("Добавьте описание инцидента.")

    with db() as c:
        order = None
        if order_id is not None:
            order = c.execute("SELECT * FROM orders WHERE id=?", (int(order_id),)).fetchone()
            if not order:
                raise ValueError("Связанный заказ не найден.")

        text = f"{category} {description}".lower()
        route_problem = any(word in text for word in (
            "перекры", "закрыт", "пробк", "авари", "задерж", "опоздан",
            "непроезд", "ремонт", "маршрут", "полом"
        ))
        action_plan = "Зафиксировать инцидент, проверить товар и связаться с ответственным ПВЗ."
        applied_route = None
        applied = 0

        if order and route_problem and order["status"] == "В пути":
            hub = c.execute(
                "SELECT * FROM pickup_points WHERE point_type='Hub' AND active=1 ORDER BY id LIMIT 1"
            ).fetchone()
            if hub and order["route"] and "Mesh" in order["route"]:
                action_plan = (
                    f"1. Исключить проблемный прямой участок Anchor → ПВЗ. "
                    f"2. Направить транспорт через транзитный узел {hub['name']}. "
                    "3. Пересчитать дорожный путь до ПВЗ назначения. "
                    "4. Уведомить оператора и подтвердить новое время прибытия. "
                    "Это сценарное перенаправление через Hub, а не команда реальному перевозчику."
                )
                applied_route = "Hub (обход)"
                hub_rate = float(c.execute("SELECT value FROM settings WHERE key='hub_cost_per_order'").fetchone()["value"])
                c.execute(
                    "UPDATE orders SET route=?,estimated_cost=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (applied_route, hub_rate, order["id"])
                )
                applied = 1
            else:
                action_plan = (
                    "1. Приостановить отправку по проблемному участку. "
                    "2. Проверить доступность альтернативного узла и остаток. "
                    "3. Передать диспетчеру на ручное подтверждение; маршрут автоматически не изменён."
                )
        elif order and order["status"] in ("Ожидает партии", "Зарезервирован"):
            action_plan = (
                "1. Не выпускать заказ на проблемный участок. "
                "2. Проверить остатки Anchor и Hub. "
                "3. При подтверждении риска выбрать доступный маршрут до отправки."
            )
        elif any(word in text for word in ("повреж", "потер", "утер")):
            action_plan = (
                "1. Изолировать затронутую единицу товара. "
                "2. Сверить физический остаток и резерв. "
                "3. Зафиксировать фото/акт и решить вопрос о возврате или компенсации."
            )

        cur = c.execute(
            """INSERT INTO incidents(order_id,category,description,action_plan,applied_route,applied)
            VALUES(?,?,?,?,?,?)""",
            (order_id, category, description, action_plan, applied_route, applied)
        )
        return {
            "incident_id": cur.lastrowid,
            "action_plan": action_plan,
            "applied_route": applied_route,
            "applied": bool(applied),
        }


def sellers_df():
    with db() as c:
        return pd.read_sql_query(
            "SELECT id,name AS Продавец,category AS Категория,contact AS Контакт,active AS Активен FROM sellers ORDER BY name", c
        )


def supplier_deliveries_df():
    with db() as c:
        return pd.read_sql_query(
            """SELECT d.id AS ID,s.name AS Продавец,p.sku AS SKU,p.name AS Товар,
            h.name AS 'Склад Hub',d.quantity AS Количество,d.reference AS Накладная,
            d.status AS Статус,d.created_at AS Создана,d.received_at AS Принята
            FROM supplier_deliveries d JOIN sellers s ON s.id=d.seller_id
            JOIN products p ON p.id=d.product_id JOIN pickup_points h ON h.id=d.hub_point_id
            ORDER BY d.id DESC""", c
        )


def create_seller(name, category="", contact=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название продавца.")
    with db() as c:
        cur = c.execute(
            "INSERT INTO sellers(name,category,contact) VALUES(?,?,?)",
            (name, (category or "").strip(), (contact or "").strip())
        )
        return cur.lastrowid


def create_supplier_delivery(seller_id, product_id, hub_point_id, quantity, reference=""):
    quantity = int(quantity)
    if quantity <= 0:
        raise ValueError("Количество поставки должно быть больше нуля.")
    with db() as c:
        seller = c.execute("SELECT id FROM sellers WHERE id=? AND active=1", (seller_id,)).fetchone()
        product = c.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
        hub = c.execute("SELECT id FROM pickup_points WHERE id=? AND point_type='Hub' AND active=1", (hub_point_id,)).fetchone()
        if not seller or not product or not hub:
            raise ValueError("Проверьте продавца, товар и активный склад Hub.")
        cur = c.execute(
            """INSERT INTO supplier_deliveries(seller_id,product_id,hub_point_id,quantity,reference)
            VALUES(?,?,?,?,?)""",
            (seller_id, product_id, hub_point_id, quantity, (reference or "").strip())
        )
        return cur.lastrowid


def receive_supplier_delivery(delivery_id):
    with db() as c:
        delivery = c.execute(
            "SELECT * FROM supplier_deliveries WHERE id=?", (delivery_id,)
        ).fetchone()
        if not delivery or delivery["status"] != "Ожидает приёмки":
            raise ValueError("Поставка уже принята или не найдена.")
        c.execute(
            "INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",
            (delivery["hub_point_id"], delivery["product_id"])
        )
        c.execute(
            "UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",
            (delivery["quantity"], delivery["hub_point_id"], delivery["product_id"])
        )
        c.execute(
            """INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
            VALUES(?,?,?,?,?,?)""",
            (delivery["hub_point_id"], delivery["product_id"], "supplier_receipt",
             delivery["quantity"], delivery["reference"] or f"Поставка {delivery_id}",
             f"Поставка от продавца ID {delivery['seller_id']}")
        )
        c.execute(
            "UPDATE supplier_deliveries SET status='Принята',received_at=CURRENT_TIMESTAMP WHERE id=?",
            (delivery_id,)
        )
        return delivery["quantity"]


def finance_summary():
    """Order-level estimated costs across the whole database; excludes unbooked batch-trip projections."""
    with db() as c:
        return dict(c.execute("""SELECT COUNT(*) AS orders_count,
        COALESCE(SUM(estimated_cost),0) AS estimated_cost,
        COALESCE(SUM(CASE WHEN route LIKE 'Mesh%' THEN 1 ELSE 0 END),0) AS mesh_orders,
        COALESCE(SUM(CASE WHEN route LIKE 'Hub%' THEN 1 ELSE 0 END),0) AS hub_orders
        FROM orders WHERE status!='Отменён'""").fetchone())


def operational_snapshot():
    """Return a compact live snapshot for the operator's control room."""
    with db() as c:
        order_rows = c.execute("""
            SELECT status, route, COUNT(*) AS n, COALESCE(SUM(estimated_cost),0) AS cost
            FROM orders GROUP BY status, route
        """).fetchall()
        status_rows = c.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status").fetchall()
        status_counts = {r["status"]: int(r["n"]) for r in status_rows}
        route_counts = {}
        total_cost = 0.0
        total_orders = 0
        for row in order_rows:
            route = row["route"] or "Не назначен"
            route_counts[route] = route_counts.get(route, 0) + int(row["n"])
            total_orders += int(row["n"])
            total_cost += float(row["cost"] or 0)

        inventory = c.execute("""
            SELECT COUNT(*) AS sku_rows,
                   COALESCE(SUM(quantity),0) AS physical_units,
                   COALESCE(SUM(reserved),0) AS reserved_units,
                   COALESCE(SUM(incoming),0) AS incoming_units,
                   COALESCE(SUM(CASE WHEN quantity+incoming-reserved<=5 THEN 1 ELSE 0 END),0) AS low_stock_rows
            FROM inventory
        """).fetchone()
        batches = c.execute("""
            SELECT COUNT(*) AS open_batches,
                   COALESCE(SUM(CASE WHEN status='waiting' THEN 1 ELSE 0 END),0) AS waiting_batches,
                   COALESCE(SUM(CASE WHEN status='blocked_hub_stock' THEN 1 ELSE 0 END),0) AS blocked_batches
            FROM mesh_batches WHERE status IN ('waiting','blocked_hub_stock')
        """).fetchone()
        incidents = c.execute("""
            SELECT COUNT(*) AS open_incidents,
                   COALESCE(SUM(CASE WHEN applied=1 THEN 1 ELSE 0 END),0) AS rerouted_incidents
            FROM incidents WHERE status='open'
        """).fetchone()
        returns = c.execute("""
            SELECT COUNT(*) AS pending_returns FROM returns WHERE status='pending'
        """).fetchone()
        deliveries = c.execute("""
            SELECT COUNT(*) AS pending_deliveries FROM supplier_deliveries
            WHERE status='Ожидает приёмки'
        """).fetchone()
        points = c.execute("""
            SELECT COUNT(*) AS active_points,
                   SUM(CASE WHEN point_type='Anchor' THEN 1 ELSE 0 END) AS anchors,
                   SUM(CASE WHEN point_type='Hub' THEN 1 ELSE 0 END) AS hubs,
                   SUM(CASE WHEN point_type='Обычный' THEN 1 ELSE 0 END) AS pickup_points
            FROM pickup_points WHERE active=1
        """).fetchone()
        today = c.execute("""
            SELECT COUNT(*) AS orders_today,
                   COALESCE(SUM(estimated_cost),0) AS cost_today
            FROM orders WHERE date(created_at)=date('now','localtime')
              AND status!='Отменён'
        """).fetchone()
        today_runs = c.execute("""
            SELECT COUNT(*) AS runs_today,
                   COALESCE(SUM(created_orders),0) AS simulated_orders
            FROM operational_runs WHERE date(created_at)=date('now','localtime')
        """).fetchone()
        return {
            "status_counts": status_counts,
            "route_counts": route_counts,
            "total_orders": total_orders,
            "estimated_cost": total_cost,
            "inventory": dict(inventory),
            "batches": dict(batches),
            "incidents": dict(incidents),
            "returns": dict(returns),
            "deliveries": dict(deliveries),
            "points": dict(points),
            "today": dict(today),
            "today_runs": dict(today_runs),
        }


def operational_runs_df(limit=20):
    with db() as c:
        return pd.read_sql_query("""
            SELECT run_code AS 'Смена',created_orders AS 'Создано заказов',
            failed_orders AS 'Не создано',dispatched_orders AS 'Отправлено',
            ready_orders AS 'Прибыло в ПВЗ',issued_orders AS 'Выдано',
            incidents_created AS 'Инцидентов',ROUND(estimated_cost,2) AS 'Расчётные затраты, ₽',
            notes AS 'Результат',created_at AS 'Время запуска'
            FROM operational_runs ORDER BY id DESC LIMIT ?
        """, c, params=(int(limit),))


def run_daily_operations(order_limit=80, simulate_incident=False, destination_point_id=None):
    """Run one persisted demo work shift using current stock, routing rules and order lifecycle."""
    from datetime import datetime
    import uuid

    order_limit = int(order_limit)
    if not 1 <= order_limit <= 500:
        raise ValueError("Размер смены должен быть от 1 до 500 заказов.")

    settings = {key: float(item["value"]) for key, item in settings_dict().items()}
    candidates = inventory_df()
    points = points_df()
    products = products_df()
    anchors = points[(points["Тип"] == "Anchor") & (points["Активен"] == 1)]
    destinations = points[(points["Тип"] == "Обычный") & (points["Активен"] == 1)]
    if candidates.empty or products.empty or anchors.empty or destinations.empty:
        raise ValueError("Для запуска смены нужны товары, активный Anchor, обычный ПВЗ и складские остатки.")

    # Map actual available stock to the Anchor selected by the normal order engine.
    stock = candidates[
        (candidates["Тип"] == "Anchor") & (candidates["Доступно"] > 0)
    ].copy()
    if stock.empty:
        raise ValueError("На Anchor нет доступного запаса. Сначала пополните локальный склад.")
    stock = stock.merge(products[["id", "SKU", "Объём, л"]], on="SKU", how="inner")
    if destination_point_id is not None:
        destinations = destinations[destinations["id"] == int(destination_point_id)]
        if destinations.empty:
            raise ValueError("Выбранный ПВЗ назначения не найден или неактивен.")
    # The normal order engine chooses the first Anchor in the destination city.
    # Keep the simulation on that same Anchor so the stock preview matches actual routing.
    destination_city = destinations.iloc[0]["Город"]
    matching_anchors = anchors[anchors["Город"] == destination_city]
    chosen_anchor = (matching_anchors.iloc[0] if not matching_anchors.empty else anchors.iloc[0])
    stock = stock[stock["ПВЗ"] == chosen_anchor["Название"]]
    if stock.empty:
        raise ValueError(f"На Anchor «{chosen_anchor['Название']}» нет доступного запаса.")
    destination_ids = destinations["id"].astype(int).tolist()
    destination_id = destination_ids[0]
    prefix = "SHIFT-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()

    created_codes = []
    failed = 0
    candidate_rows = stock.to_dict("records")
    # Concentrate the demo shift on one selected PВЗ so a full batch can be demonstrated.
    # Rotate SKUs while respecting the actual available stock on the Anchor.
    for number in range(1, order_limit + 1):
        row = candidate_rows[(number - 1) % len(candidate_rows)]
        code = f"{prefix}-{number:04d}"
        try:
            create_order(code, int(row["id"]), destination_id, 1, settings)
            created_codes.append(code)
            row["Доступно"] = int(row["Доступно"]) - 1
            if row["Доступно"] <= 0:
                candidate_rows = [item for item in candidate_rows if item["Доступно"] > 0]
                if not candidate_rows:
                    break
        except ValueError:
            failed += 1
            # A given destination/product combination may be unavailable at the Hub.
            row["Доступно"] = 0
            candidate_rows = [item for item in candidate_rows if item["Доступно"] > 0]
            if not candidate_rows:
                break

    if not created_codes:
        raise ValueError("Не удалось создать заказы: проверьте остатки Anchor и Hub, а также точки назначения.")

    batch_results = process_mesh_batches()
    placeholders = ",".join("?" for _ in created_codes)
    with db() as c:
        rows = c.execute(
            f"SELECT id,status,route FROM orders WHERE order_code IN ({placeholders}) ORDER BY id",
            created_codes
        ).fetchall()

    # Hub orders are reserved immediately; move them into transport.
    dispatched = []
    for row in rows:
        if row["status"] == "Зарезервирован":
            update_order_status(int(row["id"]), "В пути")
            dispatched.append(int(row["id"]))
        elif row["status"] == "В пути":
            dispatched.append(int(row["id"]))

    incident_count = 0
    if simulate_incident:
        with db() as c:
            incident_candidate = c.execute(
                f"""SELECT id FROM orders WHERE order_code IN ({placeholders})
                AND status='В пути' AND route LIKE 'Mesh%' ORDER BY id LIMIT 1""",
                created_codes
            ).fetchone()
        if incident_candidate:
            add_incident(
                int(incident_candidate["id"]),
                "Перекрытие дороги",
                "Сценарий смены: на прямом участке обнаружено перекрытие дороги, требуется объезд."
            )
            incident_count = 1

    # Simulate a normal part of the shift: some shipments arrive and a portion is issued.
    ready_ids = []
    issued_ids = []
    for index, order_id in enumerate(dispatched):
        if index % 3 == 0:
            update_order_status(order_id, "Готов к выдаче")
            ready_ids.append(order_id)
            if index % 2 == 0:
                update_order_status(order_id, "Выдан")
                issued_ids.append(order_id)

    with db() as c:
        costs = c.execute(
            f"SELECT COALESCE(SUM(estimated_cost),0) AS total FROM orders WHERE order_code IN ({placeholders})",
            created_codes
        ).fetchone()["total"]
        c.execute("""
            INSERT INTO operational_runs(
                run_code,created_orders,failed_orders,dispatched_orders,ready_orders,
                issued_orders,incidents_created,estimated_cost,notes
            ) VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            prefix, len(created_codes), failed, len(dispatched), len(ready_ids),
            len(issued_ids), incident_count, float(costs),
            f"Проверено партий: {len(batch_results)}; смоделирована смена на текущих остатках."
        ))

    return {
        "run_code": prefix,
        "created_orders": len(created_codes),
        "failed_orders": failed,
        "dispatched_orders": len(dispatched),
        "ready_orders": len(ready_ids),
        "issued_orders": len(issued_ids),
        "incidents_created": incident_count,
        "estimated_cost": float(costs),
        "batch_results": batch_results,
    }


def clear_temporary_simulation_data():
    """Remove demo shifts and reverse their stock ledger without touching manual operations."""
    with db() as c:
        run_codes={r["run_code"] for r in c.execute("SELECT run_code FROM operational_runs")}
        state=c.execute("SELECT shift_code FROM simulation_control WHERE id=1").fetchone()
        if state and state["shift_code"]:
            run_codes.add(state["shift_code"])
        run_codes.update(r["shift_code"] for r in c.execute(
            "SELECT DISTINCT shift_code FROM simulation_events"))
        if not run_codes:
            c.execute("DELETE FROM operational_runs")
            return {"runs":0,"orders":0,"incidents":0,"returns":0,"batches":0,"deliveries":0}

        order_ids=[]; order_codes=[]; batch_ids=set()
        for code in run_codes:
            for row in c.execute(
                "SELECT id,order_code,batch_id FROM orders WHERE order_code LIKE ?",
                (f"{code}-%",)
            ):
                order_ids.append(int(row["id"])); order_codes.append(row["order_code"])
                if row["batch_id"] is not None: batch_ids.add(int(row["batch_id"]))

        c.execute("DELETE FROM simulation_supply_transports")
        # Reverse the complete simulation ledger in strict reverse insertion order.
        # Return movements use the return ID as reference, not the order code.
        movement_rows=[]
        return_ids=[int(r["id"]) for r in c.execute(
            f"SELECT id FROM returns WHERE order_id IN ({','.join('?' for _ in order_ids)})",
            order_ids
        ).fetchall()] if order_ids else []
        for code in run_codes:
            movement_rows.extend(c.execute(
                """SELECT id,point_id,product_id,movement_type,quantity,reference
                FROM stock_movements WHERE reference LIKE ?""",(f"{code}-%",)).fetchall())
        if return_ids:
            movement_rows.extend(c.execute(
                f"""SELECT id,point_id,product_id,movement_type,quantity,reference
                FROM stock_movements WHERE movement_type='return'
                AND reference IN ({','.join('?' for _ in return_ids)})""",
                [str(rid) for rid in return_ids]
            ).fetchall())
        movement_rows.sort(key=lambda row: int(row["id"]), reverse=True)
        for m in movement_rows:
            q=int(m["quantity"]); pid=int(m["point_id"]); sku=int(m["product_id"]); kind=m["movement_type"]
            if kind=="reserve":
                c.execute("UPDATE inventory SET reserved=MAX(0,reserved-?) WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="release_reserve":
                c.execute("UPDATE inventory SET reserved=reserved+? WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="dispatch":
                c.execute("UPDATE inventory SET quantity=quantity+?,reserved=reserved+? WHERE point_id=? AND product_id=?",(q,q,pid,sku))
            elif kind=="arrival":
                c.execute("UPDATE inventory SET quantity=MAX(0,quantity-?) WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="issue":
                c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="supplier_receipt":
                c.execute("UPDATE inventory SET quantity=MAX(0,quantity-?) WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="hub_transfer_out":
                c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="hub_transfer_in":
                c.execute("UPDATE inventory SET quantity=MAX(0,quantity-?) WHERE point_id=? AND product_id=?",(q,pid,sku))
            elif kind=="return":
                c.execute("UPDATE inventory SET quantity=MAX(0,quantity-?) WHERE point_id=? AND product_id=?",(q,pid,sku))

        returns=[]
        if order_ids:
            marks=",".join("?" for _ in order_ids)
            returns=c.execute(f"SELECT id,order_id FROM returns WHERE order_id IN ({marks})",order_ids).fetchall()
            incidents_count=c.execute(f"SELECT COUNT(*) AS n FROM incidents WHERE order_id IN ({marks})",order_ids).fetchone()["n"]
            c.execute(f"DELETE FROM incidents WHERE order_id IN ({marks})",order_ids)
            c.execute(f"DELETE FROM returns WHERE order_id IN ({marks})",order_ids)
            c.execute(f"DELETE FROM stock_movements WHERE reference IN ({','.join('?' for _ in order_codes)})",order_codes)
            if returns:
                return_marks=",".join("?" for _ in returns)
                c.execute(f"DELETE FROM stock_movements WHERE movement_type='return' AND reference IN ({return_marks})",
                          [str(r["id"]) for r in returns])
        else:
            incidents_count=0

        # Events reference orders, so remove them before deleting those orders.
        for code in run_codes:
            c.execute("DELETE FROM simulation_events WHERE shift_code=?",(code,))
            c.execute("DELETE FROM supplier_deliveries WHERE reference LIKE ?",(f"{code}-SUP-%",))
        if order_ids:
            c.execute(f"DELETE FROM orders WHERE id IN ({','.join('?' for _ in order_ids)})",order_ids)

        deleted_batches=0
        for batch_id in batch_ids:
            remaining=c.execute("SELECT COUNT(*) AS n FROM orders WHERE batch_id=?",(batch_id,)).fetchone()["n"]
            if remaining==0:
                c.execute("DELETE FROM mesh_batches WHERE id=?",(batch_id,)); deleted_batches+=1
        c.execute("DELETE FROM operational_runs")
        c.execute("""UPDATE simulation_control SET running=0,shift_code=NULL,tick_no=0,
            started_at=NULL,stopped_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=1""")
        return {"runs":len(run_codes),"orders":len(order_ids),"incidents":int(incidents_count),
                "returns":len(returns),"batches":deleted_batches,
                "deliveries":sum(1 for code in run_codes for _ in [code])}

def simulation_control_state():
    """Return only the simulation control row; avoids loading the event log on every poll."""
    with db() as c:
        c.execute("INSERT OR IGNORE INTO simulation_control(id,running,tick_no) VALUES(1,0,0)")
        row = c.execute("SELECT * FROM simulation_control WHERE id=1").fetchone()
        return dict(row) if row else {
            "id": 1, "running": 0, "shift_code": None, "tick_no": 0
        }


def simulation_state():
    with db() as c:
        c.execute("INSERT OR IGNORE INTO simulation_control(id,running,tick_no) VALUES(1,0,0)")
        row = c.execute("SELECT * FROM simulation_control WHERE id=1").fetchone()
        events = pd.read_sql_query(
            """SELECT e.tick_no AS 'Такт',e.event_type AS Тип,e.message AS Событие,
               COALESCE(o.order_code,'—') AS Заказ,e.created_at AS Время
               FROM simulation_events e LEFT JOIN orders o ON o.id=e.order_id
               ORDER BY e.id DESC LIMIT 80""", c
        )
        return (dict(row) if row else {
            "id": 1, "running": 0, "shift_code": None, "tick_no": 0
        }), events



def start_continuous_simulation():
    from datetime import datetime
    import uuid
    code="LIVE-"+datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:4].upper()
    with db() as c:
        c.execute("""UPDATE simulation_control SET running=1,shift_code=?,tick_no=0,
            started_at=CURRENT_TIMESTAMP,stopped_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=1""",(code,))
        c.execute("INSERT INTO simulation_events(shift_code,tick_no,event_type,message) VALUES(?,0,'Смена','Запущена непрерывная диспетчерская смена')",(code,))
        c.execute("""INSERT INTO operational_runs(run_code,notes)
            VALUES(?,?)""",(code,"Непрерывная диспетчерская смена; статистика обновляется по тактам."))
    return code


def stop_continuous_simulation():
    with db() as c:
        row=c.execute("SELECT shift_code,tick_no,running FROM simulation_control WHERE id=1").fetchone()
        if row and row["running"]:
            c.execute("UPDATE simulation_control SET running=0,stopped_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=1")
            c.execute("INSERT INTO simulation_events(shift_code,tick_no,event_type,message) VALUES(?,?,'Смена','Диспетчер остановил смену')",
                      (row["shift_code"],row["tick_no"]))
            return True
    return False


def _simulation_event(c, shift_code, tick_no, event_type, message, order_id=None):
    c.execute("""INSERT INTO simulation_events(shift_code,tick_no,event_type,message,order_id)
        VALUES(?,?,?,?,?)""",(shift_code,tick_no,event_type,message,order_id))


def _auto_supplier_and_anchor_replenishment(tick_no, shift_code):
    """Create modelled inbound supplier and Hub-to-Anchor transport legs."""
    import random
    points=points_df()
    hubs=points[(points["Тип"]=="Hub") & (points["Активен"]==1)]
    anchors=points[(points["Тип"]=="Anchor") & (points["Активен"]==1)]
    products=products_df()
    sellers=sellers_df()
    if hubs.empty or anchors.empty or products.empty or sellers.empty:
        return
    hub=hubs.iloc[0]
    anchor=anchors.iloc[0]
    if tick_no % 12 == 1:
        product=products.sample(1).iloc[0]
        qty=random.randint(12,30)
        ref=f"{shift_code}-SUP-{tick_no:05d}"
        delivery_id=create_supplier_delivery(int(sellers.iloc[0]["id"]),int(product["id"]),
                                              int(hub["id"]),qty,ref)
        with db() as c:
            c.execute("""INSERT INTO simulation_supply_transports(
                shift_code,tick_no,transport_type,reference,delivery_id,product_id,quantity,
                from_name,to_name,from_lat,from_lon,to_lat,to_lon)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (shift_code,tick_no,"Продавец → Hub",ref,delivery_id,int(product["id"]),qty,
                 "Поставщик (условная точка)",hub["Название"],55.76,49.12,
                 55.848721,48.842408))
            _simulation_event(c,shift_code,tick_no,"Поставка продавца",
                              f"Продавец отправил {qty} ед. {product['SKU']} на Hub; машина в пути")
    # Reserve goods at Hub for a modelled Hub -> Anchor replenishment transport.
    # Replenishment planning is evaluated every third tick to reduce SQLite load.
    if tick_no % 3 != 1:
        return
    for _,product in products.iterrows():
        with db() as c:
            # Старые SQLite-базы могли не содержать строки остатков для всех SKU в Hub/Anchor.
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity,reserved,incoming) VALUES(?,?,0,0,0)",
                      (int(anchor["id"]),int(product["id"])))
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity,reserved,incoming) VALUES(?,?,0,0,0)",
                      (int(hub["id"]),int(product["id"])))
            arow=c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",
                           (int(anchor["id"]),int(product["id"]))).fetchone()
            hrow=c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",
                           (int(hub["id"]),int(product["id"]))).fetchone()
            anchor_available=int(arow["available"] or 0) if arow else 0
            hub_available=int(hrow["available"] or 0) if hrow else 0
            pending=c.execute("""SELECT COALESCE(SUM(quantity),0) AS quantity
                FROM simulation_supply_transports
                WHERE shift_code=? AND transport_type='Hub → Anchor'
                AND product_id=? AND to_name=? AND status='В пути'""",
                (shift_code,int(product["id"]),anchor["Название"])).fetchone()
            pending_quantity=int(pending["quantity"] or 0)
            if anchor_available + pending_quantity <= 2 and hub_available>=8:
                qty=min(15,hub_available)
                ref=f"{shift_code}-H2A-{tick_no:05d}-{int(product['id'])}"
                c.execute("UPDATE inventory SET quantity=quantity-? WHERE point_id=? AND product_id=?",
                          (qty,int(hub["id"]),int(product["id"])))
                c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                    VALUES(?,?,?,?,?,?)""",
                    (int(hub["id"]),int(product["id"]),"hub_transfer_out",qty,ref,
                     "Отправка пополнения Hub → Anchor"))
                c.execute("""INSERT INTO simulation_supply_transports(
                    shift_code,tick_no,transport_type,reference,product_id,quantity,
                    from_name,to_name,from_lat,from_lon,to_lat,to_lon)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (shift_code,tick_no,"Hub → Anchor",ref,int(product["id"]),qty,
                     hub["Название"],anchor["Название"],55.848721,48.842408,
                     56.631364,47.890917))
                _simulation_event(c,shift_code,tick_no,"Пополнение Anchor",
                                  f"Из Hub отправлено {qty} ед. {product['SKU']} на {anchor['Название']}")


def _advance_supply_transports(shift_code,tick_no):
    import random
    with db() as c:
        rows=c.execute("""SELECT * FROM simulation_supply_transports
            WHERE shift_code=? AND status='В пути' ORDER BY id LIMIT 15""",(shift_code,)).fetchall()
    for row in rows:
        with db() as c:
            progress=min(1.0,float(row["progress"])+random.uniform(0.18,0.32))
            status="Прибыла" if progress>=1 else "В пути"
            c.execute("UPDATE simulation_supply_transports SET progress=?,status=? WHERE id=?",
                      (progress,status,row["id"]))
        if progress>=1:
            if row["transport_type"]=="Продавец → Hub":
                receive_supplier_delivery(int(row["delivery_id"]))
            else:
                with db() as c:
                    c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES((SELECT id FROM pickup_points WHERE name=?),?,0)",
                              (row["to_name"],row["product_id"]))
                    dest=c.execute("SELECT id FROM pickup_points WHERE name=?",(row["to_name"],)).fetchone()
                    if dest:
                        c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",
                                  (row["quantity"],dest["id"],row["product_id"]))
                        c.execute("""INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note)
                            VALUES(?,?,?,?,?,?)""",
                            (dest["id"],row["product_id"],"hub_transfer_in",row["quantity"],row["reference"],
                             "Прибытие пополнения Hub → Anchor"))
            with db() as c:
                _simulation_event(c,shift_code,tick_no,"Прибытие поставки",
                                  f"{row['transport_type']}: груз {row['quantity']} ед. прибыл в {row['to_name']}")



def continuous_simulation_tick():
    """Advance one bounded simulation tick with batched progress writes."""
    import random

    with db() as c:
        state = c.execute("SELECT * FROM simulation_control WHERE id=1").fetchone()
        if not state or not state["running"]:
            return {"running": False, "tick": 0}
        shift_code = state["shift_code"]
        tick_no = int(state["tick_no"]) + 1
        c.execute(
            "UPDATE simulation_control SET tick_no=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (tick_no,),
        )

    _auto_supplier_and_anchor_replenishment(tick_no, shift_code)
    _advance_supply_transports(shift_code, tick_no)

    # One order every two ticks. Destinations are sampled independently so
    # the demo represents demand across the whole local PVZ network.
    if tick_no % 2 == 1:
        destinations = points_df()
        destinations = destinations[
            (destinations["Тип"] == "Обычный") & (destinations["Активен"] == 1)
        ]
        products = products_df()
        settings = {k: float(v["value"]) for k, v in settings_dict().items()}
        if not destinations.empty and not products.empty:
            destination = destinations.sample(1).iloc[0]
            product = products.sample(1).iloc[0]
            code = f"{shift_code}-O-{tick_no:05d}"
            # A small simulation-only batch target keeps the demo observable
            # without changing the model's saved business settings.
            simulation_settings = dict(settings)
            simulation_settings["batch_target_orders"] = min(
                max(1, int(settings["batch_target_orders"])), 6
            )
            try:
                create_order(
                    code, int(product["id"]), int(destination["id"]), 1,
                    simulation_settings,
                )
                with db() as c:
                    created = c.execute(
                        "SELECT id FROM orders WHERE order_code=?", (code,)
                    ).fetchone()
                    _simulation_event(
                        c, shift_code, tick_no, "Новый заказ",
                        f"Создан заказ {code} в ПВЗ «{destination['Название']}»; маршрут выбран по расчётным условиям",
                        int(created["id"]) if created else None,
                    )
            except ValueError as exc:
                with db() as c:
                    _simulation_event(
                        c, shift_code, tick_no, "Дефицит запаса",
                        f"Заказ не создан: {str(exc)[:180]}",
                    )

    # Open-batch timeout evaluation is less frequent; new orders evaluate their batch on insertion.
    if tick_no % 3 == 0:
        process_mesh_batches()

    # Dispatch Hub reservations in a small bounded batch.
    with db() as c:
        reserved = c.execute(
            """SELECT id,order_code FROM orders
               WHERE status='Зарезервирован' AND order_code LIKE ?
               ORDER BY id LIMIT 12""",
            (f"{shift_code}-%",),
        ).fetchall()
    for row in reserved:
        update_order_status(int(row["id"]), "В пути")
        with db() as c:
            _simulation_event(
                c, shift_code, tick_no, "Отправка",
                f"Заказ {row['order_code']} покинул Hub", int(row["id"]),
            )

    # Read and update all moving vehicles in one SQLite transaction rather
    # than opening a connection and issuing several queries for every order.
    arrived = []
    delayed = []
    with db() as c:
        moving = c.execute(
            """SELECT o.id,o.order_code,o.route,
                      COALESCE(v.progress,0) AS progress
               FROM orders o
               LEFT JOIN simulation_vehicle_progress v ON v.order_id=o.id
               WHERE o.status='В пути' AND o.order_code LIKE ?
               ORDER BY o.id""",
            (f"{shift_code}-%",),
        ).fetchall()
        for order in moving:
            progress = min(1.0, float(order["progress"]) + random.uniform(0.18, 0.30))
            c.execute(
                """INSERT INTO simulation_vehicle_progress(order_id,progress)
                   VALUES(?,?) ON CONFLICT(order_id) DO UPDATE SET
                   progress=excluded.progress,updated_at=CURRENT_TIMESTAMP""",
                (int(order["id"]), progress),
            )
            if progress >= 1.0:
                arrived.append((int(order["id"]), order["order_code"]))
            elif "Mesh" in str(order["route"]) and random.random() < 0.012:
                delayed.append((int(order["id"]), order["order_code"]))

    for order_id, order_code in arrived:
        try:
            update_order_status(order_id, "Готов к выдаче")
            with db() as c:
                _simulation_event(
                    c, shift_code, tick_no, "Прибытие в ПВЗ",
                    f"Заказ {order_code} прибыл в пункт назначения", order_id,
                )
        except ValueError:
            pass

    for order_id, order_code in delayed:
        try:
            add_incident(
                order_id, "Дорожная задержка",
                "Случайная задержка движения на маршруте; проверка обхода через Hub.",
            )
            with db() as c:
                _simulation_event(
                    c, shift_code, tick_no, "Инцидент",
                    f"Для заказа {order_code} зарегистрирована дорожная задержка", order_id,
                )
        except ValueError:
            pass

    # Customer pickup and occasional return flow.
    with db() as c:
        ready = c.execute(
            """SELECT id,order_code FROM orders
               WHERE status='Готов к выдаче' AND order_code LIKE ?
               ORDER BY id LIMIT 12""",
            (f"{shift_code}-%",),
        ).fetchall()
    for order in ready:
        if random.random() < 0.45:
            try:
                update_order_status(int(order["id"]), "Выдан")
                with db() as c:
                    _simulation_event(
                        c, shift_code, tick_no, "Выдача покупателю",
                        f"Заказ {order['order_code']} выдан покупателю", int(order["id"]),
                    )
                if random.random() < 0.10:
                    ret_id = create_return(
                        int(order["id"]), 1,
                        random.choice([
                            "Не подошёл товар", "Повреждение упаковки",
                            "Отказ покупателя", "Ошибочный заказ",
                        ]),
                    )
                    process_return(ret_id, random.choice(["Пригоден", "Повреждён"]))
                    with db() as c:
                        _simulation_event(
                            c, shift_code, tick_no, "Возврат",
                            f"Оформлен и обработан возврат по заказу {order['order_code']}",
                            int(order["id"]),
                        )
            except ValueError:
                pass

    with db() as c:
        counts = c.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='В пути' THEN 1 ELSE 0 END) AS moving,
                      SUM(CASE WHEN status='Готов к выдаче' THEN 1 ELSE 0 END) AS ready,
                      SUM(CASE WHEN status='Выдан' THEN 1 ELSE 0 END) AS issued,
                      COALESCE(SUM(estimated_cost),0) AS costs
               FROM orders WHERE order_code LIKE ?""",
            (f"{shift_code}-%",),
        ).fetchone()
        incidents_total = c.execute(
            """SELECT COUNT(*) AS n FROM incidents
               WHERE order_id IN (SELECT id FROM orders WHERE order_code LIKE ?)""",
            (f"{shift_code}-%",),
        ).fetchone()["n"]
        c.execute(
            """UPDATE operational_runs SET created_orders=?,failed_orders=?,
               dispatched_orders=?,ready_orders=?,issued_orders=?,incidents_created=?,estimated_cost=?
               WHERE run_code=?""",
            (
                int(counts["total"] or 0), 0,
                int(counts["moving"] or 0) + int(counts["ready"] or 0) + int(counts["issued"] or 0),
                int(counts["ready"] or 0), int(counts["issued"] or 0),
                int(incidents_total), float(counts["costs"] or 0), shift_code,
            ),
        )
        return {
            "running": True, "shift_code": shift_code, "tick": tick_no,
            "orders": int(counts["total"] or 0),
            "moving": int(counts["moving"] or 0),
            "ready": int(counts["ready"] or 0),
            "issued": int(counts["issued"] or 0),
            "estimated_cost": float(counts["costs"] or 0),
        }



def _simulation_destination_index(tick_no, destination_count, batch_target_orders, order_interval_ticks=2):
    """Keep orders for one destination together long enough to form a demonstrable batch."""
    if destination_count <= 0:
        return None
    interval = max(1, int(order_interval_ticks))
    target = max(1, int(batch_target_orders))
    generated_order_slot = max(0, (int(tick_no) - 1) // interval)
    return (generated_order_slot // target) % int(destination_count)


def simulation_shift_snapshot():
    """Counts for the active shift only; global network counters live in operational_snapshot()."""
    with db() as c:
        state = c.execute("SELECT shift_code FROM simulation_control WHERE id=1").fetchone()
        code = state["shift_code"] if state else None
        if not code:
            return {"shift_code":None,"total_orders":0,"status_counts":{},
                    "mesh_waiting_orders":0,"mesh_in_transit_orders":0,
                    "mesh_ready_orders":0,"mesh_blocked_orders":0,"mesh_blocked_batches":0}
        rows = c.execute("""SELECT status,COUNT(*) AS n FROM orders
            WHERE order_code LIKE ? GROUP BY status""", (f"{code}-%",)).fetchall()
        status_counts = {row["status"]:int(row["n"]) for row in rows}
        batch_rows = c.execute("""SELECT
            SUM(CASE WHEN b.status='blocked_hub_stock' THEN 1 ELSE 0 END) AS blocked_batches
            FROM mesh_batches b WHERE b.id IN (
                SELECT DISTINCT batch_id FROM orders
                WHERE order_code LIKE ? AND batch_id IS NOT NULL
            )""", (f"{code}-%",)).fetchone()
        blocked_orders = c.execute("""SELECT COUNT(*) AS n FROM orders o
            JOIN mesh_batches b ON b.id=o.batch_id
            WHERE o.order_code LIKE ? AND b.status='blocked_hub_stock'""",
            (f"{code}-%",)).fetchone()["n"]
        return {
            "shift_code":code,
            "total_orders":sum(status_counts.values()),
            "status_counts":status_counts,
            "mesh_waiting_orders":c.execute(
                """SELECT COUNT(*) FROM orders o JOIN mesh_batches b ON b.id=o.batch_id
                WHERE o.order_code LIKE ? AND o.route LIKE 'Mesh%' AND o.status='Ожидает партии'
                AND b.status='waiting'""", (f"{code}-%",)).fetchone()[0],
            "mesh_in_transit_orders":c.execute(
                "SELECT COUNT(*) FROM orders WHERE order_code LIKE ? AND route LIKE 'Mesh%' AND status='В пути'",
                (f"{code}-%",)).fetchone()[0],
            "mesh_ready_orders":c.execute(
                "SELECT COUNT(*) FROM orders WHERE order_code LIKE ? AND route LIKE 'Mesh%' AND status='Готов к выдаче'",
                (f"{code}-%",)).fetchone()[0],
            "mesh_blocked_orders":int(blocked_orders or 0),
            "mesh_blocked_batches":int(batch_rows["blocked_batches"] or 0),
        }


def simulation_timeline_df(limit=100):
    with db() as c:
        state = c.execute("SELECT shift_code FROM simulation_control WHERE id=1").fetchone()
        code = state["shift_code"] if state else None
        if not code:
            return pd.DataFrame(columns=["Такт","Тип","Событие","Заказ","Время"])
        return pd.read_sql_query("""SELECT e.tick_no AS 'Такт',e.event_type AS Тип,
            e.message AS Событие,COALESCE(o.order_code,'—') AS Заказ,e.created_at AS Время
            FROM simulation_events e LEFT JOIN orders o ON o.id=e.order_id
            WHERE e.shift_code=? ORDER BY e.id DESC LIMIT ?""",c,params=(code,int(limit)))


def simulation_vehicles_df():
    with db() as c:
        state = c.execute("SELECT shift_code FROM simulation_control WHERE id=1").fetchone()
        code = state["shift_code"] if state else None
        columns = ["id","Заказ","Маршрут","Статус","Затраты, ₽","Источник","Назначение",
                   "Город_источника","Город_назначения","Прогресс","Тип_источника",
                   "source_lat","source_lon","dest_lat","dest_lon"]
        if not code:
            return pd.DataFrame(columns=columns)
        orders=pd.read_sql_query("""SELECT o.id,o.order_code AS Заказ,o.route AS Маршрут,
            o.status AS Статус,o.estimated_cost AS 'Затраты, ₽',
            COALESCE(src.name,'Источник не указан') AS Источник,
            COALESCE(dst.name,'ПВЗ не указан') AS Назначение,
            COALESCE(src.city,'') AS Город_источника,COALESCE(dst.city,'') AS Город_назначения,
            COALESCE(v.progress,0) AS Прогресс,
            CASE WHEN src.point_type='Hub' THEN 'Hub' ELSE 'Anchor' END AS Тип_источника,
            NULL AS source_lat,NULL AS source_lon,NULL AS dest_lat,NULL AS dest_lon
            FROM orders o
            LEFT JOIN pickup_points src ON src.id=o.source_point_id
            LEFT JOIN pickup_points dst ON dst.id=o.destination_point_id
            LEFT JOIN simulation_vehicle_progress v ON v.order_id=o.id
            WHERE o.status='В пути' AND o.order_code LIKE ?
            ORDER BY o.id DESC LIMIT 80""",c,params=(f"{code}-%",))
        supplies=pd.read_sql_query("""SELECT id,reference AS Заказ,transport_type AS Маршрут,
            status AS Статус,0.0 AS 'Затраты, ₽',from_name AS Источник,to_name AS Назначение,
            '' AS Город_источника,'' AS Город_назначения,progress AS Прогресс,
            'Supply' AS Тип_источника,from_lat AS source_lat,from_lon AS source_lon,
            to_lat AS dest_lat,to_lon AS dest_lon
            FROM simulation_supply_transports WHERE status='В пути' AND shift_code=?
            ORDER BY id DESC LIMIT 40""",c,params=(code,))
        return pd.concat([orders,supplies],ignore_index=True)


def simulation_financials():
    """Transport-only model for the active shift, with blocked and waiting batches made explicit."""
    empty = {"orders":0,"mesh_orders":0,"hub_orders":0,"mesh_trips":0,
             "mesh_waiting_batches":0,"mesh_blocked_batches":0,
             "mesh_waiting_orders":0,"mesh_in_transit_orders":0,"mesh_ready_orders":0,
             "mesh_blocked_orders":0,"mesh_trip_cost":0.0,"mesh_variable_cost":0.0,
             "hub_cost":0.0,"transport_total":0.0,"hub_baseline":0.0,
             "savings":0.0,"returns":0}
    with db() as c:
        state=c.execute("SELECT shift_code FROM simulation_control WHERE id=1").fetchone()
        code=state["shift_code"] if state else None
        if not code:
            return empty
        rows=c.execute("""SELECT
                SUM(CASE WHEN route LIKE 'Mesh%' THEN 1 ELSE 0 END) AS mesh_orders,
                SUM(CASE WHEN route NOT LIKE 'Mesh%' OR route IS NULL THEN 1 ELSE 0 END) AS hub_orders,
                SUM(CASE WHEN route LIKE 'Mesh%' AND status='Ожидает партии' AND batch_id IN
                    (SELECT id FROM mesh_batches WHERE status='waiting') THEN 1 ELSE 0 END) AS mesh_waiting_orders,
                SUM(CASE WHEN route LIKE 'Mesh%' AND status='В пути' THEN 1 ELSE 0 END) AS mesh_in_transit_orders,
                SUM(CASE WHEN route LIKE 'Mesh%' AND status='Готов к выдаче' THEN 1 ELSE 0 END) AS mesh_ready_orders,
                COUNT(*) AS total
            FROM orders WHERE order_code LIKE ?""",(f"{code}-%",)).fetchone()
        mesh_orders=int(rows["mesh_orders"] or 0)
        hub_orders=int(rows["hub_orders"] or 0)
        total=int(rows["total"] or 0)
        settings=_settings_from_db(c)
        batches=c.execute("""SELECT
                SUM(CASE WHEN b.status IN ('dispatched','completed') THEN 1 ELSE 0 END) AS dispatched,
                SUM(CASE WHEN b.status='waiting' THEN 1 ELSE 0 END) AS waiting,
                SUM(CASE WHEN b.status='blocked_hub_stock' THEN 1 ELSE 0 END) AS blocked,
                SUM(CASE WHEN b.status IN ('dispatched','completed') THEN b.trip_cost ELSE 0 END) AS actual_cost
            FROM mesh_batches b
            WHERE b.id IN (SELECT DISTINCT batch_id FROM orders
                WHERE order_code LIKE ? AND route LIKE 'Mesh%' AND batch_id IS NOT NULL)""",
            (f"{code}-%",)).fetchone()
        trips=int(batches["dispatched"] or 0)
        waiting_batches=int(batches["waiting"] or 0)
        blocked_batches=int(batches["blocked"] or 0)
        blocked_orders=int(c.execute("""SELECT COUNT(*) FROM orders o JOIN mesh_batches b ON b.id=o.batch_id
            WHERE o.order_code LIKE ? AND b.status='blocked_hub_stock'""",(f"{code}-%",)).fetchone()[0] or 0)
        trip_cost=float(batches["actual_cost"] or 0) + waiting_batches*float(settings["local_trip_cost"])
        # Blocked Mesh batches are costed conservatively at the Hub reference tariff.
        mesh_variable=(mesh_orders-blocked_orders)*float(settings["mesh_variable_cost"])
        hub=(hub_orders+blocked_orders)*float(settings["hub_cost_per_order"])
        transport=trip_cost+mesh_variable+hub
        baseline=total*float(settings["hub_cost_per_order"])
        returns=c.execute("""SELECT COUNT(*) FROM returns r JOIN orders o ON o.id=r.order_id
            WHERE o.order_code LIKE ?""",(f"{code}-%",)).fetchone()[0]
        return {"orders":total,"mesh_orders":mesh_orders,"hub_orders":hub_orders,
                "mesh_trips":trips,"mesh_waiting_batches":waiting_batches,
                "mesh_blocked_batches":blocked_batches,"mesh_waiting_orders":int(rows["mesh_waiting_orders"] or 0),
                "mesh_in_transit_orders":int(rows["mesh_in_transit_orders"] or 0),
                "mesh_ready_orders":int(rows["mesh_ready_orders"] or 0),"mesh_blocked_orders":blocked_orders,
                "mesh_trip_cost":trip_cost,"mesh_variable_cost":mesh_variable,"hub_cost":hub,
                "transport_total":transport,"hub_baseline":baseline,
                "savings":baseline-transport,"returns":int(returns)}


