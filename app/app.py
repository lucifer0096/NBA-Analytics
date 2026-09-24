"""NBA Analytics dashboard -- Home page.

Run: streamlit run app/app.py

Three tabs: Standings and Schedule are live-first from ESPN's free APIs with
committed offline fallbacks (see shared.py for the contract), and Awards
Ladder is computed locally from the collected box scores:

- **Standings**    -- conference tables for the selected season
- **Schedule**     -- today's games live (scoreboard) + the selected season's
                      full schedule/results from the daily collector
- **Awards Ladder** -- MVP / DPOY / 6th Man / MIP races plus per-game stat
                      leaders (src/collector/awards.py, committed as
                      data/dashboard_awards.json): transparent homegrown
                      metrics, explicitly NOT official NBA voting -- every
                      race prints its exact formula as its caption

The Model & History page (pages/1_Model_and_History.py) carries metrics,
season leaders and the Court View.

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
awards_payload, awards_note = shared.load_awards()
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
scoring_help = "Run refresh_dashboard_fallbacks.py where box scores exist."
pts_leaders = (awards_payload.get("leaders") or {}).get("pts") or []
if pts_leaders:
    best = pts_leaders[0]
    top_scorer = f"{best.get('player_name', '—')} · {best.get('per_game', 0)} PPG"
    scoring_help = (
        f"Per-game rate, qualified at ≥{awards_payload.get('min_games', '?')} GP "
        f"({awards_payload.get('season', '—')}) — computed from collected box "
        "scores, a homegrown metric rather than official NBA stats."
    )

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
col3.metric("Season scoring leader", top_scorer, help=scoring_help)
col4.metric("Model (validation)", kpi_model, help=model_help)

shared.hero(season, f"Teams source: {teams_note}", extra="ESPN · no auth")

tabs = st.tabs(["Standings", "Schedule & Scores", "Awards Ladder"])

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
# Awards Ladder: MVP / DPOY / 6th Man / MIP races + season stat leaders
# ---------------------------------------------------------------------------

with tabs[2]:
    st.caption(f"Source: {awards_note}")
    if not awards_payload.get("races"):
        st.info(
            "No award data yet — the races are computed from collected box "
            "scores (`python src/collector/refresh_dashboard_fallbacks.py`) "
            "and committed as data/dashboard_awards.json."
        )
    else:
        st.caption(
            "Homegrown transparent metrics — not official NBA voting. Each "
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
                        f"No MIP race yet — it compares against {prev}, "
                        "which has no collected box scores in this build."
                    )
                else:
                    st.caption(
                        f"No qualifiers yet — needs ≥"
                        f"{awards_payload.get('min_games', '?')} GP."
                    )
            for row in rows:
                st.markdown(shared.race_row_html(row, key),
                            unsafe_allow_html=True)
            st.caption(
                f"{awards.RACE_LABELS.get(key, key)} is homegrown math, not "
                f"official NBA voting — {awards.RACE_FORMULAS.get(key, '')}"
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
        stat = st.radio(
            "Category", stat_keys, horizontal=True, key="awards_stat_cat",
            format_func=lambda k: (f"{awards.STAT_LABELS.get(k, k)} "
                                   f"({awards.STAT_ABBR.get(k, k)})"),
        )
        stat_rows = leaders_by_stat.get(stat) or []
        if not stat_rows:
            st.caption(
                f"No qualified {awards.STAT_LABELS.get(stat, stat).lower()} "
                f"leaders yet — needs ≥{awards_payload.get('min_games', '?')} "
                "GP."
            )
        for row in stat_rows:
            st.markdown(shared.leader_row_html(row, stat),
                        unsafe_allow_html=True)
        st.caption(
            f"Per-game rate (the fair cross-pace comparison) among players "
            f"with ≥{awards_payload.get('min_games', '?')} GP · "
            f"{awards_payload.get('season', '—')}. Totals shown for context."
        )
