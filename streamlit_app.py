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

# ============================ ДЖЕРЕЛА ============================
# Назва для селектбоксу -> схема в БД. Таблиця завжди "parts".
# Додаєш новий бренд -> просто новий рядок тут.
SOURCES = {
    "Toro":      "parsing_toro",
    "Husqvarna": "parsing_husqvarna",
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


def col_options(frame: pd.DataFrame, col: str):
    """Унікальні непорожні значення колонки (для фільтра). [] якщо колонки нема."""
    if col not in frame.columns:
        return []
    vals = frame[col].dropna().astype(str)
    vals = vals[vals.str.strip() != ""].unique().tolist()
    return sorted(vals)


def apply_filter(frame: pd.DataFrame, col: str, label: str):
    """Малює multiselect по колонці, якщо вона є і має значення. Повертає відфільтр."""
    opts = col_options(frame, col)
    if not opts:
        return frame
    sel = st.sidebar.multiselect(label, opts)
    if sel:
        return frame[frame[col].astype(str).isin(sel)]
    return frame


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


# ============================ САЙДБАР: ФІЛЬТРИ ============================
# Кожен фільтр самостійно перевіряє, чи є колонка у цієї таблиці.
# Перелік ширший, ніж колонки будь-якого одного бренду - зайве не намалюється.
st.sidebar.header("Фільтри")

f = df.copy()
f = apply_filter(f, "equipment_type", "Тип обладнання")
f = apply_filter(f, "series", "Серія")
f = apply_filter(f, "mower", "Косарка")
f = apply_filter(f, "modification", "Модифікація")   # є у Husqvarna, нема у Toro
f = apply_filter(f, "year", "Рік")                   # є у Husqvarna
f = apply_filter(f, "scheme_name", "Назва схеми")

# Пошук - по будь-яких з цих колонок, що присутні
st.sidebar.divider()
search = st.sidebar.text_input("Пошук (OEM / Description / Replaces)")
if search:
    s = search.strip().lower()
    search_cols = [c for c in ["oem", "description", "replaces"] if c in f.columns]
    if search_cols:
        mask = pd.Series(False, index=f.index)
        for c in search_cols:
            mask |= f[c].astype(str).str.lower().str.contains(s, na=False)
        f = f[mask]


# ============================ МЕТРИКИ ============================
# Малюємо тільки ті метрики, для яких є відповідна колонка.
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
# Бажаний порядок колонок; присутні - вперед, решта - як є.
preferred = [
    "brand", "equipment_type", "series", "mower",
    "modification", "year", "total_mods", "serial_numbers",
    "scheme_name", "ref_no", "oem", "description", "replaces",
]
ordered = [c for c in preferred if c in f.columns]
rest = [c for c in f.columns if c not in ordered]
show = f[ordered + rest]

st.dataframe(show, use_container_width=True, height=560, hide_index=True)


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
    # Excel .xlsx у память через BytesIO (без файлу на диску)
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        show.to_excel(writer, index=False, sheet_name=source[:31] or "Parts")
    st.download_button(
        "Завантажити Excel",
        data=buf.getvalue(),
        file_name=f"{source.lower()}_parts_filtered.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
