"""NBA Analytics -- Player Profile page.

Everything fits on ONE screen: pick up to four players, then career cards
(2 x 2 grid) sit BESIDE the official-accolades table, and the interactive
career-progression chart closes the page with its metric/scale controls in
a single row.

The chart's x-axis is a categorical season axis (plotly would otherwise
parse '2003-04' as a date and tick every 3 months instead of once per
season); hover carries season/team/GP/value for every player at once, the
legend toggles players off and on, wheel-zoom/pan is native.

Data: data/dashboard_players.json (written by refresh_dashboard_fallbacks.py
after a successful all-history fetch -- career lines from ESPN's athlete
statistics, honours from ESPN's awards API).
"""

import pandas as pd
import streamlit as st

import shared

import awards

st.set_page_config(page_title="Player Profile", page_icon="🧑‍🏀",
                   layout="wide")

shared.inject_css()
st.title("Player Profile")

with st.sidebar:
    st.markdown("### Data inventory")
    shared.sidebar_games(shared.load_games_by_season(),
                         shared.season_options())

players, _meta, note = shared.load_players()
if not players:
    st.info("No player index yet — data/dashboard_players.json is written "
            "by `python src/collector/refresh_dashboard_fallbacks.py` after "
            "a successful all-history fetch.")
    st.stop()

st.caption(f"Source: {note}")

by_name = {}
for row in players.values():
    name = row.get("player_name")
    if name and name not in by_name:
        by_name[name] = row
names = sorted(by_name)

# Default to the top of the GOAT ladder so the page opens with real careers.
defaults = [p["player_name"] for p in sorted(
    (p for p in players.values() if p.get("goat_rank")),
    key=lambda p: p["goat_rank"])[:4]]
selected = st.multiselect(
    "Players (up to 4)", names,
    default=[d for d in defaults if d in by_name],
    max_selections=4,
    help="Type to search; compare up to four career arcs at once. Legends, "
         "hover and zoom are interactive on the chart below.",
)
if not selected:
    st.info("Pick up to four players to see career cards, accolades and "
            "the progression chart.")
    st.stop()

# Cards and accolades share one row so the chart stays above the fold.
cards_col, honours_col = st.columns(2, gap="large")
with cards_col:
    shared.section("Career cards")
    grid = [st.columns(2) for _ in range((len(selected) + 1) // 2)]
    for i, name in enumerate(selected):
        with grid[i // 2][i % 2]:
            st.markdown(shared.profile_card_html(by_name[name]),
                        unsafe_allow_html=True)

with honours_col:
    shared.section("Official accolades")
    accolade_rows = []
    for name in selected:
        honours = by_name[name].get("honours") or {}
        for award_name, count in sorted(
                honours.items(),
                key=lambda kv: (-awards.GOAT_HONOURS_WEIGHTS.get(kv[0], 0.0),
                                kv[0])):
            weight = awards.GOAT_HONOURS_WEIGHTS.get(award_name, 0.0)
            accolade_rows.append({
                "Player": name,
                "Honour": awards.GOAT_HONOUR_LABELS.get(award_name,
                                                         award_name),
                "Wins": int(count),
                "Pts each": weight,
                "Pts total": round(weight * count, 2),
            })
    if accolade_rows:
        st.dataframe(pd.DataFrame(accolade_rows), width="stretch",
                     height=min(40 + 36 * len(accolade_rows), 300),
                     hide_index=True)
        st.caption(
            "ESPN's 20 official award types at the GOAT formula's weights; "
            "All-Star selections aren't in ESPN's awards API at all, so "
            "never shown or scored; 🏆 rings are display-only (cards)."
        )
    else:
        st.caption("No official honours for this selection in ESPN's "
                   "awards API.")

shared.section("Career progression")
metric_col, scale_col = st.columns([3, 1])
with metric_col:
    metric = st.radio(
        "Metric", list(shared.PROGRESSION_METRICS), horizontal=True,
        key="profile_metric",
        format_func=lambda k: shared.PROGRESSION_METRICS[k],
    )
with scale_col:
    mode = st.radio("Scale", ["Per game", "Totals"], horizontal=True,
                    key="profile_scale",
                    help="Totals = that season's per-game rate × GP. "
                         "Percentages are rates and ignore the toggle.")
series = {name: (by_name[name].get("seasons_log") or [])
          for name in selected}
figure = shared.progression_figure(series, metric, mode, height=400)
if figure.data:
    st.plotly_chart(figure, width="stretch")
    missing = [n for n, rows in series.items() if not rows]
    if missing:
        st.caption(f"No per-season rows for {', '.join(missing)} — ESPN's "
                   "career lines don't cover those seasons fully (see the "
                   "card's gap marks); their line simply isn't drawn.")
    note_bits = (
        "Hover shows the season, team, games played and value for every "
        "player at once; click the legend to toggle a player; drag to zoom, "
        "double-click to reset."
    )
    if metric in shared.PROGRESSION_PCT:
        note_bits += (" Percentages are rates — the totals toggle applies "
                      "to counting stats only.")
    st.caption(note_bits)
else:
    st.caption("No per-season data for this selection — pick another "
               "player or metric.")
