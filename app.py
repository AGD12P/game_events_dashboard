import json
import pandas as pd
import streamlit as st
import altair as alt
from supabase import create_client

st.set_page_config(
    page_title="Game Analytics",
    page_icon="🎮",
    layout="wide",
)

st.title("🎮 Game Analytics")
st.caption("Estadísticas de partidas · Supabase")

# --------------------------------------------------
# CONEXIÓN
# Configura estos valores en Streamlit Secrets.
# Utiliza una clave publicable/anon con permisos
# de solo lectura. Nunca uses service_role.
# --------------------------------------------------

@st.cache_resource
def get_client():
    return create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["key"],
    )


@st.cache_data(ttl=60)
def load_events():
    client = get_client()
    records = []
    page_size = 1000
    start = 0

    while True:
        response = (
            client.table("game_events")
            .select("id, created_at, event, game_id, data, event_at")
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )

        batch = response.data or []
        records.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return pd.DataFrame(records)


def parse_json(value):
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    return {}


try:
    raw = load_events()
except Exception as exc:
    st.error("No se pudieron cargar los eventos de Supabase.")
    st.caption(
        "Comprueba los secretos, los permisos de lectura "
        "y que la tabla esté en el esquema public."
    )
    st.exception(exc)
    st.stop()

if raw.empty:
    st.info("Todavía no hay eventos registrados.")
    st.stop()

# --------------------------------------------------
# PREPARACIÓN DE DATOS
# --------------------------------------------------

raw["data_parsed"] = raw["data"].apply(parse_json)
raw["event"] = raw["event"].fillna("").astype(str)
raw["game_id"] = raw["game_id"].astype("string")

raw["event_at"] = pd.to_datetime(
    raw["event_at"], errors="coerce", utc=True
)

raw["created_at"] = pd.to_datetime(
    raw["created_at"], errors="coerce", utc=True
)

def extract(event_name, key):
    return raw.apply(
        lambda row: (
            row["data_parsed"].get(key)
            if row["event"] == event_name
            else None
        ),
        axis=1,
    )

raw["result"] = extract("game_end", "result")
raw["dungeon"] = extract("dungeon_selected", "dungeon")
raw["power_selected"] = extract("power_selected", "selected")
raw["powers_offered"] = extract("power_selected", "offered")

# Convertir listas de poderes a listas válidas
raw["powers_offered"] = raw["powers_offered"].apply(
    lambda value: value if isinstance(value, list) else []
)

# Normalizar textos de resultado
def normalize_result(value):
    if value is None:
        return "Desconocido"

    value = str(value).strip().lower()

    if value in {"victoria", "victory", "win", "won"}:
        return "Victoria"

    if value in {"derrota", "defeat", "defeat", "loss", "lose", "lost"}:
        return "Derrota"

    return str(value).strip().capitalize() or "Desconocido"


raw["result_normalized"] = raw["result"].apply(normalize_result)

# --------------------------------------------------
# CONSTRUIR UNA FILA POR PARTIDA
# --------------------------------------------------

games = []

for game_id, group in raw.dropna(subset=["game_id"]).groupby("game_id"):
    group = group.sort_values("event_at", na_position="last")

    dates = group["event_at"].dropna()
    dungeons = group.loc[
        group["event"] == "dungeon_selected", "dungeon"
    ].dropna()

    results = group.loc[
        group["event"] == "game_end", "result_normalized"
    ]

    games.append({
        "game_id": str(game_id),
        "game_date": dates.min() if not dates.empty else pd.NaT,
        "dungeon": dungeons.iloc[0] if not dungeons.empty else "Sin dato",
        "result": results.iloc[-1] if not results.empty else "Sin concluir",
        "event_count": len(group),
        "power_count": int((group["event"] == "power_selected").sum()),
    })

games_df = pd.DataFrame(games)

if games_df.empty:
    st.warning("No se encontraron partidas con game_id.")
    st.stop()

# --------------------------------------------------
# FILTROS
# --------------------------------------------------

st.sidebar.header("Filtros")

valid_dates = games_df["game_date"].dropna()

if not valid_dates.empty:
    min_date = valid_dates.min().date()
    max_date = valid_dates.max().date()

    date_range = st.sidebar.date_input(
        "Fecha de la partida (UTC)",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        start_date, end_date = date_range
        game_dates = games_df["game_date"].dt.date

        games_df = games_df[
            game_dates.between(start_date, end_date)
        ]

dungeon_options = sorted(
    games_df["dungeon"].dropna().unique().tolist()
)

selected_dungeons = st.sidebar.multiselect(
    "Mazmorra",
    dungeon_options,
    default=dungeon_options,
)

result_options = sorted(games_df["result"].dropna().unique().tolist())

selected_results = st.sidebar.multiselect(
    "Resultado",
    result_options,
    default=result_options,
)

games_df = games_df[
    games_df["dungeon"].isin(selected_dungeons)
    & games_df["result"].isin(selected_results)
]

# Todos los eventos de las partidas seleccionadas
selected_ids = set(games_df["game_id"])
events = raw[
    raw["game_id"].astype("string").isin(selected_ids)
].copy()

# --------------------------------------------------
# KPIs
# --------------------------------------------------

st.subheader("Resumen")

total_games = len(games_df)
completed = games_df[
    games_df["result"].isin(["Victoria", "Derrota"])
]
wins = int((completed["result"] == "Victoria").sum())
losses = int((completed["result"] == "Derrota").sum())

win_rate = wins / len(completed) * 100 if len(completed) else 0
total_events = len(events)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Partidas", f"{total_games:,}")
c2.metric("Victorias", f"{wins:,}")
c3.metric("Derrotas", f"{losses:,}")
c4.metric("Win rate", f"{win_rate:.1f}%")

c5, c6, c7 = st.columns(3)
c5.metric("Partidas finalizadas", f"{len(completed):,}")
c6.metric("Eventos", f"{total_events:,}")
c7.metric(
    "Eventos por partida",
    f"{events.groupby('game_id').size().mean():.1f}"
    if not events.empty else "0"
)

st.divider()

# --------------------------------------------------
# RESULTADOS
# --------------------------------------------------

left, right = st.columns(2)

with left:
    st.subheader("Resultados")

    result_counts = (
        games_df["result"]
        .value_counts()
        .rename_axis("Resultado")
        .reset_index(name="Partidas")
    )

    if not result_counts.empty:
        chart = alt.Chart(result_counts).mark_bar().encode(
            x=alt.X("Resultado:N", title=None),
            y=alt.Y("Partidas:Q", title="Partidas"),
            color=alt.Color("Resultado:N", legend=None),
            tooltip=["Resultado", "Partidas"],
        )
        st.altair_chart(chart, use_container_width=True)

with right:
    st.subheader("Actividad por fecha")

    daily = games_df.dropna(subset=["game_date"]).copy()

    if not daily.empty:
        daily["Fecha"] = daily["game_date"].dt.strftime("%Y-%m-%d")

        daily_chart_data = (
            daily.groupby(["Fecha", "result"])
            .size()
            .reset_index(name="Partidas")
        )

        chart = alt.Chart(daily_chart_data).mark_bar().encode(
            x=alt.X("Fecha:N", title="Fecha", sort=None),
            y=alt.Y("Partidas:Q", title="Partidas"),
            color=alt.Color("result:N", title="Resultado"),
            tooltip=["Fecha", "result", "Partidas"],
        )
        st.altair_chart(chart, use_container_width=True)

# --------------------------------------------------
# PODERES
# --------------------------------------------------

st.divider()
st.subheader("Análisis de poderes")

power_events = events[events["event"] == "power_selected"].copy()

if power_events.empty:
    st.info("No hay selecciones de poderes para estos filtros.")
else:
    selected_counts = (
        power_events["power_selected"]
        .dropna()
        .astype(str)
        .value_counts()
        .rename_axis("Poder seleccionado")
        .reset_index(name="Selecciones")
    )

    offered_counts = {}

    for offered in power_events["powers_offered"]:
        for power in offered:
            if power is not None:
                label = str(power)
                offered_counts[label] = offered_counts.get(label, 0) + 1

    offered_df = pd.DataFrame(
        [
            {"Poder ofrecido": power, "Veces ofrecido": count}
            for power, count in offered_counts.items()
        ]
    )

    p1, p2 = st.columns(2)

    with p1:
        st.markdown("**Poderes seleccionados**")
        if not selected_counts.empty:
            st.bar_chart(
                selected_counts.head(15).set_index("Poder seleccionado")
            )

    with p2:
        st.markdown("**Poderes ofrecidos**")
        if not offered_df.empty:
            offered_df = offered_df.sort_values(
                "Veces ofrecido", ascending=False
            )
            st.bar_chart(
                offered_df.head(15).set_index("Poder ofrecido")
            )

    with st.expander("Ver tablas de poderes"):
        st.markdown("**Selecciones**")
        st.dataframe(selected_counts, use_container_width=True, hide_index=True)

        st.markdown("**Opciones ofrecidas**")
        st.dataframe(offered_df, use_container_width=True, hide_index=True)

# --------------------------------------------------
# EXPLORADOR DE PARTIDAS
# --------------------------------------------------

st.divider()
st.subheader("Explorador de partidas")

if not games_df.empty:
    game_options = games_df["game_id"].tolist()

    selected_game = st.selectbox(
        "Selecciona una partida",
        game_options,
    )

    game_events = events[
        events["game_id"].astype("string") == selected_game
    ].sort_values("event_at", na_position="last")

    columns_to_show = [
        "event_at", "event", "data", "id"
    ]

    st.dataframe(
        game_events[columns_to_show],
        use_container_width=True,
        hide_index=True,
    )

# --------------------------------------------------
# DESCARGA Y ACTUALIZACIÓN
# --------------------------------------------------

st.divider()

d1, d2 = st.columns(2)

with d1:
    st.download_button(
        "Descargar partidas CSV",
        data=games_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="game_statistics.csv",
        mime="text/csv",
    )

with d2:
    if st.button("🔄 Actualizar datos"):
        load_events.clear()
        st.rerun()

st.caption(
    "Las fechas se muestran en UTC. "
    "Los datos se consultan en modo lectura."
)
