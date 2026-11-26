import streamlit as st
import pandas as pd
from database import init_db,seed_demo,products_df,inventory_df
from engine import choose_route,calculate_costs

st.set_page_config(page_title="Smart Local Stock",page_icon="📦",layout="wide")
init_db()
st.title("📦 Smart Local Stock")
st.caption("Интеллектуальное локальное хранение и выбор маршрута Hub / Mesh")
with st.sidebar:
    page=st.radio("Раздел",["Обзор","Запасы","Маршрутизация","Экономика","Сценарии","Методика"])
    if st.button("Загрузить демоданные"):
        seed_demo(); st.success("Добавлены синтетические данные.")
if page=="Обзор":
    st.subheader("Локальный запас + интеллектуальная маршрутизация")
    a,b,c=st.columns(3); a.metric("Локальный рейс","2 400 ₽","гипотеза"); b.metric("Лимит рейса","130 заказов","проектное ограничение"); c.metric("Ожидание партии","до 5 ч","не SLA")
    st.markdown("**Hub** сохраняется для межрегиональных перемещений, дефицита и случаев, когда Mesh невыгоден. **Mesh** — локальный рейс от опорного Anchor к обычным ПВЗ при наличии товара, соблюдении SLA и вместимости.")
    st.info("Все числовые параметры первой версии — сценарные гипотезы, а не официальные тарифы или себестоимость RWB.")
elif page=="Запасы":
    p=products_df(); i=inventory_df()
    if p.empty: st.warning("Загрузите демоданные через боковую панель.")
    else: st.dataframe(p,use_container_width=True,hide_index=True); st.dataframe(i,use_container_width=True,hide_index=True)
elif page=="Маршрутизация":
    x,y=st.columns(2)
    with x:
        stock=st.checkbox("Товар доступен в Anchor",True); sla=st.checkbox("Mesh укладывается в допустимый срок",True)
        q=st.number_input("Заказов в партии",1,500,80); vol=st.number_input("Общий объём, л",1.0,5000.0,float(q*4))
    with y:
        trip=st.number_input("Стоимость рейса, ₽",0.0,100000.0,2400.0); cap=st.number_input("Лимит заказов",1,1000,130); vcap=st.number_input("Лимит объёма, л",1.0,10000.0,1500.0)
        hub=st.number_input("Hub, ₽/заказ (тестовая гипотеза)",0.0,10000.0,62.0)
    d=choose_route(stock,sla,int(q),float(vol),float(trip),int(cap),float(vcap),float(hub))
    st.metric("Решение",d["route"]); st.write(d["reason"]); st.caption(f"Mesh: {d['mesh_cost_per_order']:.2f} ₽/заказ; Hub: {hub:.2f} ₽/заказ.")
elif page=="Экономика":
    n=st.number_input("Заказов за период",1,100000,1200); share=st.slider("Доля Mesh, %",0,100,100,5)
    hub=st.number_input("Hub, ₽/заказ (гипотеза)",0.0,10000.0,62.0); trip=st.number_input("Рейс, ₽",0.0,100000.0,2400.0)
    extra=st.number_input("Прочие переменные Mesh, ₽/заказ",0.0,10000.0,14.5); smart=st.number_input("Smart, ₽/период (гипотеза)",0.0,1000000.0,10000.0)
    anchors=st.number_input("Количество Anchor",1,100,3); cap=st.number_input("Заказов на рейс",1,1000,130)
    m=round(n*share/100); h=n-m; trips=ceil(m/anchors/cap)*anchors if m else 0
    r=calculate_costs(int(n),m,h,float(hub),float(trip),int(trips),float(extra),float(smart))
    a,b,c=st.columns(3); a.metric("Hub baseline",f"{r['hub_baseline']:,.0f} ₽"); b.metric("Mesh + Hub",f"{r['combined_total']:,.0f} ₽"); c.metric("Эффект",f"{r['savings']:,.0f} ₽")
    st.write(f"Mesh-заказов: {m}; Hub-заказов: {h}; локальных рейсов: {trips}.")
    st.dataframe(pd.DataFrame([["Рейсы",r["trip_total"]],["Переменные Mesh",r["mesh_variable_total"]],["Hub",r["hub_total"]],["Smart (гипотеза)",smart],["Итого",r["combined_total"]]],columns=["Статья","Сумма, ₽"]),hide_index=True)
    st.warning("Упрощённый калькулятор: пополнение и хранение по SKU пока не включены. Результат не является доказанной экономией.")
elif page=="Сценарии":
    for title,desc,outcome in [
        ("Городской заказ","Товар есть в Anchor, партия набрана, SLA соблюдается.","Mesh при меньшей полной стоимости"),
        ("Межгород / Hub","Нет запаса или Mesh нарушает срок.","Hub"),
        ("Возврат","Проверить состояние товара и вернуть пригодный товар в доступный остаток.","Отдельный обратный процесс"),
        ("Ошибка логистики","Зафиксировать сбой передачи/рейса, не закрывать заказ как доставленный.","Инцидент и повторная маршрутизация"),
        ("Длительное хранение","Низкая оборачиваемость и риск залеживания.","Сократить пополнение/пересмотреть ассортимент")]:
        with st.expander(title): st.write(desc); st.caption("Ожидаемое поведение: "+outcome)
    st.caption("Сценарии описаны для демонстрации; статусы возврата и инцидентов будут автоматизированы следующими итерациями.")
else:
    st.subheader("Классификация исходных данных")
    st.markdown("""
    **Требования кейса:** выгода для маркетплейса, владельца Anchor, обычного ПВЗ, продавца и покупателя; полный цикл заказа; сценарии и масштабирование.
    
    **Решения проекта:** Streamlit, SQLite, Mesh = Anchor → обычный ПВЗ; Hub остаётся резервным/межрегиональным маршрутом.
    
    **Гипотезы:** 2 400 ₽/рейс; 130 заказов; 1 500 л; средний объём 4 л; ожидание до 5 ч; Hub 62 ₽/заказ; Smart 10 000 ₽/период. Последние два значения не подтверждены и являются только тестовыми вводными.
    """)
