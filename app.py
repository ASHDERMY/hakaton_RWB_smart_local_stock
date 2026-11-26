import streamlit as st
import pandas as pd
from database import (
    init_db, seed_demo, products_df, inventory_df, points_df, orders_df,
    settings_dict, save_setting, add_product, receive_stock, create_order,
    update_order_status, create_return, process_return, returns_df,
    movements_df, incidents_df, add_incident, finance_summary,
    mesh_batches_df, process_mesh_batches
)

st.set_page_config(page_title="Smart Local Stock", page_icon="📦", layout="wide")
init_db()

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
st.caption("Система локального хранения, учёта заказов и выбора маршрута Hub / Mesh")
with st.sidebar:
    page=st.radio("Рабочие разделы",[
        "Панель управления","Товары и ПВЗ","Склад и приёмка","Заказы","Партии Mesh","Возвраты",
        "Инциденты","Финансы","Настройки модели","Журнал операций"
    ])
    st.divider()
    if st.button("Загрузить демонстрационные данные",use_container_width=True):
        seed_demo()
        st.success("Демоданные добавлены. Повторное нажатие не дублирует записи.")
    st.caption("Локальная база SQLite")

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
        st.caption("В текущей версии типы Anchor, обычный ПВЗ и Hub используются в логике маршрутизации. Управление точками и географией расширим следующим этапом.")

elif page=="Склад и приёмка":
    st.subheader("Остатки и операции приёмки")
    st.dataframe(inventory_df(),use_container_width=True,hide_index=True)
    products=products_df(); points=points_df()
    if products.empty or points.empty:
        st.warning("Сначала добавьте товары и точки или загрузите демоданные.")
    else:
        with st.form("receipt_form"):
            pmap=dict(zip(products["Товар"],products["id"]))
            ptmap=dict(zip(points["Название"]+" · "+points["Тип"],points["id"]))
            prod=st.selectbox("Товар",list(pmap))
            point=st.selectbox("Точка приёмки",list(ptmap))
            qty=st.number_input("Количество к приёмке",min_value=1,value=20)
            note=st.text_input("Комментарий","Поставка / пополнение")
            ok=st.form_submit_button("Принять товар")
            if ok: show_error(lambda:receive_stock(ptmap[point],pmap[prod],int(qty),note))

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
        candidates=od[od["Статус"].isin(["Зарезервирован","В пути","Готов к выдаче","Ошибка"])]
        if not candidates.empty:
            with st.form("status_form"):
                selected=st.selectbox("Заказ",candidates["Заказ"].tolist())
                row=candidates[candidates["Заказ"]==selected].iloc[0]
                allowed={"Зарезервирован":["В пути","Отменён"],"В пути":["Готов к выдаче","Ошибка"],"Ошибка":["В пути"],"Готов к выдаче":["Выдан","Возврат оформлен"]}
                next_status=st.selectbox("Следующий статус",allowed[row["Статус"]])
                change=st.form_submit_button("Изменить статус")
                if change: show_error(lambda:update_order_status(int(row["id"]),next_status))

elif page=="Партии Mesh":
    st.subheader("Накопление и отправка локальных партий")
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
    - При достижении целевой загрузки (по умолчанию 80 заказов) система сравнивает полную расчётную стоимость Mesh и Hub.
    - Партия не может превышать 130 заказов или 1 500 л.
    - Если целевая загрузка не набрана, партия ожидает, но не дольше 5 часов.
    - После достижения условия закрытия Mesh отправляется только если он дешевле Hub; иначе заказы переводятся на Hub.
    - Если на Hub недостаточно товара для всей партии, она получает статус блокировки; после пополнения Hub проверку можно повторить.
    """)
    st.warning("Параметры стоимости — гипотезы модели. Максимальное ожидание 5 часов — проектное ограничение, а не подтверждённый SLA.")

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
    st.subheader("Регистрация логистических ошибок")
    od=orders_df()
    with st.form("incident_form"):
        ids=[None]+(od["id"].tolist() if not od.empty else [])
        order_id=st.selectbox("Связанный заказ (необязательно)",ids)
        category=st.selectbox("Категория",["Задержка","Повреждение","Потеря","Ошибка маршрута","Неуспешная передача","Другое"])
        description=st.text_area("Описание")
        ok=st.form_submit_button("Зарегистрировать инцидент")
        if ok: show_error(lambda:add_incident(order_id,category,description))
    st.markdown("#### Журнал инцидентов")
    inc=incidents_df()
    if inc.empty: st.info("Инцидентов пока нет.")
    else: st.dataframe(inc,use_container_width=True,hide_index=True)

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
        val=st.number_input(label,min_value=0.0,value=float(item["value"]),key="setting_"+key)
        if st.button("Сохранить: "+label,key="save_"+key):
            save_setting(key,val); st.success("Сохранено. Новые заказы будут использовать это значение.")
    st.caption("Изменение параметров не пересчитывает исторические заказы: их оценка сохраняется на момент создания.")

else:
    st.subheader("Журнал движения товаров")
    moves=movements_df()
    if moves.empty: st.info("Операций пока нет.")
    else: st.dataframe(moves,use_container_width=True,hide_index=True)
