import sqlite3
from pathlib import Path
import pandas as pd

DB_PATH=Path("data/smart_local_stock.db")
def connect():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB_PATH); c.execute("PRAGMA foreign_keys=ON"); return c

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS pickup_points(id INTEGER PRIMARY KEY,name TEXT NOT NULL,point_type TEXT NOT NULL,city TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,sku TEXT UNIQUE NOT NULL,name TEXT NOT NULL,category TEXT,volume_l REAL NOT NULL,demand_class TEXT);
        CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY,point_id INTEGER NOT NULL,product_id INTEGER NOT NULL,quantity INTEGER NOT NULL,reserved INTEGER NOT NULL DEFAULT 0,incoming INTEGER NOT NULL DEFAULT 0,UNIQUE(point_id,product_id),FOREIGN KEY(point_id) REFERENCES pickup_points(id),FOREIGN KEY(product_id) REFERENCES products(id));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,order_code TEXT UNIQUE NOT NULL,product_id INTEGER NOT NULL,destination_point_id INTEGER NOT NULL,quantity INTEGER NOT NULL,status TEXT NOT NULL,route TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS assumptions(key TEXT PRIMARY KEY,value REAL NOT NULL,unit TEXT NOT NULL,classification TEXT NOT NULL,note TEXT);
        """)
def seed_demo():
    with connect() as c:
        c.executemany("INSERT OR IGNORE INTO pickup_points VALUES(?,?,?,?)",[(1,"Anchor Центр","Anchor","Демо-город"),(2,"ПВЗ Север","Обычный","Демо-город"),(3,"ПВЗ Юг","Обычный","Демо-город")])
        c.executemany("INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?)",[(1,"SKU-001","Беспроводная мышь","Электроника",1.2,"AX"),(2,"SKU-002","Кабель USB-C","Аксессуары",0.3,"AX"),(3,"SKU-003","Настольная лампа","Дом",4.0,"BY")])
        c.executemany("INSERT OR IGNORE INTO inventory(id,point_id,product_id,quantity,reserved,incoming) VALUES(?,?,?,?,?,?)",[(1,1,1,90,5,10),(2,1,2,120,8,0),(3,1,3,15,0,0)])
def products_df():
    with connect() as c: return pd.read_sql_query("SELECT sku SKU,name Товар,category Категория,volume_l 'Объём, л',demand_class 'ABC/XYZ' FROM products",c)
def inventory_df():
    with connect() as c: return pd.read_sql_query("SELECT p.name ПВЗ,pr.sku SKU,pr.name Товар,i.quantity Остаток,i.reserved Резерв,i.incoming 'В пути',i.quantity+i.incoming-i.reserved Доступно FROM inventory i JOIN pickup_points p ON p.id=i.point_id JOIN products pr ON pr.id=i.product_id",c)
