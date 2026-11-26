"""Road-aware logistics map and dispatch simulation for Smart Local Stock.

The WB sorting-center coordinate is taken from WB's public list. City pickup-point
coordinates are demonstration coordinates and must be checked against map.wb.ru.
Road geometry is fetched from the public OSRM/OSM road graph; it is not WB's
private dispatch plan or a live GPS feed. Vehicle movement is a local simulation.
"""
import math

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

HUB = {
    "id": "hub",
    "name": "СЦ WB Казань (Зеленодольск)",
    "address": "Республика Татарстан, Зеленодольск, промышленная площадка Зеленодольск, 20",
    "lat": 55.848721,
    "lon": 48.842408,
    "kind": "Подтверждённый СЦ WB",
    "color": [125, 85, 205],
}
ANCHOR = {
    "id": "anchor",
    "name": "Anchor Smart Local Stock",
    "address": "Проектная опорная точка в Йошкар-Оле; не заявляется как действующий склад WB",
    "lat": 56.631364,
    "lon": 47.890917,
    "kind": "Проектный Anchor",
    "color": [230, 145, 35],
}
PVZ = [
    {"id": "pvz_north", "name": "Демо-ПВЗ · север", "address": "Ориентир: район ул. Воинов-Интернационалистов", "lat": 56.6388, "lon": 47.9270, "kind": "Демо-ПВЗ", "color": [35, 155, 100]},
    {"id": "pvz_center", "name": "Демо-ПВЗ · центр", "address": "Ориентир: район ул. Советской", "lat": 56.6320, "lon": 47.8990, "kind": "Демо-ПВЗ", "color": [35, 155, 100]},
    {"id": "pvz_east", "name": "Демо-ПВЗ · восток", "address": "Ориентир: район Ленинского проспекта", "lat": 56.6265, "lon": 47.9110, "kind": "Демо-ПВЗ", "color": [35, 155, 100]},
    {"id": "pvz_west", "name": "Демо-ПВЗ · запад", "address": "Ориентир: район ул. Красноармейской", "lat": 56.643115, "lon": 47.854206, "kind": "Демо-ПВЗ", "color": [35, 155, 100]},
    {"id": "pvz_kremlin", "name": "Демо-ПВЗ · Кремлёвский район", "address": "Ориентир: район ул. Кремлёвской", "lat": 56.6310, "lon": 47.8970, "kind": "Демо-ПВЗ", "color": [35, 155, 100]},
]


def _destination_for_name(name):
    value = str(name).lower()
    if "север" in value or "воинов" in value or "анникова" in value:
        return PVZ[0]
    if "юг" in value or "красноармей" in value:
        return PVZ[3]
    if "ленин" in value or "восток" in value:
        return PVZ[2]
    if "кремл" in value:
        return PVZ[4]
    return PVZ[1]


def _source_for_name(name):
    value = str(name).lower()
    return HUB if "hub" in value or "склад" in value or "сц wb" in value else ANCHOR


@st.cache_data(ttl=3600, show_spinner=False)
def _road_route(origin_lon, origin_lat, dest_lon, dest_lat):
    """Return OSRM road geometry, distance, duration and turn-by-turn step names."""
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    )
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "true"},
            timeout=8,
            headers={"User-Agent": "SmartLocalStock-MVP/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        route = payload["routes"][0]
        geometry = route["geometry"]["coordinates"]
        steps = []
        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                name = step.get("name") or "Без названия"
                maneuver = step.get("maneuver", {})
                modifier = maneuver.get("modifier", "")
                instruction = " ".join(x for x in [maneuver.get("type", ""), modifier, name] if x)
                if instruction.strip():
                    steps.append(instruction.strip())
        return {
            "coordinates": geometry,
            "distance_km": route["distance"] / 1000,
            "duration_min": route["duration"] / 60,
            "steps": steps[:12],
            "provider": "OSRM / OpenStreetMap",
            "road_route": True,
        }
    except Exception:
        # Honest fallback: no fabricated road geometry or ETA.
        return {
            "coordinates": [[origin_lon, origin_lat], [dest_lon, dest_lat]],
            "distance_km": None,
            "duration_min": None,
            "steps": [],
            "provider": "Схема по прямой — дорожный сервис недоступен",
            "road_route": False,
        }


def _point_at_fraction(coords, fraction):
    if not coords:
        return None
    if len(coords) == 1:
        return coords[0]
    lengths = []
    total = 0.0
    for first, second in zip(coords[:-1], coords[1:]):
        lat1, lon1 = math.radians(first[1]), math.radians(first[0])
        lat2, lon2 = math.radians(second[1]), math.radians(second[0])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        length = 2 * math.asin(min(1, math.sqrt(a)))
        lengths.append(length)
        total += length
    if total == 0:
        return coords[0]
    target = total * max(0, min(1, fraction))
    passed = 0.0
    for index, length in enumerate(lengths):
        if passed + length >= target:
            ratio = 0 if length == 0 else (target - passed) / length
            a, b = coords[index], coords[index + 1]
            return [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio]
        passed += length
    return coords[-1]


def _render_map(points, coordinates, vehicle, center, zoom):
    layers = [
        pdk.Layer(
            "PathLayer",
            data=[{"path": coordinates}],
            get_path="path",
            get_color=[35, 110, 215, 220],
            get_width=5,
            width_min_pixels=4,
            pickable=False,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=points,
            get_position="[lon, lat]",
            get_fill_color="color",
            get_line_color=[255, 255, 255],
            get_radius=170,
            radius_min_pixels=7,
            radius_max_pixels=17,
            stroked=True,
            pickable=True,
        ),
        pdk.Layer(
            "TextLayer",
            data=points,
            get_position="[lon, lat]",
            get_text="short",
            get_size=12,
            get_color=[30, 35, 45],
            get_alignment_baseline="'bottom'",
            get_pixel_offset=[0, -13],
            pickable=False,
        ),
    ]
    if vehicle:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=[vehicle],
                get_position="[lon, lat]",
                get_fill_color=[240, 55, 45],
                get_line_color=[255, 255, 255],
                get_radius=260,
                radius_min_pixels=12,
                radius_max_pixels=22,
                stroked=True,
                line_width_min_pixels=2,
                pickable=True,
            )
        )
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=[{**vehicle, "label": "ТРАНСПОРТ"}],
                get_position="[lon, lat]",
                get_text="label",
                get_size=13,
                get_color=[180, 25, 25],
                get_pixel_offset=[0, -23],
                pickable=False,
            )
        )
    return pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(
            latitude=center[0], longitude=center[1], zoom=zoom, pitch=0
        ),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={
            "html": "<b>{name}</b><br/>{address}<br/>Тип: {kind}",
            "style": {"backgroundColor": "#222", "color": "white"},
        },
    )


def render_logistics_map(orders: pd.DataFrame):
    st.subheader("Отправка и путь заказа")
    st.caption(
        "СЦ Казань (Зеленодольск) — подтверждённый объект WB. Дорожная линия строится по "
        "открытой дорожной сети OSRM/OpenStreetMap. Движение машины — симуляция Smart Local Stock, "
        "не GPS-трекинг и не закрытый маршрут перевозчика WB. Городские ПВЗ на карте пока "
        "демонстрационные: адреса и координаты необходимо подтвердить по официальной карте WB."
    )

    selected = None
    if not orders.empty:
        labels = orders["Заказ"].astype(str).tolist()
        chosen = st.selectbox("Выбери заказ", labels, key="map_order_select")
        selected = orders[orders["Заказ"].astype(str) == chosen].iloc[0]

    if selected is None:
        source, destination = HUB, PVZ[1]
        order_code, status, route, cost = "DEMO-ROUTE", "Планирование", "Hub", None
    else:
        route_label = str(selected.get("Маршрут", ""))
        bypass_via_hub = "Hub (обход)" in route_label
        source = _source_for_name(selected.get("Источник", ""))
        destination = _destination_for_name(selected.get("ПВЗ назначения", ""))
        order_code = str(selected.get("Заказ", "order"))
        status = str(selected.get("Статус", "—"))
        route = str(selected.get("Маршрут", "—"))
        cost = selected.get("Оценка затрат, ₽", None)

    if selected is not None and bypass_via_hub:
        # Обход строится двумя дорожными плечами: исходный Anchor → Hub → ПВЗ.
        first_leg = _road_route(source["lon"], source["lat"], HUB["lon"], HUB["lat"])
        second_leg = _road_route(HUB["lon"], HUB["lat"], destination["lon"], destination["lat"])
        first_coords = first_leg["coordinates"]
        second_coords = second_leg["coordinates"]
        route_data = {
            "coordinates": first_coords + second_coords[1:],
            "distance_km": (
                first_leg["distance_km"] + second_leg["distance_km"]
                if first_leg["distance_km"] is not None and second_leg["distance_km"] is not None
                else None
            ),
            "duration_min": (
                first_leg["duration_min"] + second_leg["duration_min"]
                if first_leg["duration_min"] is not None and second_leg["duration_min"] is not None
                else None
            ),
            "steps": (first_leg["steps"] + second_leg["steps"])[:12],
            "provider": "OSRM / OpenStreetMap · маршрут через Hub",
            "road_route": first_leg["road_route"] and second_leg["road_route"],
        }
    else:
        route_data = _road_route(source["lon"], source["lat"], destination["lon"], destination["lat"])
    coords = route_data["coordinates"]
    midpoint = _point_at_fraction(coords, 0.5) or [source["lon"], source["lat"]]
    progress_key = f"dispatch_progress_{order_code}"
    st.session_state.setdefault(progress_key, 0)

    st.markdown("#### Карточка отправки")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Маршрут", route)
    c2.metric("Статус", status)
    c3.metric("Расстояние по дороге", "—" if route_data["distance_km"] is None else f'{route_data["distance_km"]:.1f} км')
    c4.metric("Расчётное время в пути", "—" if route_data["duration_min"] is None else f'{route_data["duration_min"]:.0f} мин')
    st.write(f"**Откуда:** {source['name']}  →  **Куда:** {destination['name']}")
    if cost is not None and not pd.isna(cost):
        st.write(f"**Расчётные затраты на заказ:** {float(cost):,.2f} ₽")

    if status == "В пути":
        controls1, controls2, controls3 = st.columns([1, 1, 2])
        if controls1.button("🚚 Следующий участок +10%", key=f"step_{order_code}"):
            st.session_state[progress_key] = min(100, int(st.session_state[progress_key]) + 10)
        if controls2.button("Сбросить", key=f"reset_{order_code}"):
            st.session_state[progress_key] = 0
        progress = st.slider(
            "Положение транспорта на маршруте",
            min_value=0,
            max_value=100,
            step=5,
            key=progress_key,
            help="Демонстрационный прогресс рейса. Не отражает реальное местоположение автомобиля.",
        )
        vehicle_pos = _point_at_fraction(coords, progress / 100)
        vehicle = {
            "lon": vehicle_pos[0],
            "lat": vehicle_pos[1],
            "name": "Демонстрационный транспорт",
            "address": f"Прогресс рейса: {progress}%",
            "kind": "Симуляция",
        }
        st.progress(progress / 100, text=f"Рейс: {progress}%")
        if progress >= 100:
            st.success("Транспорт прибыл в пункт назначения. Следующий операционный шаг — приёмка и статус «Готов к выдаче».")
    else:
        vehicle = None
        if status in ("Ожидает партии", "Mesh (ожидание)"):
            st.info("Заказ ещё не отправлен: он ожидает формирования партии. Движение транспорта не запускается до статуса «В пути».")
        elif status in ("Готов к выдаче", "Выдан"):
            st.success("Транспортный этап завершён. Заказ находится на ПВЗ назначения или уже выдан.")
        else:
            st.info("Чтобы запустить демонстрацию движения, переведи заказ в статус «В пути» в разделе заказов.")

    # Всегда показываем всю городскую сеть, а не только ПВЗ выбранного заказа.
    # Это демонстрационные точки/ориентиры; они не являются подтверждённым реестром WB.
    map_points = [{**point, "short": point["name"].replace("Демо-ПВЗ · ", "")}
                  for point in PVZ]
    map_points.extend([
        {**HUB, "short": "СЦ WB"},
        {**ANCHOR, "short": "ANCHOR"},
    ])
    # Для выбранного маршрута добавляем выделенные маркеры начала и конца.
    map_points.extend([
        {**source, "short": "СТАРТ"},
        {**destination, "short": "ФИНИШ"},
    ])

    st.markdown("#### Карта дорожного маршрута")
    st.pydeck_chart(
        _render_map(
            map_points,
            coords,
            vehicle,
            (midpoint[1], midpoint[0]),
            7.5 if route_data["distance_km"] and route_data["distance_km"] > 35 else 11.5,
        ),
        use_container_width=True,
    )
    st.caption(f"Источник геометрии: {route_data['provider']}. Карта показывает дорожный маршрут для демонстрации, а не фактический трек перевозчика WB.")

    if route_data["steps"]:
        with st.expander("Указания движения по маршруту"):
            for index, step in enumerate(route_data["steps"], start=1):
                st.write(f"{index}. {step}")
    st.markdown("#### Этапы заказа")
    stages = ["Резерв товара", "Формирование партии / сборка", "Отправка", "Приёмка на ПВЗ", "Выдача покупателю"]
    st.write(" → ".join(stages))
    st.caption("Реальный процесс WB зависит от модели поставки и маршрута в кабинете партнёра. Smart Local Stock демонстрирует предлагаемую Mesh-схему и резервный сценарий через Hub.")


def render_live_dispatch_map(vehicles: pd.DataFrame):
    """Show a live simulated fleet on the regional map; markers are not GPS."""
    st.caption("Все маркеры транспорта — виртуальные позиции симулятора. Для наглядности используется проектная география Anchor → демо-ПВЗ; фактические треки WB недоступны.")
    points=[{**point,"short":point["name"].replace("Демо-ПВЗ · ","")} for point in PVZ]
    points.extend([{**HUB,"short":"СЦ WB"},{**ANCHOR,"short":"ANCHOR"}])
    route_layers=[]
    vehicle_rows=[]
    for _,row in vehicles.iterrows():
        destination=_destination_for_name(row.get("Назначение",""))
        source=HUB if str(row.get("Тип_источника",""))=="Hub" else ANCHOR
        if pd.notna(row.get("source_lat")) and pd.notna(row.get("dest_lat")):
            source={"lat":float(row["source_lat"]),"lon":float(row["source_lon"])}
            destination={"lat":float(row["dest_lat"]),"lon":float(row["dest_lon"])}
        elif str(row.get("Тип_источника",""))=="Supply":
            source={"lat":55.76,"lon":49.12}
        route=_road_route(source["lon"],source["lat"],destination["lon"],destination["lat"])
        coords=route["coordinates"]
        progress=max(0.0,min(1.0,float(row.get("Прогресс",0))))
        position=_point_at_fraction(coords,progress)
        route_layers.append({"path":coords})
        vehicle_rows.append({
            "lon":position[0],"lat":position[1],
            "name":str(row.get("Заказ","Транспорт")),
            "address":f"{row.get('Маршрут','')} · {progress:.0%}",
            "kind":"Виртуальный транспорт",
        })
    layers=[
        pdk.Layer("PathLayer",data=route_layers,get_path="path",get_color=[55,120,205,100],
                  get_width=3,width_min_pixels=2,pickable=False),
        pdk.Layer("ScatterplotLayer",data=points,get_position="[lon,lat]",get_fill_color="color",
                  get_line_color=[255,255,255],get_radius=180,radius_min_pixels=7,radius_max_pixels=16,
                  stroked=True,pickable=True),
        pdk.Layer("ScatterplotLayer",data=vehicle_rows,get_position="[lon,lat]",
                  get_fill_color=[235,55,45],get_line_color=[255,255,255],get_radius=300,
                  radius_min_pixels=10,radius_max_pixels=18,stroked=True,pickable=True),
        pdk.Layer("TextLayer",data=vehicle_rows,get_position="[lon,lat]",get_text="name",
                  get_size=10,get_color=[150,25,25],get_pixel_offset=[0,-18],pickable=False),
    ]
    deck=pdk.Deck(layers=layers,initial_view_state=pdk.ViewState(
        latitude=56.25,longitude=48.35,zoom=7.2,pitch=0),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"html":"<b>{name}</b><br/>{address}<br/>{kind}",
                 "style":{"backgroundColor":"#222","color":"white"}})
    st.pydeck_chart(deck,use_container_width=True)
