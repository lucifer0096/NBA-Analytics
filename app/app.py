"""NBA Analytics dashboard -- Home page.

Run: streamlit run app/app.py

Two league tabs (NBA | WNBA); inside each league tab the four sections nest
as sub-tabs. Standings and Schedule are live-first from ESPN's free APIs
with committed offline fallbacks (see shared.py for the contract), while the
Awards Ladder and Court View are computed locally from the collected box
scores and follow that league's season selector (NBA 2010-11 -> upcoming;
WNBA 2010 -> upcoming):

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
                          metrics, explicitly NOT official league voting --
                          every race prints its exact formula as its caption;
                          a season with no collected games shows an empty
                          state
- **Court View**       -- the selected stat's leaders on a CSS-only hardwood
                          court: 2 G / 2 F / 1 C filled in rank order by each
                          player's real position, everyone else on a bench
                          strip, hover a card for the full per-game line

Deep links: ?league=wnba reorders the league tabs (the requested league
renders first -- st.tabs has no programmatic selection), ?tab=Awards Ladder
reorders the section sub-tabs inside BOTH leagues, and ?season=2026
preselects whichever season selector carries that label -- the two label
shapes never collide ('2025-26' is NBA-only, '2026' WNBA-only).

The sidebar carries one season selector per league, both leagues' games
inventory and a one-line Model & History mention (validation headline;
full numbers in models/metrics.json -- the model is NBA-trained, so the
WNBA tab runs no projections). Left-nav pages carry the rest:
- pages/2_All-Time_Stats.py    -- career boards across the collected window
- pages/3_GOAT_Rankings.py     -- the all-history GOAT ladder (both leagues)
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

import leagues

import awards

st.set_page_config(page_title="NBA Analytics", page_icon="🏀", layout="wide")

shared.inject_css()
st.title("NBA Analytics")

# ---------------------------------------------------------------------------
# Sidebar: one season selector per league + both inventories + freshness
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Settings")
    # ?season= deep link: preselect it in the selector whose label set
    # contains it; the other league falls back to its default (the newest
    # season with collected games) instead of crashing or inventing a year.
    deep_season = st.query_params.get("season") or ""
    seasons: dict = {}
    season_options: dict = {}
    for league in ("nba", "wnba"):
        options = shared.season_options(league)
        season_options[league] = options
        index = (options.index(deep_season) if deep_season in options
                 else (shared.default_season_index(options, league)
                       if options else None))
        newest = options[0] if options else "?"
        oldest = options[-1] if options else "?"
        seasons[league] = st.selectbox(
            f"Season ({leagues.display(league)})", options,
            index=index, key=f"season_{league}",
            help=f"Leads with the newest season that has collected games; "
                 f"every season from {oldest} through {newest} is selectable. "
                 f"Deep link: ?season={newest}&league={league}.",
        )
    st.caption(f"Today (UTC): {shared.now_utc():%Y-%m-%d}")
    st.markdown("---")
    for league in ("nba", "wnba"):
        shared.sidebar_games(shared.load_games_by_season(league),
                             season_options[league], league)
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
            st.caption(
                "Projection caveat: an availability signal (games played "
                "in the last 5, read only from PRIOR box scores) feeds "
                "every projection, so a player ramping up or missing "
                "games carries that context instead of his old form "
                "unchecked."
            )
            st.caption(
                "The model is trained on NBA box scores only: the WNBA tab "
                "shows no projections (refresh_dashboard_fallbacks.py skips "
                "them by design)."
            )
        else:
            st.caption("Model not trained yet: run `python "
                       "src/model/features.py && python "
                       "src/model/train.py`.")


# ---------------------------------------------------------------------------
# One league's whole page: KPI strip + hero + the four section sub-tabs.
# Both league tabs call this with their own season; every widget key is
# league-suffixed because Streamlit renders BOTH tabs' content in one run.
# ---------------------------------------------------------------------------

def _render_league(league: str, season: str, tab_labels: list) -> None:
    display = leagues.display(league)
    awards_json = leagues.named_path("", "dashboard_awards.json", league)
    refresh_cmd = ("python src/collector/refresh_dashboard_fallbacks.py"
                   + ("" if league == "nba" else f" --league {league}"))
    snapshot_cmd = ("python src/collector/snapshot.py"
                    + ("" if league == "nba" else f" --league {league}")
                    + f" --season {season}")

    teams, teams_note = shared.load_teams(league)

    # -------------------------------------------------------------------------
    # KPI strip: three glass cards scoped to the SELECTED season
    # -------------------------------------------------------------------------

    games, sched_note = shared.load_schedule(season, league)
    awards_payload, awards_note = shared.load_awards(season, league)
    games_counts = shared.load_games_by_season(league)

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
        top_scorer = (f"{best.get('player_name', '—')} · "
                      f"{best.get('per_game', 0)} PPG")
        scoring_help = (
            f"Per-game rate, qualified at ≥{awards_payload.get('min_games', '?')} GP "
            f"({awards_payload.get('season', '—')}): computed from collected box "
            f"scores, a homegrown metric rather than official {display} stats."
        )
    elif not int(games_counts.get(season) or 0):
        scoring_help = (f"{season} has no collected games yet: the scoring "
                        f"leader appears once the season starts.")
    else:
        scoring_help = "Run refresh_dashboard_fallbacks.py where box scores exist."

    col1, col2, col3 = st.columns(3)
    col1.metric("Games collected", kpi_games, help=games_help)
    col2.metric("Next tip-off", kpi_next, help=next_help)
    col3.metric("Season scoring leader", top_scorer, help=scoring_help)

    # One consolidated freshness line for the page's sources; every tab still
    # keeps its own Source caption (its honesty contract), this is the glance.
    shared.hero(season, shared.freshness_line(
        f"Teams: {teams_note}", f"Schedule: {sched_note}",
        f"Awards: {awards_note}"))

    # Section sub-tabs: the caller owns the ?tab= reordering so both league
    # tabs show the requested section first (labels are identical).
    tabs = dict(zip(tab_labels, st.tabs(tab_labels)))

    # -------------------------------------------------------------------------
    # Standings
    # -------------------------------------------------------------------------

    with tabs["Standings"]:
        standings, note = shared.load_standings(season, league)
        st.caption(f"Source: {note}")
        if standings.empty:
            st.info(f"No standings available for {season} yet.")
        else:
            played = any((r or 0) > 0
                         for r in standings.get("wins", pd.Series(dtype=int)))

            def _zone(seed):
                """Playoff / play-in / lottery glyph -- only meaningful with
                real records, so preseason (all-zero) tables get no zone
                marks. WNBA: a straight top-8 playoff field (15 franchises),
                no play-in rows."""
                if not played or pd.isna(seed):
                    return ""
                seed = int(seed)
                if league == "wnba":
                    return "🟢 " if seed <= 8 else ""
                if seed <= 6:
                    return "🟢 "
                if seed <= 10:
                    return "🟡 "
                return ""

            # Row decoration for the WHOLE table at once: both conference views
            # and the CSV export draw the same columns, so nothing can drift.
            full = standings.copy()
            full["record"] = (
                full["wins"].astype("Int64").astype(str)
                + "-"
                + full["losses"].astype("Int64").astype(str)
            )
            full["zone"] = full["playoff_seed"].map(_zone)
            # Team +/- (per-game point differential) and last-ten form:
            # signed text for the differential, blanks when ESPN omitted the
            # stat -- never a fabricated zero.
            if "differential" in full.columns:
                full["+/-"] = full["differential"].map(
                    lambda v: "" if v is None or pd.isna(v) else f"{v:+.1f}")
            if "last_ten" in full.columns:
                full["L10"] = full["last_ten"].map(
                    lambda v: "" if v is None or pd.isna(v) else str(v))
            renames = {
                "team": "Team", "win_percent": "Win%", "playoff_seed": "Seed",
                "streak": "Streak", "avg_points_for": "PF/g",
                "avg_points_against": "PA/g", "games_behind": "GB",
            }

            for conference in ("Eastern Conference", "Western Conference"):
                table = full[full["conference"] == conference].copy()
                if table.empty:
                    continue
                shared.section(conference)
                table = table.sort_values(
                    "playoff_seed", na_position="last"
                ).reset_index(drop=True)
                table.index = table.index + 1
                display_table = table.rename(columns=renames)
                keep = ["zone", "Team", "record", "Win%", "Seed", "Streak",
                        "PF/g", "PA/g", "+/-", "L10"]
                display_table = display_table[
                    [c for c in keep if c in display_table.columns]]
                st.dataframe(
                    display_table,
                    width="stretch",
                    column_config={
                        "zone": st.column_config.TextColumn("", width="small"),
                        "Team": st.column_config.TextColumn("Team",
                                                            width="large"),
                        "record": st.column_config.TextColumn("W-L",
                                                              width="small"),
                    },
                )
            if league == "wnba":
                st.caption("🟢 playoff field (top-8 of 15) · "
                           "+/- point differential per game · "
                           "L10 last ten games")
            else:
                st.caption("🟢 top-6 (playoff) · 🟡 play-in (7–10) · "
                           "+/- point differential per game · L10 last ten games")
            # CSV of both conferences: the same decorated columns the tables
            # show (Conference kept so a spreadsheet can filter), blanks where
            # ESPN omitted a stat -- exactly what is on screen, never zeros.
            csv = full.rename(columns=renames).rename(
                columns={"conference": "Conference"})
            csv_cols = ["Conference", "Team", "record", "Win%", "Seed", "GB",
                        "Streak", "PF/g", "PA/g", "+/-", "L10"]
            csv = csv[[c for c in csv_cols if c in csv.columns]]
            st.download_button(
                "Download standings CSV", csv.to_csv(index=False).encode("utf-8"),
                file_name=f"standings_{season}.csv", mime="text/csv",
                key=f"standings_csv_{league}",
                help="Both conferences with the columns shown above.",
            )

    # -------------------------------------------------------------------------
    # Schedule & Scores: today live + the selected season's results w/ scores
    # -------------------------------------------------------------------------

    with tabs["Schedule & Scores"]:
        today = shared.now_utc().strftime("%Y%m%d")
        scoreboard, sb_note = shared.load_scoreboard(today, league)
        shared.section("Today's games")
        if scoreboard.empty:
            off_note = (
                "The NBA offseason runs Jun–Oct; historical results and the "
                "full upcoming schedule are below." if league == "nba" else
                "The WNBA offseason runs Nov–Apr; historical results and the "
                "full upcoming schedule are below.")
            st.caption(f"No games on {today}: {sb_note}. {off_note}")
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
                    f"(`{snapshot_cmd}`).")
        else:
            st.caption(f"Source: {sched_note}")
            # Toolbar: team + venue filters plus a CSV of whatever they pick.
            # Venue options depend on the team (home/away only mean something
            # relative to one), so the two selectboxes use separate keys --
            # each keeps a valid option list when the team flips back and
            # forth instead of stranding a stale value.
            f_team_c, f_venue_c, f_csv_c = st.columns([2, 2, 2])
            team_pool = set()
            for col in ("home_abbrev", "away_abbrev"):
                if col in games.columns:
                    team_pool |= set(games[col].dropna().astype(str))
            team_pick = f_team_c.selectbox(
                "Team", ["All teams"] + sorted(team_pool),
                key=f"sched_team_{league}",
                help="Shows only that team's fixtures (home or away).")
            if team_pick == "All teams":
                venue_pick = f_venue_c.selectbox(
                    "Venue", ["All", "Neutral"], key=f"sched_venue_all_{league}",
                    help="Neutral-site games only (international / relocated "
                         "arenas). Pick a team first for home/away filters.")
            else:
                venue_pick = f_venue_c.selectbox(
                    "Venue", ["All", "Home", "Away", "Neutral"],
                    key=f"sched_venue_team_{league}",
                    help=f"Relative to {team_pick}: his home games, away "
                         "games, or neutral-site games.")
            view = shared.filter_schedule_games(games, team_pick, venue_pick)
            # The filters scope the results, fixtures and the game-detail
            # picker below (same three frames, re-derived); the KPI strip
            # above already took its numbers from the unfiltered ones.
            finals = view[view["status"] == "STATUS_FINAL"].sort_values("date")
            _rest_view = view[view["status"] != "STATUS_FINAL"]
            upcoming = _rest_view[
                _rest_view["date"].astype(str).str[:10] >= _today
            ].sort_values("date")
            stale = _rest_view[
                _rest_view["date"].astype(str).str[:10] < _today
            ]
            _venue_words = {"Home": "home", "Away": "away",
                            "Neutral": "neutral-site"}
            _filter_bits = ([] if team_pick == "All teams" else [team_pick]) + (
                [] if venue_pick == "All"
                else [f"{_venue_words[venue_pick]} games"])
            filter_desc = " · ".join(_filter_bits)
            if filter_desc:
                st.caption(f"{len(view):,} of {len(games):,} games · "
                           f"{filter_desc}")
            export = view.rename(columns={
                "date": "Date", "status": "Status", "neutral": "Neutral",
                "away_abbrev": "Away", "away_score": "Away pts",
                "home_abbrev": "Home", "home_score": "Home pts",
            })
            export_cols = ["Date", "Status", "Neutral", "Away", "Away pts",
                           "Home", "Home pts"]
            export = export[[c for c in export_cols if c in export.columns]]
            f_csv_c.download_button(
                "Download schedule CSV", export.to_csv(index=False).encode("utf-8"),
                file_name=f"{season}_schedule.csv", mime="text/csv",
                key=f"schedule_csv_{league}",
                help="Every fixture the filters above select, with its status.",
            )
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
                    f"({view['game_id'].nunique():,} total){extra}: showing the "
                    "15 most recent results and the next 25 fixtures."
                )
            elif not finals.empty:
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
            elif filter_desc:
                st.caption(f"No games match {filter_desc} in {season}.")
            else:
                st.caption(
                    f"No completed or upcoming games in {season}"
                    + (f": {len(stale):,} postponed/canceled rows only."
                       if not stale.empty else "."))

            # NBA-app-style fixture detail: pick ANY game of the season (final
            # or upcoming) for its box score + full play-by-play, or the
            # tip-off/TV/venue of a game not played yet -- fetched live from
            # ESPN's summary endpoint (cached 15 min), because the committed
            # schedule carries scores but not per-player lines or PBP.
            shared.section("Game detail: box score & play-by-play")
            labels: list = []
            game_ids: list = []
            seen: set = set()
            for frame, is_final in ((finals.sort_values("date",
                                                         ascending=False), True),
                                    (upcoming.sort_values("date"), False)):
                for row in frame.itertuples():
                    day = str(row.date)[:10]
                    if is_final:
                        label = (f"{day}  {row.away_abbrev} {row.away_score}"
                                 f" @ {row.home_abbrev} {row.home_score}")
                    else:
                        when = str(row.date)[:16].replace("T", " ")
                        label = f"{when}  {row.away_abbrev} @ {row.home_abbrev}"
                    if label in seen:
                        label = f"{label} · {row.game_id}"
                    seen.add(label)
                    labels.append(label)
                    game_ids.append(str(row.game_id))
            if not labels:
                st.caption(f"No fixtures match {filter_desc} for {season}."
                           if filter_desc else
                           "No fixtures listed for this season yet.")
            else:
                pick = st.selectbox(
                    "Fixture", labels, index=0, key=f"fixture_{league}",
                    help="Box score and play-by-play (final), tip-off, TV and "
                         "venue (upcoming). Live from ESPN, cached 15 minutes.")
                meta, box_rows, plays, note = shared.load_game_summary(
                    game_ids[labels.index(pick)], league)
                st.caption(f"Source: {note}")
                if meta:
                    away = meta.get("away_abbrev") or "?"
                    home = meta.get("home_abbrev") or "?"
                    if meta.get("status") == "STATUS_FINAL":
                        st.markdown(
                            f"**{away} {shared.fmt_score(meta.get('away_score'))}"
                            f" @ {home} "
                            f"{shared.fmt_score(meta.get('home_score'))}**")
                        shared.render_box_sides(pd.DataFrame(box_rows), meta)
                        shared.render_play_by_play(plays, league)
                    else:
                        when = str(meta.get("date") or "")[:16].replace("T", " ")
                        status = (str(meta.get("status") or "")
                                  .replace("STATUS_", "").replace("_", " ")
                                  .title())
                        parts = [f"**{away} @ {home}**", when, status]
                        if meta.get("venue"):
                            parts.append(str(meta["venue"]))
                        parts.extend(meta.get("airings") or [])
                        st.markdown(" · ".join(p for p in parts if p))
                        st.caption("No box score until this game is played: "
                                   "the collector stores it as a final result.")

    # -------------------------------------------------------------------------
    # Awards Ladder: MVP / DPOY / 6th Man / MIP races + season stat leaders
    # -------------------------------------------------------------------------

    with tabs["Awards Ladder"]:
        st.caption(f"Source: {awards_note}")
        if not awards_payload.get("races"):
            if int(games_counts.get(season) or 0):
                st.info(
                    "No award data yet: the races are computed from collected "
                    f"box scores (`{refresh_cmd}`) "
                    f"and committed as data/{awards_json}."
                )
            else:
                st.info(
                    f"{season} has no collected games yet: the races and stat "
                    "leaders appear once the season starts."
                )
        else:
            st.caption(
                f"Homegrown transparent metrics, not official {display} voting. "
                "Each race's caption states the exact formula it ranks by."
            )
            races = awards_payload.get("races") or {}
            # Daily race snapshots feed the ladders' movement arrows (rank
            # delta between the last two snapshots) and the MVP trend below.
            snapshots, race_note = shared.load_race_history(season, league)
            moves = {key: shared.race_movement(snapshots, key) for key in races}
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
                                or awards.previous_season(season_label, league))
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
                    pid = int(row.get("player_id") or 0)
                    st.markdown(
                        shared.race_row_html(
                            row, key, (moves.get(key) or {}).get(pid, 0),
                            league),
                        unsafe_allow_html=True)
                st.caption(
                    f"{awards.RACE_LABELS.get(key, key)} is homegrown math, "
                    f"not official {display} voting: "
                    f"{awards.RACE_FORMULAS.get(key, '')}"
                )

            with col_l:
                _race("mvp")
                _race("sixth_man")
            with col_r:
                _race("dpoy")
                _race("mip")

            # MVP score across the daily snapshots: one line per current
            # leader. With fewer than two snapshots there is no line to draw --
            # the caption says what is missing instead of a one-point chart.
            trend = shared.race_trend_figure(snapshots, "mvp")
            if trend.data:
                shared.section("MVP race trend")
                st.plotly_chart(trend, width="stretch")
                st.caption(
                    f"MVP score across {race_note}: the top "
                    f"{shared.RACE_TREND_TOP} of the latest snapshot, and the "
                    "ladder arrows compare its last two. "
                    "refresh_dashboard_fallbacks.py records one snapshot per "
                    "daily run.")
            else:
                st.caption(
                    f"MVP race trend: {race_note} -- a line and the ladders' "
                    "rank arrows need at least two snapshots.")

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
                    "Category", stat_keys, horizontal=True,
                    key=f"awards_stat_cat_{league}",
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
                    st.markdown(shared.leader_row_html(row, stat, league),
                                unsafe_allow_html=True)
                st.caption(
                    "Per-game rate (the fair cross-pace comparison) among players "
                    f"with ≥{awards_payload.get('min_games', '?')} GP · "
                    f"{awards_payload.get('season', '—')}. Totals shown for context; "
                    "FG/3P splits + FG% come straight from the collected box scores."
                )

    # -------------------------------------------------------------------------
    # Court View: the stat race's leaders on a real court formation
    # -------------------------------------------------------------------------

    with tabs["Court View"]:
        st.caption(f"Source: {awards_note}")
        if not awards_payload.get("leaders"):
            if int(games_counts.get(season) or 0):
                st.info("No committed leader data yet: run "
                        f"`{refresh_cmd}` "
                        "where collected box scores exist.")
            else:
                st.info(f"{season} has no collected games yet: Court View "
                        "needs leaders from a played season.")
        else:
            leaders_by_stat = awards_payload.get("leaders") or {}
            stat_keys = [k for k in awards.STAT_CATEGORIES
                         if k in leaders_by_stat] or list(leaders_by_stat)
            stat = st.radio(
                "Category", stat_keys, horizontal=True,
                key=f"court_stat_{league}",
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
                    shared.load_positions(league),
                    stat,
                    int(awards_payload.get("min_games") or 8),
                    str(awards_payload.get("season", "—")),
                    league,
                )
            if league == "wnba":
                court_note = (
                    "Formation 2 G · 2 F · 1 C mirrors a fantasy-style starting "
                    "lineup slot structure (src/model/'s optimizer and "
                    "projections are NBA-only); positions are ESPN's coarse "
                    "G/F/C roster buckets, so PG/SG land in G and SF/PF in F.")
            else:
                court_note = (
                    "Formation 2 G · 2 F · 1 C mirrors a fantasy-style starting "
                    "lineup slot structure (backend optimizer untouched in "
                    "src/model/optimizer.py); positions are ESPN's coarse G/F/C "
                    "roster buckets, so PG/SG land in G and SF/PF in F.")
            st.caption(court_note)


# ?league= deep link: shared.league_tabs() renders the requested league
# FIRST (st.tabs has no programmatic selection; the other keeps its
# pinned position) -- same pattern as the ?tab= section reorder below.
league_tabs = shared.league_tabs()

# ?tab= deep link: the section labels are identical inside both league tabs,
# so one reorder serves both (requested section first, pinned default after).
tab_labels = ["Standings", "Schedule & Scores", "Awards Ladder", "Court View"]
deep_tab = st.query_params.get("tab") or ""
if deep_tab in tab_labels:
    tab_labels = [deep_tab] + [label for label in tab_labels
                               if label != deep_tab]

with league_tabs["NBA"]:
    _render_league("nba", seasons["nba"], tab_labels)

with league_tabs["WNBA"]:
    _render_league("wnba", seasons["wnba"], tab_labels)

# Corrupt-fallback warnings ride at the very END: all content first, then
# at most one warning per broken file (a clean run shows none -- the app's
# only st.warning()s, collected while the page rendered).
shared.render_fallback_warnings()
