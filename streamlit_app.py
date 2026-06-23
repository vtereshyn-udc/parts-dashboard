# -- coding: utf-8 --
"""
Parts Dashboard (multi-brand) - Оновлена версія з вбудованими фільтрами в таблиці
"""
import io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

SOURCES = {
    "Toro": "parsing_toro",
    "Husqvarna": "parsing_husqvarna",
    "Ariens": "parsing_ariens",
}
TABLE = "parts"

st.set_page_config(page_title="Parts Dashboard", page_icon="🛠️", layout="wide")

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
    eng = get_engine()
    return pd.read_sql(f'SELECT * FROM {schema}.{TABLE}', eng)


# --- Сайдбар ---
st.sidebar.header("Джерело")
source = st.sidebar.selectbox("Бренд / сайт", list(SOURCES.keys()))
schema = SOURCES[source]

if st.sidebar.button("Оновити дані з БД"):
    st.cache_data.clear()
    st.sidebar.success("Кеш очищено!")
    st.rerun()

st.sidebar.divider()

# --- Головний екран ---
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

# --- Метрики базового датафрейму ---
metric_specs = [("Рядків усього", f"{len(df):,}")]
if "mower" in df.columns:
    metric_specs.append(("Косарок", df["mower"].nunique()))
if "oem" in df.columns:
    nonempty = df[df["oem"].astype(str).str.strip() != ""]
    metric_specs.append(("Унікальних OEM", nonempty["oem"].nunique()))
if "scheme_name" in df.columns:
    metric_specs.append(("Схем", df["scheme_name"].nunique()))

cols = st.columns(len(metric_specs))
for col_box, (lbl, val) in zip(cols, metric_specs):
    col_box.metric(lbl, val)

# --- Підготовка даних для відображення ---
preferred = [
    "brand", "equipment_type", "series", "mower",
    "modification", "year", "total_mods", "serial_numbers",
    "scheme_name", "ref_no", "oem", "description", "replaces",
]
ordered = [c for c in preferred if c in df.columns]
show = df[ordered]

COL_CFG = {
    "brand": ("Бренд", 90),
    "equipment_type": ("Тип обладнання", 170),
    "series": ("Серія", 170),
    "mower": ("Косарка", 110),
    "modification": ("Модифікація", 150),
    "year": ("Рік", 90),
    "total_mods": ("К-сть модиф.", 110),
    "serial_numbers": ("Серійні номери", 160),
    "scheme_name": ("Назва схеми", 240),
    "ref_no": ("Ref", 80),
    "oem": ("OEM", 130),
    "description": ("Опис", 320),
    "replaces": ("Replaces", 130),
}

# --- Конфігурація AG Grid із вбудованими фільтрами ---
gb = GridOptionsBuilder.from_dataframe(show)

gb.configure_default_column(
    sortable=True,
    resizable=True,
    filterable=True,              # Вмикаємо фільтрацію для всіх колонок
    filter="agTextColumnFilter",  # За замовчуванням текстовий фільтр (Пошук/Містить)
    menuTabs=["filterMenuTab", "generalMenuTab"], # Додаємо вкладку фільтра в меню заголовка
    wrapText=False,
    autoHeight=False,
)

# Індивідуальне налаштування колонок
for col in show.columns:
    label, width = COL_CFG.get(col, (col, 140))
    
    # Для числових колонок робимо числовий фільтр замість текстового
    if col in ["year", "total_mods", "ref_no"]:
        gb.configure_column(col, header_name=label, width=width, filter="agNumberColumnFilter")
    else:
        gb.configure_column(col, header_name=label, width=width)

gb.configure_grid_options(
    domLayout="normal",
    enableCellTextSelection=True,
    suppressMenuHide=False,  # Змінено на False, щоб іконка меню з'являлася при наведенні мишки
)
grid_options = gb.build()

# Відображення таблиці
st.subheader("📋 Каталог деталей")
st.caption("💡 Наведіть на заголовок будь-якої колонки та натисніть на іконку меню (або лійки) для детального пошуку.")

AgGrid(
    show,
    gridOptions=grid_options,
    height=560,
    theme="streamlit",
    update_mode=GridUpdateMode.NO_UPDATE,
    key=f"grid_{source}",
)

# --- Кнопки завантаження даних ---
col_csv, col_xlsx = st.columns([1, 1])
with col_csv:
    csv = show.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Завантажити CSV",
        data=csv,
        file_name=f"{source.lower()}_parts.csv",
        mime="text/csv",
        use_container_width=True,
    )

with col_xlsx:
    safe = show.copy()
    for c in safe.columns:
        col = safe[c].astype(str)
        col = col.replace({"inf": "", "-inf": "", "nan": "", "NaN": "", "None": "", "NaT": ""})
        safe[c] = col.str.slice(0, 32000)
    
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        safe.to_excel(writer, index=False, sheet_name=(source[:31] or "Parts"))
    
    st.download_button(
        "Завантажити Excel",
        data=buf.getvalue(),
        file_name=f"{source.lower()}_parts.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.divider()

# --- Секція швидкого пошуку по OEM ---
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
        
        cols_show = [c for c in ["oem", "description", "mower", "serial_numbers", "scheme_name", "ref_no", "replaces"] if c in hit.columns]
        
        st.markdown("**Де зустрічається:**")
        st.dataframe(hit[cols_show], use_container_width=True, hide_index=True, height=260)
        
        if "replaces" in hit.columns:
            repl = hit[hit["replaces"].astype(str).str.strip() != ""]
            if not repl.empty:
                pairs = repl[["oem", "replaces"]].drop_duplicates().sort_values("oem")
                st.markdown("**Заміни (нова деталь ← стара):**")
                for _, r in pairs.iterrows():
                    st.markdown(f"- `{r['oem']}` заміняє `{r['replaces']}`")
            else:
                st.caption("Для знайдених деталей замін (Replaces) немає.")

st.divider()

# --- Графіки та Аналітика ---
st.subheader("📊 Огляд каталогу")
g1, g2 = st.columns(2)

with g1:
    if "scheme_name" in df.columns and not df.empty:
        top_sch = df.groupby("scheme_name").size().sort_values(ascending=False).head(10)
        if not top_sch.empty:
            st.markdown("Топ-10 схем за кількістю деталей")
            st.bar_chart(top_sch, horizontal=True, height=320)

with g2:
    if "mower" in df.columns and "oem" in df.columns and not df.empty:
        per_mower = df[df["oem"].astype(str).str.strip() != ""].groupby("mower")["oem"].nunique().sort_values(ascending=False).head(15)
        if not per_mower.empty:
            st.markdown("Унікальних OEM по косарках (Топ-15)")
            st.bar_chart(per_mower, horizontal=True, height=320)

if "replaces" in df.columns and "oem" in df.columns and not df.empty:
    base = df[df["oem"].astype(str).str.strip() != ""]
    total_oem = base["oem"].nunique()
    with_repl = base[base["replaces"].astype(str).str.strip() != ""]["oem"].nunique()
    
    if total_oem:
        pct = with_repl / total_oem * 100
        st.markdown("### Деталі із замінами (Replaces)")
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Усього OEM", f"{total_oem:,}")
        cc2.metric("Із замінами", f"{with_repl:,}")
        cc3.metric("Частка", f"{pct:.1f}%")
