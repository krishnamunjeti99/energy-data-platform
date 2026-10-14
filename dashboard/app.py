"""
Energy generation dashboard: reads the gold layer built by dbt.

    docker compose up -d dashboard      ->  http://localhost:8501

Everything shown here comes from gold.gold_daily_generation, so the dashboard
contains no business logic of its own: the numbers are defined once, in dbt.
"""

import os

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

st.set_page_config(page_title="Energy Generation", layout="wide")

# Colours follow the station, never its rank, so filtering never repaints a line.
# Three slots from a colour-blind-validated categorical palette, light and dark.
PALETTE = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a"],
    "dark": ["#3987e5", "#d95926", "#199e70"],
}
INK = {
    "light": {"primary": "#0b0b0b", "secondary": "#52514e", "grid": "#e6e5e1"},
    "dark": {"primary": "#f0efec", "secondary": "#c3c2b7", "grid": "#383835"},
}


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

def connect():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "energy"),
        password=os.getenv("POSTGRES_PASSWORD", "energy_pw"),
        dbname=os.getenv("POSTGRES_DB", "energy"),
    )


@st.cache_data(ttl=60)
def load_gold() -> pd.DataFrame:
    sql = """
        select reading_date, station_id, station_name, source,
               generation_kwh, auxiliary_kwh, net_export_kwh, aux_pct,
               capacity_factor_pct, reads_expected, reads_received,
               completeness_pct, gap_fills
        from gold.gold_daily_generation
        order by reading_date, station_id
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)

    numeric = ["generation_kwh", "auxiliary_kwh", "net_export_kwh", "aux_pct",
               "capacity_factor_pct", "completeness_pct"]
    df[numeric] = df[numeric].astype(float)
    df["reading_date"] = pd.to_datetime(df["reading_date"])
    df["generation_mwh"] = df["generation_kwh"] / 1000
    return df


try:
    gold = load_gold()
except psycopg.errors.UndefinedTable:
    st.warning("The gold table doesn't exist yet. Run `docker compose run --rm dbt build` first.")
    st.stop()
except psycopg.OperationalError as e:
    st.error(f"Can't reach the database: {e}")
    st.stop()

if gold.empty:
    st.info("The gold table is empty. Load data with the generator, then run dbt.")
    st.stop()

mode = "dark" if st.context.theme.type == "dark" else "light"
ink = INK[mode]

# Station order (by id) fixes each station's colour for the whole session
stations = (gold[["station_id", "station_name"]].drop_duplicates()
            .sort_values("station_id")["station_name"].tolist())
color_scale = alt.Scale(domain=stations, range=PALETTE[mode][: len(stations)])


# --------------------------------------------------------------------------- #
# Header and filters (one row, above everything they control)
# --------------------------------------------------------------------------- #

st.title("Energy generation")
st.caption("Daily energy balance per station, from the gold layer "
           "(`gold.gold_daily_generation`).")

min_d, max_d = gold["reading_date"].min().date(), gold["reading_date"].max().date()
f1, f2 = st.columns([1, 2])
with f1:
    picked = st.date_input("Date range", value=(min_d, max_d),
                           min_value=min_d, max_value=max_d)
with f2:
    chosen = st.multiselect("Stations", stations, default=stations)

# date_input returns a single date while the user is mid-selection
start, end = (picked if isinstance(picked, tuple) and len(picked) == 2
              else (min_d, max_d))
df = gold[(gold["reading_date"].dt.date >= start)
          & (gold["reading_date"].dt.date <= end)
          & (gold["station_name"].isin(chosen))]

if df.empty:
    st.info("No data for this selection.")
    st.stop()


# --------------------------------------------------------------------------- #
# Headline numbers. Ratios are weighted by energy, not averaged per day:
# averaging daily percentages would let a low-output day count as much as a
# high-output one.
# --------------------------------------------------------------------------- #

gen = df["generation_kwh"].sum()
aux = df["auxiliary_kwh"].sum()
k1, k2, k3, k4 = st.columns(4)
k1.metric("Generation", f"{gen / 1e6:,.2f} GWh")
k2.metric("Net export", f"{df['net_export_kwh'].sum() / 1e6:,.2f} GWh")
k3.metric("Auxiliary consumption", f"{100 * aux / gen:.2f} %" if gen else "n/a")
k4.metric("Data completeness",
          f"{100 * df['reads_received'].sum() / df['reads_expected'].sum():.1f} %")


# --------------------------------------------------------------------------- #
# Charts: one measure per chart, never two y-axes
# --------------------------------------------------------------------------- #

def daily_line_chart(data: pd.DataFrame, field: str, y_title: str, fmt: str) -> alt.Chart:
    """Line per station with a hover crosshair that lists every station's value."""
    base = alt.Chart(data).encode(
        x=alt.X("reading_date:T", title=None,
                axis=alt.Axis(format="%d %b", labelColor=ink["secondary"],
                              tickColor=ink["grid"], domain=False, grid=False)),
    )
    color = alt.Color("station_name:N", scale=color_scale, title=None,
                      legend=alt.Legend(orient="top", labelColor=ink["primary"],
                                        symbolType="stroke", symbolStrokeWidth=3))

    lines = base.mark_line(strokeWidth=2).encode(
        y=alt.Y(f"{field}:Q", title=y_title,
                axis=alt.Axis(labelColor=ink["secondary"], titleColor=ink["secondary"],
                              gridColor=ink["grid"], domain=False, ticks=False,
                              titleFontWeight="normal")),
        color=color,
    )

    hover = alt.selection_point(nearest=True, on="pointerover", fields=["reading_date"],
                                empty=False, clear="pointerout")

    # Invisible wide rules give a hit target much bigger than the line itself
    tooltip = [alt.Tooltip("reading_date:T", title="Date", format="%a %d %b %Y")] + [
        alt.Tooltip(f"{s}:Q", title=s, format=fmt) for s in data["station_name"].unique()
    ]
    crosshair = (base.transform_pivot("station_name", value=field, groupby=["reading_date"])
                 .mark_rule(color=ink["secondary"], strokeWidth=1)
                 .encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
                         tooltip=tooltip)
                 .add_params(hover))

    points = lines.mark_point(filled=True, size=70, stroke=None).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    )

    return (alt.layer(lines, crosshair, points)
            .properties(height=300)
            .configure_view(stroke=None)
            .configure(background="transparent",
                       font="sans-serif"))


left, right = st.columns(2)
with left:
    st.subheader("Daily generation")
    st.altair_chart(daily_line_chart(df, "generation_mwh", "MWh", ",.0f"),
                    width="stretch", theme=None)
with right:
    st.subheader("Auxiliary consumption")
    st.altair_chart(daily_line_chart(df, "aux_pct", "% of generation", ".2f"),
                    width="stretch", theme=None)


# --------------------------------------------------------------------------- #
# The numbers behind the charts
# --------------------------------------------------------------------------- #

st.subheader("Daily detail")
table = (df.sort_values(["reading_date", "station_id"], ascending=[False, True])
         [["reading_date", "station_name", "source", "generation_mwh", "aux_pct",
           "capacity_factor_pct", "completeness_pct", "gap_fills"]])
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "reading_date": st.column_config.DateColumn("Date", format="ddd D MMM YYYY"),
        "station_name": "Station",
        "source": "Source",
        "generation_mwh": st.column_config.NumberColumn("Generation (MWh)", format="%.1f"),
        "aux_pct": st.column_config.NumberColumn("Auxiliary %", format="%.2f"),
        "capacity_factor_pct": st.column_config.NumberColumn("Capacity factor %", format="%.1f"),
        "completeness_pct": st.column_config.NumberColumn("Completeness %", format="%.1f"),
        "gap_fills": st.column_config.NumberColumn("Gap fills"),
    },
)
