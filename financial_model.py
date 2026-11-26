from math import ceil


def calculate_mesh_unit_cost(
    orders,
    trips,
    trip_cost,
    variable_cost,
    anchor_fee=0.0,
    replenishment=0.0,
    storage=0.0,
    handling=0.0,
    risk=0.0,
    system_cost_per_order=0.0,
):
    orders = int(orders)
    if orders <= 0:
        raise ValueError("Число заказов должно быть больше нуля.")
    if int(trips) < 0:
        raise ValueError("Число рейсов не может быть отрицательным.")
    trip_total = int(trips) * float(trip_cost)
    components = {
        "transport_per_order": trip_total / orders,
        "variable_per_order": float(variable_cost),
        "anchor_fee_per_order": float(anchor_fee),
        "replenishment_per_order": float(replenishment),
        "storage_per_order": float(storage),
        "handling_per_order": float(handling),
        "risk_per_order": float(risk),
        "system_per_order": float(system_cost_per_order),
    }
    components["total_per_order"] = sum(components.values())
    components["total_cost"] = components["total_per_order"] * orders
    return components


def forecast_financials(
    monthly_orders=1200,
    mesh_share_pct=100.0,
    anchors=3,
    batch_orders=80,
    max_orders=130,
    avg_order_volume_l=4.0,
    max_volume_l=1500.0,
    trip_cost=2400.0,
    hub_cost_per_order=62.0,
    variable_cost=14.5,
    anchor_fee=18.0,
    replenishment_per_order=9.0,
    storage_per_order=2.5,
    handling_per_order=0.0,
    return_rate_pct=0.0,
    return_cost=0.0,
    monthly_system_cost=10000.0,
    growth_pct=0.0,
    months=12,
):
    """Comparable Hub baseline vs Mesh + Hub hybrid forecast, same demand."""
    monthly_orders = max(0, int(monthly_orders))
    anchors = max(1, int(anchors))
    max_capacity_by_volume = int(float(max_volume_l) // max(float(avg_order_volume_l), 0.000001))
    capacity = max(1, min(int(max_orders), max_capacity_by_volume))
    batch_orders = max(1, min(int(batch_orders), capacity))
    share = max(0.0, min(100.0, float(mesh_share_pct))) / 100.0
    risk_per_order = max(0.0, float(return_rate_pct)) / 100.0 * max(0.0, float(return_cost))
    rows = []
    cumulative_savings = 0.0

    for month in range(1, int(months) + 1):
        demand = int(round(monthly_orders * ((1 + float(growth_pct) / 100) ** (month - 1))))
        mesh_orders = int(round(demand * share))
        hub_orders = demand - mesh_orders
        if mesh_orders:
            orders_per_anchor = mesh_orders / anchors
            trips = anchors * ceil(orders_per_anchor / batch_orders)
            mesh = calculate_mesh_unit_cost(
                mesh_orders, trips, trip_cost, variable_cost, anchor_fee,
                replenishment_per_order, storage_per_order, handling_per_order,
                risk_per_order, monthly_system_cost / max(demand, 1),
            )
            mesh_total = mesh["total_cost"]
            system_total = float(monthly_system_cost)
        else:
            trips = 0
            mesh_total = 0.0
            system_total = 0.0
            mesh = {"total_per_order": 0.0}
        hub_total = hub_orders * float(hub_cost_per_order)
        baseline = demand * float(hub_cost_per_order)
        combined = mesh_total + hub_total
        savings = baseline - combined
        cumulative_savings += savings
        rows.append({
            "month": month, "orders": demand, "mesh_orders": mesh_orders,
            "hub_orders": hub_orders, "mesh_share_pct": share * 100,
            "trips": trips, "mesh_cost": round(mesh_total, 2),
            "hub_cost": round(hub_total, 2), "system_cost": round(system_total, 2),
            "baseline_hub_cost": round(baseline, 2),
            "combined_cost": round(combined, 2),
            "savings": round(savings, 2),
            "cumulative_savings": round(cumulative_savings, 2),
            "mesh_cost_per_order": round(float(mesh["total_per_order"]), 2),
            "hub_cost_per_order": float(hub_cost_per_order),
        })

    last = rows[-1] if rows else {}
    return {
        "rows": rows,
        "monthly": last,
        "total_orders": sum(r["orders"] for r in rows),
        "total_trips": sum(r["trips"] for r in rows),
        "total_baseline": round(sum(r["baseline_hub_cost"] for r in rows), 2),
        "total_combined": round(sum(r["combined_cost"] for r in rows), 2),
        "total_savings": round(sum(r["savings"] for r in rows), 2),
        "capacity_per_trip": capacity,
        "effective_batch_target": batch_orders,
    }


def break_even_anchor_fee(
    orders_per_month=1200,
    anchors=3,
    batch_orders=80,
    max_orders=130,
    avg_order_volume_l=4.0,
    max_volume_l=1500.0,
    trip_cost=2400.0,
    hub_cost_per_order=62.0,
    variable_cost=14.5,
    replenishment_per_order=9.0,
    storage_per_order=2.5,
    handling_per_order=0.0,
    return_rate_pct=0.0,
    return_cost=0.0,
    monthly_system_cost=10000.0,
):
    demand = max(1, int(orders_per_month))
    anchors = max(1, int(anchors))
    capacity = max(1, min(int(max_orders), int(float(max_volume_l) // max(float(avg_order_volume_l), 0.000001))))
    batch_orders = max(1, min(int(batch_orders), capacity))
    trips = anchors * ceil((demand / anchors) / batch_orders)
    transport = trips * float(trip_cost) / demand
    risk = float(return_rate_pct) / 100.0 * float(return_cost)
    return (
        float(hub_cost_per_order) - transport - float(variable_cost)
        - float(replenishment_per_order) - float(storage_per_order)
        - float(handling_per_order) - risk - float(monthly_system_cost) / demand
    )
