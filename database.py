import sqlite3
from pathlib import Path
import pandas as pd
from contextlib import contextmanager

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
            quantity INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0,
            incoming INTEGER NOT NULL DEFAULT 0, UNIQUE(point_id,product_id),
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
        """)
        _add_column(c, "orders", "source_point_id INTEGER")
        _add_column(c, "orders", "estimated_cost REAL NOT NULL DEFAULT 0")
        _add_column(c, "orders", "updated_at TEXT")
        defaults = [
            ("local_trip_cost",2400,"RUB/рейс","Гипотеза","Стоимость локального рейса"),
            ("hub_cost_per_order",62,"RUB/заказ","Неподтверждённая гипотеза","Тестовый ориентир, не себестоимость RWB"),
            ("mesh_variable_cost",14.5,"RUB/заказ","Гипотеза","Прочие переменные расходы"),
            ("batch_target_orders",80,"заказов","Проектное допущение","Ожидаемая загрузка рейса для предварительной оценки"),
            ("max_orders",130,"заказов/рейс","Проектное ограничение","Лимит количества"),
            ("max_volume_l",1500,"л/рейс","Проектное ограничение","Объёмная вместимость"),
            ("max_wait_hours",5,"часов","Проектное ограничение","Не является SLA"),
        ]
        c.executemany("INSERT OR IGNORE INTO settings VALUES(?,?,?,?,?)", defaults)

def seed_demo():
    with connect() as c:
        c.executemany("INSERT OR IGNORE INTO pickup_points(id,name,point_type,city) VALUES(?,?,?,?)", [
            (1,"Anchor Центр","Anchor","Демо-город"),
            (2,"ПВЗ Север","Обычный","Демо-город"),
            (3,"ПВЗ Юг","Обычный","Демо-город"),
            (4,"Центральный склад Hub","Hub","Демо-город")
        ])
        c.executemany("INSERT OR IGNORE INTO products(id,sku,name,category,volume_l,demand_class) VALUES(?,?,?,?,?,?)", [
            (1,"SKU-001","Беспроводная мышь","Электроника",1.2,"AX"),
            (2,"SKU-002","Кабель USB-C","Аксессуары",0.3,"AX"),
            (3,"SKU-003","Настольная лампа","Дом",4.0,"BY")
        ])
        c.executemany("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity,reserved,incoming) VALUES(?,?,?,0,0)", [
            (1,1,90),(1,2,120),(1,3,15),
            (4,1,500),(4,2,800),(4,3,200)
        ])

def products_df():
    with connect() as c:
        return pd.read_sql_query("SELECT id,sku AS SKU,name AS Товар,category AS Категория,volume_l AS 'Объём, л',demand_class AS 'ABC/XYZ' FROM products ORDER BY sku", c)

def inventory_df():
    with connect() as c:
        return pd.read_sql_query("""SELECT p.name AS ПВЗ,p.point_type AS Тип,pr.sku AS SKU,pr.name AS Товар,
        i.quantity AS Остаток,i.reserved AS Резерв,i.incoming AS 'В пути',
        (i.quantity+i.incoming-i.reserved) AS Доступно
        FROM inventory i JOIN pickup_points p ON p.id=i.point_id
        JOIN products pr ON pr.id=i.product_id ORDER BY p.name,pr.sku""", c)

def points_df():
    with connect() as c:
        return pd.read_sql_query("SELECT id,name AS Название,point_type AS Тип,city AS Город,active AS Активен FROM pickup_points ORDER BY id", c)

def orders_df():
    with connect() as c:
        return pd.read_sql_query("""SELECT o.id,o.order_code AS Заказ,pr.sku AS SKU,pr.name AS Товар,o.quantity AS Количество,
        d.name AS 'ПВЗ назначения',s.name AS Источник,o.route AS Маршрут,o.status AS Статус,
        ROUND(o.estimated_cost,2) AS 'Оценка затрат, ₽',o.created_at AS Создан
        FROM orders o JOIN products pr ON pr.id=o.product_id
        JOIN pickup_points d ON d.id=o.destination_point_id
        LEFT JOIN pickup_points s ON s.id=o.source_point_id ORDER BY o.id DESC""", c)

def settings_dict():
    with connect() as c:
        return {r["key"]: dict(r) for r in c.execute("SELECT * FROM settings")}

def save_setting(key, value):
    with connect() as c:
        c.execute("UPDATE settings SET value=? WHERE key=?", (float(value),key))

def add_product(sku,name,category,volume_l,demand_class):
    with connect() as c:
        cur=c.execute("INSERT INTO products(sku,name,category,volume_l,demand_class) VALUES(?,?,?,?,?)",
                      (sku.strip(),name.strip(),category.strip(),float(volume_l),demand_class))
        pid=cur.lastrowid
        for point in c.execute("SELECT id FROM pickup_points WHERE point_type IN ('Anchor','Hub')").fetchall():
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",(point["id"],pid))
        return pid

def receive_stock(point_id,product_id,quantity,note="Приёмка"):
    if quantity<=0: raise ValueError("Количество должно быть больше нуля.")
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",(point_id,product_id))
        c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",(quantity,point_id,product_id))
        c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                  (point_id,product_id,"receipt",quantity,None,note))

def create_order(order_code,product_id,destination_point_id,quantity,settings):
    if quantity<=0: raise ValueError("Количество заказа должно быть больше нуля.")
    with connect() as c:
        dest=c.execute("SELECT * FROM pickup_points WHERE id=? AND active=1",(destination_point_id,)).fetchone()
        if not dest or dest["point_type"]!="Обычный": raise ValueError("Назначением должен быть активный обычный ПВЗ.")
        product=c.execute("SELECT * FROM products WHERE id=?",(product_id,)).fetchone()
        if not product: raise ValueError("Товар не найден.")
        anchor=c.execute("SELECT * FROM pickup_points WHERE point_type='Anchor' AND active=1 ORDER BY id LIMIT 1").fetchone()
        hub=c.execute("SELECT * FROM pickup_points WHERE point_type='Hub' AND active=1 ORDER BY id LIMIT 1").fetchone()
        if not hub: raise ValueError("Добавьте точку типа Hub для резервного маршрута.")
        mesh_capable=bool(anchor and product["volume_l"]*quantity<=settings["max_volume_l"] and quantity<=settings["max_orders"])
        mesh_available=0
        if anchor:
            row=c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",(anchor["id"],product_id)).fetchone()
            mesh_available=row["available"] if row else 0
        mesh_unit=settings["local_trip_cost"]/max(1,settings["batch_target_orders"])+settings["mesh_variable_cost"]
        if mesh_capable and mesh_available>=quantity and mesh_unit<settings["hub_cost_per_order"]:
            source=anchor; route="Mesh"; estimate=mesh_unit*quantity
        else:
            row=c.execute("SELECT quantity-reserved AS available FROM inventory WHERE point_id=? AND product_id=?",(hub["id"],product_id)).fetchone()
            hub_available=row["available"] if row else 0
            if hub_available<quantity: raise ValueError("Недостаточно доступного остатка и в Anchor, и в Hub. Пополните запас.")
            source=hub; route="Hub"; estimate=settings["hub_cost_per_order"]*quantity
        c.execute("INSERT INTO orders(order_code,product_id,destination_point_id,quantity,status,route,source_point_id,estimated_cost,updated_at) VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                  (order_code.strip(),product_id,destination_point_id,quantity,"Зарезервирован",route,source["id"],estimate))
        c.execute("UPDATE inventory SET reserved=reserved+? WHERE point_id=? AND product_id=?",(quantity,source["id"],product_id))
        c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                  (source["id"],product_id,"reserve",quantity,order_code,f"Резерв под заказ {order_code}"))
        return {"route":route,"source":source["name"],"estimated_cost":estimate}

def update_order_status(order_id,new_status):
    transitions={"Зарезервирован":{"В пути","Отменён"},"В пути":{"Готов к выдаче","Ошибка"},"Ошибка":{"В пути","Отменён"},"Готов к выдаче":{"Выдан","Возврат оформлен"}}
    with connect() as c:
        order=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not order: raise ValueError("Заказ не найден.")
        if new_status not in transitions.get(order["status"],set()): raise ValueError(f"Недопустимый переход: {order['status']} → {new_status}.")
        if new_status=="Отменён":
            c.execute("UPDATE inventory SET reserved=MAX(0,reserved-?) WHERE point_id=? AND product_id=?",(order["quantity"],order["source_point_id"],order["product_id"]))
        if new_status=="В пути":
            c.execute("UPDATE inventory SET quantity=quantity-?,reserved=MAX(0,reserved-?) WHERE point_id=? AND product_id=?",
                      (order["quantity"],order["quantity"],order["source_point_id"],order["product_id"]))
            c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                      (order["source_point_id"],order["product_id"],"dispatch",order["quantity"],order["order_code"],"Отправка по заказу"))
        if new_status=="Готов к выдаче":
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",
                      (order["destination_point_id"],order["product_id"]))
            c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",
                      (order["quantity"],order["destination_point_id"],order["product_id"]))
            c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                      (order["destination_point_id"],order["product_id"],"arrival",order["quantity"],order["order_code"],"Поступление в ПВЗ назначения"))
        if new_status=="Выдан":
            available=c.execute("SELECT quantity FROM inventory WHERE point_id=? AND product_id=?",
                                (order["destination_point_id"],order["product_id"])).fetchone()
            if not available or available["quantity"]<order["quantity"]:
                raise ValueError("На ПВЗ назначения недостаточно товара для выдачи.")
            c.execute("UPDATE inventory SET quantity=quantity-? WHERE point_id=? AND product_id=?",
                      (order["quantity"],order["destination_point_id"],order["product_id"]))
            c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                      (order["destination_point_id"],order["product_id"],"issue",order["quantity"],order["order_code"],"Выдача покупателю"))
        c.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,order_id))

def create_return(order_id,quantity,reason):
    with connect() as c:
        order=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not order or order["status"]!="Выдан": raise ValueError("Возврат можно оформить только по выданному заказу.")
        if quantity<1 or quantity>order["quantity"]: raise ValueError("Некорректное количество возврата.")
        cur=c.execute("INSERT INTO returns(order_id,quantity,reason) VALUES(?,?,?)",(order_id,quantity,reason.strip()))
        c.execute("UPDATE orders SET status='Возврат оформлен',updated_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        return cur.lastrowid

def process_return(return_id,condition_status):
    if condition_status not in ("Пригоден","Повреждён"): raise ValueError("Выберите состояние товара.")
    with connect() as c:
        ret=c.execute("SELECT * FROM returns WHERE id=?",(return_id,)).fetchone()
        if not ret or ret["status"]!="pending": raise ValueError("Возврат уже обработан или не найден.")
        order=c.execute("SELECT * FROM orders WHERE id=?",(ret["order_id"],)).fetchone()
        if condition_status=="Пригоден":
            point_id=order["destination_point_id"]
            c.execute("INSERT OR IGNORE INTO inventory(point_id,product_id,quantity) VALUES(?,?,0)",(point_id,order["product_id"]))
            c.execute("UPDATE inventory SET quantity=quantity+? WHERE point_id=? AND product_id=?",(ret["quantity"],point_id,order["product_id"]))
            c.execute("INSERT INTO stock_movements(point_id,product_id,movement_type,quantity,reference,note) VALUES(?,?,?,?,?,?)",
                      (point_id,order["product_id"],"return",ret["quantity"],str(return_id),"Возврат пригодного товара на ПВЗ назначения"))
        c.execute("UPDATE returns SET condition_status=?,status='processed' WHERE id=?",(condition_status,return_id))

def returns_df():
    with connect() as c:
        return pd.read_sql_query("""SELECT r.id,r.order_id AS 'ID заказа',o.order_code AS Заказ,r.quantity AS Количество,
        r.reason AS Причина,r.condition_status AS Состояние,r.status AS Статус,r.created_at AS Создан
        FROM returns r JOIN orders o ON o.id=r.order_id ORDER BY r.id DESC""",c)

def movements_df():
    with connect() as c:
        return pd.read_sql_query("""SELECT m.id,p.name AS ПВЗ,pr.sku AS SKU,m.movement_type AS Операция,
        m.quantity AS Количество,m.reference AS Документ,m.note AS Комментарий,m.created_at AS Дата
        FROM stock_movements m JOIN pickup_points p ON p.id=m.point_id
        JOIN products pr ON pr.id=m.product_id ORDER BY m.id DESC""",c)

def incidents_df():
    with connect() as c:
        return pd.read_sql_query("SELECT id,order_id AS 'ID заказа',category AS Категория,description AS Описание,status AS Статус,created_at AS Дата FROM incidents ORDER BY id DESC",c)

def add_incident(order_id,category,description):
    with connect() as c:
        c.execute("INSERT INTO incidents(order_id,category,description) VALUES(?,?,?)",(order_id,category,description.strip()))

def finance_summary():
    with connect() as c:
        return dict(c.execute("""SELECT COUNT(*) AS orders_count,
        COALESCE(SUM(estimated_cost),0) AS estimated_cost,
        COALESCE(SUM(CASE WHEN route='Mesh' THEN 1 ELSE 0 END),0) AS mesh_orders,
        COALESCE(SUM(CASE WHEN route='Hub' THEN 1 ELSE 0 END),0) AS hub_orders
        FROM orders WHERE status!='Отменён'""").fetchone())
