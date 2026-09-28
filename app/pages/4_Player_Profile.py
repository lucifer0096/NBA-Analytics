"""NBA Analytics -- Player Profile page.

Two league tabs, each with its own player index: pick up to four players,
then the career cards (2 x 2 grid) and the official-accolades table each
get the FULL page width: cards so the two meta lines fit without wrapping
into micro-text, the table as a pivot (one row per ESPN award type, one
column per player) tall enough for EVERY row -- the old half-width,
height-capped table hid most of its rows behind a scrollbar. The
interactive career-progression chart closes the page with its
metric/scale controls in a single row.

The chart's x-axis is a categorical season axis (plotly would otherwise
parse '2003-04' as a date and tick every 3 months instead of once per
season); hover carries season/team/GP/value for every player at once, the
legend toggles players off and on, wheel-zoom/pan is native.

Data: data/dashboard[_wnba]_players.json (written by
refresh_dashboard_fallbacks.py after a successful all-history fetch --
career lines from ESPN's athlete statistics, honours from ESPN's awards
API).
"""

import pandas as pd
import streamlit as st

import shared

import leagues

import awards

st.set_page_config(page_title="Player Profile", page_icon="🧑‍🏀",
                   layout="wide")

shared.inject_css()
st.title("Player Profile")

with st.sidebar:
    st.markdown("### Data inventory")
    for league in ("nba", "wnba"):
        shared.sidebar_games(shared.load_games_by_season(league),
                             shared.season_options(league), league)


def _render_league(league: str) -> None:
    players_json = leagues.named_path("", "dashboard_players.json", league)
    refresh_cmd = ("python src/collector/refresh_dashboard_fallbacks.py"
                   + ("" if league == "nba" else f" --league {league}"))

    players, _meta, note = shared.load_players(league)
    if not players:
        st.info(f"No player index yet: data/{players_json} is written "
                f"by `{refresh_cmd}` after a successful all-history "
                "fetch.")
        return

    st.caption(f"Source: {note}")

    by_name = {}
    for row in players.values():
        name = row.get("player_name")
        if name and name not in by_name:
            by_name[name] = row
    names = sorted(by_name)

    # Default to the top of the GOAT ladder so the page opens with real
    # careers. A ?player= deep link (Court View cards link here) opens
    # straight on that one player when THIS league's index carries the
    # name; an unknown name falls back to the ladder defaults honestly.
    defaults = [p["player_name"] for p in sorted(
        (p for p in players.values() if p.get("goat_rank")),
        key=lambda p: p["goat_rank"])[:4]]
    deep_player = st.query_params.get("player") or ""
    if deep_player in by_name:
        defaults = [deep_player]
    selected = st.multiselect(
        "Players (up to 4)", names,
        default=[d for d in defaults if d in by_name],
        max_selections=4,
        key=f"profile_players_{league}",
        help="Type to search; compare up to four career arcs at once. "
             "Legends, hover and zoom are interactive on the chart below.",
    )
    if not selected:
        st.info("Pick up to four players to see career cards, accolades "
                "and the progression chart.")
        return

    # Cards and the accolades pivot each take the FULL page width: the
    # card's two meta lines only read well with ~430px of text box, and the
    # old half-width, height-capped table showed 8 of its 40 rows behind a
    # scrollbar. One row per award type bounds the pivot at ESPN's types.
    shared.section("Career cards")
    grid = [st.columns(2) for _ in range((len(selected) + 1) // 2)]
    for i, name in enumerate(selected):
        with grid[i // 2][i % 2]:
            st.markdown(shared.profile_card_html(by_name[name], league),
                        unsafe_allow_html=True)

    shared.section("Official accolades")
    honour_weights = awards.honours_weights(league)
    honour_labels = awards.honour_labels(league)
    honours_by_award: dict = {}
    for name in selected:
        for award_name, count in (by_name[name].get("honours") or {}).items():
            honours_by_award.setdefault(award_name, {})[name] = int(count)
    if honours_by_award:
        award_keys = sorted(
            honours_by_award,
            key=lambda k: (-honour_weights.get(k, 0.0),
                           honour_labels.get(k, k)))
        rows = []
        for award_name in award_keys:
            row = {"Honour": honour_labels.get(award_name, award_name),
                   "Pts each": honour_weights.get(award_name, 0.0)}
            for name in selected:
                row[name] = honours_by_award[award_name].get(name)
            rows.append(row)
        table = pd.DataFrame(rows)
        for name in selected:
            # An award the player never won stays BLANK (pd.NA), never a 0.
            table[name] = table[name].astype("Int64")
        st.dataframe(table, width="stretch",
                     height=40 + 36 * len(table), hide_index=True)
        st.caption(
            "Cell = that player's wins of the award; points are Pts each × "
            "wins (blank where ESPN's API returned no award, never a zero). "
            f"ESPN's {len(honour_weights)} official award types at the GOAT "
            "formula's weights; "
            "All-Star selections aren't in ESPN's awards API at all, so "
            "never shown or scored; 🏆 rings come from indexed champion "
            "seasons or the verified official record."
        )
    else:
        st.caption("No official honours for this selection in ESPN's "
                   "awards API.")

    shared.section("Career progression")
    metric_col, scale_col, axis_col = st.columns([3, 1, 1])
    with metric_col:
        metric = st.radio(
            "Metric", list(shared.PROGRESSION_METRICS), horizontal=True,
            key=f"profile_metric_{league}",
            format_func=lambda k: shared.PROGRESSION_METRICS[k],
        )
    with scale_col:
        mode = st.radio(
            "Scale", ["Per game", "Totals"], horizontal=True,
            key=f"profile_scale_{league}",
            help="Totals = that season's per-game rate × GP. "
                 "Percentages are rates and ignore the toggle.")
    with axis_col:
        axis = st.radio(
            "Axis", ["Season", "Career year"], horizontal=True,
            key=f"profile_axis_{league}",
            help="Career year numbers each player's seasons 1, 2, 3 ... "
                 "from his first, so careers from different eras line up "
                 "on one grid instead of sitting in non-overlapping "
                 "decades.")
    series = {name: (by_name[name].get("seasons_log") or [])
              for name in selected}
    figure = shared.progression_figure(series, metric, mode, height=400,
                                       axis=axis)
    if figure.data:
        st.plotly_chart(figure, width="stretch")
        missing = [n for n, rows in series.items() if not rows]
        if missing:
            st.caption(f"No per-season rows for {', '.join(missing)}: "
                       "ESPN's career lines don't cover those seasons "
                       "fully (see the card's gap marks); their line "
                       "simply isn't drawn.")
        note_bits = (
            "Hover shows the season, team, games played and value for "
            "every player at once; click the legend to toggle a player; "
            "drag to zoom, double-click to reset."
        )
        if axis == "Career year":
            note_bits += (" Career year counts each player's seasons "
                          "from his first; hover carries the calendar "
                          "season.")
        if metric in shared.PROGRESSION_PCT:
            note_bits += (" Percentages are rates; the totals toggle "
                          "applies to counting stats only.")
        st.caption(note_bits)
    else:
        st.caption("No per-season data for this selection: pick another "
                   "player or metric.")


league_tabs = shared.league_tabs()
with league_tabs["NBA"]:
    _render_league("nba")
with league_tabs["WNBA"]:
    _render_league("wnba")

# Corrupt-fallback warnings ride at the very END (see app.py).
shared.render_fallback_warnings()
