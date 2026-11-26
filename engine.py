from math import ceil, floor

def replenishment_qty(current, incoming, reserved, daily_demand, lead_time_days, safety_stock):
    available=max(0,current+incoming-reserved)
    target=max(0,ceil(daily_demand*lead_time_days)+safety_stock)
    return max(0,target-available)

def choose_route(stock_available, sla_ok, batch_orders, batch_volume_l, trip_cost, max_orders, max_volume_l, hub_cost_per_order):
    if batch_orders<=0: return {"route":"Hub","reason":"Партия пуста","mesh_cost_per_order":float("inf")}
    mesh=trip_cost/batch_orders
    if not stock_available: reason="Нет доступного товара в Anchor"
    elif not sla_ok: reason="Mesh не укладывается в допустимый срок"
    elif batch_orders>max_orders or batch_volume_l>max_volume_l: reason="Превышена вместимость рейса"
    elif mesh>=hub_cost_per_order: reason="Mesh не дешевле Hub при текущей загрузке"
    else: return {"route":"Mesh","reason":"Ограничения соблюдены, Mesh дешевле Hub","mesh_cost_per_order":mesh}
    return {"route":"Hub","reason":reason,"mesh_cost_per_order":mesh}

def calculate_costs(total_orders, mesh_orders, hub_orders, hub_cost_per_order, trip_cost, trips, mesh_variable_cost_per_order, smart_cost):
    baseline=total_orders*hub_cost_per_order
    trip_total=trips*trip_cost
    variable=mesh_orders*mesh_variable_cost_per_order
    hub=hub_orders*hub_cost_per_order
    total=trip_total+variable+hub+smart_cost
    return {"hub_baseline":baseline,"trip_total":trip_total,"mesh_variable_total":variable,"hub_total":hub,"combined_total":total,"savings":baseline-total}
