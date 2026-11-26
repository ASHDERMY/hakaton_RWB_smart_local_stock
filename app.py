import streamlit as st
import pandas as pd

from database import (
    init_db, seed_demo, products_df, inventory_df, points_df, orders_df,
    mesh_batches_df, process_mesh_batches, run_daily_operations,
    prepare_mature_simulation, mature_history_summary, mature_history_daily_df,
    settings_dict,
)
from financial_model import forecast_financials, break_even_anchor_fee

st.set_page_config(page_title="Smart Local Stock", page_icon="📦", layout="wide")
init_db()

st.title("Smart Local Stock")
st.caption(
    "Инструмент оператора локальной логистики: сравнение Hub и пополнения через якорные ПВЗ. "
    "Прогноз спроса и данные о заканчивающихся товарах предполагаются входными данными от RWB."
)

page = st.radio(
    "Рабочий раздел",
    ["Решения оператора", "Экономика и сценарии"],
    horizontal=True,
    label_visibility="collapsed",
)

def current_settings():
    return {key: float(item["value"]) for key, item in settings_dict().items()}

def money(value):
    return f"{value:,.0f} ₽".replace(",", " ")

def show_demo_setup():
    products = products_df()
    points = points_df()
    has_anchor = not points.empty and (points["Тип"] == "Anchor").any()
    has_hub = not points.empty and (points["Тип"] == "Hub").any()
    has_pvz = not points.empty and (points["Тип"] == "Обычный").any()
    if products.empty or not (has_anchor and has_hub and has_pvz):
        st.info(
            "Для демонстрации нужны тестовые товары, один якорный ПВЗ, обычный ПВЗ и Hub. "
            "Создайте демонстрационный набор. Он не является реестром или данными RWB."
        )
        if st.button("Создать демонстрационный набор", type="primary"):
            seed_demo()
            st.success("Демонстрационный набор создан. Повторный запуск не дублирует основные записи.")
            st.rerun()
        return False
    return True

if page == "Решения оператора":
    st.subheader("Очередь локального пополнения")
    st.caption(
        "Оператор получает потребность от системы RWB. Smart Local Stock не прогнозирует спрос: "
        "он проверяет доступность запаса в Anchor, загрузку партии, ограничения и полную стоимость."
    )

    if show_demo_setup():
        settings = current_settings()
        orders = orders_df()
        batches = mesh_batches_df()

        open_batches = (
            batches[batches["Статус"].isin(["waiting", "blocked_hub_stock"])]
            if not batches.empty else pd.DataFrame()
        )
        active_orders = (
            orders[orders["Статус"].isin(["Ожидает партии", "Зарезервирован", "В пути"])]
            if not orders.empty else pd.DataFrame()
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Заявок в работе", len(active_orders))
        c2.metric("Открытых партий", len(open_batches))
        c3.metric("Целевая загрузка", f'{int(settings["batch_target_orders"])} заказов')

        st.markdown("#### Решения, ожидающие обработки")
        if open_batches.empty:
            st.success("Нет накопленных партий, требующих решения.")
        else:
            display_cols = [
                "Партия", "Anchor", "ПВЗ назначения", "Статус",
                "Заказов в ожидании", "Объём ожидания, л", "Ожидание, ч",
                "Решение",
            ]
            st.dataframe(
                open_batches[[c for c in display_cols if c in open_batches.columns]],
                use_container_width=True, hide_index=True
            )
            if st.button("Проверить партии и выбрать Mesh или Hub", type="primary"):
                results = process_mesh_batches()
                mesh_count = sum(1 for item in results if item.get("decision") == "mesh")
                hub_count = sum(1 for item in results if item.get("decision") == "hub")
                wait_count = sum(1 for item in results if item.get("decision") == "wait")
                st.success(
                    f"Проверено партий: {len(results)} · Mesh: {mesh_count} · Hub: {hub_count} · "
                    f"ожидают накопления: {wait_count}"
                )
                st.rerun()

        st.markdown("#### Текущие заявки")
        if active_orders.empty:
            st.info("Активных заявок пока нет. Для показа процесса можно создать тестовую смену ниже.")
        else:
            columns = ["Заказ", "Товар", "Количество", "ПВЗ назначения", "Источник", "Маршрут", "Статус", "Оценка затрат, ₽"]
            st.dataframe(
                active_orders[[c for c in columns if c in active_orders.columns]].head(30),
                use_container_width=True, hide_index=True
            )

        with st.expander("Демонстрация работы на тестовых данных", expanded=active_orders.empty):
            st.caption(
                "Создаёт тестовые заявки на основе имеющихся остатков и проводит их через текущую логику "
                "резервирования, консолидации и выбора маршрута. Не использует реальные заказы RWB."
            )
            demo_count = st.slider("Количество заявок в тестовой смене", 10, 120, 80, step=10)
            if st.button("Запустить тестовую смену", type="primary"):
                try:
                    result = run_daily_operations(order_limit=demo_count)
                    st.success(
                        f"Создано заявок: {result['created_orders']} · "
                        f"отправлено: {result['dispatched_orders']} · "
                        f"готово к выдаче: {result['ready_orders']} · "
                        f"выдано: {result['issued_orders']}"
                    )
                    st.caption(f"Идентификатор сценария: {result['run_code']}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Не удалось запустить тестовую смену: {exc}")

        with st.expander("Подготовить зрелый сценарий — 150-й день"):
            st.caption(
                "Создаёт один раз синтетическую историю на 150 дней (по текущему прогнозу заказов). "
                "Это демонстрационный сценарий, а не фактическая история маркетплейса."
            )
            if st.button("Подготовить историю 150 дней"):
                try:
                    history = prepare_mature_simulation()
                    st.success(
                        f"История подготовлена: {history.get('orders_count', 0):,} заказов. "
                        "Повторный запуск не создаёт дубликаты."
                    )
                except Exception as exc:
                    st.error(f"Не удалось подготовить историю: {exc}")

        st.markdown("#### Логика решения")
        st.write(
            "1. Получаем потребность в пополнении от системы RWB.  "
            "\n2. Проверяем доступный запас на якорном ПВЗ.  "
            "\n3. Накапливаем заявки до целевой загрузки или предельного времени ожидания.  "
            "\n4. Сравниваем полную стоимость Mesh с Hub.  "
            "\n5. Рекомендуем локальную отправку только при соблюдении ограничений и экономическом преимуществе."
        )
        st.caption(
            "Многоточечная консолидация нескольких ПВЗ пока остаётся расчётной рекомендацией. "
            "Текущий исполнительный процесс проводит отдельные партии Anchor → ПВЗ."
        )

elif page == "Экономика и сценарии":
    st.subheader("Экономический эффект якорных ПВЗ")
    st.caption(
        "Одинаковый объём потребности сравнивается в двух сценариях: доставка через Hub "
        "и гибридная модель с локальным пополнением через Anchor там, где оно выгодно."
    )

    cfg = current_settings()
    a, b, c = st.columns(3)
    with a:
        monthly_orders = st.number_input(
            "Заявок в месяц", min_value=1, value=int(cfg["monthly_orders_forecast"]),
            step=100, key="econ_orders"
        )
        anchors = st.number_input("Количество Anchor", min_value=1, value=3, step=1, key="econ_anchors")
        mesh_share = st.slider("Доля заявок для локального пополнения, %", 0, 100, 100, key="econ_share")
    with b:
        batch_orders = st.number_input(
            "Размер партии, заказов", min_value=1, max_value=int(cfg["max_orders"]),
            value=min(100, int(cfg["max_orders"])), step=5, key="econ_batch"
        )
        trip_cost = st.number_input(
            "Стоимость локального рейса, ₽", min_value=0.0,
            value=float(cfg["local_trip_cost"]), step=100.0, key="econ_trip"
        )
        hub_cost = st.number_input(
            "Сравнительная стоимость Hub, ₽/заказ", min_value=0.0,
            value=float(cfg["hub_cost_per_order"]), step=1.0, key="econ_hub"
        )
    with c:
        anchor_fee = st.number_input(
            "Доплата Anchor, ₽/заказ", min_value=0.0,
            value=float(cfg["anchor_fee_per_order"]), step=0.5, key="econ_anchor_fee"
        )
        replenishment = st.number_input(
            "Пополнение Anchor, ₽/заказ", min_value=0.0,
            value=float(cfg["replenishment_per_order"]), step=0.5, key="econ_replenishment"
        )
        storage = st.number_input(
            "Хранение, ₽/заказ", min_value=0.0,
            value=float(cfg["storage_per_order"]), step=0.5, key="econ_storage"
        )

    system_cost = st.number_input(
        "Эксплуатация системы, ₽/месяц", min_value=0.0,
        value=float(cfg["monthly_system_cost"]), step=1000.0, key="econ_system"
    )
    avg_volume = st.number_input(
        "Средний объём заказа, л", min_value=0.1, value=4.0, step=0.5, key="econ_volume"
    )
    with st.expander("Дополнительные расходы и параметры прогноза"):
        x1, x2, x3 = st.columns(3)
        with x1:
            handling = st.number_input("Обработка и риск, ₽/заказ", min_value=0.0, value=0.0, step=0.5)
            return_rate = st.number_input("Возвраты, %", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
        with x2:
            return_cost = st.number_input("Стоимость возврата, ₽", min_value=0.0, value=0.0, step=10.0)
            growth = st.number_input("Рост заявок в месяц, %", min_value=-50.0, max_value=100.0, value=0.0, step=1.0)
        with x3:
            discount = st.number_input("Ставка дисконтирования, %", min_value=0.0, max_value=100.0, value=15.0, step=1.0)

    forecast = forecast_financials(
        monthly_orders=monthly_orders, mesh_share_pct=mesh_share, anchors=anchors,
        batch_orders=batch_orders, max_orders=int(cfg["max_orders"]),
        avg_order_volume_l=avg_volume, max_volume_l=float(cfg["max_volume_l"]),
        trip_cost=trip_cost, hub_cost_per_order=hub_cost,
        variable_cost=float(cfg["mesh_variable_cost"]), anchor_fee=anchor_fee,
        replenishment_per_order=replenishment, storage_per_order=storage,
        handling_per_order=handling, return_rate_pct=return_rate, return_cost=return_cost,
        monthly_system_cost=system_cost, growth_pct=growth, months=12,
        discount_rate_annual_pct=discount,
    )
    month = forecast["monthly"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Стоимость Hub", money(month["baseline_hub_cost"]))
    m2.metric("Mesh + Hub", money(month["combined_cost"]))
    m3.metric("Эффект за месяц", money(month["savings"]),
              delta="экономия" if month["savings"] >= 0 else "дороже",
              delta_color="normal" if month["savings"] >= 0 else "inverse")
    m4.metric("Стоимость Mesh на заказ", f'{month["mesh_cost_per_order"]:.2f} ₽')

    if month["savings"] > 0:
        st.success("При выбранных допущениях гибридная схема дешевле сценария 100% Hub.")
    elif month["savings"] < 0:
        st.warning("При выбранных допущениях локальная схема дороже. Такие партии следует оставить на Hub.")
    else:
        st.info("При выбранных допущениях сценарии находятся на уровне безубыточности.")

    max_fee = break_even_anchor_fee(
        orders_per_month=monthly_orders, anchors=anchors, batch_orders=batch_orders,
        max_orders=int(cfg["max_orders"]), avg_order_volume_l=avg_volume,
        max_volume_l=float(cfg["max_volume_l"]), trip_cost=trip_cost,
        hub_cost_per_order=hub_cost, variable_cost=float(cfg["mesh_variable_cost"]),
        replenishment_per_order=replenishment, storage_per_order=storage,
        handling_per_order=handling, return_rate_pct=return_rate, return_cost=return_cost,
        monthly_system_cost=system_cost, mesh_share_pct=mesh_share,
    )
    st.metric("Предельная доплата Anchor для безубыточности", f"{max_fee:.2f} ₽/заказ")
    st.caption(
        "Отрицательное значение означает, что Mesh не окупается даже при нулевой дополнительной выплате Anchor. "
        "Расчёт включает рейсы, переменные расходы, доплату Anchor, пополнение, хранение и систему."
    )

    st.markdown("#### Прогноз на 12 месяцев")
    rows = pd.DataFrame(forecast["rows"])
    st.line_chart(rows.set_index("month")[["cumulative_savings"]])
    st.dataframe(
        rows[["month", "orders", "trips", "avg_orders_per_trip", "baseline_hub_cost",
              "combined_cost", "savings", "cumulative_savings"]].rename(columns={
            "month": "Месяц", "orders": "Заявки", "trips": "Рейсы",
            "avg_orders_per_trip": "Средняя загрузка", "baseline_hub_cost": "Hub, ₽",
            "combined_cost": "Гибридная схема, ₽", "savings": "Эффект за месяц, ₽",
            "cumulative_savings": "Накопленный эффект, ₽",
        }),
        use_container_width=True, hide_index=True
    )

    st.markdown("#### Исторический демонстрационный сценарий")
    history = mature_history_summary()
    if history:
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Заказов за 150 дней", f'{history["orders_count"]:,}'.replace(",", " "))
        h2.metric("Расчётная база Hub", money(history["baseline_cost"]))
        h3.metric("Система и логистика", money(history["total_cost"]))
        h4.metric("Эффект", money(history["net_effect"]))
        daily = mature_history_daily_df()
        if not daily.empty:
            st.line_chart(daily.set_index("День")[["Накопленный эффект, ₽"]])
        st.caption(
            "Это синтетическая история: в ней моделируется базовый поток через Hub и фиксированные "
            "расходы системы. Она нужна для демонстрации горизонта эксплуатации, а не доказывает "
            "экономию Mesh на реальных операциях."
        )
    else:
        st.info("История на 150 дней ещё не подготовлена. Её можно создать на экране «Решения оператора».")

    st.warning(
        "Все тарифы и объёмы — проектные гипотезы, а не подтверждённые внутренние данные RWB. "
        "Фактическая экономия должна быть проверена пилотом. Объединённые многоточечные рейсы "
        "пока учитываются как потенциальный сценарий, а не реализованное исполнение."
    )

st.divider()
st.caption(
    "Smart Local Stock — дополнительный слой принятия логистических решений. "
    "Источником спроса и потребности в пополнении остаётся информационная система RWB."
)
