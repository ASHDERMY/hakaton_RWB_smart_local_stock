"""Интерактивная схема сети Smart Local Stock.

Координаты СЦ WB взяты из официальной карты партнёрских объектов.
Координаты городских ПВЗ нанесены ориентировочно по адресам; это не навигационный
трек автомобиля. Линии показывают логистическую связь между узлами.
"""
import pandas as pd
import pydeck as pdk
import streamlit as st

# lon/lat: городской Anchor — проектная точка модели, а не заявленный склад WB.
HUB = {"id": "hub", "name": "СЦ WB Казань (Зеленодольск)", "address": "Республика Татарстан, г. Зеленодольск, промышленный парк Зеленодольск, 20", "lat": 55.848721, "lon": 48.842408, "kind": "Hub", "color": [130, 90, 210]}
ANCHOR = {"id": "anchor", "name": "Anchor Йошкар-Ола (проектная точка)", "address": "Условная опорная точка модели, Йошкар-Ола", "lat": 56.631364, "lon": 47.890917, "kind": "Anchor", "color": [245, 160, 45]}
PVZ = [
    {"id": "pvz_north", "name": "ПВЗ WB · Воинов-Интернационалистов, 34", "address": "Йошкар-Ола, ул. Воинов-Интернационалистов, 34", "lat": 56.6388, "lon": 47.9270, "kind": "ПВЗ", "color": [40, 160, 100]},
    {"id": "pvz_center", "name": "ПВЗ WB · Советская, 97", "address": "Йошкар-Ола, ул. Советская, 97", "lat": 56.6320, "lon": 47.8990, "kind": "ПВЗ", "color": [40, 160, 100]},
    {"id": "pvz_east", "name": "ПВЗ WB · Ленинский проспект, 16А", "address": "Йошкар-Ола, Ленинский проспект, 16А", "lat": 56.6265, "lon": 47.9110, "kind": "ПВЗ", "color": [40, 160, 100]},
    {"id": "pvz_west", "name": "ПВЗ WB · Красноармейская, 115", "address": "Йошкар-Ола, ул. Красноармейская, 115", "lat": 56.643115, "lon": 47.854206, "kind": "ПВЗ", "color": [40, 160, 100]},
    {"id": "pvz_kremlin", "name": "ПВЗ WB · Кремлёвская, 20", "address": "Йошкар-Ола, ул. Кремлёвская, 20", "lat": 56.6310, "lon": 47.8970, "kind": "ПВЗ", "color": [40, 160, 100]},
]

def _destination_for_name(name):
    text = str(name).lower()
    if "север" in text or "воинов" in text or "анникова" in text:
        return PVZ[0]
    if "юг" in text or "красноармей" in text:
        return PVZ[3]
    if "ленин" in text or "восток" in text:
        return PVZ[2]
    if "кремл" in text:
        return PVZ[4]
    return PVZ[1]

def _source_for_name(name):
    text = str(name).lower()
    return HUB if "hub" in text or "склад" in text or "сц wb" in text else ANCHOR

def _deck(points, path, center, zoom, tooltip=True):
    layers = []
    if path:
        layers.append(pdk.Layer(
            "PathLayer", data=[{"path": [[p["lon"], p["lat"]] for p in path]}],
            get_path="path", get_color=[30, 110, 220, 210], width_min_pixels=4,
            get_width=5, pickable=False,
        ))
    layers.append(pdk.Layer(
        "ScatterplotLayer", data=points, get_position="[lon, lat]",
        get_fill_color="color", get_line_color=[255, 255, 255],
        get_radius=190, radius_min_pixels=7, radius_max_pixels=18,
        line_width_min_pixels=1, stroked=True, pickable=True,
    ))
    layers.append(pdk.Layer(
        "TextLayer", data=points, get_position="[lon, lat]", get_text="short",
        get_size=12, get_color=[30, 35, 45], get_alignment_baseline="'bottom'",
        get_pixel_offset=[0, -14], pickable=False,
    ))
    return pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(latitude=center[0], longitude=center[1], zoom=zoom, pitch=0),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"html": "<b>{name}</b><br/>{address}<br/>Тип: {kind}", "style": {"backgroundColor": "#222", "color": "white"}},
    )

def render_logistics_map(orders: pd.DataFrame):
    st.subheader("Карта логистической сети и пути заказа")
    st.caption("СЦ WB Казань (Зеленодольск) — реальный объект из перечня СЦ WB. Адреса ПВЗ в Йошкар-Оле сверены с открытыми картографическими справочниками; координаты на схеме ориентировочные. Anchor — проектная точка модели. Линии не являются автомобильной навигацией.")

    selected = None
    if not orders.empty:
        labels = orders["Заказ"].astype(str).tolist()
        chosen = st.selectbox("Заказ для отображения маршрута", labels, key="map_order_select")
        selected = orders[orders["Заказ"].astype(str) == chosen].iloc[0]

    if selected is None:
        source, dest, route, status, cost = HUB, PVZ[1], "Демонстрационный", "Ожидание заказа", None
    else:
        source = _source_for_name(selected.get("Источник", ""))
        dest = _destination_for_name(selected.get("ПВЗ назначения", ""))
        route = str(selected.get("Маршрут", "—"))
        status = str(selected.get("Статус", "—"))
        cost = selected.get("Оценка затрат, ₽", None)

    st.markdown("#### Текущий заказ")
    a, b, c = st.columns(3)
    a.metric("Маршрут", route)
    b.metric("Статус", status)
    c.metric("Расчётные затраты", "—" if cost is None or pd.isna(cost) else f"{float(cost):,.2f} ₽")

    if "Hub" in route or route == "Демонстрационный":
        regional_path = [HUB, ANCHOR, dest]
        route_label = "Hub → локальная сеть → ПВЗ" if route != "Демонстрационный" else "Пример региональной цепочки"
    else:
        regional_path = [ANCHOR, dest]
        route_label = "Anchor → ПВЗ (Mesh)"

    regional_points = [dict(p, short=p["kind"]) for p in [HUB, ANCHOR, *PVZ]]
    city_points = [dict(p, short=p["kind"]) for p in [ANCHOR, *PVZ]]
    tab_region, tab_city = st.tabs(["Региональный маршрут", "ПВЗ Йошкар-Олы"])
    with tab_region:
        st.caption(route_label + ". На обзорной карте расстояние показано географически; линия схематическая.")
        deck = _deck(regional_points, regional_path, (56.22, 48.36), 7.2)
        st.pydeck_chart(deck, use_container_width=True)
        st.write(f"**Источник:** {source['name']}  →  **Назначение:** {dest['name']}")
        st.caption("СЦ Казань (Зеленодольск): Республика Татарстан, Зеленодольск, промышленный парк Зеленодольск, 20. Адрес и координаты опубликованы в перечне сортировочных центров WB.")
    with tab_city:
        local_path = [ANCHOR, dest]
        deck = _deck(city_points, local_path, (56.632, 47.895), 11.1)
        st.pydeck_chart(deck, use_container_width=True)
        st.dataframe(pd.DataFrame([{"Объект": p["name"], "Адрес": p["address"], "Роль": p["kind"], "Координаты": "ориентировочно" if p["id"] != "anchor" else "проектная точка"} for p in [ANCHOR, *PVZ]]), use_container_width=True, hide_index=True)

    st.markdown("#### Как читать маршрут")
    if status == "Ожидает партии":
        st.info("Заказ зарезервирован в Anchor и ожидает накопления партии. После достижения целевой загрузки, ограничения вместимости или срока ожидания система сравнит Mesh и Hub.")
    elif status == "В пути":
        st.info("Заказ отправлен. Для Mesh это локальное плечо Anchor → ПВЗ; для Hub — резервный маршрут через складской узел.")
    elif status == "Готов к выдаче":
        st.success("Заказ прибыл в ПВЗ назначения и ожидает покупателя.")
    elif status == "Выдан":
        st.success("Заказ выдан покупателю. Цикл доставки завершён.")
    elif status == "Ошибка":
        st.error("Заказ остановлен из-за логистического инцидента. Зарегистрируйте причину в разделе «Инциденты».")
    else:
        st.caption("Выбери заказ, чтобы увидеть его текущую логистическую цепочку.")
