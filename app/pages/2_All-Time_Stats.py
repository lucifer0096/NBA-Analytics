"""NBA Analytics -- All-Time Stats page.

Career boards across EVERY collected season (NBA 2010-11 -> the newest
with box scores; WNBA 2010 -> its newest), moved out of the home tabs so
the ladder sections get a full page each. Two league tabs carry one board
set each; the sidebar shows both leagues' inventories. Computed locally
from the collected box scores (src/collector/awards.py, committed as
data/dashboard_awards.json per league), with the honest window caption:
this is all-time WITHIN the collector's window, not full league history --
the GOAT ladder (pages/3_GOAT_Rankings.py) is the all-history view.
"""

import pandas as pd
import streamlit as st

import shared

import leagues

import awards

st.set_page_config(page_title="All-Time Stats", page_icon="📊", layout="wide")

shared.inject_css()
st.title("All-Time Stats")

with st.sidebar:
    st.markdown("### Data inventory")
    for league in ("nba", "wnba"):
        shared.sidebar_games(shared.load_games_by_season(league),
                             shared.season_options(league), league)


def _render_league(league: str) -> None:
    display = leagues.display(league)
    awards_json = leagues.named_path("", "dashboard_awards.json", league)
    refresh_cmd = ("python src/collector/refresh_dashboard_fallbacks.py"
                   + ("" if league == "nba" else f" --league {league}"))

    alltime, _, window, career_note = shared.load_awards_career(league)
    st.caption(f"Source: {career_note}")
    career_leaders = alltime.get("leaders") or {}
    if not career_leaders:
        st.info("No all-time data yet: the career boards are computed from "
                f"collected box scores (`{refresh_cmd}`) into "
                f"data/{awards_json}.")
        return
    st.caption(
        f"Career totals across {window.get('seasons', 0)} collected "
        f"seasons ({window.get('first', '—')} → "
        f"{window.get('last', '—')}). The collector starts at "
        f"{window.get('first', leagues.first_season(league))}, "
        f"so this is all-time WITHIN that window, not full {display} "
        f"history. Qualified at ≥{awards.ALLTIME_MIN_GP} career GP; "
        "% boards additionally need ≥5 FGA/g (3P ≥2 3PA/g, FT ≥1 FTA/g)."
    )
    stat_keys = [k for k in awards.ALLTIME_CATEGORIES
                 if k in career_leaders] or list(career_leaders)
    stat = st.radio(
        "Rank by", stat_keys, horizontal=True,
        key=f"alltime_stat_cat_{league}",
        format_func=lambda k: (f"{awards.STAT_LABELS.get(k, k)} "
                               f"({awards.STAT_ABBR.get(k, k)})"),
    )
    if stat == "plus_minus":
        st.caption(
            "Career +/- sums only the collected box scores "
            f"({window.get('first', leagues.first_season(league))} → "
            f"{window.get('last', '—')}); ESPN's career statistics carry "
            "no +/-, so careers outside the collector's window aren't "
            "ranked here (blanks in other boards' +/- column mean the "
            "same thing)."
        )
    frame = pd.DataFrame(career_leaders.get(stat) or [])
    if frame.empty:
        st.caption(
            f"No qualified {awards.STAT_LABELS.get(stat, stat).lower()} "
            "leaders in the collected window."
        )
        return
    for col in ("fgp", "fg3p", "ftp", "efg", "ts"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "plus_minus" in frame.columns:
        # None (career outside the window) stays blank, never 0.
        frame["plus_minus"] = pd.to_numeric(
            frame["plus_minus"], errors="coerce").astype("Int64")
    frame["FG"] = (frame["fgm"].astype(int).astype(str) + "-"
                   + frame["fga"].astype(int).astype(str))
    frame["3P"] = (frame["fg3m"].astype(int).astype(str) + "-"
                   + frame["fg3a"].astype(int).astype(str))
    frame["FT"] = (frame["ftm"].astype(int).astype(str) + "-"
                   + frame["fta"].astype(int).astype(str))
    display_frame = frame.rename(columns={
        "rank": "", "player_name": "Player", "team_abbrev": "Team",
        "seasons": "Seas", "gp": "GP", "minutes": "MIN",
        "pts": "PTS", "reb": "REB", "oreb": "ORB", "dreb": "DREB",
        "ast": "AST", "stl": "STL", "blk": "BLK", "to": "TO",
        "plus_minus": "+/-",
        "fgp": "FG%", "fg3p": "3P%", "ftp": "FT%",
        "efg": "eFG%", "ts": "TS%",
    })
    keep = ["", "Player", "Team", "Seas", "GP", "MIN", "PTS", "REB",
            "ORB", "DREB", "AST", "STL", "BLK", "TO", "+/-", "FG", "FG%",
            "3P", "3P%", "FT", "FT%", "eFG%", "TS%"]
    st.dataframe(
        display_frame[[c for c in keep if c in display_frame.columns]],
        width="stretch", hide_index=True,
    )
    st.caption(
        "FG/3P/FT are made-attempt season totals rolled into the "
        "career; eFG% = (FGM + 0.5·3PM) / FGA, "
        "TS% = PTS / (2·(FGA + 0.44·FTA)): computed here from the "
        f"box scores, not copied from any official {display} source."
    )


league_tabs = shared.league_tabs()
with league_tabs["NBA"]:
    _render_league("nba")
with league_tabs["WNBA"]:
    _render_league("wnba")

# Corrupt-fallback warnings ride at the very END (see app.py).
shared.render_fallback_warnings()
