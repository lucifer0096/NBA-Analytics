"""NBA Analytics -- All-Time Stats page.

Career boards across EVERY collected season (2010-11 -> the newest with box
scores), moved out of the home tabs so the ladder sections get a full page
each. Computed locally from the collected box scores (src/collector/awards.py,
committed as data/dashboard_awards.json), with the honest window caption: this
is all-time WITHIN the collector's window, not full NBA history -- the GOAT
ladder (pages/3_GOAT_Rankings.py) is the all-NBA-history view.
"""

import pandas as pd
import streamlit as st

import shared

import awards

st.set_page_config(page_title="All-Time Stats", page_icon="📊", layout="wide")

shared.inject_css()
st.title("All-Time Stats")

with st.sidebar:
    st.markdown("### Data inventory")
    shared.sidebar_games(shared.load_games_by_season(),
                         shared.season_options())

alltime, _, window, career_note = shared.load_awards_career()
st.caption(f"Source: {career_note}")
career_leaders = alltime.get("leaders") or {}
if not career_leaders:
    st.info("No all-time data yet — the career boards are computed from "
            "collected box scores (`python src/collector/"
            "refresh_dashboard_fallbacks.py`) into data/dashboard_awards.json.")
else:
    st.caption(
        f"Career totals across {window.get('seasons', 0)} collected "
        f"seasons ({window.get('first', '—')} → "
        f"{window.get('last', '—')}) — the collector starts at 2010-11, "
        "so this is all-time WITHIN that window, not full NBA history. "
        f"Qualified at ≥{awards.ALLTIME_MIN_GP} career GP; % boards "
        "additionally need ≥5 FGA/g (3P ≥2 3PA/g, FT ≥1 FTA/g)."
    )
    stat_keys = [k for k in awards.ALLTIME_CATEGORIES
                 if k in career_leaders] or list(career_leaders)
    stat = st.radio(
        "Rank by", stat_keys, horizontal=True, key="alltime_stat_cat",
        format_func=lambda k: (f"{awards.STAT_LABELS.get(k, k)} "
                               f"({awards.STAT_ABBR.get(k, k)})"),
    )
    frame = pd.DataFrame(career_leaders.get(stat) or [])
    if frame.empty:
        st.caption(
            f"No qualified {awards.STAT_LABELS.get(stat, stat).lower()} "
            "leaders in the collected window."
        )
    else:
        for col in ("fgp", "fg3p", "ftp", "efg", "ts"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame["FG"] = (frame["fgm"].astype(int).astype(str) + "-"
                       + frame["fga"].astype(int).astype(str))
        frame["3P"] = (frame["fg3m"].astype(int).astype(str) + "-"
                       + frame["fg3a"].astype(int).astype(str))
        frame["FT"] = (frame["ftm"].astype(int).astype(str) + "-"
                       + frame["fta"].astype(int).astype(str))
        display = frame.rename(columns={
            "rank": "", "player_name": "Player", "team_abbrev": "Team",
            "seasons": "Seas", "gp": "GP", "minutes": "MIN",
            "pts": "PTS", "reb": "REB", "oreb": "ORB", "dreb": "DREB",
            "ast": "AST", "stl": "STL", "blk": "BLK", "to": "TO",
            "fgp": "FG%", "fg3p": "3P%", "ftp": "FT%",
            "efg": "eFG%", "ts": "TS%",
        })
        keep = ["", "Player", "Team", "Seas", "GP", "MIN", "PTS", "REB",
                "ORB", "DREB", "AST", "STL", "BLK", "TO", "FG", "FG%",
                "3P", "3P%", "FT", "FT%", "eFG%", "TS%"]
        st.dataframe(
            display[[c for c in keep if c in display.columns]],
            width="stretch", hide_index=True,
        )
        st.caption(
            "FG/3P/FT are made-attempt season totals rolled into the "
            "career; eFG% = (FGM + 0.5·3PM) / FGA, "
            "TS% = PTS / (2·(FGA + 0.44·FTA)) — computed here from the "
            "box scores, not copied from any official NBA source."
        )
