import streamlit as st
import pandas as pd
from map_view import render_logistics_map
from engine import replenishment_qty
from database import (
    init_db, seed_demo, products_df, inventory_df, points_df, orders_df,
    settings_dict, save_setting, add_product, receive_stock, create_order,
    update_order_status, create_return, process_return, returns_df,
    movements_df, incidents_df, add_incident, finance_summary,
    mesh_batches_df, process_mesh_batches, sellers_df, supplier_deliveries_df,
    create_seller, create_supplier_delivery, receive_supplier_delivery, add_pickup_point,
    operational_snapshot, operational_runs_df, run_daily_operations, clear_temporary_simulation_data, simulation_state, simulation_control_state, start_continuous_simulation, stop_continuous_simulation, continuous_simulation_tick, simulation_timeline_df, simulation_vehicles_df, simulation_financials, simulation_shift_snapshot
)

st.set_page_config(page_title="Smart Local Stock", page_icon="📦", layout="wide")
init_db()
# Партии проверяются внутри тика симуляции и при явных операциях, а не на каждом rerun Streamlit.

def settings_values():
    raw=settings_dict()
    return {k: float(v["value"]) for k,v in raw.items()}

def show_error(fn):
    try:
        result=fn()
        st.success("Операция выполнена.")
        return result
    except (ValueError, Exception) as exc:
        st.error(str(exc))
        return None

st.title("📦 Smart Local Stock")
st.caption("Локальный запас → консолидация → выбор Mesh или Hub → доставка в ПВЗ → выдача. Расчётные тарифы помечены как гипотезы.")
with st.sidebar:
    page=st.radio("Рабочие разделы",[
        "Оперативный центр","Панель управления","Карта логистики","Товары и ПВЗ","Склад и приёмка","Продавцы и поставки",
        "Заказы","Партии Mesh","Возвраты","Инциденты","Экономика участников","Финансы","Настройки модели","Журнал операций"
    ])
    st.divider()
    if st.button("Загрузить демонстрационные данные",use_container_width=True):
        seed_demo()
        st.success("Демоданные добавлены. Повторное нажатие не дублирует записи.")
    st.caption("Локальная база SQLite")

# Background fragment keeps the simulation advancing even when the operator opens another page.
_background_state = simulation_control_state()
@st.fragment(run_every="3s" if _background_state["running"] else None)
def _continuous_simulation_runner():
    state = simulation_control_state()
    if state["running"]:
        try:
            continuous_simulation_tick()
        except Exception as exc:
            st.error(f"Симуляция остановлена из-за ошибки: {exc}")
            stop_continuous_simulation()
_continuous_simulation_runner()

if page=="Оперативный центр":
    st.subheader("Оперативный центр логистической сети")
    st.caption("Единый экран диспетчера: заказы, запасы, партии, происшествия, поставки, возвраты и расчётные финансы. Все показатели берутся из текущей SQLite-базы.")
    snap = operational_snapshot()
    status = snap["status_counts"]
    inv = snap["inventory"]
    batches = snap["batches"]
    incidents = snap["incidents"]
    returns = snap["returns"]
    deliveries = snap["deliveries"]
    points = snap["points"]
    today = snap["today"]

    with st.container(border=True):
        st.markdown("#### Непрерывная диспетчерская смена")
        st.caption("Смена работает, пока открыто приложение и не нажата кнопка «Стоп». Такт — каждые 3 секунды; заказ создаётся раз в 6 секунд и случайно направляется в один из обычных ПВЗ. Для демонстрации целевая партия — до 6 заказов на направление; настройки основной модели не меняются.")
        sim_state = simulation_control_state()

    @st.fragment(run_every="3s" if sim_state["running"] else None)
    def live_dispatch_fragment():
        current = simulation_control_state()
        state_col, start_col, stop_col = st.columns([2,1,1])
        state_col.metric("Состояние смены", "РАБОТАЕТ" if current["running"] else "ОСТАНОВЛЕНА",
                         f"Такт {current['tick_no']} · {current['shift_code'] or 'нет активной смены'}")
        if start_col.button("▶ Старт", type="primary", disabled=bool(current["running"]), use_container_width=True):
            start_continuous_simulation()
            st.rerun()
        if stop_col.button("■ Стоп", type="secondary", disabled=not bool(current["running"]), use_container_width=True):
            stop_continuous_simulation()
            st.rerun()

        shift = simulation_shift_snapshot()
        live_status = shift["status_counts"]
        a,b,c,d = st.columns(4)
        a.metric("Заказы текущей смены", shift["total_orders"])
        b.metric("В пути", live_status.get("В пути",0))
        c.metric("Ожидают выдачи", live_status.get("Готов к выдаче",0))
        d.metric("Выдано", live_status.get("Выдан",0))
        st.caption(f"Смена: {current.get('shift_code') or '—'} · Автообновление каждые 3 секунды · Показатели ниже относятся только к этой смене")
        timeline = simulation_timeline_df(30)
        st.markdown("##### Хронология событий")
        if timeline.empty:
            st.info("События появятся после старта смены.")
        else:
            st.dataframe(timeline,use_container_width=True,hide_index=True,height=260)
        vehicles = simulation_vehicles_df()
        st.markdown("##### Карта активных перевозок")
        if vehicles.empty:
            st.info(f"В текущей смене нет транспорта в пути. Mesh ожидают партии: {shift['mesh_waiting_orders']} заказов; заблокировано из-за остатка Hub: {shift['mesh_blocked_orders']}.")
        else:
            from map_view import render_live_dispatch_map
            render_live_dispatch_map(vehicles)
        fin_live = simulation_financials()
        st.markdown("##### Экономика текущей смены")
        f1,f2,f3,f4 = st.columns(4)
        f1.metric("Заказы Mesh / Hub", f"{fin_live['mesh_orders']} / {fin_live['hub_orders']}")
        f2.metric("Отправленные рейсы Mesh", fin_live["mesh_trips"])
        f3.metric("Расчётные затраты", f"{fin_live['transport_total']:,.2f} ₽")
        f4.metric("Эффект к сценарию 100% Hub", f"{fin_live['savings']:+,.2f} ₽",
                  delta="экономия" if fin_live["savings"]>=0 else "дороже",
                  delta_color="normal" if fin_live["savings"]>=0 else "inverse")
        st.caption(f"Mesh: ожидают партии — {fin_live['mesh_waiting_orders']}; в пути — {fin_live['mesh_in_transit_orders']}; готовы к выдаче — {fin_live['mesh_ready_orders']}; заблокированы — {fin_live['mesh_blocked_orders']}. Ожидающих партий: {fin_live['mesh_waiting_batches']}; заблокированных партий: {fin_live['mesh_blocked_batches']}.")
        with st.expander("Расшифровка финансовых формул"):
            st.write(f"Рейсы Mesh (отправленные и завершённые): {fin_live['mesh_trip_cost']:,.2f} ₽")
            st.write(f"Переменные расходы Mesh: {fin_live['mesh_variable_cost']:,.2f} ₽")
            st.write(f"Маршрут Hub и заблокированные Mesh-партии (консервативная оценка): {fin_live['hub_cost']:,.2f} ₽")
            st.write(f"Сравнительная база 100% Hub: {fin_live['hub_baseline']:,.2f} ₽")
            st.write(f"Возвраты в смене: {fin_live['returns']}")
            st.code("C_total = C_dispatched_trips + C_waiting_batch_estimates + C_Mesh_variable + C_Hub_reference\nSaving = N_all_orders × C_Hub_reference - C_total",language="text")
        st.caption("Это транспортная экономика текущей смены, а не полная прибыль/P&L. Ожидающие партии учитываются по гипотетической стоимости рейса; заблокированные партии консервативно оцениваются по тарифу Hub. Хранение, закупочная стоимость, труд, компенсации и возвратная логистика не включены. Тарифы — гипотезы, не фактические данные RWB.")
        if current["running"]:
            st.info("Смена активна. Нажми «Стоп» выше, чтобы завершить её.")
    live_dispatch_fragment()


    st.markdown("#### Состояние сети (вся база SQLite)")
    alerts = []
    if batches["blocked_batches"]:
        alerts.append(f"Заблокировано партий из-за остатка Hub: {batches['blocked_batches']}")
    if incidents["open_incidents"]:
        alerts.append(f"Открытых инцидентов: {incidents['open_incidents']}")
    if deliveries["pending_deliveries"]:
        alerts.append(f"Ожидают приёмки поставки продавца: {deliveries['pending_deliveries']}")
    if returns["pending_returns"]:
        alerts.append(f"Не обработано возвратов: {returns['pending_returns']}")
    if inv["low_stock_rows"]:
        alerts.append(f"Низкий остаток: {inv['low_stock_rows']} товарных позиций (порог ≤ 5 шт.)")
    if alerts:
        for alert in alerts:
            st.warning(alert)
    else:
        st.success("Критичных сигналов по текущим правилам мониторинга нет.")

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Всего заказов", f"{snap['total_orders']:,}")
    m2.metric("Заказы сегодня", f"{today['orders_today']:,}")
    m3.metric("В пути", f"{status.get('В пути',0):,}")
    m4.metric("Ожидают партии", f"{status.get('Ожидает партии',0):,}")
    m5,m6,m7,m8 = st.columns(4)
    m5.metric("Активные точки", f"{points['active_points'] or 0}", f"Anchor {points['anchors'] or 0} · Hub {points['hubs'] or 0}")
    m6.metric("Физический запас", f"{inv['physical_units']:,} шт.")
    m7.metric("Зарезервировано", f"{inv['reserved_units']:,} шт.")
    m8.metric("Входящие поставки", f"{deliveries['pending_deliveries']:,}")

    st.markdown("#### Поток заказов по статусам")
    status_order = ["Ожидает партии","Зарезервирован","В пути","Готов к выдаче","Выдан","Возврат оформлен","Ошибка","Отменён"]
    status_frame = pd.DataFrame([{"Статус": name, "Количество": status.get(name,0)} for name in status_order])
    chart_col, finance_col = st.columns([1.15,1])
    with chart_col:
        st.bar_chart(status_frame.set_index("Статус"), use_container_width=True)
    with finance_col:
        st.markdown("#### Финансовая картина по всей базе")
        fin = finance_summary()
        st.metric("Учтённые затраты по заказам (вся база)", f"{fin['estimated_cost']:,.2f} ₽")
        st.metric("Расходы, созданные сегодня", f"{today['cost_today']:,.2f} ₽")
        route_frame = pd.DataFrame([
            {"Маршрут": route, "Заказов": count}
            for route,count in snap["route_counts"].items()
        ])
        if not route_frame.empty:
            st.dataframe(route_frame,use_container_width=True,hide_index=True)
        st.caption("Здесь суммируются записанные в orders поля estimated_cost по неотменённым заказам всей базы SQLite. Это не та же величина, что транспортная оценка текущей смены выше: здесь не добавляются прогнозные расходы ещё не отправленных Mesh-партий. Все тарифы — гипотезы, не подтверждённая внутренняя себестоимость RWB.")

    st.markdown("#### Что требует внимания диспетчера")
    alert_cols = st.columns(4)
    alert_cols[0].metric("Открытые инциденты", incidents["open_incidents"])
    alert_cols[1].metric("Обходы через Hub", incidents["rerouted_incidents"])
    alert_cols[2].metric("Ожидают возврата", returns["pending_returns"])
    alert_cols[3].metric("Заблокированные партии", batches["blocked_batches"])
    table_col1, table_col2 = st.columns(2)
    with table_col1:
        st.markdown("**Активные заказы**")
        live_orders = orders_df()
        if not live_orders.empty:
            live_orders = live_orders[live_orders["Статус"].isin(["Ожидает партии","Зарезервирован","В пути","Готов к выдаче","Ошибка"])]
            st.dataframe(live_orders.head(15),use_container_width=True,hide_index=True)
        else:
            st.info("Активных заказов пока нет.")
    with table_col2:
        st.markdown("**Открытые происшествия**")
        live_incidents = incidents_df()
        if not live_incidents.empty:
            live_incidents = live_incidents[live_incidents["Статус"]=="open"]
            st.dataframe(live_incidents.head(10),use_container_width=True,hide_index=True)
        else:
            st.info("Инцидентов пока нет.")

    st.markdown("#### Партии, остатки и поставки")
    batch_col, stock_col = st.columns(2)
    with batch_col:
        st.markdown("**Ожидающие и заблокированные партии**")
        live_batches = mesh_batches_df()
        if not live_batches.empty:
            live_batches = live_batches[live_batches["Статус"].isin(["waiting","blocked_hub_stock"])]
            st.dataframe(live_batches,use_container_width=True,hide_index=True)
        else:
            st.info("Открытых партий нет.")
    with stock_col:
        st.markdown("**Товары с низким доступным остатком**")
        low_stock = inventory_df()
        if not low_stock.empty:
            low_stock = low_stock[low_stock["Доступно"] <= 5]
            st.dataframe(low_stock.head(15),use_container_width=True,hide_index=True)
        else:
            st.info("Нет складских данных.")

    st.markdown("#### История запусков повседневной смены")
    runs = operational_runs_df()
    if runs.empty:
        st.info("Симуляции ещё не запускались.")
    else:
        st.dataframe(runs,use_container_width=True,hide_index=True)
    if st.button("↻ Обновить оперативные показатели",use_container_width=True):
        st.rerun()

    with st.expander("Очистка временных данных симуляции", expanded=False):
        st.warning("Будут удалены заказы всех тестовых смен SHIFT-* и LIVE-*, их события, происшествия, возвраты, поставки и движения. Складские остатки откатываются в обратном порядке операций. Ручные заказы, каталог, точки и настройки сохраняются.")
        confirm_clear = st.checkbox("Подтверждаю очистку данных тестовых смен", key="confirm_clear_simulation")
        if st.button("🧹 Очистить временные данные", type="secondary", disabled=not confirm_clear, use_container_width=True):
            try:
                cleared = clear_temporary_simulation_data()
                st.success(
                    f"Очистка завершена: смен — {cleared['runs']}, заказов — {cleared['orders']}, "
                    f"происшествий — {cleared['incidents']}, возвратов — {cleared['returns']}, "
                    f"пустых партий — {cleared['batches']}."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Не удалось очистить данные: {exc}")
    st.caption("Симуляция использует случайное распределение заказов по обычным ПВЗ, модельные события и виртуальные перевозки. Она не является интеграцией с API Wildberries, не использует GPS и прекращается при остановке приложения.")


if page=="Панель управления":
    st.subheader("Состояние логистической сети")
    f=finance_summary(); inv=inventory_df(); od=orders_df()
    a,b,c,d=st.columns(4)
    a.metric("Заказов в системе",f["orders_count"])
    b.metric("Mesh-заказов",f["mesh_orders"])
    c.metric("Hub-заказов",f["hub_orders"])
    d.metric("Оценка логистических затрат",f'{f["estimated_cost"]:,.0f} ₽')
    st.markdown("#### Текущие остатки")
    if inv.empty: st.info("Добавьте товары и выполните приёмку либо загрузите демоданные.")
    else: st.dataframe(inv,use_container_width=True,hide_index=True)
    st.markdown("#### Последние заказы")
    if od.empty: st.info("Заказов пока нет.")
    else: st.dataframe(od.head(10),use_container_width=True,hide_index=True)
    st.info("Mesh используется только при наличии запаса в Anchor, соблюдении ограничений и меньшей расчётной стоимости. В остальных случаях система выбирает Hub.")

elif page=="Карта логистики":
    render_logistics_map(orders_df())

elif page=="Товары и ПВЗ":
    left,right=st.columns([1,1])
    with left:
        st.markdown("#### Каталог товаров")
        st.dataframe(products_df(),use_container_width=True,hide_index=True)
        with st.form("new_product"):
            st.markdown("**Добавить товар**")
            sku=st.text_input("Артикул / SKU")
            name=st.text_input("Название")
            category=st.text_input("Категория")
            volume=st.number_input("Объём единицы, л",min_value=0.01,value=1.0,step=0.1)
            abc=st.selectbox("Класс спроса",["AX","AY","AZ","BX","BY","BZ","CX","CY","CZ"])
            submitted=st.form_submit_button("Создать товар")
            if submitted:
                show_error(lambda:add_product(sku,name,category,volume,abc))
    with right:
        st.markdown("#### Точки сети")
        st.dataframe(points_df(),use_container_width=True,hide_index=True)
        st.caption("Активные Anchor используются для размещения локального запаса; Hub — для поставок продавца и резервного маршрута.")
        with st.expander("Добавить точку сети"):
            with st.form("new_pickup_point_form"):
                new_point_name=st.text_input("Название точки")
                new_point_city=st.text_input("Город")
                new_point_type=st.selectbox("Тип точки",["Anchor","Обычный","Hub"])
                point_submit=st.form_submit_button("Добавить активную точку")
                if point_submit:
                    show_error(lambda:add_pickup_point(new_point_name,new_point_type,new_point_city))

elif page=="Склад и приёмка":
    st.subheader("Остатки и операции приёмки")
    inv_data=inventory_df()
    st.dataframe(inv_data,use_container_width=True,hide_index=True)
    products=products_df(); points=points_df()
    active_points = points[points["Активен"] == 1] if not points.empty else points
    anchors_storage = active_points[active_points["Тип"] == "Anchor"]
    if products.empty or active_points.empty:
        st.warning("Сначала добавьте товары и активные точки или загрузите демоданные.")
    else:
        st.markdown("#### Размещение товара на опорном ПВЗ (Anchor)")
        st.caption("Выбери конкретный Anchor: поступление будет записано именно в его остаток, а не в автоматически выбранную точку.")
        if anchors_storage.empty:
            st.warning("В сети пока нет активного Anchor. Добавь точку типа Anchor в разделе «Товары и ПВЗ».")
        else:
            with st.form("anchor_storage_form"):
                anchor_map = dict(zip(
                    anchors_storage["Название"] + " · " + anchors_storage["Город"] + " · ID " + anchors_storage["id"].astype(str),
                    anchors_storage["id"]
                ))
                product_map = dict(zip(products["Товар"] + " · " + products["SKU"], products["id"]))
                chosen_anchor = st.selectbox("Опорный ПВЗ для хранения", list(anchor_map), key="storage_anchor_choice")
                chosen_product = st.selectbox("Товар для размещения", list(product_map), key="storage_anchor_product")
                anchor_qty = st.number_input("Количество для размещения", min_value=1, value=20, key="storage_anchor_qty")
                anchor_note = st.text_input("Основание операции", "Размещение запаса продавца на Anchor", key="storage_anchor_note")
                anchor_submit = st.form_submit_button("Разместить товар на выбранном Anchor")
                if anchor_submit:
                    show_error(lambda: receive_stock(anchor_map[chosen_anchor], product_map[chosen_product], int(anchor_qty), anchor_note))

        st.divider()
        st.markdown("#### Обычная приёмка на любую точку, включая Hub")
        with st.form("receipt_form"):
            pmap=dict(zip(products["Товар"] + " · " + products["SKU"],products["id"]))
            ptmap=dict(zip(
                active_points["Название"]+" · "+active_points["Тип"]+" · ID "+active_points["id"].astype(str),
                active_points["id"]
            ))
            prod=st.selectbox("Товар",list(pmap), key="receipt_product")
            point=st.selectbox("Точка приёмки",list(ptmap), key="receipt_point")
            qty=st.number_input("Количество к приёмке",min_value=1,value=20,key="receipt_qty")
            note=st.text_input("Комментарий","Поставка / пополнение",key="receipt_note")
            ok=st.form_submit_button("Принять товар")
            if ok: show_error(lambda:receive_stock(ptmap[point],pmap[prod],int(qty),note))


    st.divider()
    st.markdown("#### Автоматический расчёт потребности в пополнении Anchor")
    st.caption("Введи средний спрос D, срок пополнения L и страховой запас SS. Это сценарный расчёт: D задаётся оператором, а не прогнозируется обученной ML-моделью.")
    anchors = points[points["Тип"] == "Anchor"]
    if not products.empty and not anchors.empty:
        pmap_r = dict(zip(products["Товар"] + " (" + products["SKU"] + ")", products["id"]))
        amap_r = dict(zip(anchors["Название"], anchors["id"]))
        r1, r2 = st.columns(2)
        with r1:
            r_product = st.selectbox("SKU для расчёта", list(pmap_r), key="replenish_product")
            r_anchor = st.selectbox("Опорный ПВЗ", list(amap_r), key="replenish_anchor")
        with r2:
            daily_demand = st.number_input("Средний спрос D, шт./день", min_value=0.0, value=5.0, step=0.5, key="replenish_d")
            lead_days = st.number_input("Срок пополнения L, дней", min_value=0.0, value=2.0, step=0.5, key="replenish_l")
            safety = st.number_input("Страховой запас SS, шт.", min_value=0, value=5, step=1, key="replenish_ss")
        sku = products.loc[products["id"] == pmap_r[r_product], "SKU"].iloc[0]
        row = inv_data[(inv_data["SKU"] == sku) & (inv_data["ПВЗ"] == r_anchor)]
        physical = int(row["Остаток"].iloc[0]) if not row.empty else 0
        reserved = int(row["Резерв"].iloc[0]) if not row.empty else 0
        incoming = int(row["В пути"].iloc[0]) if not row.empty else 0
        available = physical + incoming - reserved
        import math
        target = math.ceil(daily_demand * lead_days) + int(safety)
        need = replenishment_qty(physical, incoming, reserved, daily_demand, lead_days, int(safety))
        m1, m2, m3 = st.columns(3)
        m1.metric("Доступный остаток", f"{available} шт.")
        m2.metric("Целевой остаток", f"{target} шт.")
        m3.metric("Рекомендация к пополнению", f"{need} шт.")
        st.code("I_available = I + I_in - I_res\nI_target = ceil(D × L) + SS\nQ = max(0, I_target - I_available)", language="text")
    else:
        st.info("Для расчёта нужны товары и хотя бы одна точка Anchor.")
elif page=="Заказы":
    st.subheader("Создание и обработка заказов")
    products=products_df(); points=points_df(); settings=settings_values()
    if products.empty or points.empty:
        st.warning("Сначала загрузите демоданные или создайте каталог и точки.")
    else:
        with st.form("order_form"):
            code=st.text_input("Номер заказа")
            pmap=dict(zip(products["Товар"]+" ("+products["SKU"]+")",products["id"]))
            dests=points[points["Тип"]=="Обычный"]
            dmap=dict(zip(dests["Название"],dests["id"]))
            product=st.selectbox("Товар",list(pmap))
            destination=st.selectbox("ПВЗ назначения",list(dmap))
            qty=st.number_input("Количество",min_value=1,value=1)
            submit=st.form_submit_button("Создать заказ и выбрать маршрут")
            if submit:
                result=show_error(lambda:create_order(code,pmap[product],dmap[destination],int(qty),settings))
                if result:
                    if result["status"] == "Ожидает партии":
                        st.info(f'Заказ добавлен в Mesh-партию №{result["batch_id"]}. Он ожидает накопления партии; резерв создан в Anchor.')
                    else:
                        st.success(f'Маршрут: {result["route"]}; источник: {result["source"]}; статус: {result["status"]}; оценка затрат: {result["estimated_cost"]:.2f} ₽')
    st.markdown("#### Заказы и переходы статусов")
    od=orders_df()
    if od.empty: st.info("Заказов пока нет.")
    else:
        st.dataframe(od,use_container_width=True,hide_index=True)
        candidates=od[od["Статус"].isin(["Ожидает партии","Зарезервирован","В пути","Готов к выдаче","Ошибка"])]
        if not candidates.empty:
            with st.form("status_form"):
                selected=st.selectbox("Заказ",candidates["Заказ"].tolist())
                row=candidates[candidates["Заказ"]==selected].iloc[0]
                allowed={"Ожидает партии":["Отменён"],"Зарезервирован":["В пути","Отменён"],"В пути":["Готов к выдаче","Ошибка"],"Ошибка":["В пути"],"Готов к выдаче":["Выдан","Возврат оформлен"]}
                next_status=st.selectbox("Следующий статус",allowed[row["Статус"]])
                change=st.form_submit_button("Изменить статус")
                if change: show_error(lambda:update_order_status(int(row["id"]),next_status))

elif page=="Партии Mesh":
    st.subheader("Накопление и отправка локальных партий")
    st.markdown("#### Быстрая демонстрация Mesh для защиты")
    demo_products = products_df()
    demo_points = points_df()
    demo_inventory = inventory_df()
    demo_settings = settings_values()
    demo_target = min(int(demo_settings["batch_target_orders"]), int(demo_settings["max_orders"]))
    demo_anchors = demo_points[demo_points["Тип"] == "Anchor"]
    if not demo_products.empty and not demo_anchors.empty:
        anchor_names = demo_anchors["Название"].tolist()
        available_demo = demo_inventory[
            (demo_inventory["Тип"] == "Anchor") &
            (demo_inventory["ПВЗ"].isin(anchor_names)) &
            (demo_inventory["Доступно"] >= demo_target)
        ]
        candidate_skus = available_demo["SKU"].unique().tolist()
        candidate_products = demo_products[
            demo_products["SKU"].isin(candidate_skus) &
            (demo_products["Объём, л"] * demo_target <= float(demo_settings["max_volume_l"]))
        ]
        demo_destinations = demo_points[demo_points["Тип"] == "Обычный"]
        if not candidate_products.empty and not demo_destinations.empty:
            dpmap = dict(zip(candidate_products["Товар"] + " (" + candidate_products["SKU"] + ")", candidate_products["id"]))
            ddmap = dict(zip(demo_destinations["Название"], demo_destinations["id"]))
            dc1, dc2 = st.columns(2)
            with dc1:
                demo_product = st.selectbox("Товар для демонстрационной партии", list(dpmap), key="demo_mesh_product")
            with dc2:
                default_dest = next((n for n in ddmap if "Советская" in n), list(ddmap)[0])
                demo_destination = st.selectbox("ПВЗ назначения", list(ddmap), index=list(ddmap).index(default_dest), key="demo_mesh_destination")
            st.caption(f"Будет создано {demo_target} отдельных заказов. Система соберёт их в одну партию, проверит вместимость и сравнит Mesh с Hub по фактическому размеру партии. Демозаказы останутся в базе и изменят остатки.")
            if st.button("Создать и обработать демонстрационную партию", type="primary", key="run_mesh_demo"):
                from datetime import datetime
                prefix = datetime.now().strftime("%Y%m%d%H%M%S")
                results = []
                try:
                    for i in range(1, demo_target + 1):
                        results.append(create_order(
                            f"DEMO-MESH-{prefix}-{i:03d}",
                            dpmap[demo_product], ddmap[demo_destination], 1, demo_settings
                        ))
                    route_result = results[-1]
                    st.success(f"Создано {len(results)} заказов. Итоговый маршрут: {route_result['route']}; статус: {route_result['status']}; источник: {route_result['source']}; оценка на заказ: {route_result['estimated_cost']:.2f} ₽.")
                    st.info("Открой «Карта логистики» и выбери заказ DEMO-MESH, чтобы показать маршрут. В разделе «Заказы» можно провести его по статусам до выдачи.")
                except Exception as exc:
                    st.error(f"Демонстрация остановлена: {exc}. Проверь остаток Anchor и параметры модели; часть заказов могла успеть сохраниться.")
        else:
            st.warning(f"Для партии из {demo_target} заказов не найден товар с достаточным доступным остатком в Anchor и подходящим суммарным объёмом. Пополни Anchor или скорректируй целевую загрузку.")
    else:
        st.info("Для демонстрации сначала добавь демонстрационные товары, точки и остатки.")

    st.caption("Партии формируются отдельно для пары Anchor → обычный ПВЗ. Учитываются реальные количество заказов и суммарный объём товаров.")
    if st.button("Обработать ожидающие партии", type="primary"):
        results = process_mesh_batches()
        if not results:
            st.info("Нет партий, требующих проверки.")
        else:
            for result in results:
                st.write(f'Партия №{result["batch_id"]}: {result["decision"]} — {result["reason"]}')
    batches = mesh_batches_df()
    if batches.empty:
        st.info("Пока нет Mesh-партий. Создай заказы с доступным запасом в Anchor.")
    else:
        st.dataframe(batches, use_container_width=True, hide_index=True)
    st.markdown("#### Правила закрытия партии")
    st.markdown("""
    - При достижении целевой загрузки (по умолчанию 80 заказов) система сравнивает расчётные транспортные затраты Mesh и Hub.
    - Партия не может превышать 130 заказов или 1 500 л.
    - Если целевая загрузка не набрана, партия ожидает до контрольной проверки по лимиту 5 часов.
    - После достижения условия закрытия Mesh отправляется только если его расчётные транспортные затраты ниже Hub; иначе заказы переводятся на Hub.
    - Если на Hub недостаточно товара для всей партии, она получает статус блокировки; после пополнения Hub проверку можно повторить.
    """)
    st.warning("Параметры стоимости — гипотезы модели. Максимальное ожидание 5 часов — проектное ограничение, а не подтверждённый SLA. Проверка выполняется при каждом обращении/перерисовке приложения и вручную; отдельный фоновый планировщик пока не подключён.")

elif page=="Возвраты":
    st.subheader("Оформление и обработка возвратов")
    od=orders_df()
    eligible=od[od["Статус"]=="Выдан"]
    if not eligible.empty:
        with st.form("return_form"):
            omap=dict(zip(eligible["Заказ"],eligible["id"]))
            selected=st.selectbox("Выданный заказ",list(omap))
            max_qty=int(eligible[eligible["Заказ"]==selected]["Количество"].iloc[0])
            qty=st.number_input("Количество к возврату",1,max_qty,1)
            reason=st.text_input("Причина возврата")
            ok=st.form_submit_button("Зарегистрировать возврат")
            if ok: show_error(lambda:create_return(omap[selected],int(qty),reason))
    else: st.info("Нет выданных заказов, доступных для оформления возврата.")
    ret=returns_df()
    st.markdown("#### Реестр возвратов")
    if ret.empty: st.info("Возвратов пока нет.")
    else:
        st.dataframe(ret,use_container_width=True,hide_index=True)
        pending=ret[ret["Статус"]=="pending"]
        if not pending.empty:
            with st.form("return_process"):
                rid=st.selectbox("Возврат ID",pending["id"].tolist())
                condition=st.selectbox("Состояние товара",["Пригоден","Повреждён"])
                process=st.form_submit_button("Завершить проверку")
                if process: show_error(lambda:process_return(int(rid),condition))

elif page=="Инциденты":
    st.subheader("Диспетчер инцидентов и перестроение маршрута")
    st.caption("Система анализирует описание по правилам MVP, формирует план действий и при подходящем сценарии меняет маршрут заказа. Это алгоритм принятия решений, не подключённая LLM и не команда реальному перевозчику.")
    od=orders_df()
    with st.form("incident_form"):
        order_options = {"Без привязки к заказу": None}
        if not od.empty:
            order_options.update({
                f"{row['Заказ']} · {row['Маршрут']} · {row['Статус']}": int(row["id"])
                for _, row in od.iterrows()
            })
        selected_order = st.selectbox("Заказ, затронутый инцидентом", list(order_options))
        category=st.selectbox("Категория",["Задержка","Перекрытие дороги","Авария / поломка транспорта","Ошибка маршрута","Повреждение","Потеря","Неуспешная передача","Другое"])
        description=st.text_area("Описание происшествия", placeholder="Например: дорога на прямом участке Anchor → ПВЗ перекрыта из-за аварии, проезд невозможен.")
        ok=st.form_submit_button("Проанализировать инцидент и построить план")
        if ok:
            result = show_error(lambda:add_incident(order_options[selected_order],category,description))
            if result:
                st.markdown("#### Сформированный план")
                st.write(result["action_plan"])
                if result["applied"]:
                    st.success(f"Маршрут заказа автоматически изменён на: {result['applied_route']}. Открой карту логистики для просмотра нового плеча через Hub.")
                else:
                    st.info("Маршрут автоматически не менялся: требуется проверка оператора или инцидент не влияет на маршрут.")
    st.markdown("#### Журнал инцидентов и принятые решения")
    inc=incidents_df()
    if inc.empty: st.info("Инцидентов пока нет.")
    else: st.dataframe(inc,use_container_width=True,hide_index=True)


elif page=="Продавцы и поставки":
    st.subheader("Кабинет продавца: поставка товара в Hub")
    st.caption("Пример продавца и поставки для демонстрации сквозного процесса. Продавец и контакт ниже — демонстрационные, не реальные партнёры RWB.")
    seller_data=sellers_df()
    if seller_data.empty:
        st.info("Создай продавца, чтобы оформить поставку.")
    else:
        st.markdown("#### Продавцы")
        st.dataframe(seller_data,use_container_width=True,hide_index=True)
    with st.expander("Добавить продавца"):
        with st.form("seller_form"):
            seller_name=st.text_input("Название продавца")
            seller_category=st.text_input("Категория товара")
            seller_contact=st.text_input("Контакт / email")
            seller_submit=st.form_submit_button("Создать продавца")
            if seller_submit:
                show_error(lambda:create_seller(seller_name,seller_category,seller_contact))

    products=sellers_products=products_df()
    points=points_df()
    hubs=points[(points["Тип"]=="Hub") & (points["Активен"]==1)] if not points.empty else points
    seller_data=sellers_df()
    if not seller_data.empty and not products.empty and not hubs.empty:
        seller_map=dict(zip(seller_data["Продавец"]+" · ID "+seller_data["id"].astype(str),seller_data["id"]))
        product_map=dict(zip(products["Товар"]+" · "+products["SKU"],products["id"]))
        hub_map=dict(zip(hubs["Название"]+" · "+hubs["Город"],hubs["id"]))
        with st.form("supplier_delivery_form"):
            seller_choice=st.selectbox("Продавец",list(seller_map))
            product_choice=st.selectbox("Товар",list(product_map))
            hub_choice=st.selectbox("Склад Hub-получатель",list(hub_map))
            supply_qty=st.number_input("Количество в поставке",min_value=1,value=100)
            supply_ref=st.text_input("Номер накладной","DEMO-SUPPLY-001")
            delivery_submit=st.form_submit_button("Оформить поставку продавца в Hub")
            if delivery_submit:
                show_error(lambda:create_supplier_delivery(seller_map[seller_choice],product_map[product_choice],hub_map[hub_choice],int(supply_qty),supply_ref))
    else:
        st.warning("Для поставки нужны активный продавец, товар и точка типа Hub. Добавь демоданные или создай недостающие сущности.")

    st.markdown("#### Поставки и приёмка на Hub")
    deliveries=supplier_deliveries_df()
    if deliveries.empty:
        st.info("Поставок пока нет.")
    else:
        st.dataframe(deliveries,use_container_width=True,hide_index=True)
        pending=deliveries[deliveries["Статус"]=="Ожидает приёмки"]
        if not pending.empty:
            delivery_map=dict(zip(pending["ID"].astype(str)+" · "+pending["Продавец"]+" · "+pending["SKU"],pending["ID"]))
            with st.form("receive_supplier_delivery_form"):
                delivery_choice=st.selectbox("Поставка к приёмке",list(delivery_map))
                receive_submit=st.form_submit_button("Принять поставку на склад Hub")
                if receive_submit:
                    show_error(lambda:receive_supplier_delivery(int(delivery_map[delivery_choice])))

    st.markdown("#### Остатки на Hub")
    hub_inventory=inventory_df()
    hub_inventory=hub_inventory[hub_inventory["Тип"]=="Hub"] if not hub_inventory.empty else hub_inventory
    if hub_inventory.empty: st.info("На Hub пока нет товарных остатков.")
    else: st.dataframe(hub_inventory,use_container_width=True,hide_index=True)


elif page=="Экономика участников":
    st.subheader("Экономика четырёх сторон")
    st.caption("Сценарный калькулятор на один заказ. Это гипотезы для проверки бизнес-модели, а не действующие тарифы Wildberries.")

    cfg = settings_values()
    n1, n2, n3 = st.columns(3)
    with n1:
        trip_cost = st.number_input("Рейс Anchor → ПВЗ, ₽", min_value=0.0, value=float(cfg["local_trip_cost"]), step=100.0, key="stake_trip")
        batch_n = st.number_input("Заказов в партии", min_value=1, max_value=int(cfg["max_orders"]), value=min(80, int(cfg["max_orders"])), key="stake_batch")
    with n2:
        hub_cost = st.number_input("Сравнительная стоимость Hub, ₽/заказ", min_value=0.0, value=float(cfg["hub_cost_per_order"]), step=1.0, key="stake_hub")
        variable = st.number_input("Переменные Mesh, ₽/заказ", min_value=0.0, value=float(cfg["mesh_variable_cost"]), step=0.5, key="stake_var")
    with n3:
        pvz_reward = st.number_input("Вознаграждение ПВЗ за операции, ₽/заказ", min_value=0.0, value=18.0, step=1.0, key="stake_pvz")
        storage_cost = st.number_input("Прочие расходы сети, ₽/заказ", min_value=0.0, value=5.0, step=0.5, key="stake_storage")

    mesh_transport = trip_cost / batch_n + variable
    marketplace_delta = hub_cost - mesh_transport - pvz_reward - storage_cost
    p1, p2, p3 = st.columns(3)
    p1.metric("Mesh транспорт на заказ", f"{mesh_transport:,.2f} ₽")
    p2.metric("Hub сравнение", f"{hub_cost:,.2f} ₽")
    p3.metric("Эффект маркетплейса", f"{marketplace_delta:+,.2f} ₽/заказ",
              delta="положительный" if marketplace_delta >= 0 else "отрицательный",
              delta_color="normal" if marketplace_delta >= 0 else "inverse")

    st.divider()
    st.markdown("#### Доходность ПВЗ")
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        receiving_fee = st.number_input("Приёмка, ₽", min_value=0.0, value=3.0, step=0.5, key="stake_receive")
    with r2:
        storage_fee = st.number_input("Хранение, ₽/день", min_value=0.0, value=1.0, step=0.5, key="stake_storage_fee")
    with r3:
        storage_days = st.number_input("Дней хранения", min_value=0.0, value=2.0, step=1.0, key="stake_days")
    with r4:
        picking_fee = st.number_input("Сборка и выдача, ₽", min_value=0.0, value=10.0, step=1.0, key="stake_pick")
    pvz_income = receiving_fee + storage_fee * storage_days + picking_fee
    st.metric("Начисление ПВЗ за заказ", f"{pvz_income:,.2f} ₽")
    st.caption("Чистая прибыль ПВЗ = начисление минус труд, площадь, упаковка и потери. Эти расходы необходимо уточнить пилотом.")

    st.divider()
    st.markdown("#### Экономика продавца")
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        sale_price = st.number_input("Цена продажи, ₽", min_value=0.0, value=1500.0, step=50.0, key="stake_sale")
    with s2:
        purchase_cost = st.number_input("Себестоимость товара, ₽", min_value=0.0, value=700.0, step=50.0, key="stake_purchase")
    with s3:
        commission_pct = st.number_input("Комиссия маркетплейса, %", min_value=0.0, max_value=100.0, value=15.0, step=1.0, key="stake_commission")
    with s4:
        seller_logistics = st.number_input("Логистика продавца, ₽", min_value=0.0, value=50.0, step=5.0, key="stake_seller_log")
    seller_profit = sale_price - purchase_cost - sale_price * commission_pct / 100 - seller_logistics
    st.metric("Расчётная прибыль продавца", f"{seller_profit:,.2f} ₽/заказ",
              delta="прибыль" if seller_profit >= 0 else "убыток",
              delta_color="normal" if seller_profit >= 0 else "inverse")

    st.divider()
    st.markdown("#### Условия для покупателя")
    b1, b2, b3 = st.columns(3)
    with b1:
        buyer_price_hub = st.number_input("Цена доставки через Hub, ₽", min_value=0.0, value=99.0, step=5.0, key="stake_buyer_hub")
    with b2:
        buyer_price_mesh = st.number_input("Цена доставки Mesh, ₽", min_value=0.0, value=99.0, step=5.0, key="stake_buyer_mesh")
    with b3:
        eta_difference = st.number_input("Изменение срока Mesh к Hub, часов", value=-4.0, step=1.0, key="stake_eta")
    st.metric("Изменение цены для покупателя", f"{buyer_price_mesh - buyer_price_hub:+,.2f} ₽")
    if buyer_price_mesh <= buyer_price_hub and eta_difference <= 0:
        st.success("В этом сценарии цена покупателя не выросла, а срок не ухудшился.")
    else:
        st.warning("Сценарий ухудшает хотя бы одно условие покупателя. Проверь тариф или SLA.")

    st.markdown("#### Итоговое решение")
    if marketplace_delta >= 0 and seller_profit >= 0 and buyer_price_mesh <= buyer_price_hub and eta_difference <= 0:
        st.success("Сценарий проходит заданные ограничения. Дополнительно проверь расходы ПВЗ, возвраты и риски хранения.")
    else:
        st.warning("Сценарий пока не проходит все ограничения. Следует оставить Hub или изменить загрузку/тарифы.")
    st.code("C_Mesh = C_trip / N_batch + C_variable\nDelta_Marketplace = C_Hub - C_Mesh - Reward_PVZ - Other_Costs\nProfit_Seller = Price - COGS - Commission - Seller_Logistics\nIncome_PVZ = Receiving + Storage * Days + Picking", language="text")

elif page=="Финансы":
    st.subheader("Фактическая модель по зарегистрированным заказам")
    f=finance_summary()
    a,b,c=st.columns(3)
    a.metric("Всего заказов",f["orders_count"])
    b.metric("Оценка затрат",f'{f["estimated_cost"]:,.2f} ₽')
    c.metric("Mesh / Hub",f'{f["mesh_orders"]} / {f["hub_orders"]}')
    od=orders_df()
    if not od.empty:
        st.markdown("#### Структура маршрутов")
        chart=od.groupby("Маршрут",dropna=False)["Оценка затрат, ₽"].sum().reset_index()
        st.bar_chart(chart.set_index("Маршрут"))
        st.dataframe(od[["Заказ","Маршрут","Статус","Оценка затрат, ₽"]],use_container_width=True,hide_index=True)
    st.warning("Это расчётная оценка по параметрам модели, а не бухгалтерские данные или подтверждённая себестоимость RWB.")

elif page=="Настройки модели":
    st.subheader("Параметры логистической модели")
    raw=settings_dict()
    labels={
        "local_trip_cost":"Стоимость локального рейса, ₽",
        "hub_cost_per_order":"Сравнительная стоимость Hub, ₽/заказ",
        "mesh_variable_cost":"Прочие переменные Mesh, ₽/заказ",
        "batch_target_orders":"Плановая загрузка партии, заказов",
        "max_orders":"Лимит заказов на рейс",
        "max_volume_l":"Лимит объёма, л",
        "max_wait_hours":"Максимальное ожидание накопления, ч"
    }
    for key,label in labels.items():
        item=raw[key]
        st.write(f"**{label}** · {item['classification']} · {item['note']}")
        minimum = 1.0 if key in {"batch_target_orders","max_orders","max_volume_l","max_wait_hours","hub_cost_per_order"} else 0.0
        val=st.number_input(label,min_value=minimum,value=float(item["value"]),key="setting_"+key)
        if st.button("Сохранить: "+label,key="save_"+key):
            save_setting(key,val); st.success("Сохранено. Новые заказы будут использовать это значение.")
    st.caption("Изменение параметров не пересчитывает исторические заказы: их оценка сохраняется на момент создания.")

else:
    st.subheader("Журнал движения товаров")
    moves=movements_df()
    if moves.empty: st.info("Операций пока нет.")
    else: st.dataframe(moves,use_container_width=True,hide_index=True)
