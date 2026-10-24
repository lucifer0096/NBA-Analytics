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
    # The reader's local 'today' line lives in the hero strip, not here:
    # it is page context, not a setting, and the sidebar burying it made
    # "what's on today" easy to miss.
    st.markdown("---")
    shared.sidebar_inventory([
        (league, shared.load_games_by_season(league), season_options[league])
        for league in ("nba", "wnba")
    ])
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
    # The viewer's zone, resolved once per render: every game time below
    # (today's slate, Date columns, fixture labels, the tip-off split)
    # converts into it, so a reader in New Zealand reads their own clock.
    tz = shared.viewer_tz()
    awards_json = leagues.named_path("", "dashboard_awards.json", league)
    refresh_cmd = ("python src/collector/refresh_dashboard_fallbacks.py"
                   + ("" if league == "nba" else f" --league {league}"))
    snapshot_cmd = ("python src/collector/snapshot.py"
                    + ("" if league == "nba" else f" --league {league}")
                    + f" --season {season}")

    teams, teams_note = shared.load_teams(league)

    # -------------------------------------------------------------------------
    # Page data (feeds the hero line and the KPI strip)
    # -------------------------------------------------------------------------

    games, sched_note = shared.load_schedule(season, league)
    awards_payload, awards_note = shared.load_awards(season, league)
    games_counts = shared.load_games_by_season(league)

    # -------------------------------------------------------------------------
    # Hero line: the page's CONTEXT -- season pill, the reader's own 'today'
    # pill, and the consolidated data freshness -- rendered above the KPI
    # strip it frames, so the numbers below are never read unmoored from the
    # season and the clock they describe.
    # -------------------------------------------------------------------------

    shared.hero(
        season,
        shared.freshness_line(f"Teams: {teams_note}",
                              f"Schedule: {sched_note}",
                              f"Awards: {awards_note}"),
        extra=f"Today ({shared.tz_label(tz)}): {shared.local_today(tz)}",
    )

    # -------------------------------------------------------------------------
    # KPI strip: three glass cards scoped to the SELECTED season
    # -------------------------------------------------------------------------

    if not games.empty:
        finals = games[games["status"] == "STATUS_FINAL"].sort_values("date")
        rest = games[games["status"] != "STATUS_FINAL"]
        # The split follows the READER's calendar day, not the server's
        # UTC one: a 23:30Z tip is tomorrow in Auckland, and filing rows
        # by the UTC date pushed today's fixtures into the postponed bucket.
        _today = shared.local_today(tz)
        _local_day = shared.local_dates(rest["date"], tz)
        upcoming = rest[_local_day >= _today].sort_values("date")
        stale = rest[_local_day < _today]  # postponed etc.
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

    # Next tip-off exists only for seasons with unplayed games left. The
    # value carries the local TIME as well as the day: a tip-off without a
    # clock is half an answer to "when does it start".
    kpi_next = (shared.local_stamp(upcoming.iloc[0]["date"], tz)
                if not upcoming.empty else "—")
    next_help = ("First unplayed game of this season's schedule, on your "
                 "local clock."
                 if not upcoming.empty else
                 "No unplayed games left in this season.")

    top_scorer = "—"
    scorer_delta = None
    pts_leaders = (awards_payload.get("leaders") or {}).get("pts") or []
    if pts_leaders:
        best = pts_leaders[0]
        # Name on the tile, the PPG rate as a muted second line: a long
        # "Name · 32.4 PPG" wraps the big metric font into a wall of text.
        top_scorer = str(best.get("player_name") or "—")
        scorer_delta = f"{best.get('per_game', 0)} PPG"
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
    # delta_color="off": the PPG rate is context, not a movement to judge.
    col3.metric("Season scoring leader", top_scorer, delta=scorer_delta,
                delta_color="off", help=scoring_help)

    # Section sub-tabs: the caller owns the ?tab= reordering so both league
    # tabs show the requested section first (labels are identical).
    tabs = dict(zip(tab_labels, st.tabs(tab_labels)))

    # -------------------------------------------------------------------------
    # Standings
    # -------------------------------------------------------------------------

    with tabs["Standings"]:
        standings, note = shared.load_standings(season, league)
        shared.source_caption(note)
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
                keep = ["zone", "Team", "record", "Win%", "GB", "Seed",
                        "Streak", "PF/g", "PA/g", "+/-", "L10"]
                display_table = display_table[
                    [c for c in keep if c in display_table.columns]]
                # GB and Win% render as NUMBERS: right-aligned next to the
                # other numeric columns, and a header click sorts
                # numerically ('12.5' after '2.5', never before it). ESPN's
                # '-' leader mark becomes the 0.0 it means; the CSV export
                # keeps the raw strings.
                if "GB" in display_table.columns:
                    display_table["GB"] = pd.to_numeric(
                        display_table["GB"].replace("-", 0.0),
                        errors="coerce").astype("Float64")
                if "Win%" in display_table.columns:
                    display_table["Win%"] = pd.to_numeric(
                        display_table["Win%"], errors="coerce").astype(
                            "Float64")
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
        # 'Today' is the READER's day: the loader pulls the ESPN buckets
        # covering it and keeps the tips inside it, so a viewer in New
        # Zealand sees the slate playing on their clock (their evening is
        # the US's PREVIOUS date) instead of tomorrow's games or nothing.
        today = shared.local_today(tz)
        scoreboard, sb_note = shared.load_today_scoreboard(league, str(tz))
        shared.section("Today's games")
        if scoreboard.empty:
            off_note = (
                "The NBA offseason runs Jun–Oct; historical results and the "
                "full upcoming schedule are below." if league == "nba" else
                "The WNBA offseason runs Nov–Apr; historical results and the "
                "full upcoming schedule are below.")
            # Info block, like every other empty state on the page: during
            # the offseason this line IS the answer to "what's on today",
            # and quiet small print reads like a broken section.
            st.info(f"No games on {today}: {sb_note}. {off_note}")
        else:
            # game_id/raw UTC date are internal: the reader gets the local
            # tip-off column (zoned in the header) in their place; the
            # Status cell shows a live game's REAL period and clock
            # ('Q3 4:32') and human words otherwise (the raw STATUS_* enum
            # stays in the CSV exports -- data, not display); scores
            # render as numbers, so the columns right-align and sort
            # numerically -- blanks stay blank, never a fabricated 0.
            _slate = scoreboard.drop(columns=["game_id", "date"],
                                     errors="ignore").copy()
            for _col in ("status_period", "status_clock"):
                if _col not in _slate.columns:
                    _slate[_col] = None
            _slate["status"] = [
                shared.status_detail(s, p, c) for s, p, c in zip(
                    _slate["status"], _slate["status_period"],
                    _slate["status_clock"])]
            for _col in ("away_score", "home_score"):
                _slate[_col] = pd.to_numeric(
                    _slate[_col], errors="coerce").astype("Int64")
            _tip = f"Tip-off ({shared.tz_label(tz)})"
            st.dataframe(
                _slate.rename(columns={
                        "time": _tip, "away": "Away",
                        "home": "Home",
                        "away_score": "Away pts",
                        "home_score": "Home pts",
                        "status": "Status",
                    })[[
                        _tip, "Status", "Away", "Away pts",
                        "Home", "Home pts",
                    ]],
                width="stretch", hide_index=True,
            )
            shared.source_caption(sb_note)

        shared.section(f"{season} schedule & results")
        if games.empty:
            st.info(f"No collected schedule for {season} yet: run the collector "
                    f"(`{snapshot_cmd}`).")
        else:
            shared.source_caption(sched_note)
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
            _view_day = shared.local_dates(_rest_view["date"], tz)
            upcoming = _rest_view[_view_day >= _today].sort_values("date")
            stale = _rest_view[_view_day < _today]
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
                    recent["day"] = shared.local_dates(recent["date"], tz)
                    recent["matchup"] = (
                        recent["away_abbrev"] + " @ " + recent["home_abbrev"]
                        + " · " + recent["away_score"].astype(str)
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
                # Upcoming rows carry the TIP time, not just the day: what
                # a reader needs is when it starts on their clock.
                nxt["day"] = shared.local_dates(nxt["date"], tz, with_time=True)
                nxt["matchup"] = nxt["away_abbrev"] + " @ " + nxt["home_abbrev"]
                st.markdown("**Upcoming**")
                st.dataframe(
                    nxt[["day", "matchup"]].rename(
                        columns={"day": f"Tip-off ({shared.tz_label(tz)})",
                                 "matchup": "Matchup"}
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
                # its score (chronological; the dataframe header sorts too --
                # numerically, now that the scores are numbers on screen).
                table = finals.copy()
                table["Date"] = shared.local_dates(table["date"], tz)
                for _col in ("away_score", "home_score"):
                    table[_col] = pd.to_numeric(
                        table[_col], errors="coerce").astype("Int64")
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

            # -----------------------------------------------------------------
            # Postseason: playoffs + play-in, clearly labeled and sourced from
            # data/postseason*/ (its OWN tree, separate from schedules/) so
            # standings, leaders, GOAT and the model stay regular-season-only.
            # Same team/venue filters as the results above, its own CSV, and
            # an honest empty state for a bracket that doesn't exist yet.
            # -----------------------------------------------------------------
            shared.section(f"{season} postseason")
            post, post_note = shared.load_postseason_schedule(season, league)
            post_view = shared.filter_schedule_games(post, team_pick,
                                                     venue_pick)
            post_finals = (post_view[post_view["status"] == "STATUS_FINAL"]
                           if not post_view.empty else post_view)
            post_upcoming = (post_view[post_view["status"] != "STATUS_FINAL"]
                             if not post_view.empty else post_view)
            if post.empty:
                st.caption(
                    f"No postseason schedule for {season}: the bracket does "
                    "not exist until the regular season ends, or it has not "
                    f"been collected yet (`{snapshot_cmd}`).")
            elif post_view.empty:
                st.caption(f"No postseason games match {filter_desc} "
                           f"in {season}.")
            else:
                shared.source_caption(post_note)
                table = post_view.copy().sort_values("date")
                table["Date"] = shared.local_dates(table["date"], tz)
                table["Status"] = table["status"].map(shared.status_label)
                for _col in ("away_score", "home_score"):
                    table[_col] = pd.to_numeric(
                        table[_col], errors="coerce").astype("Int64")
                table = table.rename(columns={
                    "away_abbrev": "Away", "away_score": "Away pts",
                    "home_abbrev": "Home", "home_score": "Home pts",
                })
                st.dataframe(
                    table[["Date", "Status", "Away", "Away pts",
                           "Home", "Home pts"]],
                    width="stretch", hide_index=True,
                )
                post_export = post_view.rename(columns={
                    "date": "Date", "status": "Status", "neutral": "Neutral",
                    "away_abbrev": "Away", "away_score": "Away pts",
                    "home_abbrev": "Home", "home_score": "Home pts",
                })
                post_export = post_export[[
                    c for c in ("Date", "Status", "Neutral", "Away",
                                "Away pts", "Home", "Home pts")
                    if c in post_export.columns]]
                st.download_button(
                    "Download postseason CSV",
                    post_export.to_csv(index=False).encode("utf-8"),
                    file_name=f"{season}_postseason.csv", mime="text/csv",
                    key=f"postseason_csv_{league}",
                    help="Playoffs and play-in fixtures the filters above "
                         "select, with their scores and status.",
                )
                st.caption(
                    f"{len(post_finals):,} final · {len(post_upcoming):,} "
                    f"upcoming ({post_view['game_id'].nunique():,} total) · "
                    "playoffs + play-in: not part of standings, leaders, "
                    "GOAT or the projection model, which stay "
                    "regular-season-only.")

            # NBA-app-style fixture detail: pick ANY game of the season
            # (regular or postseason, final or upcoming) for its box score +
            # full play-by-play, or the tip-off/TV/venue of a game not played
            # yet -- fetched live from ESPN's summary endpoint (cached 15
            # min), because the committed schedule carries scores but not
            # per-player lines or PBP. Postseason labels carry a (PO) mark.
            shared.section("Game detail: box score & play-by-play")
            labels: list = []
            game_ids: list = []
            seen: set = set()
            detail_frames = ((finals, True, False),
                             (post_finals, True, True),
                             (upcoming, False, False),
                             (post_upcoming, False, True))
            for frame, is_final, is_post in detail_frames:
                if frame.empty:  # also skips a season with no postseason file
                    continue
                for row in frame.sort_values(
                        "date", ascending=not is_final).itertuples():
                    if is_final:
                        day = shared.local_stamp(row.date, tz, "%Y-%m-%d")
                        label = (f"{day} · {row.away_abbrev} {row.away_score}"
                                 f" @ {row.home_abbrev} {row.home_score}")
                    else:
                        when = shared.local_stamp(row.date, tz)
                        label = (f"{when} · {row.away_abbrev} "
                                 f"@ {row.home_abbrev}")
                    if is_post:
                        label += " (PO)"
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
                    help="Box score and play-by-play (final or in progress), "
                         "tip-off, TV and venue (upcoming). Live from ESPN, "
                         "cached 15 minutes.")
                meta, box_rows, plays, note = shared.load_game_summary(
                    game_ids[labels.index(pick)], league)
                shared.source_caption(note)
                if meta:
                    away = meta.get("away_abbrev") or "?"
                    home = meta.get("home_abbrev") or "?"
                    if box_rows:
                        # Whichever state the game is in: the summary
                        # endpoint ships the box score + PBP for finals
                        # AND games in progress, so a live game renders its
                        # live box -- the status word rides the matchup
                        # line so a half-played game never reads final.
                        header = (f"**{away} "
                                  f"{shared.fmt_score(meta.get('away_score'))}"
                                  f" @ {home} "
                                  f"{shared.fmt_score(meta.get('home_score'))}"
                                  "**")
                        if meta.get("status") != "STATUS_FINAL":
                            header += (f" · {shared.status_label(meta.get('status'))}")
                        st.markdown(header)
                        shared.render_box_sides(pd.DataFrame(box_rows), meta)
                        shared.render_play_by_play(plays, league)
                    else:
                        when = shared.local_stamp(meta.get("date"), tz)
                        status = shared.status_label(meta.get("status"))
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
        shared.source_caption(awards_note)

        # Season headline: the SELECTED season's two official awards off
        # ESPN's award index -- MVP first, then Finals MVP -- so the page
        # opens on who actually won, not on a scoring leader. Unconditional
        # (an unannounced season renders honest pending rows), and the
        # stat leaders stay below the races where they were.
        shared.section("Official season awards")
        official_top = shared.load_official_winners(season, league)
        st.markdown(
            shared.season_award_row_html(
                "MVP", (official_top.get("mvp") or [None])[0]),
            unsafe_allow_html=True)
        finals_top = next((r for r in shared.load_finals_mvp(league)[0]
                           if r.get("season") == season), None)
        st.markdown(shared.season_award_row_html("Finals MVP", finals_top),
                    unsafe_allow_html=True)
        st.caption(
            "Official winners from ESPN's award index for the selected "
            "season -- the same source as the honours and the tracker "
            "below. A dash means the award isn't announced yet."
        )

        # Live in-season MVP race: only while the selected season is the
        # one being played -- a finished season's race is the committed
        # one further down (never re-fetched), and this is the page's most
        # expensive read (leaderboards + one totals call per candidate),
        # hence the 10 min cache. Movement arrows come from the committed
        # daily snapshots; the MVP trend chart below already covers the
        # trend, so it is never drawn twice.
        if season == shared.current_season_label(league):
            shared.section("MVP progress tracker -- live")
            live_rows, live_note = shared.load_live_mvp_race(season, league)
            if live_rows:
                snapshots, _ = shared.load_race_history(season, league)
                moves = shared.race_movement(snapshots, "mvp")
                for row in live_rows[:shared.LIVE_MVP_SHOW]:
                    row["player_name"] = shared.live_athlete_name(
                        int(row["player_id"]), league)
                    st.markdown(shared.race_row_html(
                        row, "mvp",
                        moves.get(int(row["player_id"]), 0), league),
                        unsafe_allow_html=True)
            else:
                st.info(live_note)
            st.caption(
                f"MVP race is homegrown math, not official {display} "
                f"voting: {awards.RACE_FORMULAS['mvp']} · {live_note}")

        # Finals MVP calculator: the season MVP formula on the selected
        # season's Finals series, beside the same official winner the
        # headline just showed -- formula vs award, one glance. Live ESPN
        # box scores (15 min cache), so it also tracks the series WHILE it
        # is played (the series block names the Finals mid-series); an
        # honest info row when the Finals isn't in the record yet or ESPN
        # is unreachable, never a guess.
        shared.section("Finals MVP calculator -- official vs formula")
        fmvp_rows, fmvp_info, fmvp_note = shared.load_finals_series(
            season, league)
        if fmvp_rows:
            official = ([finals_top]
                        if (finals_top or {}).get("player_name") else [])
            st.markdown(shared.official_vs_algo_row_html(
                "finals_mvp", official, fmvp_rows[0]),
                unsafe_allow_html=True)
            for row in fmvp_rows[:shared.FINALS_MVP_FORMULA_SHOW]:
                st.markdown(shared.race_row_html(
                    row, "finals_mvp", league=league),
                    unsafe_allow_html=True)
        else:
            st.info(fmvp_note)
        st.caption(
            f"Formula (homegrown math, not official voting): "
            f"{awards.FINALS_MVP_FORMULA}")
        if fmvp_rows:
            st.caption(
                f"Series: {fmvp_info['standings']} · Qualifier: appeared in "
                f"at least {fmvp_info['min_gp']} of the series' "
                f"{fmvp_info['games']} games · "
                f"Source: {shared.localize_note(fmvp_note)}"
                f"{shared.freshness_age(fmvp_note)}")

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
                st.plotly_chart(trend, width="stretch",
                                config=shared.CHART_CONFIG)
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

            # Official vs algorithm: each race's homegrown #1 next to the
            # season's ACTUAL award winner from ESPN's award index -- a
            # transparent scorecard, not official voting on either side.
            shared.section("Official vs algorithm winners")
            official_here = shared.load_official_winners(season, league)
            for race_key in ("mvp", "dpoy", "sixth_man", "mip"):
                st.markdown(
                    shared.official_vs_algo_row_html(
                        race_key, official_here.get(race_key) or [],
                        (races.get(race_key) or [None])[0]),
                    unsafe_allow_html=True)
            st.caption(
                "Official winner: ESPN's award index (the honours' own "
                "source); algorithm winner: the homegrown race's #1 above "
                "(transparent formula, not official voting). ✓ same "
                "player, ✗ different, dash not announced yet."
            )

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

        # Official Finals MVP tracker: ESPN's own award index (official
        # history, unlike the homegrown races above) -- current season
        # first, one row per season the award exists, pending until the
        # winner is announced.
        shared.section("🏆 Finals MVP")
        finals_rows, finals_note = shared.load_finals_mvp(league)
        decided_rows = [r for r in finals_rows if not r.get("pending")]
        if not decided_rows:
            st.info("No Finals MVP seasons in the committed index yet: "
                    f"regenerate it with `{refresh_cmd}`.")
        for row in finals_rows[:shared.FINAL_MVP_SHOW]:
            st.markdown(shared.finals_mvp_row_html(row, league),
                        unsafe_allow_html=True)
        if len(finals_rows) > shared.FINAL_MVP_SHOW:
            st.caption(f"Showing the {shared.FINAL_MVP_SHOW} most recent "
                       f"of {len(finals_rows)} seasons: the award index "
                       "carries the full history.")
        st.caption(finals_note)

    # -------------------------------------------------------------------------
    # Court View: the stat race's leaders on a real court formation
    # -------------------------------------------------------------------------

    with tabs["Court View"]:
        shared.source_caption(awards_note)
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
                    "Formation 2 G · 2 F · 1 C mirrors a traditional starting "
                    "lineup (src/model/'s optimizer and projections are "
                    "NBA-only); positions are ESPN's coarse G/F/C roster "
                    "buckets, so PG/SG land in G and SF/PF in F.")
            else:
                court_note = (
                    "Formation 2 G · 2 F · 1 C mirrors a traditional starting "
                    "lineup (the backend optimizer lives in "
                    "src/model/optimizer.py); positions are ESPN's coarse "
                    "G/F/C roster buckets, so PG/SG land in G and SF/PF in F.")
            st.caption(court_note)


# ?league= deep link: shared.league_tabs() renders the requested league
# FIRST (st.tabs has no programmatic selection; the other keeps its
# pinned position) -- same pattern as the ?tab= section reorder below.
league_tabs = shared.league_tabs()

# ?tab= deep link: the section labels are identical inside both league tabs,
# so one reorder serves both (requested section first, pinned default after).
# The landing section is today's slate: Schedule & Scores leads, then the
# standings, the ladder and the court (?tab= reorders either way).
tab_labels = ["Schedule & Scores", "Standings", "Awards Ladder", "Court View"]
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
