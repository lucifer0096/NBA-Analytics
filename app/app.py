"""NBA Analytics dashboard -- Home page.

Run: streamlit run app/app.py

Three tabs, all live-first from ESPN's free APIs with committed offline
fallbacks (see shared.py for the contract):

- **Standings**   -- conference tables for the selected season
- **Schedule**    -- today's games live (scoreboard) + the selected season's
                     full schedule/results from the daily collector
- **Projections** -- model-projected fantasy points for upcoming games (the
                     committed fallback was computed where raw data exists;
                     Streamlit Cloud can't rebuild 20k box scores itself)

The Model & History page (pages/1_Model_and_History.py) carries metrics,
season leaders and the lineup optimizer.

Presentation: shared.inject_css() owns the theme CSS (accent-gradient title,
hero strip, metric cards, accent tabs) -- all CSS-only, built on Streamlit's
own theme tokens from .streamlit/config.toml so it survives theme changes,
and reduced-motion safe.
"""

import pandas as pd
import streamlit as st

import shared

st.set_page_config(page_title="NBA Analytics", page_icon="🏀", layout="wide")

shared.inject_css()
st.title("NBA Analytics")

# ---------------------------------------------------------------------------
# Sidebar: season selector + honest freshness everywhere
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Settings")
    options = shared.season_options()
    season = st.selectbox(
        "Season", options,
        index=0 if options else None,
        help="Newest season with collected data first.",
    )
    st.caption(f"Today (UTC): {shared.now_utc():%Y-%m-%d}")
    st.markdown("---")
    st.caption(
        "Live data: [ESPN public APIs](https://site.api.espn.com) — no auth, "
        "no key. Offline copies refresh daily via GitHub Actions."
    )

teams, teams_note = shared.load_teams()

# ---------------------------------------------------------------------------
# KPI strip: four glass cards giving the page instant context
# ---------------------------------------------------------------------------

games, sched_note = shared.load_schedule(season)
leaders, leaders_note = shared.load_leaderboards()
metrics = shared.load_metrics()

if not games.empty:
    finals = games[games["status"] == "STATUS_FINAL"]
    upcoming = games[games["status"] != "STATUS_FINAL"].sort_values("date")
    kpi_games = f"{len(finals):,} / {len(games):,}"
    kpi_next = str(upcoming.iloc[0]["date"])[:10] if not upcoming.empty else "—"
else:
    kpi_next = "—"
    kpi_games = "—"

top_scorer = "—"
if not leaders.empty:
    best = leaders.sort_values("fantasy_points", ascending=False).iloc[0]
    top_scorer = f"{best['player_name']} · {best['fantasy_points']:,.0f}"

single = metrics.get("single_stage", {})
naive = metrics.get("naive_baseline", {})
if single and naive:
    kpi_model = f"{single.get('mae', float('nan')):.3f} MAE"
    model_help = f"Naive baseline: {naive.get('mae', float('nan')):.3f} on {metrics.get('validation_season')}"
else:
    kpi_model = "not trained"
    model_help = "Run src/model/train.py to populate this."

col1, col2, col3, col4 = st.columns(4)
col1.metric("Games collected", kpi_games)
col2.metric("Next tip-off", kpi_next)
col3.metric("Season fantasy leader", top_scorer)
col4.metric("Model (validation)", kpi_model, help=model_help)

shared.hero(season, f"Teams source: {teams_note}", extra="ESPN · no auth")

tabs = st.tabs(["Standings", "Schedule & Scores", "Projections"])

# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

with tabs[0]:
    standings, note = shared.load_standings(season)
    st.caption(f"Source: {note}")
    if standings.empty:
        st.info(f"No standings available for {season} yet.")
    else:
        played = any((r or 0) > 0 for r in standings.get("wins", pd.Series(dtype=int)))

        def _zone(seed):
            """Playoff / play-in / lottery glyph -- only meaningful with real
            records, so preseason (all-zero) tables get no zone marks."""
            if not played or pd.isna(seed):
                return ""
            seed = int(seed)
            if seed <= 6:
                return "🟢 "
            if seed <= 10:
                return "🟡 "
            return ""

        for conference in ("Eastern Conference", "Western Conference"):
            table = standings[standings["conference"] == conference].copy()
            if table.empty:
                continue
            shared.section(conference)
            table = table.sort_values(
                "playoff_seed", na_position="last"
            ).reset_index(drop=True)
            table["record"] = (
                table["wins"].astype("Int64").astype(str)
                + "-"
                + table["losses"].astype("Int64").astype(str)
            )
            table["zone"] = table["playoff_seed"].map(_zone)
            table.index = table.index + 1
            display = table.rename(columns={
                "team": "Team", "win_percent": "Win%", "playoff_seed": "Seed",
                "streak": "Streak", "avg_points_for": "PF/g",
                "avg_points_against": "PA/g",
            })
            keep = ["zone", "Team", "record", "Win%", "Seed", "Streak",
                    "PF/g", "PA/g"]
            display = display[[c for c in keep if c in display.columns]]
            st.dataframe(
                display,
                width="stretch",
                column_config={
                    "zone": st.column_config.TextColumn("", width="small"),
                    "Team": st.column_config.TextColumn("Team", width="large"),
                    "record": st.column_config.TextColumn("W-L", width="small"),
                },
            )
        st.caption("🟢 top-6 (playoff) · 🟡 play-in (7–10)")

# ---------------------------------------------------------------------------
# Schedule & Scores
# ---------------------------------------------------------------------------

with tabs[1]:
    today = shared.now_utc().strftime("%Y%m%d")
    scoreboard, sb_note = shared.load_scoreboard(today)
    shared.section("Today's games")
    if scoreboard.empty:
        st.caption(
            f"No games on {today} — {sb_note}. The NBA offseason runs Jun–Oct; "
            "historical results and the full upcoming schedule are below."
        )
    else:
        st.dataframe(
            scoreboard.rename(columns={
                "away": "Away", "home": "Home",
                "away_score": "Away pts", "home_score": "Home pts",
                "status": "Status",
            }),
            width="stretch",
        )

    shared.section(f"{season} schedule & results")
    if games.empty:
        st.info(f"No collected schedule for {season} yet — run the collector "
                f"(`python src/collector/snapshot.py --season {season}`).")
    else:
        st.caption(f"Source: {sched_note}")
        frame = games.copy()
        finals = frame[frame["status"] == "STATUS_FINAL"].copy()
        upcoming = frame[frame["status"] != "STATUS_FINAL"].sort_values("date")

        if not finals.empty:
            recent = finals.sort_values("date", ascending=False).head(15).copy()
            recent["day"] = recent["date"].astype(str).str[:10]
            recent["matchup"] = (
                recent["away_abbrev"] + " @ " + recent["home_abbrev"]
                + "  " + recent["away_score"].astype(str)
                + ":" + recent["home_score"].astype(str)
            )
            st.markdown("**Most recent results**")
            st.dataframe(
                recent[["day", "matchup"]].rename(
                    columns={"day": "Date", "matchup": "Matchup (away : home)"}
                ),
                width="stretch", hide_index=True,
            )
        if not upcoming.empty:
            nxt = upcoming.head(15).copy()
            nxt["day"] = nxt["date"].astype(str).str[:10]
            nxt["matchup"] = nxt["away_abbrev"] + " @ " + nxt["home_abbrev"]
            st.markdown("**Upcoming**")
            st.dataframe(
                nxt[["day", "matchup"]].rename(
                    columns={"day": "Date", "matchup": "Matchup"}
                ),
                width="stretch", hide_index=True,
            )
        st.caption(
            f"{len(finals):,} final · {len(upcoming):,} upcoming "
            f"({frame['game_id'].nunique():,} total)"
        )

# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------

with tabs[2]:
    projections, proj_note = shared.load_projections()
    st.caption(f"Source: {proj_note}")
    if projections.empty:
        st.info(
            "No projections available yet. They're generated where the raw "
            "collector data exists (`python src/model/predict.py`) and "
            "committed as a fallback for this page."
        )
    else:
        projections = projections.copy()
        projections["day"] = projections["date"].astype(str).str[:10]
        game_days = sorted(projections["day"].unique())
        shared.section("Projected fantasy points")
        day = st.selectbox("Game day", game_days)
        day_rows = projections[projections["day"] == day].sort_values(
            "projected_points", ascending=False
        ).head(30)
        st.dataframe(
            day_rows.rename(columns={
                "player_name": "Player",
                "team_abbrev": "Team",
                "opponent_abbrev": "Opp",
                "position": "Pos",
            })[["Player", "Team", "Opp", "Pos", "projected_points"]],
            width="stretch", hide_index=True,
            column_config={
                "projected_points": st.column_config.ProgressColumn(
                    "Proj FPTS", min_value=0, max_value=60, format="%.1f",
                ),
            },
        )
        st.caption(
            "Projected fantasy points use the default configurable scoring "
            "weights (src/model/scoring.py), not any platform's scheme."
        )
