from math import ceil, floor


def replenishment_qty(current, incoming, reserved, daily_demand, lead_time_days, safety_stock):
    available = max(0, current + incoming - reserved)
    target = max(0, ceil(daily_demand * lead_time_days) + safety_stock)
    return max(0, target - available)


def choose_route(stock_available, sla_ok, batch_orders, batch_volume_l, trip_cost, max_orders, max_volume_l, hub_cost_per_order):
    """Legacy single-call route estimator retained for compatibility with v0.1 tests."""
    if batch_orders <= 0:
        return {"route": "Hub", "reason": "Партия пуста", "mesh_cost_per_order": float("inf")}
    mesh = trip_cost / batch_orders
    if not stock_available:
        reason = "Нет доступного товара в Anchor"
    elif not sla_ok:
        reason = "Mesh не укладывается в допустимый срок"
    elif batch_orders > max_orders or batch_volume_l > max_volume_l:
        reason = "Превышена вместимость рейса"
    elif mesh >= hub_cost_per_order:
        reason = "Mesh не дешевле Hub при текущей загрузке"
    else:
        return {"route": "Mesh", "reason": "Ограничения соблюдены, Mesh дешевле Hub", "mesh_cost_per_order": mesh}
    return {"route": "Hub", "reason": reason, "mesh_cost_per_order": mesh}


def evaluate_batch(
    order_count,
    total_volume_l,
    age_hours,
    trip_cost,
    mesh_variable_cost_per_order,
    hub_cost_per_order,
    target_orders,
    max_orders,
    max_volume_l,
    max_wait_hours,
    additional_cost_per_order=0.0,
):
    """
    Decide whether a real accumulated Anchor -> ordinary pickup point batch
    should wait, travel by Mesh, or fall back to Hub using full incremental costs.

    The fixed trip and all incremental per-order costs are compared with the Hub baseline.
    """
    if order_count <= 0:
        return {"decision": "empty", "reason": "В партии нет активных заказов."}

    if order_count > max_orders or total_volume_l > max_volume_l:
        return {
            "decision": "capacity_error",
            "reason": "Партия превышает установленную вместимость и не может быть отправлена.",
        }

    target_reached = order_count >= min(max(1, target_orders), max_orders)
    capacity_reached = order_count >= max_orders or total_volume_l >= max_volume_l
    timeout_reached = age_hours >= max_wait_hours

    if not (target_reached or capacity_reached or timeout_reached):
        return {
            "decision": "wait",
            "reason": "Ожидание накопления партии: целевой объём ещё не достигнут, лимит времени не истёк.",
        }

    mesh_cost_per_order = (trip_cost / order_count + mesh_variable_cost_per_order
                          + max(0.0, float(additional_cost_per_order)))
    if mesh_cost_per_order < hub_cost_per_order:
        trigger = (
            "Достигнута целевая загрузка партии."
            if target_reached
            else "Достигнут предел вместимости."
            if capacity_reached
            else "Истекло максимальное время ожидания; Mesh дешевле Hub."
        )
        return {
            "decision": "mesh",
            "reason": f"{trigger} Полная расчётная стоимость Mesh ниже Hub.",
            "mesh_cost_per_order": mesh_cost_per_order,
        }

    return {
        "decision": "hub",
        "reason": (
            "После достижения условия отправки полная стоимость Mesh не ниже Hub "
            f"({mesh_cost_per_order:.2f} ₽ против {hub_cost_per_order:.2f} ₽ на заказ)."
        ),
        "mesh_cost_per_order": mesh_cost_per_order,
    }


def calculate_costs(total_orders, mesh_orders, hub_orders, hub_cost_per_order, trip_cost, trips,
                    mesh_variable_cost_per_order, smart_cost, anchor_fee_per_order=0.0,
                    replenishment_per_order=0.0, storage_per_order=0.0,
                    handling_per_order=0.0, risk_per_order=0.0):
    """Legacy aggregate calculator with optional full Mesh operating-cost components."""
    baseline = total_orders * hub_cost_per_order
    trip_total = trips * trip_cost
    variable = mesh_orders * mesh_variable_cost_per_order
    anchor = mesh_orders * anchor_fee_per_order
    replenishment = mesh_orders * replenishment_per_order
    storage = mesh_orders * storage_per_order
    handling = mesh_orders * handling_per_order
    risk = mesh_orders * risk_per_order
    hub = hub_orders * hub_cost_per_order
    total = trip_total + variable + anchor + replenishment + storage + handling + risk + hub + smart_cost
    return {
        "hub_baseline": baseline,
        "trip_total": trip_total,
        "mesh_variable_total": variable,
        "anchor_fee_total": anchor,
        "replenishment_total": replenishment,
        "storage_total": storage,
        "handling_total": handling,
        "risk_total": risk,
        "hub_total": hub,
        "smart_cost": smart_cost,
        "combined_total": total,
        "savings": baseline - total,
    }


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance for preliminary geographic clustering."""
    from math import radians, sin, cos, sqrt, atan2
    radius = 6371.0088
    dlat, dlon = radians(float(lat2)-float(lat1)), radians(float(lon2)-float(lon1))
    a = sin(dlat/2)**2 + cos(radians(float(lat1)))*cos(radians(float(lat2)))*sin(dlon/2)**2
    return 2*radius*atan2(sqrt(a), sqrt(max(0.0, 1.0-a)))


def plan_multi_stop_routes(anchor, stops, max_orders, max_volume_l, trip_cost,
                           variable_cost_per_order, hub_cost_per_order, max_detour_km=2.0,
                           additional_cost_per_order=0.0):
    """Plan combined Anchor-to-PVZ routes; recommendations only, no order mutation."""
    if not anchor or anchor.get("lat") is None or anchor.get("lon") is None:
        raise ValueError("Для Anchor не заданы координаты.")
    max_orders = max(1, int(max_orders))
    max_volume_l = float(max_volume_l)
    max_detour_km = max(0.0, float(max_detour_km))
    trip_cost = max(0.0, float(trip_cost))
    variable_cost_per_order = max(0.0, float(variable_cost_per_order))
    hub_cost_per_order = max(0.0, float(hub_cost_per_order))
    additional_cost_per_order = max(0.0, float(additional_cost_per_order))
    anchor_lat, anchor_lon = float(anchor["lat"]), float(anchor["lon"])
    remaining, skipped = [], []
    for stop in stops:
        try:
            lat, lon = float(stop["lat"]), float(stop["lon"])
            count, volume = int(stop["orders"]), float(stop["volume_l"])
            point_id = int(stop["point_id"])
            if count <= 0:
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
            if count > max_orders or volume > max_volume_l:
                skipped.append({"point_id": point_id, "name": str(stop["name"]),
                    "orders": count, "volume_l": volume,
                    "reason": "Спрос одной точки превышает вместимость рейса; требуется разделение партии."})
                continue
            remaining.append({"point_id": point_id, "name": str(stop["name"]),
                "lat": lat, "lon": lon, "orders": count, "volume_l": volume})
        except (KeyError, TypeError, ValueError):
            skipped.append({"point_id": stop.get("point_id"), "name": str(stop.get("name", "ПВЗ без названия")),
                "orders": int(stop.get("orders", 0) or 0), "volume_l": float(stop.get("volume_l", 0) or 0),
                "reason": "Нет корректных координат или параметров спроса."})
    routes = []
    while remaining:
        first = min(remaining, key=lambda p: haversine_km(anchor_lat, anchor_lon, p["lat"], p["lon"]))
        remaining.remove(first)
        group, count, volume = [first], first["orders"], first["volume_l"]
        distance = haversine_km(anchor_lat, anchor_lon, first["lat"], first["lon"])
        current = first
        while remaining:
            direct_back = haversine_km(current["lat"], current["lon"], anchor_lat, anchor_lon)
            candidates = []
            for candidate in remaining:
                if count + candidate["orders"] > max_orders or volume + candidate["volume_l"] > max_volume_l:
                    continue
                detour = (haversine_km(current["lat"], current["lon"], candidate["lat"], candidate["lon"])
                    + haversine_km(candidate["lat"], candidate["lon"], anchor_lat, anchor_lon) - direct_back)
                if detour <= max_detour_km:
                    candidates.append((detour, candidate["point_id"], candidate))
            if not candidates:
                break
            _, _, chosen = min(candidates, key=lambda item: (item[0], item[1]))
            distance += haversine_km(current["lat"], current["lon"], chosen["lat"], chosen["lon"])
            count += chosen["orders"]
            volume += chosen["volume_l"]
            group.append(chosen)
            remaining.remove(chosen)
            current = chosen
        distance += haversine_km(current["lat"], current["lon"], anchor_lat, anchor_lon)
        mesh_total = trip_cost + count * (variable_cost_per_order + additional_cost_per_order)
        hub_total = count * hub_cost_per_order
        mesh_per_order = mesh_total / count
        decision = "Mesh" if mesh_total < hub_total else "Hub"
        routes.append({"route_no": len(routes)+1, "stops": group,
            "stop_names": [p["name"] for p in group], "total_orders": count,
            "total_volume_l": round(volume, 2), "distance_km_straight": round(distance, 2),
            "mesh_total": round(mesh_total, 2), "mesh_per_order": round(mesh_per_order, 2),
            "hub_total": round(hub_total, 2), "hub_per_order": round(hub_cost_per_order, 2),
            "decision": decision,
            "reason": "Объединённый рейс дешевле расчётного варианта Hub."
                if decision == "Mesh" else "Для этой группы Hub дешевле или равен Mesh."})
    return {"routes": routes, "skipped": skipped}
