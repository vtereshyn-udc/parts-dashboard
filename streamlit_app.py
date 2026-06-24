# -- coding: utf-8 --
"""
Parts Dashboard (multi-brand) - Версія з Mito Sheets (Безкоштовні Excel-фільтри)
"""
import io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from mitosheet.streamlit.v1 import spreadsheet

SOURCES = {
    "Toro":      "parsing_toro",
    "Husqvarna": "parsing_husqvarna",
    "Ariens":    "parsing_ariens",
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

# ============================ ШВИДКІ ФІЛЬТРИ (з «Обрати всі / Зняти всі») ============================
# Над таблицею Mito. Потрібні саме для тисяч значень: щоб не клікати галочки
# по одній, а швидко обрати/зняти все або вибрати кілька значень списком.
st.subheader("🔎 Швидкі фільтри")
st.caption("Звужують дані ще ДО таблиці. Зручно, коли значень тисячі. "
           "Кнопка «Обрати всі / Зняти всі» — для кожного фільтра.")

f = df.copy()

# колонки, по яких має сенс фільтрувати списком (наявні в цього бренду)
FILTER_LABELS = {
    "equipment_type": "Тип обладнання",
    "series": "Серія",
    "mower": "Косарка",
    "modification": "Модифікація",
    "year": "Рік",
    "scheme_name": "Назва схеми",
}
filter_cols = [c for c in FILTER_LABELS if c in f.columns]


def col_values(frame, col):
    v = frame[col].dropna().astype(str)
    v = v[v.str.strip() != ""]
    return sorted(v.unique().tolist())


with st.expander("Відкрити швидкі фільтри", expanded=False):
    boxes = st.columns(min(3, len(filter_cols)) or 1)
    selections = {}
    for i, col in enumerate(filter_cols):
        box = boxes[i % len(boxes)]
        opts = col_values(f, col)
        label = FILTER_LABELS[col]
        # стан вибору тримаємо в session_state, щоб кнопки select-all/clear працювали
        sel_key = f"selvals_{source}_{col}"
        if sel_key not in st.session_state:
            st.session_state[sel_key] = []  # порожньо = фільтр не застосований (усі)

        with box:
            st.markdown(f"**{label}**  ·  значень: {len(opts)}")
            bc1, bc2 = st.columns(2)
            if bc1.button("Обрати всі", key=f"all_{source}_{col}", use_container_width=True):
                st.session_state[sel_key] = opts
            if bc2.button("Зняти всі", key=f"clr_{source}_{col}", use_container_width=True):
                st.session_state[sel_key] = []
            chosen = st.multiselect(
                label, opts,
                default=st.session_state[sel_key],
                key=f"ms_{source}_{col}",
                label_visibility="collapsed",
            )
            st.session_state[sel_key] = chosen
            if chosen:
                selections[col] = chosen

    # застосовуємо вибране (порожній фільтр = не звужуємо)
    for col, vals in selections.items():
        f = f[f[col].astype(str).isin(vals)]

    txt = st.text_input("Текстовий пошук (OEM / Опис / Replaces)", key=f"qsearch_{source}")
    if txt:
        s = txt.strip().lower()
        scols = [c for c in ["oem", "description", "replaces"] if c in f.columns]
        if scols:
            mask = pd.Series(False, index=f.index)
            for c in scols:
                mask |= f[c].astype(str).str.lower().str.contains(s, na=False)
            f = f[mask]

st.caption(f"Після швидких фільтрів рядків: {len(f):,} із {len(df):,}")

# --- Метрики (рахуються по відфільтрованому f, реагують на швидкі фільтри) ---
metric_specs = [("Рядків", f"{len(f):,}")]
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

# --- Підготовка даних для відображення: ВСІ стовпці, крім службових і ПОРОЖНІХ ---
HIDE_TECH = {"id", "scraped_at"}


def is_empty_col(frame, col):
    """Колонка вважається порожньою, якщо всі значення NaN або порожній рядок."""
    s = frame[col]
    if s.dropna().empty:
        return True
    nonblank = s.dropna().astype(str).str.strip()
    return (nonblank == "").all()


# мертві/дублюючі колонки зі старих версій парсера (порожні) — ховаємо
empty_cols = {c for c in f.columns if is_empty_col(f, c)}

preferred = [
    "brand", "equipment_type", "series", "mower",
    "modification", "year", "total_mods", "serial_numbers",
    "scheme_name", "ref_no", "oem", "description", "replaces", "scheme_url",
]
drop = HIDE_TECH | empty_cols
ordered = [c for c in preferred if c in f.columns and c not in drop]
rest = [c for c in f.columns if c not in ordered and c not in drop]
show = f[ordered + rest].copy()

# Людські назви колонок (Mito показує технічні назви, тож перейменовуємо)
COL_CFG = {
    "brand": "Бренд",
    "equipment_type": "Тип обладнання",
    "series": "Серія",
    "mower": "Косарка",
    "modification": "Модифікація",
    "year": "Рік",
    "total_mods": "К-сть модиф.",
    "serial_numbers": "Серійні номери",
    "scheme_name": "Назва схеми",
    "ref_no": "Ref",
    "oem": "OEM",
    "description": "Опис",
    "replaces": "Replaces",
    "scheme_url": "URL схеми",
}
show = show.rename(columns={k: v for k, v in COL_CFG.items() if k in show.columns})

# --- Відображення інтерактивної таблиці Mito ---
st.subheader("📋 Каталог деталей")
st.caption("💡 У заголовку колонки — воронка (фільтр за значеннями) і вкладка "
           "Filter/Sort (фільтр за умовою — зручно для тисяч значень). "
           "Швидкі фільтри вище звужують дані ще до таблиці.")

final_dfs, edit_history = spreadsheet(show)

if final_dfs and list(final_dfs.keys()):
    first_key = list(final_dfs.keys())[0]
    filtered_show = final_dfs[first_key]
else:
    filtered_show = show

# --- Кнопки завантаження ---
st.write("")
col_csv, col_xlsx = st.columns([1, 1])
with col_csv:
    csv = filtered_show.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Завантажити відфільтрований CSV",
        data=csv,
        file_name=f"{source.lower()}_parts.csv",
        mime="text/csv",
        use_container_width=True,
    )

with col_xlsx:
    safe = filtered_show.copy()
    for c in safe.columns:
        col = safe[c].astype(str)
        col = col.replace({"inf": "", "-inf": "", "nan": "", "NaN": "", "None": "", "NaT": ""})
        safe[c] = col.str.slice(0, 32000)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        safe.to_excel(writer, index=False, sheet_name=(source[:31] or "Parts"))

    st.download_button(
        "Завантажити відфільтрований Excel",
        data=buf.getvalue(),
        file_name=f"{source.lower()}_parts.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.divider()

# --- Секція швидкого окремого пошуку по OEM ---
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
