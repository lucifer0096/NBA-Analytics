"""NBA Analytics dashboard -- Home page.

Run: streamlit run app/app.py

Four tabs: Standings and Schedule are live-first from ESPN's free APIs with
committed offline fallbacks (see shared.py for the contract), while the
Awards Ladder and Court View are computed locally from the collected box
scores and follow the sidebar's season selector (every collected season,
2010-11 -> upcoming):

- **Standings**        -- conference tables for the selected season; a
                          season that hasn't started shows an honest empty
                          state, never another season's table under its label
- **Schedule & Scores** -- today's games live (scoreboard) + the selected
                          season's schedule from the committed MULTI-SEASON
                          envelope: every completed game with its final score
                          (finished seasons render the full table)
- **Awards Ladder**    -- MVP / DPOY / 6th Man / MIP races plus per-game stat
                          leaders incl. 3PM and shooting splits
                          (src/collector/awards.py, committed as
                          data/dashboard_awards.json): transparent homegrown
                          metrics, explicitly NOT official NBA voting -- every
                          race prints its exact formula as its caption; a
                          season with no collected games shows an empty state
- **Court View**       -- the selected stat's leaders on a CSS-only hardwood
                          court: 2 G / 2 F / 1 C filled in rank order by each
                          player's real position, everyone else on a bench
                          strip, hover a card for the full per-game line

The sidebar carries the per-season games inventory and a one-line Model &
History mention (validation headline; full numbers in models/metrics.json).
Left-nav pages carry the rest:
- pages/2_All-Time_Stats.py    -- career boards across the collected window
- pages/3_GOAT_Rankings.py     -- the all-NBA-history GOAT ladder
- pages/4_Player_Profile.py    -- careers, official accolades, progression chart

Presentation: shared.inject_css() owns the theme CSS (accent-gradient title,
hero strip, metric cards, accent tabs) -- all CSS-only, built on Streamlit's
own theme tokens from .streamlit/config.toml so it survives theme changes,
and reduced-motion safe. Player headshots/team logos use the CSS-only
fallback (shared.headshot_html) -- no JS, a 404 just shows the team logo.
"""

import pandas as pd
import streamlit as st

import shared

import awards

st.set_page_config(page_title="NBA Analytics", page_icon="🏀", layout="wide")

shared.inject_css()
st.title("NBA Analytics")

# ---------------------------------------------------------------------------
# Sidebar: season selector + per-season inventory + honest freshness
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Settings")
    options = shared.season_options()
    season = st.selectbox(
        "Season", options,
        index=shared.default_season_index(options) if options else None,
        help="Leads with the newest season that has collected games; every "
             "year from 2010-11 through the upcoming season is selectable.",
    )
    st.caption(f"Today (UTC): {shared.now_utc():%Y-%m-%d}")
    st.markdown("---")
    shared.sidebar_games(shared.load_games_by_season(), options)
    with st.expander("📈 Model & History"):
        _m = shared.load_metrics()
        _single, _naive = _m.get("single_stage", {}), _m.get("naive_baseline", {})
        if _single and _naive:
            _beats = "beats" if (_single.get("mae", 1)
                                 < _naive.get("mae", 0)) else "trails"
            st.caption(
                f"{_m.get('validation_season', '—')}: model "
                f"{_single.get('mae', float('nan')):.3f} MAE vs naive "
                f"{_naive.get('mae', float('nan')):.3f}: model {_beats} "
                "the baseline, trained on the box-score history inventoried "
                "above (full validation: models/metrics.json)."
            )
        else:
            st.caption("Model not trained yet: run `python "
                       "src/model/features.py && python "
                       "src/model/train.py`.")

teams, teams_note = shared.load_teams()

# ---------------------------------------------------------------------------
# KPI strip: three glass cards scoped to the SELECTED season
# ---------------------------------------------------------------------------

games, sched_note = shared.load_schedule(season)
awards_payload, awards_note = shared.load_awards(season)
games_counts = shared.load_games_by_season()

if not games.empty:
    finals = games[games["status"] == "STATUS_FINAL"].sort_values("date")
    rest = games[games["status"] != "STATUS_FINAL"]
    _today = f"{shared.now_utc():%Y-%m-%d}"
    upcoming = rest[rest["date"].astype(str).str[:10] >= _today].sort_values(
        "date")
    stale = rest[rest["date"].astype(str).str[:10] < _today]  # postponed etc.
else:
    finals = upcoming = stale = games  # empty frames (no columns to touch)

# Games collected: the box-score inventory per season (0 for a season that
# hasn't started -- an honest number, not a fallback to another year's).
if season in games_counts:
    kpi_games = f"{int(games_counts[season] or 0):,}"
    games_help = ("Box-score files collected for this season -- the sidebar "
                  "inventory lists every year.")
elif not finals.empty:
    kpi_games = f"{len(finals):,}"
    games_help = "Final games in the committed schedule (inventory absent)."
else:
    kpi_games = "—"
    games_help = "No games collected for this season yet."

# Next tip-off exists only for seasons with unplayed games left.
kpi_next = str(upcoming.iloc[0]["date"])[:10] if not upcoming.empty else "—"
next_help = ("First unplayed game of this season's schedule."
             if not upcoming.empty else
             "No unplayed games left in this season.")

top_scorer = "—"
pts_leaders = (awards_payload.get("leaders") or {}).get("pts") or []
if pts_leaders:
    best = pts_leaders[0]
    top_scorer = f"{best.get('player_name', '—')} · {best.get('per_game', 0)} PPG"
    scoring_help = (
        f"Per-game rate, qualified at ≥{awards_payload.get('min_games', '?')} GP "
        f"({awards_payload.get('season', '—')}): computed from collected box "
        "scores, a homegrown metric rather than official NBA stats."
    )
elif not int(games_counts.get(season) or 0):
    scoring_help = (f"{season} has no collected games yet: the scoring "
                    "leader appears once the season starts.")
else:
    scoring_help = "Run refresh_dashboard_fallbacks.py where box scores exist."

col1, col2, col3 = st.columns(3)
col1.metric("Games collected", kpi_games, help=games_help)
col2.metric("Next tip-off", kpi_next, help=next_help)
col3.metric("Season scoring leader", top_scorer, help=scoring_help)

shared.hero(season, f"Teams source: {teams_note}")

tabs = st.tabs(["Standings", "Schedule & Scores", "Awards Ladder",
                "Court View"])

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
# Schedule & Scores: today live + the selected season's results with scores
# ---------------------------------------------------------------------------

with tabs[1]:
    today = shared.now_utc().strftime("%Y%m%d")
    scoreboard, sb_note = shared.load_scoreboard(today)
    shared.section("Today's games")
    if scoreboard.empty:
        st.caption(
            f"No games on {today}: {sb_note}. The NBA offseason runs Jun–Oct; "
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
        st.info(f"No collected schedule for {season} yet: run the collector "
                f"(`python src/collector/snapshot.py --season {season}`).")
    else:
        st.caption(f"Source: {sched_note}")
        if not upcoming.empty:
            # Season in progress (or not started): scores of what's played
            # plus the next fixtures. Historical leftovers (postponed rows
            # never replayed) are counted in the caption, not listed.
            if not finals.empty:
                recent = finals.sort_values(
                    "date", ascending=False).head(15).copy()
                recent["day"] = recent["date"].astype(str).str[:10]
                recent["matchup"] = (
                    recent["away_abbrev"] + " @ " + recent["home_abbrev"]
                    + "  " + recent["away_score"].astype(str)
                    + ":" + recent["home_score"].astype(str)
                )
                st.markdown("**Most recent results**")
                st.dataframe(
                    recent[["day", "matchup"]].rename(
                        columns={"day": "Date",
                                 "matchup": "Matchup (away : home)"}
                    ),
                    width="stretch", hide_index=True,
                )
            nxt = upcoming.head(25).copy()
            nxt["day"] = nxt["date"].astype(str).str[:10]
            nxt["matchup"] = nxt["away_abbrev"] + " @ " + nxt["home_abbrev"]
            st.markdown("**Upcoming**")
            st.dataframe(
                nxt[["day", "matchup"]].rename(
                    columns={"day": "Date", "matchup": "Matchup"}
                ),
                width="stretch", hide_index=True,
            )
            extra = (f" · {len(stale):,} postponed/canceled rows"
                     if not stale.empty else "")
            st.caption(
                f"{len(finals):,} final · {len(upcoming):,} upcoming "
                f"({games['game_id'].nunique():,} total){extra}: showing the "
                "15 most recent results and the next 25 fixtures."
            )
        else:
            # Finished season: the full schedule, every completed game with
            # its score (chronological; the dataframe header sorts too).
            table = finals.copy()
            table["Date"] = table["date"].astype(str).str[:10]
            table = table.rename(columns={
                "away_abbrev": "Away", "away_score": "Away pts",
                "home_abbrev": "Home", "home_score": "Home pts",
            })
            st.markdown("**Final scores (every completed game)**")
            st.dataframe(
                table[["Date", "Away", "Away pts", "Home", "Home pts"]],
                width="stretch", hide_index=True,
            )
            extra = (f" · {len(stale):,} postponed/canceled rows excluded"
                     if not stale.empty else "")
            st.caption(
                f"{len(finals):,} games, all final ({season} complete)"
                f"{extra}: chronological; click a column header to sort."
            )

# ---------------------------------------------------------------------------
# Awards Ladder: MVP / DPOY / 6th Man / MIP races + season stat leaders
# ---------------------------------------------------------------------------

with tabs[2]:
    st.caption(f"Source: {awards_note}")
    if not awards_payload.get("races"):
        if int(games_counts.get(season) or 0):
            st.info(
                "No award data yet: the races are computed from collected box "
                "scores (`python src/collector/refresh_dashboard_fallbacks.py`) "
                "and committed as data/dashboard_awards.json."
            )
        else:
            st.info(
                f"{season} has no collected games yet: the races and stat "
                "leaders appear once the season starts."
            )
    else:
        st.caption(
            "Homegrown transparent metrics, not official NBA voting. Each "
            "race's caption states the exact formula it ranks by."
        )
        races = awards_payload.get("races") or {}
        col_l, col_r = st.columns(2)

        def _race(key: str) -> None:
            """One race block: section header, top rows, formula caption."""
            shared.section(f"{awards.RACE_EMOJI.get(key, '')} "
                           f"{awards.RACE_LABELS.get(key, key)}")
            rows = races.get(key) or []
            if not rows:
                if key == "mip":
                    season_label = str(awards_payload.get("season", ""))
                    prev = (awards_payload.get("prev_season")
                            or awards.previous_season(season_label))
                    st.caption(
                        f"No MIP race yet: it compares against {prev}, "
                        "which has no collected box scores in this build."
                    )
                else:
                    st.caption(
                        f"No qualifiers yet: needs ≥"
                        f"{awards_payload.get('min_games', '?')} GP."
                    )
            for row in rows:
                st.markdown(shared.race_row_html(row, key),
                            unsafe_allow_html=True)
            st.caption(
                f"{awards.RACE_LABELS.get(key, key)} is homegrown math, not "
                f"official NBA voting: {awards.RACE_FORMULAS.get(key, '')}"
            )

        with col_l:
            _race("mvp")
            _race("sixth_man")
        with col_r:
            _race("dpoy")
            _race("mip")

        shared.section("📈 Season stat leaders")
        leaders_by_stat = awards_payload.get("leaders") or {}
        stat_keys = [k for k in awards.STAT_CATEGORIES
                     if k in leaders_by_stat] or list(leaders_by_stat)
        if not stat_keys:
            st.caption(
                f"No qualified leaders yet: needs ≥"
                f"{awards_payload.get('min_games', '?')} GP."
            )
        else:
            stat = st.radio(
                "Category", stat_keys, horizontal=True, key="awards_stat_cat",
                format_func=lambda k: (f"{awards.STAT_LABELS.get(k, k)} "
                                       f"({awards.STAT_ABBR.get(k, k)})"),
            )
            stat_rows = leaders_by_stat.get(stat) or []
            if not stat_rows:
                st.caption(
                    f"No qualified {awards.STAT_LABELS.get(stat, stat).lower()} "
                    f"leaders yet: needs ≥{awards_payload.get('min_games', '?')} "
                    "GP."
                )
            for row in stat_rows:
                st.markdown(shared.leader_row_html(row, stat),
                            unsafe_allow_html=True)
            st.caption(
                f"Per-game rate (the fair cross-pace comparison) among players "
                f"with ≥{awards_payload.get('min_games', '?')} GP · "
                f"{awards_payload.get('season', '—')}. Totals shown for context; "
                "FG/3P splits + FG% come straight from the collected box scores."
            )

# ---------------------------------------------------------------------------
# Court View: the stat race's leaders on a real court formation
# ---------------------------------------------------------------------------

with tabs[3]:
    st.caption(f"Source: {awards_note}")
    if not awards_payload.get("leaders"):
        if int(games_counts.get(season) or 0):
            st.info("No committed leader data yet: run "
                    "`python src/collector/refresh_dashboard_fallbacks.py` "
                    "where collected box scores exist.")
        else:
            st.info(f"{season} has no collected games yet: Court View "
                    "needs leaders from a played season.")
    else:
        leaders_by_stat = awards_payload.get("leaders") or {}
        stat_keys = [k for k in awards.STAT_CATEGORIES
                     if k in leaders_by_stat] or list(leaders_by_stat)
        stat = st.radio(
            "Category", stat_keys, horizontal=True, key="court_stat",
            format_func=lambda k: (f"{awards.STAT_LABELS.get(k, k)} "
                                   f"({awards.STAT_ABBR.get(k, k)})"),
        )
        rows = leaders_by_stat.get(stat) or []
        if not rows:
            st.caption(
                f"No qualified {awards.STAT_LABELS.get(stat, stat).lower()} "
                f"leaders yet: needs ≥{awards_payload.get('min_games', '?')} "
                "GP."
            )
        else:
            shared.render_court(
                rows,
                shared.load_positions(),
                stat,
                int(awards_payload.get("min_games") or 8),
                str(awards_payload.get("season", "—")),
            )
        st.caption(
            "Formation 2 G · 2 F · 1 C mirrors a fantasy-style starting "
            "lineup slot structure (backend optimizer untouched in "
            "src/model/optimizer.py); positions are ESPN's coarse G/F/C "
            "roster buckets, so PG/SG land in G and SF/PF in F."
        )
