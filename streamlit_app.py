# -*- coding: utf-8 -*-
"""
Parts Dashboard (multi-brand)
Читає OEM-каталоги з PostgreSQL. Кожен бренд = окрема схема parsing_<бренд>,
таблиця parts. Перемикання джерела — селектбокс угорі сайдбару.

Дашборд НЕ хардкодить колонки: фільтри, пошук і метрики будуються тільки
по тих колонках, що реально є у вибраній таблиці. Тому різні бренди можуть
мати різний набір колонок (у Toro є replaces/serial_numbers, у Husqvarna —
модифікація/рік) — код підлаштовується.

Підключення — st.secrets["DATABASE_URL_TECH"]
(Streamlit Cloud -> App settings -> Secrets, формат TOML).
"""

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from st_aggrid import AgGrid, GridOptionsBuilder
from st_aggrid.shared import GridUpdateMode

# ============================ ДЖЕРЕЛА ============================
SOURCES = {
    "Toro":      "parsing_toro",
    "Husqvarna": "parsing_husqvarna",
    "Ariens":    "parsing_ariens",
}
TABLE = "parts"

st.set_page_config(page_title="Parts Dashboard", page_icon="tools", layout="wide")


# ============================ ПІДКЛЮЧЕННЯ ДО БД ============================
@st.cache_resource
def get_engine():
    url = st.secrets.get("DATABASE_URL_TECH", "")
    if not url:
        st.error(
            "Немає DATABASE_URL_TECH у Secrets. Додай у App settings -> Secrets:\n\n"
            'DATABASE_URL_TECH = "postgresql://user:pass@host:5432/db?sslmode=require"'
        )
        st.stop()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=300)
def table_exists(schema: str) -> bool:
    eng = get_engine()
    q = text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :table
        )
    """)
    with eng.connect() as conn:
        return bool(conn.execute(q, {"schema": schema, "table": TABLE}).scalar())


@st.cache_data(ttl=300)
def load_data(schema: str) -> pd.DataFrame:
    """Тягне всю таблицю обраної схеми. Кеш - окремий на кожну схему."""
    eng = get_engine()
    return pd.read_sql(f'SELECT * FROM {schema}.{TABLE}', eng)


# ============================ УМНИЙ ФІЛЬТР (Офіційний патерн Streamlit) ============================
def filter_dataframe(df: pd.DataFrame, modify: bool) -> pd.DataFrame:
    """
    Додає UI для фільтрації всіх колонок DataFrame.
    Автоматично підбирає тип віджета:
    - Категорії (до 50 унікальних) -> Multiselect
    - Категорії (більше 50) -> Текстовий пошук (Contains)
    - Числа -> Slider (від мінімума до максимума)
    - Дати -> Date picker
    """
    if not modify:
        return df

    df = df.copy()
    df = df.convert_dtypes()

    modification_container = st.container()

    with modification_container:
        to_filter_columns = st.multiselect("Фільтрувати по колонках", df.columns)
        
        for column in to_filter_columns:
            left, right = st.columns((1, 20))
            left.write("↳")

            # Текстові колонки та категорії
            if pd.api.types.is_string_dtype(df[column]) or df[column].dtype == "object":
                # Якщо унікальних значень небагато - даємо мультиселект
                if df[column].nunique() <= 50:
                    user_cat_input = right.multiselect(
                        f"Значення для {column}",
                        df[column].unique(),
                        default=list(df[column].unique()),
                    )
                    df = df[df[column].isin(user_cat_input)]
                # Якщо значень дуже багато (напр. довгі описи чи OEM) - даємо текстовий пошук
                else:
                    user_text_input = right.text_input(
                        f"Пошук по {column}", key=f"text_{column}"
                    )
                    if user_text_input:
                        df = df[df[column].astype(str).str.contains(user_text_input, case=False, na=False)]
            
            # Числові колонки
            elif pd.api.types.is_numeric_dtype(df[column]):
                _min = float(df[column].min())
                _max = float(df[column].max())
                step = (_max - _min) / 100
                
                user_num_input = right.slider(
                    f"Діапазон для {column}",
                    min_value=_min,
                    max_value=_max,
                    value=(_min, _max),
                    step=step,
                )
                df = df[df[column].between(user_num_input[0], user_num_input[1])]
            
            # Дати
            elif pd.api.types.is_datetime64_any_dtype(df[column]):
                user_date_input = right.date_input(
                    f"Діапазон для {column}",
                    value=(df[column].min(), df[column].max()),
                )
                if len(user_date_input) == 2:
                    user_date_input = tuple(map(pd.to_datetime, user_date_input))
                    start_date, end_date = user_date_input
                    df = df[df[column].between(start_date, end_date)]

    return df


# ============================ САЙДБАР: ДЖЕРЕЛО ============================
st.sidebar.header("Джерело")
source = st.sidebar.selectbox("Бренд / сайт", list(SOURCES.keys()))
schema = SOURCES[source]

if st.sidebar.button("Оновити дані з БД"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()


# ============================ ЗАГОЛОВОК ============================
st.title(f"{source} Parts")
st.caption(f"Джерело: {schema}.{TABLE} (PostgreSQL)")

if not table_exists(schema):
    st.warning(
        f"Таблиця {schema}.{TABLE} ще не існує. "
        "Парсер на сервері її створить і наповнить - потім натисни «Оновити дані з БД»."
    )
    st.stop()

df = load_data(schema)

if df.empty:
    st.info(f"Таблиця {schema}.{TABLE} існує, але поки порожня - парсер ще не залив дані.")
    st.stop()


# ============================ ЗАСТОСУВАННЯ ФІЛЬТРІВ ============================
st.subheader("Налаштування відображення")
modify = st.checkbox("Додати фільтри", key=f"add_filters_{source}")
f = filter_dataframe(df, modify)


# ============================ МЕТРИКИ ============================
metric_specs = []
metric_specs.append(("Рядків", f"{len(f):,}"))
if "mower" in f.columns:
    metric_specs.append(("Косарок", f["mower"].nunique()))
if "oem" in f.columns:
    nonempty = f[f["oem"].astype(str).str.strip() != ""]
    metric_specs.append(("Унікальних OEM", nonempty["oem"].nunique()))
if "scheme_name" in f.columns:
    metric_specs.append(("Схем", f["scheme_name"].nunique()))

cols = st.columns(len(metric_specs))
for col_box, (lbl, val) in zip(cols, metric_specs):
    col_box.metric(lbl, val)


# ============================ ТАБЛИЦЯ ============================
preferred = [
    "brand", "equipment_type", "series", "mower",
    "modification", "year", "total_mods", "serial_numbers",
    "scheme_name", "ref_no", "oem", "description", "replaces",
]
ordered = [c for c in preferred if c in f.columns]
show = f[ordered]

COL_CFG = {
    "brand":          ("Бренд", 90),
    "equipment_type": ("Тип обладнання", 170),
    "series":         ("Серія", 170),
    "mower":          ("Косарка", 110),
    "modification":   ("Модифікація", 150),
    "year":           ("Рік", 90),
    "total_mods":     ("К-сть модиф.", 110),
    "serial_numbers": ("Серійні номери", 160),
    "scheme_name":    ("Назва схеми", 240),
    "ref_no":         ("Ref", 80),
    "oem":            ("OEM", 130),
    "description":    ("Опис", 320),
    "replaces":       ("Replaces", 130),
}

gb = GridOptionsBuilder.from_dataframe(show)
gb.configure_default_column(
    filter="agTextColumnFilter",
    filterParams={
        "buttons": ["reset", "apply", "clear"],
        "closeOnApply": True,
        "debounceMs": 150,
    },
    floatingFilter=True,
    sortable=True,
    resizable=True,
    menuTabs=["filterMenuTab", "generalMenuTab"],
    wrapText=False,
    autoHeight=False,
)

for col in show.columns:
    label, width = COL_CFG.get(col, (col, 140))
    gb.configure_column(col, header_name=label, width=width)

gb.configure_grid_options(
    domLayout="normal",
    enableCellTextSelection=True,
    suppressMenuHide=True,
)
grid_options = gb.build()

AgGrid(
    show,
    gridOptions=grid_options,
    height=560,
    theme="streamlit",
    fit_columns_on_grid_load=False,
    allow_unsafe_jscode=True,
    update_mode=GridUpdateMode.NO_UPDATE,
    key=f"grid_{source}",
)


# ============================ ЕКСПОРТ ============================
col_csv, col_xlsx = st.columns([1, 1])

with col_csv:
    csv = show.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Завантажити CSV",
        data=csv,
        file_name=f"{source.lower()}_parts_filtered.csv",
        mime="text/csv",
        use_container_width=True,
    )

with col_xlsx:
    import io
    safe = show.copy()
    for c in safe.columns:
        col = safe[c].astype(str)
        col = col.replace(
            {"inf": "", "-inf": "", "nan": "", "NaN": "", "None": "", "NaT": ""}
        )
        safe[c] = col.str.slice(0, 32000)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        safe.to_excel(writer, index=False, sheet_name=(source[:31] or "Parts"))
    st.download_button(
        "Завантажити Excel",
        data=buf.getvalue(),
        file_name=f"{source.lower()}_parts_filtered.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


# ============================ ПОШУК OEM: ДЕ ВСТРІЧАЄТЬСЯ ============================
st.divider()
st.subheader("🔎 Пошук запчастини за OEM")

oem_q = st.text_input(
    "Введи OEM-номер (повний або частину)",
    placeholder="напр. 132-0384",
    key="oem_lookup",
)

if oem_q and "oem" in df.columns:
    q = oem_q.strip().lower()
    hit = df[df["oem"].astype(str).str.lower().str.contains(q, na=False)]
    if hit.empty:
        st.info(f"OEM, що містить «{oem_q}», у каталозі не знайдено.")
    else:
        uniq_oem = hit["oem"].nunique()
        st.caption(f"Знайдено збігів: {len(hit)} рядків, {uniq_oem} унікальних OEM")

        cols_show = [c for c in
                     ["oem", "description", "mower", "serial_numbers",
                      "scheme_name", "ref_no", "replaces"]
                     if c in hit.columns]
        st.markdown("**Де встрічається:**")
        st.dataframe(hit[cols_show], use_container_width=True,
                     hide_index=True, height=260)

        if "replaces" in hit.columns:
            repl = hit[hit["replaces"].astype(str).str.strip() != ""]
            if not repl.empty:
                pairs = (repl[["oem", "replaces"]]
                         .drop_duplicates()
                         .sort_values("oem"))
                st.markdown("**Заміни (нова деталь ← стара):**")
                for _, r in pairs.iterrows():
                    st.markdown(f"- `{r['oem']}` заміняє `{r['replaces']}`")
            else:
                st.caption("Для знайдених деталей замін (Replaces) немає.")


# ============================ ГРАФІКИ ============================
st.divider()
st.subheader("📊 Огляд каталогу")

g1, g2 = st.columns(2)

with g1:
    if "scheme_name" in f.columns and not f.empty:
        top_sch = (f.groupby("scheme_name").size()
                   .sort_values(ascending=False).head(10))
        if not top_sch.empty:
            st.markdown("**Топ схем за числом деталей**")
            st.bar_chart(top_sch, horizontal=True, height=320)

with g2:
    if "mower" in f.columns and "oem" in f.columns and not f.empty:
        per_mower = (f[f["oem"].astype(str).str.strip() != ""]
                     .groupby("mower")["oem"].nunique()
                     .sort_values(ascending=False).head(15))
        if not per_mower.empty:
            st.markdown("**Унікальних OEM по косарках**")
            st.bar_chart(per_mower, horizontal=True, height=320)

if "replaces" in f.columns and "oem" in f.columns and not f.empty:
    base = f[f["oem"].astype(str).str.strip() != ""]
    total_oem = base["oem"].nunique()
    with_repl = base[base["replaces"].astype(str).str.strip() != ""]["oem"].nunique()
    if total_oem:
        pct = with_repl / total_oem * 100
        st.markdown("**Деталі із замінами (Replaces)**")
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Усього OEM", f"{total_oem:,}")
        cc2.metric("Із замінами", f"{with_repl:,}")
        cc3.metric("Частка", f"{pct:.1f}%")
